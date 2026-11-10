"""Tests for shadow-pick tracking (MM-T020). Nothing here places an order."""

import json
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from market_mover.llm_client import LLMClient
from market_mover.models import RankedArticle, RawArticle, SourceType
from market_mover.shadow_picks import (
    ShadowPick,
    ShadowRecord,
    ShadowSelection,
    load_shadow_records,
    record_shadow_picks,
    score_shadow_records,
    summarize,
)

PRE_MARKET = datetime(2026, 11, 9, 13, 2, tzinfo=timezone.utc)
AFTER_OPEN = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


def _ranked(rank=1, ticker="SPY", url="https://x.com/top"):
    return RankedArticle(
        rank=rank, title=f"Top {rank}", url=url, source_name="x", market_impact_summary="s",
        impact_score=9.0, primary_ticker=ticker, category="macro",
    )


def test_ledger_path_is_inside_the_project_not_the_working_directory(mock_settings, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = mock_settings.shadow_picks_jsonl_full_path
    assert path.name == "shadow_picks.jsonl"
    assert tmp_path not in path.parents


def _article(url, title="Other story"):
    return RawArticle(title=title, url=url, source_name="x", source_type=SourceType.RSS, summary="s")


def _response(**over):
    body = {
        "top_directions": [{"rank": 1, "direction": "bearish"}],
        "runner_ups": [{
            "url": "https://x.com/a", "ticker": "nvda", "category": "single_name",
            "impact_score": 7.5, "direction": "BULLISH",
        }],
    }
    body.update(over)
    return json.dumps(body)


@pytest.fixture
def llm_client(mock_settings):
    return LLMClient(mock_settings)


class TestGenerate:
    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_binds_runner_up_to_real_article(self, mock_claude, llm_client):
        mock_claude.return_value = _response()
        out = llm_client.generate_shadow_picks([_ranked()], [_article("https://x.com/a", "Real")], 5)
        pick = out.runner_ups[0]
        assert (pick.title, pick.ticker, pick.rank, pick.direction) == ("Real", "NVDA", 2, "BULLISH")
        assert out.top_directions == {1: "BEARISH"}

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_drops_invented_and_already_picked_urls(self, mock_claude, llm_client):
        mock_claude.return_value = _response(runner_ups=[
            {"url": "https://x.com/fake", "ticker": "A", "category": "macro", "impact_score": 5, "direction": "MIXED"},
            {"url": "https://x.com/top", "ticker": "B", "category": "macro", "impact_score": 5, "direction": "MIXED"},
        ])
        articles = [_article("https://x.com/top"), _article("https://x.com/a")]
        assert llm_client.generate_shadow_picks([_ranked()], articles, 5) is None

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_caps_count_and_cleans_bad_fields(self, mock_claude, llm_client):
        item = {"ticker": "not a ticker", "category": "nonsense", "impact_score": 5, "direction": "UP"}
        mock_claude.return_value = _response(runner_ups=[
            {"url": "https://x.com/a", **item}, {"url": "https://x.com/b", **item},
        ])
        articles = [_article("https://x.com/a"), _article("https://x.com/b")]
        out = llm_client.generate_shadow_picks([_ranked()], articles, 1)
        assert len(out.runner_ups) == 1
        pick = out.runner_ups[0]
        assert (pick.ticker, pick.category, pick.direction) == (None, "other", None)

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_unparseable_response_returns_none(self, mock_claude, llm_client):
        mock_claude.return_value = "not json"
        assert llm_client.generate_shadow_picks([_ranked()], [_article("https://x.com/a")], 5) is None


class _Client:
    def __init__(self, result=None, error=None):
        self.calls = 0
        self._result = result
        self._error = error

    def generate_shadow_picks(self, ranked, articles, count):
        self.calls += 1
        if self._error:
            raise self._error
        return self._result


class _Settings:
    shadow_tracking_enabled = True
    shadow_runner_up_count = 5

    def __init__(self, path):
        self.shadow_picks_jsonl_full_path = path


def _selection():
    runner = ShadowPick(rank=4, title="R", ticker="NVDA", category="single_name",
                        impact_score=7.0, direction="BULLISH", url="https://x.com/a")
    return ShadowSelection(top_directions={1: "BEARISH"}, runner_ups=[runner])


class TestRecord:
    def test_writes_traded_and_runner_ups_with_timing(self, tmp_path):
        path = tmp_path / "shadow.jsonl"
        rec = record_shadow_picks(_Client(_selection()), [_ranked()], [], _Settings(path),
                                  date(2026, 11, 9), now=PRE_MARKET)
        assert rec.late_entry is False
        assert (rec.traded[0].ticker, rec.traded[0].direction) == ("SPY", "BEARISH")
        assert load_shadow_records(path)[0].runner_ups[0].ticker == "NVDA"

    def test_second_run_same_day_makes_no_llm_call(self, tmp_path):
        path = tmp_path / "shadow.jsonl"
        client = _Client(_selection())
        for _ in range(2):
            record_shadow_picks(client, [_ranked()], [], _Settings(path), date(2026, 11, 9), now=PRE_MARKET)
        assert client.calls == 1
        assert len(load_shadow_records(path)) == 1

    def test_llm_failure_never_raises_or_writes(self, tmp_path):
        path = tmp_path / "shadow.jsonl"
        out = record_shadow_picks(_Client(error=RuntimeError("boom")), [_ranked()], [], _Settings(path),
                                  date(2026, 11, 9), now=PRE_MARKET)
        assert out is None
        assert not path.exists()

    def test_disabled_makes_no_call(self, tmp_path):
        settings = _Settings(tmp_path / "shadow.jsonl")
        settings.shadow_tracking_enabled = False
        client = _Client(_selection())
        assert record_shadow_picks(client, [_ranked()], [], settings, date(2026, 11, 9)) is None
        assert client.calls == 0


def _bars(*rows):
    return [{"t": f"{d}T05:00:00Z", "o": o} for d, o in rows]


class TestScore:
    def _record(self, late=False):
        traded = ShadowPick(rank=1, title="T", ticker="SPY", category="macro", impact_score=9.0,
                            direction="BEARISH", url="u1")
        runner = ShadowPick(rank=4, title="R", ticker="NVDA", category="single_name", impact_score=7.0,
                            direction="BULLISH", url="u2")
        return ShadowRecord(date="2026-11-09", run_started_at="x", late_entry=late,
                            traded=[traded], runner_ups=[runner])

    def test_scores_open_to_next_trading_day_open(self):
        bars = {
            "SPY": _bars(("2026-11-09", 100.0), ("2026-11-10", 101.0)),
            "NVDA": _bars(("2026-11-09", 200.0), ("2026-11-10", 190.0)),
        }
        rows = {r.group: r for r in score_shadow_records([self._record()], bars)}
        assert rows["traded"].return_pct == pytest.approx(1.0)
        assert rows["runner_up"].return_pct == pytest.approx(-5.0)

    def test_skips_late_runs_and_unfinished_or_tickerless_picks(self):
        bars = {"SPY": _bars(("2026-11-09", 100.0), ("2026-11-10", 101.0)), "NVDA": _bars(("2026-11-09", 200.0))}
        assert score_shadow_records([self._record(late=True)], bars) == []
        assert [r.group for r in score_shadow_records([self._record()], bars)] == ["traded"]

    def test_summarize_groups(self):
        bars = {
            "SPY": _bars(("2026-11-09", 100.0), ("2026-11-10", 101.0)),
            "NVDA": _bars(("2026-11-09", 200.0), ("2026-11-10", 190.0)),
        }
        out = summarize(score_shadow_records([self._record()], bars), lambda r: r.group)
        assert out["traded"]["n"] == 1
        assert out["traded"]["win_rate"] == 100.0
        assert out["runner_up"]["mean_return"] == pytest.approx(-5.0)
