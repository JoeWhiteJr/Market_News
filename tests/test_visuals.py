"""Tests for the Gmail-safe email visuals pack (MM-T006)."""

from datetime import date

from market_mover import visuals as V
from market_mover.learning import compute_category_performance
from market_mover.models import SparklineSeries

TODAY = date(2026, 7, 10)


def _record(day, *cat_verdicts):
    """(category, verdict) tuples -> a graded briefing record."""
    return {
        "date": day,
        "picks": [
            {"rank": i + 1, "category": c, "title": "t", "primary_ticker": "X"}
            for i, (c, _v) in enumerate(cat_verdicts)
        ],
        "judgments": [
            {"rank": i + 1, "verdict": v} for i, (_c, v) in enumerate(cat_verdicts)
        ],
    }


class TestHeatColors:
    def test_zero_is_neutral(self):
        bg, _fg = V.heat_colors(0.0)
        assert bg.lower() == "#f6f8fa"

    def test_positive_trends_green_negative_trends_red(self):
        assert V.heat_colors(2.0)[0] == "#1a7f37"   # capped green
        assert V.heat_colors(-2.0)[0] == "#cf222e"  # capped red

    def test_saturates_beyond_cap(self):
        assert V.heat_colors(50.0)[0] == V.heat_colors(2.0)[0]
        assert V.heat_colors(-50.0)[0] == V.heat_colors(-2.0)[0]

    def test_foreground_is_legible(self):
        # Dark backgrounds get white text, light backgrounds get near-black.
        assert V.heat_colors(2.0)[1] == "#ffffff"
        assert V.heat_colors(0.0)[1] == "#111111"


class TestIndexStrip:
    def test_renders_ticker_and_pct(self):
        sp = {"SPY": SparklineSeries(ticker="SPY", close_prices=[100, 105],
                                     pct_change=5.0, direction="up")}
        html = V.render_index_strip_html(sp)
        assert "SPY" in html and "+5.0%" in html
        assert "bgcolor=" in html and "<svg" not in html

    def test_empty_hides_block(self):
        assert V.render_index_strip_html({}) == ""
        assert V.render_index_strip_plain({}) == ""


class TestStreakRow:
    def test_counts_only_graded(self):
        recs = [_record("2026-07-08", ("macro", "HIT"), ("macro", "MISS")),
                _record("2026-07-09", ("macro", "PARTIAL"), ("macro", None))]
        html = V.render_streak_row_html(recs)
        assert 'data-block="streak"' in html
        # 3 graded (HIT, MISS, PARTIAL); the None pick is pending, not graded.
        assert "1/3 HIT over the last 3 graded picks" in html

    def test_limit_keeps_most_recent(self):
        recs = [_record(f"2026-06-{d:02d}", ("macro", "HIT")) for d in range(1, 30)]
        html = V.render_streak_row_html(recs, limit=5)
        # 5 cells of 13px squares.
        assert html.count("width=\"13\"") == 5

    def test_empty_hides_block(self):
        assert V.render_streak_row_html([]) == ""
        assert V.render_streak_row_plain([]) == ""

    def test_plain_row_has_glyphs(self):
        recs = [_record("2026-07-09", ("macro", "HIT"), ("macro", "MISS"))]
        out = V.render_streak_row_plain(recs)
        assert "Recent form:" in out and "#" in out


class TestCategoryCard:
    def _report(self):
        recs = (
            [_record(f"2026-06-{d:02d}", ("geopolitical", "HIT")) for d in range(1, 6)]
            + [_record(f"2026-06-{d:02d}", ("macro", "MISS")) for d in range(6, 11)]
        )
        return compute_category_performance(recs, TODAY)

    def test_renders_bars_for_each_category(self):
        html = V.render_category_card_html(self._report())
        assert 'data-block="category-card"' in html
        assert "geopolitical" in html and "macro" in html
        assert "background-color:" in html

    def test_best_category_first(self):
        html = V.render_category_card_html(self._report())
        assert html.index("geopolitical") < html.index("macro")

    def test_thin_category_excluded_by_min_n(self):
        recs = [_record("2026-07-09", ("crypto", "HIT"))]  # n=1
        html = V.render_category_card_html(compute_category_performance(recs, TODAY), min_n=3)
        assert html == ""

    def test_plain_variant(self):
        out = V.render_category_card_plain(self._report())
        assert "Report card" in out and "geopolitical" in out


class TestSectorHeatmap:
    def test_renders_grid(self):
        moves = [("XLK", "Tech", 1.2), ("XLE", "Energy", -0.8), ("XLF", "Fin", 0.3)]
        html = V.render_sector_heatmap_html(moves)
        assert 'data-block="sector-heatmap"' in html
        for t in ("XLK", "XLE", "XLF"):
            assert t in html
        assert "+1.2%" in html and "-0.8%" in html
        assert "bgcolor=" in html

    def test_empty_hides_block(self):
        assert V.render_sector_heatmap_html([]) == ""
        assert V.render_sector_heatmap_plain([]) == ""

    def test_plain_variant(self):
        out = V.render_sector_heatmap_plain([("XLK", "Tech", 1.2)])
        assert "XLK +1.2%" in out
