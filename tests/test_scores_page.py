"""Tests for the scores & grades history page (MM-T003)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from market_mover.scores_page import (
    _benchmark_pair,
    _category_pnl,
    _overall_stats,
    _pick_return_series,
    _pnl_series,
    _render_benchmark_chart,
    _render_category_pnl,
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


def _cyc(day: str, trades: list[tuple[float, float, str]]) -> dict:
    """Cycle with closed trades as (pnl_abs, pnl_pct, category) tuples."""
    return {
        "cycle_date": day,
        "closed": [
            {"ticker": "X", "pnl_abs": a, "pnl_pct": p, "category": c}
            for a, p, c in trades
        ],
    }


class TestBenchmark:
    def test_pick_return_series_is_per_cycle_average(self):
        cycles = [_cyc("2026-07-01", [(0, 1.0, "macro"), (0, 3.0, "macro")])]
        assert _pick_return_series(cycles) == [("2026-07-01", 2.0)]

    def test_pick_return_series_skips_cycles_with_no_closes(self):
        cycles = [{"cycle_date": "2026-07-01", "closed": []},
                  _cyc("2026-07-02", [(0, 1.0, "macro")])]
        assert [d for d, _ in _pick_return_series(cycles)] == ["2026-07-02"]

    def test_benchmark_pair_indexes_both_to_zero_and_compounds(self):
        cycles = [
            _cyc("2026-07-01", [(0, 5.0, "macro")]),   # baseline day (dropped to 0)
            _cyc("2026-07-02", [(0, 10.0, "macro")]),  # +10% picks
        ]
        spy = {"2026-07-01": 100.0, "2026-07-02": 102.0}  # SPY +2%
        picks_pts, spy_pts = _benchmark_pair(cycles, spy)
        assert picks_pts[0] == ("2026-07-01", 0.0)
        assert spy_pts[0] == ("2026-07-01", 0.0)
        assert picks_pts[1] == ("2026-07-02", 10.0)
        assert spy_pts[1] == ("2026-07-02", 2.0)

    def test_benchmark_pair_needs_two_aligned_days(self):
        cycles = [_cyc("2026-07-01", [(0, 5.0, "macro")]),
                  _cyc("2026-07-02", [(0, 1.0, "macro")])]
        # Only one day has a SPY close → not enough overlap → hidden.
        assert _benchmark_pair(cycles, {"2026-07-01": 100.0}) == ([], [])

    def test_benchmark_chart_has_legend_and_both_series(self):
        picks = [("2026-07-01", 0.0), ("2026-07-02", 10.0)]
        spy = [("2026-07-01", 0.0), ("2026-07-02", 2.0)]
        svg = _render_benchmark_chart(picks, spy)
        assert "bm-picks-line" in svg and "bm-spy-line" in svg  # both series
        assert "bm-legend" in svg                                # identity not color-alone
        assert "Edge vs SPY:" in svg and "+8.00%" in svg         # alpha = 10 - 2

    def test_benchmark_chart_hidden_when_short(self):
        assert _render_benchmark_chart([], []) == ""
        assert _render_benchmark_chart([("d", 0.0)], [("d", 0.0)]) == ""

    def test_page_shows_benchmark_card_when_present(self):
        picks = [("2026-07-01", 0.0), ("2026-07-02", 10.0)]
        spy = [("2026-07-01", 0.0), ("2026-07-02", 2.0)]
        html_doc = render_scores_html([], today=TODAY, benchmark=(picks, spy))
        assert "Picks vs. the market" in html_doc

    def test_page_hides_benchmark_card_when_empty(self):
        html_doc = render_scores_html([], today=TODAY, benchmark=([], []))
        assert "Picks vs. the market" not in html_doc


class TestCategoryPnl:
    def test_pools_by_category_and_sorts_by_total(self):
        cycles = [
            _cyc("2026-07-01", [(100.0, 1.0, "macro"), (-40.0, -2.0, "single_name")]),
            _cyc("2026-07-02", [(20.0, 0.5, "macro")]),
        ]
        rows = _category_pnl(cycles)
        assert rows[0]["category"] == "macro"       # +120 sorts first
        assert rows[0]["total"] == 120.0
        assert rows[0]["trades"] == 2
        assert rows[0]["win_rate"] == 1.0
        assert rows[1]["category"] == "single_name"  # -40 last
        assert rows[1]["total"] == -40.0
        assert rows[1]["win_rate"] == 0.0

    def test_missing_category_falls_into_unmapped(self):
        cycles = [{"cycle_date": "2026-07-01",
                   "closed": [{"ticker": "X", "pnl_abs": 5.0, "pnl_pct": 1.0}]}]
        assert _category_pnl(cycles)[0]["category"] == "unmapped"

    def test_render_shows_diverging_bars_and_sign_colors(self):
        rows = _category_pnl([_cyc("2026-07-01",
                                   [(100.0, 1.0, "macro"), (-40.0, -2.0, "energy")])])
        out = _render_category_pnl(rows)
        assert "cat-pos" in out and "cat-neg" in out   # both signs drawn
        assert "$+100.00" in out and "$-40.00" in out

    def test_render_empty_is_blank(self):
        assert _render_category_pnl([]) == ""

    def test_page_shows_category_pnl_card_when_present(self):
        rows = _category_pnl([_cyc("2026-07-01", [(10.0, 1.0, "macro")])])
        html_doc = render_scores_html([], today=TODAY, category_pnl=rows)
        assert "Where the money comes from" in html_doc


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
