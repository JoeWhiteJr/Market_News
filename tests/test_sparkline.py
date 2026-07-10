"""Tests for the sparkline DATA layer + the Gmail-safe index strip that renders it.

The data fetch (`fetch_sparkline_data`) is unchanged. The rendering moved from
an inline-SVG polyline (stripped by Gmail) to a colored-table-cell "index strip"
in MM-T006 — the render tests below assert the new, Gmail-safe contract.
"""

from unittest.mock import patch

from market_mover.email_template import render_email_html, render_plain_text
from market_mover.models import RankedArticle, SparklineSeries
from market_mover.sources.quotes_source import (
    _classify_direction,
    fetch_sparkline_data,
)


def _make_article(title: str = "Fed Holds Rates") -> RankedArticle:
    return RankedArticle(
        rank=1,
        title=title,
        url="https://www.reuters.com/markets/fed-holds",
        source_name="ignored",
        market_impact_summary="Markets reacted modestly.",
        impact_score=8.5,
    )


def _alpaca_bars(closes: list[float]) -> list[dict]:
    """Build a list of Alpaca daily bars (oldest-first) with the given closes."""
    return [
        {
            "t": f"2026-05-{11 + i:02d}T04:00:00Z",
            "o": c,
            "h": c,
            "l": c,
            "c": c,
            "v": 1000,
            "n": 10,
            "vw": c,
        }
        for i, c in enumerate(closes)
    ]


class TestDirectionClassification:
    def test_up_above_threshold(self):
        assert _classify_direction(1.5) == "up"

    def test_down_below_negative_threshold(self):
        assert _classify_direction(-2.3) == "down"

    def test_flat_within_threshold(self):
        # Default threshold is 0.1% in either direction.
        assert _classify_direction(0.05) == "flat"
        assert _classify_direction(-0.05) == "flat"
        assert _classify_direction(0.0) == "flat"

    def test_exact_threshold_is_up(self):
        # 0.1 is NOT less than 0.1, so it falls through to up.
        assert _classify_direction(0.1) == "up"

    def test_exact_negative_threshold_is_down(self):
        assert _classify_direction(-0.1) == "down"


_QS = "market_mover.sources.quotes_source.fetch_daily_bars"


class TestFetchSparklineData:
    def test_empty_without_creds(self):
        assert fetch_sparkline_data(["SPY"], api_key_id="", api_secret_key="") == {}

    def test_empty_without_tickers(self):
        result = fetch_sparkline_data([], api_key_id="k", api_secret_key="s")
        assert result == {}

    def test_fetches_all_tickers(self):
        bars = {
            "SPY": _alpaca_bars([100.0, 101.0, 102.0, 103.0, 105.0]),
            "QQQ": _alpaca_bars([200.0, 199.0, 198.0, 197.0, 195.0]),
        }
        with patch(_QS, return_value=bars):
            result = fetch_sparkline_data(
                ["SPY", "QQQ"], api_key_id="k", api_secret_key="s", min_call_interval=0.0
            )
        assert set(result.keys()) == {"SPY", "QQQ"}
        assert result["SPY"].direction == "up"
        assert abs(result["SPY"].pct_change - 5.0) < 1e-6
        assert result["QQQ"].direction == "down"

    def test_truncates_to_last_n_closes(self):
        bars = {"SPY": _alpaca_bars([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0])}
        with patch(_QS, return_value=bars):
            result = fetch_sparkline_data(
                ["SPY"], days=5, api_key_id="k", api_secret_key="s", min_call_interval=0.0
            )
        assert result["SPY"].close_prices == [102.0, 103.0, 104.0, 105.0, 106.0]

    def test_vix_mapped_to_vixy_proxy(self):
        # The caller asks for VIX; Alpaca is queried for (and labels) VIXY.
        bars = {"VIXY": _alpaca_bars([20.0, 20.5, 21.0, 21.5, 22.0])}
        with patch(_QS, return_value=bars) as mock_bars:
            result = fetch_sparkline_data(
                ["VIX"], api_key_id="k", api_secret_key="s", min_call_interval=0.0
            )
        # VIXY was the symbol actually requested.
        assert "VIXY" in mock_bars.call_args[0][0]
        # And the series is labeled VIXY (honest — it's not the VIX index).
        assert set(result.keys()) == {"VIXY"}

    def test_single_close_ticker_omitted(self):
        bars = {"SPY": _alpaca_bars([100.0])}  # only 1 close -> no line / pct
        with patch(_QS, return_value=bars):
            result = fetch_sparkline_data(
                ["SPY"], api_key_id="k", api_secret_key="s", min_call_interval=0.0
            )
        assert result == {}

    def test_total_failure_returns_empty(self):
        # fetch_daily_bars returns {} on any failure -> no strip.
        with patch(_QS, return_value={}):
            result = fetch_sparkline_data(
                ["SPY", "QQQ"], api_key_id="k", api_secret_key="s", min_call_interval=0.0
            )
        assert result == {}


class TestRenderIndexStrip:
    """MM-T006: the Gmail-safe colored-cell replacement for the SVG sparklines."""

    def test_renders_index_strip_block_no_svg(self):
        series = SparklineSeries(
            ticker="SPY",
            close_prices=[100.0, 101.0, 102.0, 103.0, 105.0],
            pct_change=5.0,
            direction="up",
        )
        html = render_email_html([_make_article()], sparklines={"SPY": series})
        assert 'data-block="index-strip"' in html
        assert "SPY" in html
        assert "+5.0%" in html
        # The whole point of the rewrite: no inline SVG (Gmail strips it).
        assert "<svg" not in html
        assert "<polyline" not in html

    def test_index_strip_appears_before_date_header(self):
        series = SparklineSeries(
            ticker="SPY", close_prices=[100.0, 102.0], pct_change=2.0, direction="up"
        )
        html = render_email_html([_make_article()], sparklines={"SPY": series})
        strip_pos = html.find('data-block="index-strip"')
        header_pos = html.find("Top 3 Market-Moving Stories")
        assert 0 < strip_pos < header_pos

    def test_uses_background_color_cells(self):
        """Colored table cells are the Gmail-safe technique — verify bgcolor is used."""
        series = SparklineSeries(
            ticker="SPY", close_prices=[100.0, 105.0], pct_change=5.0, direction="up"
        )
        html = render_email_html([_make_article()], sparklines={"SPY": series})
        assert "bgcolor=" in html
        assert "background-color:" in html

    def test_no_strip_when_empty(self):
        html = render_email_html([_make_article()], sparklines={})
        assert 'data-block="index-strip"' not in html
        assert "Top 3 Market-Moving Stories" in html

    def test_no_strip_when_none(self):
        html = render_email_html([_make_article()])  # default arg
        assert 'data-block="index-strip"' not in html

    def test_dark_mode_media_query_still_present(self):
        series = SparklineSeries(
            ticker="SPY", close_prices=[100.0, 105.0], pct_change=5.0, direction="up"
        )
        html = render_email_html([_make_article()], sparklines={"SPY": series})
        assert "@media (prefers-color-scheme: dark)" in html

    def test_plain_text_includes_index_strip(self):
        series_a = SparklineSeries(
            ticker="SPY", close_prices=[100.0, 101.2], pct_change=1.2, direction="up"
        )
        series_b = SparklineSeries(
            ticker="VIX", close_prices=[20.0, 19.58], pct_change=-2.1, direction="down"
        )
        text = render_plain_text(
            [_make_article()], sparklines={"SPY": series_a, "VIX": series_b}
        )
        assert "SPY +1.2%" in text
        assert "VIX -2.1%" in text
