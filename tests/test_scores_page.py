"""Tests for the scores & grades history page (MM-T003)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from market_mover.scores_page import (
    _overall_stats,
    _pnl_series,
    _render_pnl_chart,
    _verdict_badge,
    render_scores_html,
    write_scores_page,
)

TODAY = date(2026, 7, 9)


def _record(day: str, verdicts: list[str | None], *, category: str = "macro") -> dict:
    """Build a minimal briefing record with N picks and parallel judgments."""
    picks = [
        {
            "rank": i + 1,
            "primary_ticker": f"TK{i}",
            "category": category,
            "title": f"Story {i} on {day}",
        }
        for i in range(len(verdicts))
    ]
    judgments = [
        {"rank": i + 1, "verdict": v, "justification": f"because {v}"}
        for i, v in enumerate(verdicts)
        if v is not None
    ]
    rec = {"date": day, "picks": picks}
    if judgments:
        rec["judgments"] = judgments
    return rec


def test_verdict_badge_known_and_unknown():
    assert 'badge-hit' in _verdict_badge("HIT")
    assert ">HIT<" in _verdict_badge("HIT")
    assert 'badge-na' in _verdict_badge("NOT_APPLICABLE")
    assert "N/A" in _verdict_badge("NOT_APPLICABLE")
    # Missing / ungraded → PENDING chip, never a crash.
    assert 'badge-pending' in _verdict_badge(None)
    assert 'badge-pending' in _verdict_badge("SOMETHING_WEIRD")


def test_overall_stats_counts_only_gradeable():
    records = [
        _record("2026-07-07", ["HIT", "MISS", "PARTIAL"]),
        _record("2026-07-08", ["TOO_EARLY", "NOT_APPLICABLE", None]),
    ]
    graded, days = _overall_stats(records)
    # Only HIT/PARTIAL/MISS are gradeable; TOO_EARLY, N/A, and None are not.
    assert graded == 3
    assert days == 2


def test_render_contains_core_sections_and_escapes():
    records = [_record("2026-07-08", ["HIT", "MISS", "PARTIAL"])]
    # Inject an XSS-y title to prove escaping.
    records[0]["picks"][0]["title"] = "<script>alert(1)</script> & bad"
    html_doc = render_scores_html(records, today=TODAY)
    assert "<!doctype html>" in html_doc
    assert "By category" in html_doc
    assert "Full history" in html_doc
    # Escaped, not raw.
    assert "<script>alert(1)</script>" not in html_doc
    assert "&lt;script&gt;" in html_doc


def test_render_empty_history_is_graceful():
    html_doc = render_scores_html([], today=TODAY)
    assert "No graded picks yet" in html_doc
    assert "<!doctype html>" in html_doc  # still a valid page


def test_newest_day_appears_before_older_day():
    records = [
        _record("2026-07-01", ["HIT", "HIT", "HIT"]),
        _record("2026-07-08", ["MISS", "MISS", "MISS"]),
    ]
    html_doc = render_scores_html(records, today=TODAY)
    assert html_doc.index("2026-07-08") < html_doc.index("2026-07-01")


def test_category_summary_reflects_pooled_rate():
    # Two all-HIT geopolitical days should show a high pooled rate for it.
    records = [
        _record("2026-07-07", ["HIT", "HIT", "HIT"], category="geopolitical"),
        _record("2026-07-08", ["HIT", "HIT", "HIT"], category="geopolitical"),
    ]
    html_doc = render_scores_html(records, today=TODAY)
    assert "geopolitical" in html_doc
    # Global mean is 100% here; it should surface in the summary header.
    assert "100%" in html_doc


def test_write_scores_page_roundtrip(tmp_path):
    jsonl = tmp_path / "briefings.jsonl"
    out = tmp_path / "nested" / "scores.html"
    with jsonl.open("w", encoding="utf-8") as f:
        f.write(json.dumps(_record("2026-07-08", ["HIT", "MISS", "PARTIAL"])) + "\n")
    ok = write_scores_page(jsonl, out, today=TODAY)
    assert ok is True
    assert out.exists()
    assert "Full history" in out.read_text(encoding="utf-8")


def test_write_scores_page_never_raises_on_bad_input(tmp_path):
    # Nonexistent ledger → False, no exception (best-effort contract).
    ok = write_scores_page(
        tmp_path / "does_not_exist.jsonl", tmp_path / "out.html", today=TODAY
    )
    assert ok in (True, False)  # must return a bool, not raise


def test_malformed_rows_do_not_crash_render(tmp_path):
    jsonl = tmp_path / "briefings.jsonl"
    with jsonl.open("w", encoding="utf-8") as f:
        f.write(json.dumps(_record("2026-07-08", ["HIT"])) + "\n")
        f.write("this is not json\n")  # load_briefing_records should skip it
    out = tmp_path / "scores.html"
    assert write_scores_page(jsonl, out, today=TODAY) is True
    assert out.exists()


def _cycle(day: str, *pnls: float) -> dict:
    return {"cycle_date": day, "closed": [{"ticker": "X", "pnl_abs": p} for p in pnls]}


class TestPnlChart:
    def test_series_is_cumulative(self):
        cycles = [_cycle("2026-07-01", 100.0), _cycle("2026-07-02", -30.0, 10.0)]
        assert _pnl_series(cycles) == [("2026-07-01", 100.0), ("2026-07-02", 80.0)]

    def test_series_orders_by_date(self):
        cycles = [_cycle("2026-07-02", 5.0), _cycle("2026-07-01", 10.0)]
        assert [d for d, _ in _pnl_series(cycles)] == ["2026-07-01", "2026-07-02"]

    def test_chart_needs_two_points(self):
        assert _render_pnl_chart([]) == ""
        assert _render_pnl_chart([("2026-07-01", 5.0)]) == ""

    def test_chart_positive_is_green_negative_is_red(self):
        up = _render_pnl_chart([("2026-07-01", 0.0), ("2026-07-02", 50.0)])
        assert "pnl-pos-line" in up and "pnl-neg-line" not in up
        down = _render_pnl_chart([("2026-07-01", 0.0), ("2026-07-02", -50.0)])
        assert "pnl-neg-line" in down and "pnl-pos-line" not in down

    def test_chart_has_zero_baseline_and_tooltips(self):
        svg = _render_pnl_chart([("2026-07-01", 10.0), ("2026-07-02", 40.0)])
        assert "pnl-zero" in svg          # zero reference line
        assert "<title>" in svg           # native per-point tooltips
        assert "2026-07-02: $+40.00" in svg

    def test_page_shows_pnl_card_when_series_present(self):
        html_doc = render_scores_html(
            [], today=TODAY,
            pnl_series=[("2026-07-01", 10.0), ("2026-07-02", 25.0)],
        )
        assert "Paper P&amp;L" in html_doc
        assert "$+25.00" in html_doc

    def test_page_hides_pnl_card_when_no_series(self):
        html_doc = render_scores_html([], today=TODAY, pnl_series=[])
        assert "Paper P&amp;L" not in html_doc


def test_scores_page_path_is_sandboxed_in_tests():
    """Regression: the autouse ``_sandbox_scores_page`` fixture must redirect the
    scores path off the committed ``docs/scores.html`` so no test can clobber it
    (see MM-T003 — an end-to-end run_pipeline test overwrote the real file)."""
    from market_mover.config import MarketMoverSettings

    resolved = str(MarketMoverSettings().scores_page_full_path)
    assert resolved.endswith("scores.html")
    assert "/docs/scores.html" not in resolved


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
