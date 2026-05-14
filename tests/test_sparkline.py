"""Tests for the Cycle 3 sparkline feature.

Covers:
- Finnhub candle fetch (mocked) -> ``SparklineSeries`` shape
- Per-ticker failure isolation (bad ticker omitted, others succeed)
- Total failure -> empty dict -> email still renders without the strip
- Direction classification (up / down / flat thresholds)
- SVG ``<polyline>`` carries the right number of points
- WCAG AA contrast of the shipped up/down colors against both light and dark backgrounds
"""

from unittest.mock import MagicMock, patch

from market_mover.email_template import (
    SPARKLINE_COLORS,
    _polyline_points,
    render_email_html,
    render_plain_text,
)
from market_mover.models import RankedArticle, SparklineSeries
from market_mover.sources.quotes_source import (
    _build_series,
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


def _candle_response(closes: list[float]) -> dict:
    """Build a Finnhub /stock/candle response with the given close prices."""
    return {
        "s": "ok",
        "c": closes,
        "o": closes,
        "h": closes,
        "l": closes,
        "v": [1000] * len(closes),
        "t": list(range(1700000000, 1700000000 + len(closes) * 86400, 86400)),
    }


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


class TestBuildSeries:
    def test_builds_series_from_ok_payload(self):
        series = _build_series("SPY", _candle_response([100.0, 101.0, 102.0, 103.0, 105.0]), days=5)
        assert series is not None
        assert series.ticker == "SPY"
        assert series.close_prices == [100.0, 101.0, 102.0, 103.0, 105.0]
        assert abs(series.pct_change - 5.0) < 1e-6
        assert series.direction == "up"

    def test_returns_none_on_no_data(self):
        assert _build_series("ZZZ", {"s": "no_data"}, days=5) is None

    def test_returns_none_on_empty_closes(self):
        assert _build_series("ZZZ", {"s": "ok", "c": []}, days=5) is None

    def test_returns_none_on_single_close(self):
        # Need >=2 points to draw a line and to compute pct change.
        assert _build_series("ZZZ", {"s": "ok", "c": [100.0]}, days=5) is None

    def test_truncates_to_last_n_days(self):
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
        series = _build_series("SPY", _candle_response(closes), days=5)
        assert series is not None
        assert series.close_prices == [102.0, 103.0, 104.0, 105.0, 106.0]

    def test_handles_non_dict_payload(self):
        assert _build_series("ZZZ", "not-a-dict", days=5) is None  # type: ignore[arg-type]


class TestFetchSparklineData:
    def test_empty_without_api_key(self):
        result = fetch_sparkline_data(["SPY"], api_key="")
        assert result == {}

    def test_empty_without_tickers(self):
        result = fetch_sparkline_data([], api_key="test-key")
        assert result == {}

    def test_fetches_all_tickers(self):
        mock_resp_spy = MagicMock()
        mock_resp_spy.json.return_value = _candle_response([100.0, 101.0, 102.0, 103.0, 105.0])
        mock_resp_spy.raise_for_status.return_value = None

        mock_resp_qqq = MagicMock()
        mock_resp_qqq.json.return_value = _candle_response([200.0, 199.0, 198.0, 197.0, 195.0])
        mock_resp_qqq.raise_for_status.return_value = None

        mock_requests = MagicMock()
        mock_requests.get.side_effect = [mock_resp_spy, mock_resp_qqq]

        with patch.dict("sys.modules", {"requests": mock_requests}):
            result = fetch_sparkline_data(
                ["SPY", "QQQ"], api_key="test-key", min_call_interval=0.0
            )

        assert set(result.keys()) == {"SPY", "QQQ"}
        assert result["SPY"].direction == "up"
        assert result["QQQ"].direction == "down"
        # Verify the request was made with the right endpoint + timeout.
        call_args = mock_requests.get.call_args_list[0]
        assert "stock/candle" in call_args[0][0]
        assert call_args[1]["timeout"] == 20
        assert call_args[1]["params"]["symbol"] == "SPY"

    def test_bad_ticker_omitted_others_render(self):
        """If one ticker errors, the rest still come back."""
        good_resp = MagicMock()
        good_resp.json.return_value = _candle_response([100.0, 102.0, 104.0, 106.0, 108.0])
        good_resp.raise_for_status.return_value = None

        def get_side_effect(url, **kwargs):
            sym = kwargs["params"]["symbol"]
            if sym == "BAD":
                raise RuntimeError("network broke")
            return good_resp

        mock_requests = MagicMock()
        mock_requests.get.side_effect = get_side_effect

        with patch.dict("sys.modules", {"requests": mock_requests}):
            result = fetch_sparkline_data(
                ["SPY", "BAD", "QQQ"], api_key="test-key", min_call_interval=0.0
            )

        assert set(result.keys()) == {"SPY", "QQQ"}

    def test_no_data_response_omits_ticker(self):
        """Finnhub's `{"s": "no_data"}` -> ticker is dropped, no crash."""
        no_data_resp = MagicMock()
        no_data_resp.json.return_value = {"s": "no_data"}
        no_data_resp.raise_for_status.return_value = None

        mock_requests = MagicMock()
        mock_requests.get.return_value = no_data_resp

        with patch.dict("sys.modules", {"requests": mock_requests}):
            result = fetch_sparkline_data(["SPY"], api_key="test-key", min_call_interval=0.0)

        assert result == {}

    def test_total_failure_returns_empty(self):
        """When every request raises, fetch_sparkline_data still returns {}."""
        mock_requests = MagicMock()
        mock_requests.get.side_effect = RuntimeError("DNS down")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            result = fetch_sparkline_data(
                ["SPY", "QQQ"], api_key="test-key", min_call_interval=0.0
            )

        assert result == {}


class TestPolylinePoints:
    def test_correct_point_count(self):
        points_str = _polyline_points([100.0, 101.0, 99.0, 102.0, 105.0], 80, 24, 2)
        # 5 input points -> 5 "x,y" pairs separated by spaces.
        assert len(points_str.split(" ")) == 5

    def test_monotonic_up_series_ends_higher_visually(self):
        """In SVG coords, smaller Y = higher on the page. A rising series should
        have its first point's Y greater than its last point's Y."""
        points_str = _polyline_points([100.0, 110.0, 120.0, 130.0, 140.0], 80, 24, 2)
        pairs = [tuple(map(float, p.split(","))) for p in points_str.split(" ")]
        first_y = pairs[0][1]
        last_y = pairs[-1][1]
        assert first_y > last_y

    def test_flat_series_renders_horizontal(self):
        points_str = _polyline_points([100.0, 100.0, 100.0, 100.0, 100.0], 80, 24, 2)
        pairs = [tuple(map(float, p.split(","))) for p in points_str.split(" ")]
        ys = {pair[1] for pair in pairs}
        assert len(ys) == 1  # all the same y

    def test_empty_series_returns_empty_string(self):
        assert _polyline_points([], 80, 24, 2) == ""


class TestRenderEmailWithSparkline:
    def test_renders_polyline_with_5_points(self):
        series = SparklineSeries(
            ticker="SPY",
            close_prices=[100.0, 101.0, 102.0, 103.0, 105.0],
            pct_change=5.0,
            direction="up",
        )
        html = render_email_html([_make_article()], sparklines={"SPY": series})
        assert 'data-block="sparkline"' in html
        assert "<polyline" in html
        # The polyline points attribute should hold 5 comma-separated pairs.
        points_attr_start = html.find('points="') + len('points="')
        points_attr_end = html.find('"', points_attr_start)
        points_str = html[points_attr_start:points_attr_end]
        assert len(points_str.split(" ")) == 5

    def test_sparkline_block_appears_before_date_header(self):
        series = SparklineSeries(
            ticker="SPY",
            close_prices=[100.0, 102.0],
            pct_change=2.0,
            direction="up",
        )
        html = render_email_html([_make_article()], sparklines={"SPY": series})
        spark_pos = html.find('data-block="sparkline"')
        header_pos = html.find("Top 3 Market-Moving Stories")
        assert 0 < spark_pos < header_pos

    def test_no_sparkline_block_when_empty(self):
        """Email still renders cleanly when there is no sparkline data."""
        html = render_email_html([_make_article()], sparklines={})
        assert 'data-block="sparkline"' not in html
        # Sanity: the rest of the email still renders.
        assert "Top 3 Market-Moving Stories" in html

    def test_no_sparkline_block_when_none(self):
        html = render_email_html([_make_article()])  # default arg
        assert 'data-block="sparkline"' not in html

    def test_up_color_is_green_in_light_mode(self):
        series = SparklineSeries(
            ticker="SPY", close_prices=[100.0, 105.0], pct_change=5.0, direction="up"
        )
        html = render_email_html([_make_article()], sparklines={"SPY": series})
        assert SPARKLINE_COLORS["up_light"] in html

    def test_down_color_is_red_in_light_mode(self):
        series = SparklineSeries(
            ticker="QQQ", close_prices=[200.0, 190.0], pct_change=-5.0, direction="down"
        )
        html = render_email_html([_make_article()], sparklines={"QQQ": series})
        assert SPARKLINE_COLORS["down_light"] in html

    def test_outlook_mso_fallback_present(self):
        """Outlook desktop ignores SVG — verify the MSO conditional + text fallback."""
        series = SparklineSeries(
            ticker="SPY", close_prices=[100.0, 105.0], pct_change=5.0, direction="up"
        )
        html = render_email_html([_make_article()], sparklines={"SPY": series})
        assert "<!--[if mso]>" in html
        assert "SPY +5.0%" in html

    def test_mobile_media_query_present(self):
        series = SparklineSeries(
            ticker="SPY", close_prices=[100.0, 105.0], pct_change=5.0, direction="up"
        )
        html = render_email_html([_make_article()], sparklines={"SPY": series})
        assert "@media (max-width: 600px)" in html
        assert "mm-spark-cell" in html

    def test_plain_text_includes_sparkline_strip(self):
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


# ---------------------------------------------------------------------------
# WCAG AA contrast — the up/down colors we ship must be readable on both the
# light email body (#ffffff) and the dark-mode card background (#1a1d24).
# ---------------------------------------------------------------------------


def _relative_luminance(hex_color: str) -> float:
    """Compute WCAG relative luminance for an ``#rrggbb`` color."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(fg: str, bg: str) -> float:
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


WCAG_AA_NORMAL = 4.5


class TestSparklineColorsPassWCAG:
    """The shipped colors must clear WCAG AA (4.5:1) against the backgrounds
    they're actually rendered on."""

    LIGHT_BG = "#ffffff"
    DARK_BG = "#1a1d24"  # `.mm-card` background in the dark-mode block

    def test_up_light_passes_against_white(self):
        ratio = _contrast_ratio(SPARKLINE_COLORS["up_light"], self.LIGHT_BG)
        assert ratio >= WCAG_AA_NORMAL, f"up_light contrast {ratio:.2f}:1 fails AA on white"

    def test_down_light_passes_against_white(self):
        ratio = _contrast_ratio(SPARKLINE_COLORS["down_light"], self.LIGHT_BG)
        assert ratio >= WCAG_AA_NORMAL, f"down_light contrast {ratio:.2f}:1 fails AA on white"

    def test_flat_light_passes_against_white(self):
        ratio = _contrast_ratio(SPARKLINE_COLORS["flat_light"], self.LIGHT_BG)
        assert ratio >= WCAG_AA_NORMAL, f"flat_light contrast {ratio:.2f}:1 fails AA on white"

    def test_up_dark_passes_against_dark_bg(self):
        ratio = _contrast_ratio(SPARKLINE_COLORS["up_dark"], self.DARK_BG)
        assert ratio >= WCAG_AA_NORMAL, f"up_dark contrast {ratio:.2f}:1 fails AA on dark"

    def test_down_dark_passes_against_dark_bg(self):
        ratio = _contrast_ratio(SPARKLINE_COLORS["down_dark"], self.DARK_BG)
        assert ratio >= WCAG_AA_NORMAL, f"down_dark contrast {ratio:.2f}:1 fails AA on dark"

    def test_flat_dark_passes_against_dark_bg(self):
        ratio = _contrast_ratio(SPARKLINE_COLORS["flat_dark"], self.DARK_BG)
        assert ratio >= WCAG_AA_NORMAL, f"flat_dark contrast {ratio:.2f}:1 fails AA on dark"
