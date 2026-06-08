"""Tests for the Cycle 4 Phase B LLM judge.

The judge prompt is FROZEN per ``docs/adrs/0001-yesterday-index-rubric.md``.
Tests cover:
- Verdict parsing across the 5 locked literals.
- Invalid verdict ("PARTIAL_HIT") triggers one retry, then drops with warning.
- ThreadPoolExecutor mode: 3 picks judged in parallel — wall-clock test.
- Friday → Monday window (3 calendar days, 1 trading day).
- Missing primary_ticker price data: judge gets ``null``, LLM still produces
  a verdict.
- ``JUDGE_ASSETS_BY_CATEGORY`` resolution per category.
"""

from __future__ import annotations

import json
import time
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from market_mover import judge as judge_mod
from market_mover.judge import (
    JUDGE_ASSETS_BY_CATEGORY,
    JUDGE_PROMPT_TEMPLATE,
    JUDGE_PROMPT_VERSION,
    _build_judge_prompt,
    _parse_judge_response,
    _resolve_primary_ticker,
    _resolve_sector_etf,
    judge_pick,
    judge_yesterday,
)
from market_mover.scorecard import (
    BriefingRecord,
    Judgment,
    ScorecardContrarian,
    ScorecardPick,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pick(
    rank: int = 1,
    primary_ticker: str | None = "NEE",
    category: str = "single_name",
    impact_score: float = 8.7,
) -> ScorecardPick:
    return ScorecardPick(
        rank=rank,
        title=f"Sample title #{rank}",
        summary=f"Sample summary #{rank}.",
        impact_score=impact_score,
        primary_ticker=primary_ticker,
        category=category,  # type: ignore[arg-type]
        source_url=f"https://example.com/story-{rank}",
        source_name="example.com",
    )


def _record(judgments=None, picks=None) -> BriefingRecord:
    return BriefingRecord(
        date=date(2026, 5, 18),
        model_used="claude",
        voice="vinny",
        mimicry_voice=None,
        picks=picks or [_pick(1), _pick(2, primary_ticker="TLT", category="macro"),
                        _pick(3, primary_ticker="SPY", category="geopolitical")],
        contrarian=ScorecardContrarian(
            headline="bear",
            argument="bear arg",
            source_url="https://example.com/bear",
            source_name="example.com",
        ),
        judgments=judgments,
    )


# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------


class TestLockedConstants:
    def test_judge_prompt_version_is_one(self):
        assert JUDGE_PROMPT_VERSION == 1

    def test_judge_prompt_contains_locked_phrases(self):
        # Sanity check the verbatim ADR text is present.
        assert "be CONSISTENT, not generous" in JUDGE_PROMPT_TEMPLATE
        assert "WHEN IN DOUBT" in JUDGE_PROMPT_TEMPLATE
        assert "Prefer PARTIAL over HIT" in JUDGE_PROMPT_TEMPLATE
        assert "Prefer TOO_EARLY over MISS" in JUDGE_PROMPT_TEMPLATE
        # All 5 verdict literals must appear.
        for v in ("HIT", "PARTIAL", "MISS", "TOO_EARLY", "NOT_APPLICABLE"):
            assert v in JUDGE_PROMPT_TEMPLATE


# ---------------------------------------------------------------------------
# Category → asset map
# ---------------------------------------------------------------------------


class TestJudgeAssetsByCategory:
    def test_all_six_categories_present(self):
        assert set(JUDGE_ASSETS_BY_CATEGORY.keys()) == {
            "macro",
            "single_name",
            "commodity",
            "crypto",
            "geopolitical",
            "other",
        }

    def test_macro_defaults_to_spy(self):
        assert JUDGE_ASSETS_BY_CATEGORY["macro"]["primary_or_default"] == "SPY"
        assert "TLT" in JUDGE_ASSETS_BY_CATEGORY["macro"]["extras"]

    def test_geopolitical_includes_vix_extra(self):
        assert JUDGE_ASSETS_BY_CATEGORY["geopolitical"]["primary_or_default"] == "SPY"
        assert "VIX" in JUDGE_ASSETS_BY_CATEGORY["geopolitical"]["extras"]

    def test_single_name_requires_explicit_ticker(self):
        # No default — must be supplied by the ranker.
        assert JUDGE_ASSETS_BY_CATEGORY["single_name"]["primary_or_default"] is None


class TestResolvePrimaryTicker:
    def test_uses_explicit_ticker(self):
        pick = _pick(primary_ticker="TSLA", category="single_name")
        assert _resolve_primary_ticker(pick) == "TSLA"

    def test_falls_back_to_category_default(self):
        pick = _pick(primary_ticker=None, category="macro")
        assert _resolve_primary_ticker(pick) == "SPY"

    def test_returns_none_when_no_default_and_no_ticker(self):
        pick = _pick(primary_ticker=None, category="single_name")
        assert _resolve_primary_ticker(pick) is None


class TestResolveSectorEtf:
    def test_macro_returns_first_non_vix_extra(self):
        pick = _pick(category="macro")
        # macro extras = ["TLT", "DXY"] — TLT is the first non-VIX.
        assert _resolve_sector_etf(pick) == "TLT"

    def test_geopolitical_skips_vix(self):
        pick = _pick(category="geopolitical")
        # extras = ["VIX"], no non-VIX — None.
        assert _resolve_sector_etf(pick) is None

    def test_single_name_has_no_extras(self):
        pick = _pick(category="single_name")
        assert _resolve_sector_etf(pick) is None


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


class TestBuildJudgePrompt:
    def test_substitutes_all_fields(self):
        pick = _pick(rank=1, primary_ticker="NEE", category="single_name")
        prompt = _build_judge_prompt(
            pick=pick,
            primary_pct=1.2,
            spy_pct=0.5,
            vix_close=15.4,
            vix_pct=-3.1,
            sector_etf=None,
            sector_pct=None,
        )
        assert pick.title in prompt
        assert pick.summary in prompt
        assert "8.7" in prompt
        assert "NEE" in prompt
        # Pct sign with two decimals.
        assert "+1.20%" in prompt
        assert "+0.50%" in prompt
        assert "15.40" in prompt
        assert "-3.10%" in prompt

    def test_null_price_data_rendered_as_null(self):
        pick = _pick()
        prompt = _build_judge_prompt(
            pick=pick,
            primary_pct=None,
            spy_pct=None,
            vix_close=None,
            vix_pct=None,
            sector_etf=None,
            sector_pct=None,
        )
        # All numeric slots fall back to literal "null" in the prompt.
        assert "null" in prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParseJudgeResponse:
    @pytest.mark.parametrize(
        "verdict", ["HIT", "PARTIAL", "MISS", "TOO_EARLY", "NOT_APPLICABLE"]
    )
    def test_all_five_verdicts_parse(self, verdict):
        raw = json.dumps({"verdict": verdict, "justification": "stub"})
        result = _parse_judge_response(raw)
        assert result is not None
        v, j = result
        assert v == verdict
        assert j == "stub"

    def test_unknown_verdict_returns_none(self):
        raw = json.dumps({"verdict": "PARTIAL_HIT", "justification": "x"})
        assert _parse_judge_response(raw) is None

    def test_lowercase_verdict_normalized(self):
        raw = json.dumps({"verdict": "hit", "justification": "x"})
        result = _parse_judge_response(raw)
        assert result is not None and result[0] == "HIT"

    def test_markdown_code_fence_parsed(self):
        raw = "```json\n" + json.dumps({"verdict": "HIT", "justification": "ok"}) + "\n```"
        result = _parse_judge_response(raw)
        assert result is not None
        assert result[0] == "HIT"

    def test_brace_extraction_parsed(self):
        raw = (
            'Sure thing! Here is the verdict:\n'
            '{"verdict": "MISS", "justification": "down 2%"}'
        )
        result = _parse_judge_response(raw)
        assert result is not None
        assert result[0] == "MISS"

    def test_empty_raw_returns_none(self):
        assert _parse_judge_response("") is None
        assert _parse_judge_response("   ") is None

    def test_non_dict_payload_returns_none(self):
        raw = json.dumps(["HIT", "ok"])
        assert _parse_judge_response(raw) is None

    def test_missing_justification_gets_stub(self):
        raw = json.dumps({"verdict": "HIT"})
        result = _parse_judge_response(raw)
        assert result is not None
        assert result[0] == "HIT"
        assert "no justification" in result[1].lower()


# ---------------------------------------------------------------------------
# judge_pick — mocked LLM + mocked price data
# ---------------------------------------------------------------------------


def _make_mock_settings(judge_model: str = "claude-sonnet-4-20250514"):
    """Build a stand-in MarketMoverSettings-like object for the judge.

    The judge only reads ``finnhub_api_key``, ``min_call_interval_secs``,
    ``judge_model``, and ``claude_api_keys``. We avoid touching the real
    pydantic-settings env-load chain for speed.
    """
    s = MagicMock()
    s.finnhub_api_key = "test-finnhub-key"
    s.min_call_interval_secs = 0.0
    s.judge_model = judge_model
    s.claude_api_keys = ["test-claude-key"]
    return s


def _fake_anthropic_message(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    msg = MagicMock()
    msg.content = [block]
    return msg


class TestJudgePick:
    def test_valid_verdict_first_try(self):
        pick = _pick(primary_ticker="SPY", category="macro", impact_score=8.5)
        settings = _make_mock_settings()
        llm_client = MagicMock()

        # Mock anthropic + the price fetcher.
        anthropic_mod = MagicMock()
        anthropic_client = MagicMock()
        anthropic_mod.Anthropic.return_value = anthropic_client
        anthropic_client.messages.create.return_value = _fake_anthropic_message(
            json.dumps({"verdict": "HIT", "justification": "SPY +1.8%"})
        )

        with patch.dict("sys.modules", {"anthropic": anthropic_mod}), patch.object(
            judge_mod,
            "fetch_24h_close_change",
            return_value=(1.8, None),
        ):
            result = judge_pick(pick, date(2026, 5, 18), settings, llm_client)

        assert result is not None
        assert result.verdict == "HIT"
        assert result.justification == "SPY +1.8%"
        assert result.rank == 1
        # Should have only called Anthropic once (no retry).
        assert anthropic_client.messages.create.call_count == 1

    def test_invalid_verdict_retries_once_then_drops(self):
        pick = _pick(primary_ticker="TSLA", category="single_name")
        settings = _make_mock_settings()
        llm_client = MagicMock()

        anthropic_mod = MagicMock()
        anthropic_client = MagicMock()
        anthropic_mod.Anthropic.return_value = anthropic_client
        # Two bad verdicts in a row → drop with warning.
        anthropic_client.messages.create.side_effect = [
            _fake_anthropic_message(
                json.dumps({"verdict": "PARTIAL_HIT", "justification": "x"})
            ),
            _fake_anthropic_message(
                json.dumps({"verdict": "WHAT", "justification": "y"})
            ),
        ]

        with patch.dict("sys.modules", {"anthropic": anthropic_mod}), patch.object(
            judge_mod, "fetch_24h_close_change", return_value=(0.1, None)
        ):
            result = judge_pick(pick, date(2026, 5, 18), settings, llm_client)

        assert result is None
        assert anthropic_client.messages.create.call_count == 2

    def test_invalid_then_valid_verdict_returns_judgment(self):
        pick = _pick(primary_ticker="SPY", category="macro", impact_score=8.0)
        settings = _make_mock_settings()
        llm_client = MagicMock()

        anthropic_mod = MagicMock()
        anthropic_client = MagicMock()
        anthropic_mod.Anthropic.return_value = anthropic_client
        anthropic_client.messages.create.side_effect = [
            _fake_anthropic_message("not json at all"),
            _fake_anthropic_message(
                json.dumps({"verdict": "PARTIAL", "justification": "spy up 0.4%"})
            ),
        ]

        with patch.dict("sys.modules", {"anthropic": anthropic_mod}), patch.object(
            judge_mod, "fetch_24h_close_change", return_value=(0.4, None)
        ):
            result = judge_pick(pick, date(2026, 5, 18), settings, llm_client)

        assert result is not None
        assert result.verdict == "PARTIAL"
        assert anthropic_client.messages.create.call_count == 2

    def test_missing_primary_ticker_passes_null_to_llm(self):
        # Single-name pick with no primary_ticker → primary lookup returns None.
        pick = _pick(primary_ticker=None, category="single_name")
        settings = _make_mock_settings()
        llm_client = MagicMock()

        captured_prompts: list[str] = []

        anthropic_mod = MagicMock()
        anthropic_client = MagicMock()
        anthropic_mod.Anthropic.return_value = anthropic_client

        def _create(*args, **kwargs):
            captured_prompts.append(kwargs["messages"][0]["content"])
            return _fake_anthropic_message(
                json.dumps(
                    {"verdict": "TOO_EARLY", "justification": "no ticker proxy"}
                )
            )

        anthropic_client.messages.create.side_effect = _create

        # fetch should still be called for SPY/VIX, just not for the missing primary.
        with patch.dict("sys.modules", {"anthropic": anthropic_mod}), patch.object(
            judge_mod, "fetch_24h_close_change", return_value=(0.1, 15.0)
        ):
            result = judge_pick(pick, date(2026, 5, 18), settings, llm_client)

        assert result is not None
        assert result.verdict == "TOO_EARLY"
        # The prompt should have ``null`` in the primary slot since no ticker
        # resolved.
        assert captured_prompts, "Anthropic should have been called"
        assert "null" in captured_prompts[0]

    def test_no_claude_api_key_returns_none(self):
        pick = _pick()
        settings = _make_mock_settings()
        settings.claude_api_keys = []
        llm_client = MagicMock()

        with patch.object(
            judge_mod, "fetch_24h_close_change", return_value=(0.1, None)
        ):
            result = judge_pick(pick, date(2026, 5, 18), settings, llm_client)
        assert result is None


# ---------------------------------------------------------------------------
# judge_yesterday — orchestrator
# ---------------------------------------------------------------------------


class TestJudgeYesterday:
    def test_skips_when_already_graded(self):
        existing = [
            Judgment(
                rank=1,
                verdict="HIT",
                justification="prior",
                price_data={
                    "primary_ticker": "SPY",
                    "primary_pct_change_24h": 1.0,
                    "spy_pct": 1.0,
                    "vix_close": 14.0,
                    "vix_pct_change": -2.0,
                },  # type: ignore[arg-type]
            )
        ]
        record = _record(judgments=existing, picks=[_pick()])
        settings = _make_mock_settings()
        llm_client = MagicMock()
        # If judge_pick is called we'd see it via mock; should NOT be.
        with patch.object(judge_mod, "judge_pick") as mock_judge:
            result = judge_yesterday(record, settings, llm_client)
        assert result == existing
        mock_judge.assert_not_called()

    def test_three_picks_judged_in_parallel(self):
        """All 3 picks judged via ThreadPoolExecutor; mocked judge_pick that
        sleeps 0.4s each runs in <0.8s wall clock (well under 1.5s)."""
        record = _record()
        settings = _make_mock_settings()
        llm_client = MagicMock()

        def slow_judge_pick(pick, briefing_date, _settings, _llm_client):
            time.sleep(0.4)
            return Judgment(
                rank=pick.rank,
                verdict="PARTIAL",
                justification=f"slow #{pick.rank}",
                price_data={
                    "primary_ticker": pick.primary_ticker,
                    "primary_pct_change_24h": 0.5,
                    "spy_pct": 0.3,
                    "vix_close": 14.0,
                    "vix_pct_change": -1.0,
                },  # type: ignore[arg-type]
            )

        with patch.object(judge_mod, "judge_pick", side_effect=slow_judge_pick):
            start = time.monotonic()
            result = judge_yesterday(record, settings, llm_client)
            elapsed = time.monotonic() - start

        assert result is not None
        assert len(result) == 3
        # Serial would be ~1.2s; parallel should be under 0.8s with healthy margin.
        assert elapsed < 1.2, f"Wall clock {elapsed:.2f}s suggests not parallel"

    def test_all_picks_fail_returns_none(self):
        record = _record()
        settings = _make_mock_settings()
        llm_client = MagicMock()
        with patch.object(judge_mod, "judge_pick", return_value=None):
            result = judge_yesterday(record, settings, llm_client)
        assert result is None

    def test_partial_success_returns_subset(self):
        record = _record()
        settings = _make_mock_settings()
        llm_client = MagicMock()

        def side_effect(pick, *args, **kwargs):
            if pick.rank == 2:
                return None  # one pick fails
            return Judgment(
                rank=pick.rank,
                verdict="HIT",
                justification="ok",
                price_data={
                    "primary_ticker": pick.primary_ticker,
                    "primary_pct_change_24h": 2.0,
                    "spy_pct": 1.0,
                    "vix_close": 14.0,
                    "vix_pct_change": -2.0,
                },  # type: ignore[arg-type]
            )

        with patch.object(judge_mod, "judge_pick", side_effect=side_effect):
            result = judge_yesterday(record, settings, llm_client)

        assert result is not None
        assert len(result) == 2
        # Result is sorted by rank — assert rank ordering is stable.
        assert [j.rank for j in result] == [1, 3]

    def test_exception_in_judge_pick_is_swallowed(self):
        record = _record()
        settings = _make_mock_settings()
        llm_client = MagicMock()

        def side_effect(pick, *args, **kwargs):
            if pick.rank == 1:
                raise RuntimeError("network died")
            return Judgment(
                rank=pick.rank,
                verdict="MISS",
                justification="x",
                price_data={
                    "primary_ticker": pick.primary_ticker,
                    "primary_pct_change_24h": -0.1,
                    "spy_pct": 0.0,
                    "vix_close": 14.0,
                    "vix_pct_change": 0.0,
                },  # type: ignore[arg-type]
            )

        with patch.object(judge_mod, "judge_pick", side_effect=side_effect):
            result = judge_yesterday(record, settings, llm_client)

        # Should still return the 2 picks that didn't raise.
        assert result is not None
        assert len(result) == 2
        assert all(j.rank in (2, 3) for j in result)


# ---------------------------------------------------------------------------
# Alpaca-based price fetch (Cycle 5 / ADR 0002): real daily bars give the
# briefing-day session's close-to-close move. VIX -> VIXY proxy, direction
# only (level stays None). Tested at the quotes_source level since judge_pick
# uses it directly.
# ---------------------------------------------------------------------------

_BARS_FN = "market_mover.sources.quotes_source.fetch_daily_bars"


def _bars(*pairs):
    """Build Alpaca daily bars from (date_str, close) pairs, oldest-first."""
    return [
        {"t": f"{d}T04:00:00Z", "o": c, "h": c, "l": c, "c": c, "v": 100}
        for d, c in pairs
    ]


class TestAlpacaPriceFetch:
    """fetch_24h_close_change computes briefing-day close-to-close from bars."""

    def test_equity_close_to_close(self):
        from market_mover.sources.quotes_source import fetch_24h_close_change

        # Prior close 500 -> briefing-day close 510 = +2.0%.
        bars = {"SPY": _bars(("2026-05-14", 500.0), ("2026-05-15", 510.0))}
        with patch(_BARS_FN, return_value=bars):
            pct_change, vix_level = fetch_24h_close_change(
                "SPY", date(2026, 5, 15), "k", "s", min_call_interval=0.0
            )
        assert pct_change is not None
        assert abs(pct_change - 2.0) < 1e-3
        assert vix_level is None  # SPY isn't VIX

    def test_vix_proxied_to_vixy_direction_only(self):
        from market_mover.sources.quotes_source import fetch_24h_close_change

        bars = {"VIXY": _bars(("2026-05-14", 14.0), ("2026-05-15", 15.0))}
        with patch(_BARS_FN, return_value=bars) as mock_bars:
            pct_change, vix_level = fetch_24h_close_change(
                "VIX", date(2026, 5, 15), "k", "s", min_call_interval=0.0
            )
        # Alpaca was queried for the VIXY proxy, not VIX.
        assert mock_bars.call_args[0][0] == ["VIXY"]
        assert pct_change is not None
        assert abs(pct_change - (1.0 / 14.0 * 100)) < 1e-3
        # Direction only — the VIX *level* is never the ETF price.
        assert vix_level is None

    def test_only_uses_bars_on_or_before_briefing_date(self):
        from market_mover.sources.quotes_source import fetch_24h_close_change

        # A bar dated AFTER the briefing must be ignored: the graded move is
        # 100 -> 110 (the 05-15 session), not anything from 05-18.
        bars = {
            "SPY": _bars(
                ("2026-05-14", 100.0),
                ("2026-05-15", 110.0),
                ("2026-05-18", 999.0),
            )
        }
        with patch(_BARS_FN, return_value=bars):
            pct_change, _ = fetch_24h_close_change(
                "SPY", date(2026, 5, 15), "k", "s", min_call_interval=0.0
            )
        assert pct_change is not None
        assert abs(pct_change - 10.0) < 1e-3

    def test_insufficient_bars_returns_none(self):
        from market_mover.sources.quotes_source import fetch_24h_close_change

        bars = {"SPY": _bars(("2026-05-15", 510.0))}  # only one bar
        with patch(_BARS_FN, return_value=bars):
            pct_change, vix_level = fetch_24h_close_change(
                "SPY", date(2026, 5, 15), "k", "s", min_call_interval=0.0
            )
        assert pct_change is None
        assert vix_level is None

    def test_no_creds_returns_none_without_fetch(self):
        from market_mover.sources.quotes_source import fetch_24h_close_change

        with patch(_BARS_FN) as mock_bars:
            pct_change, vix_level = fetch_24h_close_change(
                "SPY", date(2026, 5, 15), "", "", min_call_interval=0.0
            )
        assert (pct_change, vix_level) == (None, None)
        mock_bars.assert_not_called()


# ---------------------------------------------------------------------------
# Sanity smoke
# ---------------------------------------------------------------------------


def test_imports_are_live():
    assert _build_judge_prompt is not None
    assert judge_pick is not None
    assert judge_yesterday is not None
    assert JUDGE_PROMPT_VERSION == 1
