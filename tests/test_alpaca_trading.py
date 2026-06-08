"""Tests for the Alpaca paper trading client (Cycle 6)."""

from unittest.mock import MagicMock, patch

from market_mover.alpaca_trading import AlpacaTradingClient


class _Settings:
    alpaca_paper_base_url = "https://paper-api.alpaca.markets"
    alpaca_api_key_id = "key-id"
    alpaca_api_secret_key = "secret"


def _mock(resp_json, ok=True):
    resp = MagicMock()
    if ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = RuntimeError("HTTP 422")
    resp.json.return_value = resp_json
    m = MagicMock()
    m.request.return_value = resp
    return m


class TestAlpacaTradingClient:
    def test_paper_base_url(self):
        c = AlpacaTradingClient(_Settings())
        assert c._base == "https://paper-api.alpaca.markets"
        assert "paper" in c._base  # safety rail: never a live host

    def test_submit_notional_order_body(self):
        m = _mock({"id": "o1"})
        with patch.dict("sys.modules", {"requests": m}):
            out = AlpacaTradingClient(_Settings()).submit_notional_order("nvda", 1000.0)
        assert out == {"id": "o1"}
        _, kwargs = m.request.call_args
        body = kwargs["json"]
        assert body["symbol"] == "NVDA"
        assert body["notional"] == 1000.0
        assert body["side"] == "buy"
        assert body["type"] == "market"
        assert body["time_in_force"] == "day"

    def test_close_position_uses_delete(self):
        m = _mock({"id": "c1"})
        with patch.dict("sys.modules", {"requests": m}):
            AlpacaTradingClient(_Settings()).close_position("aapl")
        args, kwargs = m.request.call_args
        assert args[0] == "DELETE"
        assert args[1].endswith("/v2/positions/AAPL")

    def test_list_positions_returns_list(self):
        m = _mock([{"symbol": "NVDA"}])
        with patch.dict("sys.modules", {"requests": m}):
            out = AlpacaTradingClient(_Settings()).list_positions()
        assert out == [{"symbol": "NVDA"}]

    def test_failure_returns_none(self):
        m = _mock({}, ok=False)
        with patch.dict("sys.modules", {"requests": m}):
            c = AlpacaTradingClient(_Settings())
            assert c.get_account() is None
            assert c.list_positions() == []  # non-list -> []

    def test_get_account_returns_dict(self):
        m = _mock({"equity": "100000"})
        with patch.dict("sys.modules", {"requests": m}):
            out = AlpacaTradingClient(_Settings()).get_account()
        assert out == {"equity": "100000"}
