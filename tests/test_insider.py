"""Tests for the Insider / Form 4 Spotlight (creative #16)."""

from datetime import date

from unittest.mock import MagicMock, patch

from market_mover.models import RankedArticle
from market_mover.sources.insider_source import (
    InsiderBuy,
    fetch_insider_transactions,
    notable_buys_for_ticker,
    notable_insider_buys,
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


def _row(code, change, price, dt, name="Jane Exec"):
    return {"name": name, "change": change, "transactionPrice": price,
            "transactionDate": dt, "transactionCode": code}


def _pick(ticker, rank=1):
    return RankedArticle(rank=rank, title="t", url=f"https://x.com/{rank}",
                         source_name="x", market_impact_summary="s", impact_score=8.0,
                         primary_ticker=ticker, category="single_name")


class TestFetchInsiderTransactions:
    def test_no_key_returns_empty(self):
        assert fetch_insider_transactions("NVDA", "") == []

    def test_parses_data(self):
        payload = {"data": [_row("P", 1000, 50.0, "2026-06-05")]}
        with patch.dict("sys.modules", {"requests": _mock_requests(payload)}):
            out = fetch_insider_transactions("nvda", "k", 0.0)
        assert len(out) == 1

    def test_http_error_returns_empty(self):
        with patch.dict("sys.modules", {"requests": _mock_requests({}, ok=False)}):
            assert fetch_insider_transactions("NVDA", "k", 0.0) == []

    def test_missing_data_key(self):
        with patch.dict("sys.modules", {"requests": _mock_requests({"x": 1})}):
            assert fetch_insider_transactions("NVDA", "k", 0.0) == []


class TestNotableBuysForTicker:
    TODAY = date(2026, 6, 9)

    def test_keeps_only_purchases(self):
        rows = [
            _row("P", 2000, 100.0, "2026-06-05"),   # buy, $200k -> keep
            _row("S", 5000, 100.0, "2026-06-05"),   # sale -> drop
            _row("G", 1000, 100.0, "2026-06-05"),   # gift -> drop
        ]
        buys = notable_buys_for_ticker(rows, "NVDA", self.TODAY)
        assert [b.shares for b in buys] == [2000]
        assert buys[0].value == 200000.0

    def test_min_value_filter(self):
        rows = [_row("P", 100, 50.0, "2026-06-05")]  # $5k < 100k default
        assert notable_buys_for_ticker(rows, "NVDA", self.TODAY) == []

    def test_lookback_filter(self):
        rows = [_row("P", 5000, 100.0, "2026-01-01")]  # > 14d ago
        assert notable_buys_for_ticker(rows, "NVDA", self.TODAY, lookback_days=14) == []

    def test_sorted_by_value(self):
        rows = [
            _row("P", 1000, 200.0, "2026-06-05"),  # $200k
            _row("P", 5000, 200.0, "2026-06-06"),  # $1M
        ]
        buys = notable_buys_for_ticker(rows, "NVDA", self.TODAY)
        assert [b.value for b in buys] == [1_000_000.0, 200_000.0]

    def test_ignores_malformed_rows(self):
        rows = [_row("P", None, 100.0, "2026-06-05"), "not-a-dict", {}]
        assert notable_buys_for_ticker(rows, "NVDA", self.TODAY) == []


class TestNotableInsiderBuys:
    TODAY = date(2026, 6, 9)

    def test_aggregates_across_tickers_and_caps(self):
        def fake_fetch(symbol, api_key, mci):
            return {"NVDA": [_row("P", 5000, 200.0, "2026-06-06")],
                    "AVGO": [_row("P", 1000, 200.0, "2026-06-06")]}.get(symbol, [])
        picks = [_pick("NVDA"), _pick("AVGO"), _pick(None)]  # last has no ticker
        with patch("market_mover.sources.insider_source.fetch_insider_transactions",
                   side_effect=fake_fetch):
            buys = notable_insider_buys(picks, "k", self.TODAY, limit=1, min_call_interval=0.0)
        assert len(buys) == 1
        assert buys[0].ticker == "NVDA"  # biggest

    def test_no_key_returns_empty(self):
        assert notable_insider_buys([_pick("NVDA")], "", self.TODAY) == []


class TestInsiderRendering:
    def _arts(self):
        return [RankedArticle(rank=1, title="X", url="https://x.com/a",
                              source_name="x", market_impact_summary="s", impact_score=8.0)]

    def test_html_card(self):
        from market_mover.email_template import render_email_html
        buys = [InsiderBuy(ticker="ACME", insider="Jane Exec", shares=5000,
                           price=200.0, value=1_000_000.0, transaction_date="2026-06-06")]
        html = render_email_html(self._arts(), insider_buys=buys)
        assert "INSIDER BUYING" in html
        assert "ACME" in html
        assert "$1.0M" in html

    def test_no_card_when_empty(self):
        from market_mover.email_template import render_email_html
        assert "INSIDER BUYING" not in render_email_html(self._arts(), insider_buys=[])

    def test_plain_text(self):
        from market_mover.email_template import render_plain_text
        buys = [InsiderBuy(ticker="ACME", insider="Jane Exec", shares=5000,
                           price=200.0, value=1_000_000.0, transaction_date="2026-06-06")]
        text = render_plain_text(self._arts(), insider_buys=buys)
        assert "INSIDER BUYING" in text
        assert "ACME" in text
