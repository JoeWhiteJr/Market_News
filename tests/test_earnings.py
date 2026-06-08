"""Tests for the Pre-Market Earnings Card (creative #14)."""

from datetime import date

from unittest.mock import MagicMock, patch

from market_mover.models import RankedArticle
from market_mover.sources.earnings_source import (
    EarningsEntry,
    default_window,
    fetch_earnings_calendar,
    notable_earnings_for,
)


def _mock_requests(payload, ok=True):
    resp = MagicMock()
    if ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = RuntimeError("HTTP 403")
    resp.json.return_value = payload
    m = MagicMock()
    m.get.return_value = resp
    return m


class TestFetchEarningsCalendar:
    def test_no_key_returns_empty(self):
        assert fetch_earnings_calendar("", date(2026, 6, 8), date(2026, 6, 10)) == []

    def test_parses_entries(self):
        payload = {"earningsCalendar": [
            {"symbol": "aapl", "date": "2026-06-09", "hour": "amc",
             "epsEstimate": 1.5, "revenueEstimate": 1.2e11},
            {"symbol": "JPM", "date": "2026-06-09", "hour": "bmo",
             "epsEstimate": None, "revenueEstimate": None},
        ]}
        with patch.dict("sys.modules", {"requests": _mock_requests(payload)}):
            out = fetch_earnings_calendar("k", date(2026, 6, 8), date(2026, 6, 10), 0.0)
        assert len(out) == 2
        assert out[0].symbol == "AAPL"  # upper-cased
        assert out[0].hour == "amc"
        assert out[0].eps_estimate == 1.5
        assert out[1].eps_estimate is None

    def test_http_error_returns_empty(self):
        with patch.dict("sys.modules", {"requests": _mock_requests({}, ok=False)}):
            assert fetch_earnings_calendar("k", date(2026, 6, 8), date(2026, 6, 10), 0.0) == []

    def test_missing_calendar_key_returns_empty(self):
        with patch.dict("sys.modules", {"requests": _mock_requests({"foo": 1})}):
            assert fetch_earnings_calendar("k", date(2026, 6, 8), date(2026, 6, 10), 0.0) == []

    def test_skips_rows_without_symbol_or_date(self):
        payload = {"earningsCalendar": [
            {"symbol": "AAPL", "date": "2026-06-09"},
            {"date": "2026-06-09"},          # no symbol
            {"symbol": "X"},                 # no date
        ]}
        with patch.dict("sys.modules", {"requests": _mock_requests(payload)}):
            out = fetch_earnings_calendar("k", date(2026, 6, 8), date(2026, 6, 10), 0.0)
        assert [e.symbol for e in out] == ["AAPL"]


class TestNotableEarningsFor:
    def _entries(self):
        return [
            EarningsEntry(symbol="A", date="2026-06-09", revenue_estimate=1e9),
            EarningsEntry(symbol="B", date="2026-06-09", revenue_estimate=5e9),
            EarningsEntry(symbol="C", date="2026-06-09", revenue_estimate=None),
            EarningsEntry(symbol="D", date="2026-06-10", revenue_estimate=9e9),
        ]

    def test_filters_to_date_and_sorts_by_revenue(self):
        out = notable_earnings_for(self._entries(), date(2026, 6, 9))
        # D is a different day; B>A>C by revenue (None last).
        assert [e.symbol for e in out] == ["B", "A", "C"]

    def test_respects_limit(self):
        out = notable_earnings_for(self._entries(), date(2026, 6, 9), limit=1)
        assert [e.symbol for e in out] == ["B"]

    def test_empty_when_no_match(self):
        assert notable_earnings_for(self._entries(), date(2026, 6, 30)) == []


class TestWhenLabel:
    def test_labels(self):
        assert EarningsEntry(symbol="X", date="d", hour="bmo").when_label == "Before open"
        assert EarningsEntry(symbol="X", date="d", hour="amc").when_label == "After close"
        assert EarningsEntry(symbol="X", date="d", hour="").when_label == "Time TBD"


class TestDefaultWindow:
    def test_window_starts_today(self):
        start, end = default_window(date(2026, 6, 9))
        assert start == date(2026, 6, 9)
        assert end > start


class TestEarningsRendering:
    def _arts(self):
        return [RankedArticle(rank=1, title="X", url="https://x.com/a",
                              source_name="X", market_impact_summary="s", impact_score=8.0)]

    def test_html_card_renders_with_entries(self):
        from market_mover.email_template import render_email_html
        entries = [EarningsEntry(symbol="AAPL", date="2026-06-09", hour="bmo",
                                 eps_estimate=1.5, revenue_estimate=1.2e11)]
        html = render_email_html(self._arts(), earnings=entries)
        assert "REPORTING EARNINGS TODAY" in html
        assert "AAPL" in html
        assert "Before open" in html
        assert "Rev est $120.0B" in html

    def test_html_no_card_when_empty(self):
        from market_mover.email_template import render_email_html
        assert "REPORTING EARNINGS" not in render_email_html(self._arts(), earnings=[])

    def test_plain_text_card(self):
        from market_mover.email_template import render_plain_text
        entries = [EarningsEntry(symbol="MSFT", date="2026-06-09", hour="amc",
                                 eps_estimate=2.93, revenue_estimate=6.2e10)]
        text = render_plain_text(self._arts(), earnings=entries)
        assert "EARNINGS TODAY" in text
        assert "MSFT" in text
        assert "After close" in text
