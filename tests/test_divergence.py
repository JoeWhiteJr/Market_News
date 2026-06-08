"""Tests for the Sentiment vs Price Divergence flag (creative #15)."""

from datetime import date

from unittest.mock import patch

from market_mover.divergence import (
    DivergenceFlag,
    analyze_divergences,
    score_sentiment,
)
from market_mover.models import RankedArticle

_BARS_FN = "market_mover.divergence.fetch_daily_bars"


def _pick(ticker, title, summary="", rank=1):
    return RankedArticle(
        rank=rank, title=title, url=f"https://x.com/{rank}", source_name="x",
        market_impact_summary=summary, impact_score=8.0, primary_ticker=ticker,
        category="single_name",
    )


class TestScoreSentiment:
    def test_bullish(self):
        assert score_sentiment("NVDA beats estimates, surges").label == "bullish"

    def test_bearish(self):
        assert score_sentiment("Boeing plunges after downgrade and probe").label == "bearish"

    def test_neutral(self):
        assert score_sentiment("Company holds its annual meeting").label == "neutral"

    def test_net_lean_wins(self):
        # one bullish, two bearish -> bearish
        s = score_sentiment("Stock beats but warns of probe")
        assert s.label == "bearish"
        assert s.score == -1

    def test_word_boundary(self):
        # "missed" the word isn't "miss"? ensure no spurious match inside words
        assert score_sentiment("dismisses rumor").label == "neutral"


def _bars_for(symbol, closes):
    """fetch_daily_bars-shaped return for one symbol."""
    return {symbol: [{"t": f"2026-06-0{i + 1}T04:00:00Z", "c": c} for i, c in enumerate(closes)]}


class TestAnalyzeDivergences:
    def test_no_creds_returns_empty(self):
        assert analyze_divergences([_pick("NVDA", "surges")], "", "", date(2026, 6, 9)) == []

    def test_bullish_news_falling_price_flags(self):
        picks = [_pick("NVDA", "NVDA beats estimates and surges on record demand")]
        # 100 -> 95 over the window = -5% (down >= 2% threshold)
        with patch(_BARS_FN, return_value=_bars_for("NVDA", [100, 99, 98, 96, 95])):
            flags = analyze_divergences(picks, "k", "s", date(2026, 6, 9), min_call_interval=0.0)
        assert len(flags) == 1
        assert flags[0].ticker == "NVDA"
        assert flags[0].sentiment == "bullish"
        assert flags[0].price_pct < 0

    def test_bearish_news_rising_price_flags(self):
        picks = [_pick("BA", "Boeing plunges after downgrade and lawsuit")]
        with patch(_BARS_FN, return_value=_bars_for("BA", [100, 101, 103, 104, 105])):
            flags = analyze_divergences(picks, "k", "s", date(2026, 6, 9), min_call_interval=0.0)
        assert len(flags) == 1
        assert flags[0].sentiment == "bearish"
        assert flags[0].price_pct > 0

    def test_aligned_news_and_price_no_flag(self):
        # bullish news + rising price = no divergence
        picks = [_pick("NVDA", "NVDA beats estimates and surges")]
        with patch(_BARS_FN, return_value=_bars_for("NVDA", [100, 101, 102, 104, 106])):
            flags = analyze_divergences(picks, "k", "s", date(2026, 6, 9), min_call_interval=0.0)
        assert flags == []

    def test_below_threshold_no_flag(self):
        # bullish news but price only down 0.5% (< 2% threshold)
        picks = [_pick("NVDA", "NVDA beats estimates and surges")]
        with patch(_BARS_FN, return_value=_bars_for("NVDA", [100, 100, 100, 100, 99.5])):
            flags = analyze_divergences(picks, "k", "s", date(2026, 6, 9), min_call_interval=0.0)
        assert flags == []

    def test_neutral_sentiment_skipped(self):
        picks = [_pick("NVDA", "NVDA holds its shareholder meeting")]
        with patch(_BARS_FN, return_value=_bars_for("NVDA", [100, 90, 80, 70, 60])) as m:
            flags = analyze_divergences(picks, "k", "s", date(2026, 6, 9), min_call_interval=0.0)
        assert flags == []
        m.assert_not_called()  # neutral short-circuits before any price fetch

    def test_no_ticker_skipped(self):
        picks = [_pick(None, "Markets surge broadly")]
        with patch(_BARS_FN) as m:
            flags = analyze_divergences(picks, "k", "s", date(2026, 6, 9), min_call_interval=0.0)
        assert flags == []
        m.assert_not_called()

    def test_missing_price_data_skipped(self):
        picks = [_pick("NVDA", "NVDA beats estimates and surges")]
        with patch(_BARS_FN, return_value={}):  # no bars
            flags = analyze_divergences(picks, "k", "s", date(2026, 6, 9), min_call_interval=0.0)
        assert flags == []


class TestDivergenceRendering:
    def _arts(self):
        return [RankedArticle(rank=1, title="X", url="https://x.com/a",
                              source_name="x", market_impact_summary="s", impact_score=8.0)]

    def test_html_flag_renders(self):
        from market_mover.email_template import render_email_html
        flags = [DivergenceFlag(ticker="NVDA", sentiment="bullish", price_pct=-3.2,
                                lookback=5, headline="X", note="Bullish but down 3.2%.")]
        html = render_email_html(self._arts(), divergences=flags)
        assert "NARRATIVE vs TAPE" in html
        assert "NVDA" in html

    def test_html_no_flag_when_empty(self):
        from market_mover.email_template import render_email_html
        assert "NARRATIVE vs TAPE" not in render_email_html(self._arts(), divergences=[])

    def test_plain_text_flag(self):
        from market_mover.email_template import render_plain_text
        flags = [DivergenceFlag(ticker="BA", sentiment="bearish", price_pct=4.1,
                                lookback=5, headline="X", note="Bearish but up 4.1%.")]
        text = render_plain_text(self._arts(), divergences=flags)
        assert "NARRATIVE vs TAPE" in text
        assert "BA" in text
