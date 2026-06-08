"""Tests for the Alpaca Market Data layer (ADR 0002)."""

from datetime import date

from unittest.mock import MagicMock, patch

from market_mover.sources.alpaca_source import fetch_daily_bars, trailing_window


def _mock_requests(payload, status_ok=True):
    resp = MagicMock()
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = RuntimeError("HTTP 403")
    resp.json.return_value = payload
    mock = MagicMock()
    mock.get.return_value = resp
    return mock


class TestFetchDailyBars:
    def test_no_creds_returns_empty(self):
        assert fetch_daily_bars(["SPY"], date(2026, 5, 1), date(2026, 5, 8), "", "") == {}

    def test_no_symbols_returns_empty(self):
        assert fetch_daily_bars([], date(2026, 5, 1), date(2026, 5, 8), "k", "s") == {}

    def test_success_returns_symbol_to_bars(self):
        payload = {
            "bars": {
                "SPY": [{"t": "2026-05-07T04:00:00Z", "c": 500.0}],
                "QQQ": [{"t": "2026-05-07T04:00:00Z", "c": 400.0}],
            }
        }
        with patch.dict("sys.modules", {"requests": _mock_requests(payload)}):
            out = fetch_daily_bars(
                ["spy", "qqq"], date(2026, 5, 1), date(2026, 5, 8), "k", "s",
                min_call_interval=0.0,
            )
        assert set(out) == {"SPY", "QQQ"}
        assert out["SPY"][0]["c"] == 500.0

    def test_sends_correct_params_and_headers(self):
        payload = {"bars": {"SPY": [{"t": "2026-05-07T04:00:00Z", "c": 500.0}]}}
        mock = _mock_requests(payload)
        with patch.dict("sys.modules", {"requests": mock}):
            fetch_daily_bars(
                ["SPY"], date(2026, 5, 1), date(2026, 5, 8), "key-id", "secret",
                feed="iex", min_call_interval=0.0,
            )
        _, kwargs = mock.get.call_args
        assert kwargs["params"]["symbols"] == "SPY"
        assert kwargs["params"]["timeframe"] == "1Day"
        assert kwargs["params"]["feed"] == "iex"
        assert kwargs["params"]["start"] == "2026-05-01"
        assert kwargs["params"]["end"] == "2026-05-08"
        assert kwargs["headers"]["APCA-API-KEY-ID"] == "key-id"
        assert kwargs["headers"]["APCA-API-SECRET-KEY"] == "secret"

    def test_http_error_returns_empty(self):
        with patch.dict("sys.modules", {"requests": _mock_requests({}, status_ok=False)}):
            out = fetch_daily_bars(
                ["SPY"], date(2026, 5, 1), date(2026, 5, 8), "k", "s",
                min_call_interval=0.0,
            )
        assert out == {}

    def test_empty_series_dropped(self):
        payload = {"bars": {"SPY": [{"t": "2026-05-07T04:00:00Z", "c": 1.0}], "ZZZ": []}}
        with patch.dict("sys.modules", {"requests": _mock_requests(payload)}):
            out = fetch_daily_bars(
                ["SPY", "ZZZ"], date(2026, 5, 1), date(2026, 5, 8), "k", "s",
                min_call_interval=0.0,
            )
        assert set(out) == {"SPY"}

    def test_missing_bars_key_returns_empty(self):
        with patch.dict("sys.modules", {"requests": _mock_requests({"next_page_token": None})}):
            out = fetch_daily_bars(
                ["SPY"], date(2026, 5, 1), date(2026, 5, 8), "k", "s",
                min_call_interval=0.0,
            )
        assert out == {}


class TestTrailingWindow:
    def test_end_is_today_and_start_is_before(self):
        start, end = trailing_window(12)
        assert end == date.today()
        assert start < end
        assert (end - start).days == 12
