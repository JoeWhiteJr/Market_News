"""Tests for the Yesterday-Index persistence + scorecard rendering.

Cycle 4 Phase A. Schema is locked in ``docs/adrs/0001-yesterday-index-rubric.md``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from market_mover import cli, scorecard
from market_mover.models import ContrarianCoda, RankedArticle, RawArticle, SourceType
from market_mover.scorecard import (
    SCHEMA_VERSION,
    BriefingRecord,
    ScorecardContrarian,
    ScorecardPick,
    append_record,
    build_record_from_pipeline,
    compute_running_stats,
    load_yesterday,
    render_scorecard_html,
    render_scorecard_plain_text,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_pick(rank: int = 1) -> ScorecardPick:
    return ScorecardPick(
        rank=rank,
        title=f"Sample title #{rank}",
        summary=f"Sample summary #{rank}.",
        impact_score=8.0 + rank * 0.1,
        primary_ticker="SPY" if rank == 1 else None,
        category="macro",
        source_url=f"https://example.com/story-{rank}",
        source_name="example.com",
    )


def _sample_record(d: date | None = None) -> BriefingRecord:
    return BriefingRecord(
        date=d or date(2026, 5, 14),
        schema_version=SCHEMA_VERSION,
        model_used="claude",
        voice="vinny",
        mimicry_voice=None,
        picks=[_sample_pick(1), _sample_pick(2), _sample_pick(3)],
        contrarian=ScorecardContrarian(
            headline="Bear case headline",
            argument="Bear case argument.",
            source_url="https://example.com/bear",
            source_name="example.com",
        ),
        graded_at=None,
        judge_model=None,
        judge_prompt_version=None,
        judgments=None,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestAppendRecord:
    def test_append_creates_file_and_writes_one_line(self, tmp_path):
        path = tmp_path / "briefings.jsonl"
        record = _sample_record()
        append_record(record, path)
        assert path.exists()
        text = path.read_text()
        lines = text.strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["date"] == "2026-05-14"
        assert parsed["schema_version"] == SCHEMA_VERSION

    def test_append_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "deeper" / "briefings.jsonl"
        record = _sample_record()
        append_record(record, path)
        assert path.exists()

    def test_append_two_records_produces_two_lines(self, tmp_path):
        path = tmp_path / "briefings.jsonl"
        append_record(_sample_record(date(2026, 5, 13)), path)
        append_record(_sample_record(date(2026, 5, 14)), path)
        lines = [ln for ln in path.read_text().split("\n") if ln.strip()]
        assert len(lines) == 2
        dates = [json.loads(ln)["date"] for ln in lines]
        assert dates == ["2026-05-13", "2026-05-14"]

    def test_append_load_roundtrip_preserves_all_fields(self, tmp_path):
        path = tmp_path / "briefings.jsonl"
        original = _sample_record()
        append_record(original, path)
        # Load directly through pydantic to confirm every field roundtripped.
        lines = [ln for ln in path.read_text().split("\n") if ln.strip()]
        roundtripped = BriefingRecord.model_validate_json(lines[-1])
        assert roundtripped == original


class TestLoadYesterday:
    def test_returns_none_for_missing_file(self, tmp_path):
        path = tmp_path / "nope.jsonl"
        assert load_yesterday(path, date(2026, 5, 15)) is None

    def test_returns_none_when_last_line_date_equals_today(self, tmp_path):
        path = tmp_path / "briefings.jsonl"
        append_record(_sample_record(date(2026, 5, 15)), path)
        # Same-day call -> no double-grade.
        assert load_yesterday(path, date(2026, 5, 15)) is None

    def test_returns_none_when_last_line_date_in_future(self, tmp_path):
        path = tmp_path / "briefings.jsonl"
        append_record(_sample_record(date(2026, 5, 16)), path)
        assert load_yesterday(path, date(2026, 5, 15)) is None

    def test_returns_none_for_malformed_last_line(self, tmp_path):
        path = tmp_path / "briefings.jsonl"
        append_record(_sample_record(date(2026, 5, 13)), path)
        # Corrupt the file by appending a half-written JSON line.
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"date": "2026-05-14", "schema_version": 1, "mod')
        assert load_yesterday(path, date(2026, 5, 15)) is None

    def test_returns_record_for_yesterday(self, tmp_path):
        path = tmp_path / "briefings.jsonl"
        append_record(_sample_record(date(2026, 5, 14)), path)
        loaded = load_yesterday(path, date(2026, 5, 15))
        assert loaded is not None
        assert loaded.date == date(2026, 5, 14)
        assert len(loaded.picks) == 3

    def test_returns_last_record_when_multiple_lines(self, tmp_path):
        path = tmp_path / "briefings.jsonl"
        append_record(_sample_record(date(2026, 5, 12)), path)
        append_record(_sample_record(date(2026, 5, 13)), path)
        append_record(_sample_record(date(2026, 5, 14)), path)
        loaded = load_yesterday(path, date(2026, 5, 15))
        assert loaded is not None
        assert loaded.date == date(2026, 5, 14)

    def test_blank_lines_are_skipped(self, tmp_path):
        path = tmp_path / "briefings.jsonl"
        append_record(_sample_record(date(2026, 5, 14)), path)
        # Add trailing blank lines that some editors leave behind.
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n\n  \n")
        loaded = load_yesterday(path, date(2026, 5, 15))
        assert loaded is not None
        assert loaded.date == date(2026, 5, 14)


class TestComputeRunningStats:
    def test_phase_a_returns_none(self, tmp_path):
        # Phase A always returns None — signature is locked for Phase C.
        path = tmp_path / "briefings.jsonl"
        assert compute_running_stats(path) is None
        assert compute_running_stats(path, window_days=7) is None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRenderScorecardHtml:
    def test_returns_empty_string_when_no_yesterday(self):
        assert render_scorecard_html(None, date(2026, 5, 15)) == ""

    def test_wraps_in_section_data_block(self):
        record = _sample_record()
        html = render_scorecard_html(record, date(2026, 5, 15))
        assert '<section data-block="scorecard"' in html

    def test_renders_tbd_placeholder_for_each_pick(self):
        record = _sample_record()
        html = render_scorecard_html(record, date(2026, 5, 15))
        # All 3 picks should carry the placeholder text.
        assert html.count("TBD &mdash; judging launches in Phase B") == 3 or html.count(
            "TBD"
        ) >= 3

    def test_renders_each_pick_title(self):
        record = _sample_record()
        html = render_scorecard_html(record, date(2026, 5, 15))
        for pick in record.picks:
            assert pick.title in html

    def test_renders_yesterday_date_label(self):
        record = _sample_record(date(2026, 5, 14))
        html = render_scorecard_html(record, date(2026, 5, 15))
        assert "May 14" in html

    def test_renders_model_label(self):
        record = _sample_record()
        html = render_scorecard_html(record, date(2026, 5, 15))
        assert "Claude" in html

    def test_html_is_balanced(self):
        record = _sample_record()
        html = render_scorecard_html(record, date(2026, 5, 15))
        # Sanity: every opening section tag has a closing one.
        assert html.count("<section") == html.count("</section>")
        assert html.count("<table") == html.count("</table>")


class TestRenderScorecardPlainText:
    def test_returns_empty_string_when_no_yesterday(self):
        assert render_scorecard_plain_text(None, date(2026, 5, 15)) == ""

    def test_renders_each_pick_with_placeholder(self):
        record = _sample_record()
        text = render_scorecard_plain_text(record, date(2026, 5, 15))
        assert "YESTERDAY'S SCORECARD" in text
        for pick in record.picks:
            assert pick.title in text
        # Every pick gets a TBD verdict line.
        assert text.count("TBD") == 3


# ---------------------------------------------------------------------------
# Schema lock — ADR v1
# ---------------------------------------------------------------------------


class TestSchemaSerialization:
    """A serialized record matches the ADR's frozen v1 schema (keys + types)."""

    def test_record_json_keys_match_adr_v1(self):
        record = _sample_record()
        data = json.loads(record.model_dump_json())
        # Top-level keys.
        assert set(data.keys()) == {
            "date",
            "schema_version",
            "model_used",
            "voice",
            "mimicry_voice",
            "picks",
            "contrarian",
            # Additive, backward-compatible field (ADR 0005). Absent on legacy
            # rows → defaults False; does not bump schema_version.
            "learning_feedback_active",
            "graded_at",
            "judge_model",
            "judge_prompt_version",
            "judgments",
        }

    def test_pick_json_keys_match_adr_v1(self):
        record = _sample_record()
        data = json.loads(record.model_dump_json())
        pick = data["picks"][0]
        assert set(pick.keys()) == {
            "rank",
            "title",
            "summary",
            "impact_score",
            "primary_ticker",
            "category",
            "source_url",
            "source_name",
        }

    def test_contrarian_json_keys_match_adr_v1(self):
        record = _sample_record()
        data = json.loads(record.model_dump_json())
        contrarian = data["contrarian"]
        assert set(contrarian.keys()) == {
            "headline",
            "argument",
            "source_url",
            "source_name",
        }

    def test_phase_b_fields_default_null(self):
        record = _sample_record()
        data = json.loads(record.model_dump_json())
        assert data["graded_at"] is None
        assert data["judge_model"] is None
        assert data["judge_prompt_version"] is None
        assert data["judgments"] is None

    def test_schema_version_is_one(self):
        record = _sample_record()
        assert record.schema_version == 1

    def test_invalid_verdict_category_rejected(self):
        # Picks must use one of the locked categories.
        with pytest.raises(Exception):
            ScorecardPick(
                rank=1,
                title="x",
                summary="y",
                impact_score=5.0,
                category="not_a_real_category",  # type: ignore[arg-type]
                source_url="https://example.com/x",
                source_name="example.com",
            )

    def test_invalid_voice_rejected(self):
        with pytest.raises(Exception):
            BriefingRecord(
                date=date(2026, 5, 14),
                model_used="claude",
                voice="not_a_voice",  # type: ignore[arg-type]
                picks=[_sample_pick(1)],
            )

    def test_invalid_model_used_rejected(self):
        with pytest.raises(Exception):
            BriefingRecord(
                date=date(2026, 5, 14),
                model_used="gpt-4",  # type: ignore[arg-type]
                voice="vinny",
                picks=[_sample_pick(1)],
            )


class TestBuildRecordFromPipeline:
    def test_maps_ranked_articles_to_picks(self):
        ranked = [
            RankedArticle(
                rank=i,
                title=f"Title {i}",
                url=f"https://example.com/{i}",
                source_name="example.com",
                market_impact_summary=f"Summary {i}",
                impact_score=8.0 + i * 0.1,
                primary_ticker="SPY" if i == 1 else None,
                category="macro",
            )
            for i in (1, 2, 3)
        ]
        record = build_record_from_pipeline(
            today=date(2026, 5, 15),
            ranked=ranked,
            coda=None,
            model_used="claude",
            voice="vinny",
            mimicry_voice=None,
        )
        assert record.date == date(2026, 5, 15)
        assert len(record.picks) == 3
        assert record.picks[0].primary_ticker == "SPY"
        assert record.picks[0].category == "macro"
        assert record.contrarian is None
        # Learning feedback (ADR 0005) defaults to the feedback-off baseline.
        assert record.learning_feedback_active is False

    def test_learning_feedback_active_is_recorded(self):
        """ADR 0005: the flag must persist so lift can be measured later."""
        ranked = [
            RankedArticle(
                rank=1,
                title="t",
                url="https://example.com/1",
                source_name="example.com",
                market_impact_summary="s",
                impact_score=8.0,
                category="macro",
            )
        ]
        record = build_record_from_pipeline(
            today=date(2026, 7, 10),
            ranked=ranked,
            coda=None,
            model_used="claude",
            voice="vinny",
            mimicry_voice=None,
            learning_feedback_active=True,
        )
        assert record.learning_feedback_active is True
        # Survives a JSONL round-trip.
        restored = BriefingRecord.model_validate_json(record.model_dump_json())
        assert restored.learning_feedback_active is True

    def test_legacy_record_without_flag_defaults_false(self):
        """Pre-ADR-0005 rows lack the field → must load as the False baseline."""
        legacy = (
            '{"date":"2026-06-01","schema_version":1,"model_used":"claude",'
            '"voice":"vinny","picks":[{"rank":1,"title":"t","summary":"s",'
            '"impact_score":8.0,"primary_ticker":"SPY","category":"macro",'
            '"source_url":"https://example.com/1","source_name":"example.com"}]}'
        )
        restored = BriefingRecord.model_validate_json(legacy)
        assert restored.learning_feedback_active is False

    def test_maps_coda_to_scorecard_contrarian(self):
        ranked = [
            RankedArticle(
                rank=1,
                title="t",
                url="https://example.com/1",
                source_name="example.com",
                market_impact_summary="s",
                impact_score=8.0,
            )
        ]
        coda = ContrarianCoda(
            headline="Bear headline",
            argument="Bear argument.",
            source_url="https://example.com/bear",
            source_name="example.com",
        )
        record = build_record_from_pipeline(
            today=date(2026, 5, 15),
            ranked=ranked,
            coda=coda,
            model_used="gemini",
            voice="neutral",
            mimicry_voice="cramer",
        )
        assert record.model_used == "gemini"
        assert record.mimicry_voice == "cramer"
        assert record.contrarian is not None
        assert record.contrarian.headline == "Bear headline"


# ---------------------------------------------------------------------------
# Integration with cli.py — after-send hook persists today's record
# ---------------------------------------------------------------------------


class TestCliPersistsAfterSend:
    @patch("market_mover.cli.send_email")
    def test_pipeline_appends_record_after_successful_send(
        self, mock_send_email, mock_settings, sample_raw_articles, tmp_path, monkeypatch
    ):
        """End-to-end: a successful send writes today's row to the JSONL path."""
        jsonl_path = tmp_path / "data" / "briefings.jsonl"
        # Point the settings at our temp path so the test doesn't touch the repo.
        mock_settings.briefings_jsonl_path = str(jsonl_path)
        mock_send_email.return_value = True

        sample_ranked = [
            RankedArticle(
                rank=i,
                title=f"Pipeline title {i}",
                url=f"https://example.com/pipeline-{i}",
                source_name="example.com",
                market_impact_summary=f"Summary {i}",
                impact_score=9.0 - i * 0.2,
                primary_ticker="SPY",
                category="macro",
            )
            for i in (1, 2, 3)
        ]

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def analyze_articles(self, articles, voice=None, macro_mode=False, track_record=None):
                from market_mover.voices import get_voice

                return sample_ranked, "claude-sonnet-4-6", get_voice("vinny")

            def generate_contrarian_coda(self, top_story, all_articles):
                return None

        with patch.object(cli, "fetch_newsapi_articles", return_value=sample_raw_articles), \
            patch.object(cli, "fetch_finnhub_articles", return_value=[]), \
            patch.object(cli, "fetch_rss_articles", return_value=[]), \
            patch.object(cli, "fetch_youtube_videos", return_value=[]), \
            patch.object(cli, "fetch_sparkline_data", return_value={}), \
            patch.object(cli, "LLMClient", _FakeClient), \
            patch("market_mover.cli.MarketMoverSettings", return_value=mock_settings):
            cli.run_pipeline()

        assert mock_send_email.called
        assert jsonl_path.exists(), "Expected JSONL row to be appended after send"
        lines = [ln for ln in jsonl_path.read_text().split("\n") if ln.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["model_used"] == "claude"
        assert parsed["voice"] == "vinny"
        assert len(parsed["picks"]) == 3
        assert parsed["picks"][0]["primary_ticker"] == "SPY"
        # Phase B fields stay null.
        assert parsed["graded_at"] is None
        assert parsed["judgments"] is None

    @patch("market_mover.cli.send_email")
    def test_persistence_failure_does_not_break_send(
        self, mock_send_email, mock_settings, sample_raw_articles, tmp_path
    ):
        """If the persistence write raises, the run still succeeds.

        The pipeline sends the email (Step 4) before persisting the record
        (Step 5, ``commit_daily_record``), and wraps the write in a try/except
        so a disk failure can't crash a run whose email already went out.
        """
        mock_send_email.return_value = True
        mock_settings.briefings_jsonl_path = str(tmp_path / "x.jsonl")

        sample_ranked = [
            RankedArticle(
                rank=i,
                title=f"t{i}",
                url=f"https://example.com/{i}",
                source_name="example.com",
                market_impact_summary=f"s{i}",
                impact_score=8.0,
            )
            for i in (1, 2, 3)
        ]

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def analyze_articles(self, articles, voice=None, macro_mode=False, track_record=None):
                from market_mover.voices import get_voice

                return sample_ranked, "claude-sonnet-4-6", get_voice("vinny")

            def generate_contrarian_coda(self, top_story, all_articles):
                return None

        with patch.object(cli, "fetch_newsapi_articles", return_value=sample_raw_articles), \
            patch.object(cli, "fetch_finnhub_articles", return_value=[]), \
            patch.object(cli, "fetch_rss_articles", return_value=[]), \
            patch.object(cli, "fetch_youtube_videos", return_value=[]), \
            patch.object(cli, "fetch_sparkline_data", return_value={}), \
            patch.object(cli, "LLMClient", _FakeClient), \
            patch.object(cli, "commit_daily_record", side_effect=OSError("disk full")), \
            patch("market_mover.cli.MarketMoverSettings", return_value=mock_settings):
            # Must not raise.
            cli.run_pipeline()

        assert mock_send_email.called


class TestPriceSnapshotRender:
    def test_sector_not_double_listed_when_equal_to_primary(self):
        # Regression: a macro/TLT pick whose sector proxy is also TLT must not
        # render "TLT ... · TLT ..." twice in the snapshot.
        from market_mover.scorecard import Judgment, PriceData, _render_price_snapshot
        j = Judgment(
            rank=3, verdict="PARTIAL", justification="x",
            price_data=PriceData(
                primary_ticker="TLT", primary_pct_change_24h=0.6, spy_pct=-0.3,
                vix_close=0.0, vix_pct_change=1.6, sector_etf="TLT", sector_pct=0.6,
            ),
        )
        snapshot = _render_price_snapshot(j)
        assert snapshot.count("TLT") == 1

    def test_distinct_sector_still_listed(self):
        from market_mover.scorecard import Judgment, PriceData, _render_price_snapshot
        j = Judgment(
            rank=1, verdict="HIT", justification="x",
            price_data=PriceData(
                primary_ticker="NVDA", primary_pct_change_24h=2.0, spy_pct=0.5,
                vix_close=0.0, vix_pct_change=-1.0, sector_etf="XLK", sector_pct=1.5,
            ),
        )
        snapshot = _render_price_snapshot(j)
        assert "NVDA" in snapshot and "XLK" in snapshot


# ---------------------------------------------------------------------------
# Unused-import shim
# ---------------------------------------------------------------------------


def test_imports_are_live():
    """Smoke check — ensure top-level imports are real and live."""
    assert scorecard is not None
    assert RawArticle is not None
    assert SourceType.RSS == SourceType("rss")
    # tmp_path-like helper is exercised in the other tests; this avoids
    # ruff F401 on the Path import.
    assert Path("/").is_absolute()
