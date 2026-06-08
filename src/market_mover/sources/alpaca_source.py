"""Alpaca Market Data v2 — daily bars for the judge + sparklines (ADR 0002).

Finnhub's ``/stock/candle`` is premium-only on our plan (403), and ``/quote``
is a history-less snapshot. Alpaca's Market Data API gives real historical
daily bars on the free IEX feed, which is what the close-to-close judge window
and the 5-day sparkline strip both need.

This module is the thin HTTP layer (``fetch_daily_bars``); ``quotes_source``
builds the judge/sparkline helpers on top of it so callers' imports don't move.
"""

import logging
import time
from datetime import date, timedelta

logger = logging.getLogger("market_mover.sources.alpaca")

_DATA_BASE = "https://data.alpaca.markets/v2/stocks/bars"
_HTTP_TIMEOUT_SECS = 20

# Shared rate-limit clock across all Alpaca data calls in this process.
_last_call_time: float = 0.0


def _enforce_rate_limit(min_interval: float) -> None:
    """Enforce a minimum interval between Alpaca HTTP calls."""
    global _last_call_time
    now = time.monotonic()
    elapsed = now - _last_call_time
    if _last_call_time > 0 and elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_call_time = time.monotonic()


def fetch_daily_bars(
    symbols: list[str],
    start: date,
    end: date,
    api_key_id: str,
    api_secret_key: str,
    feed: str = "iex",
    min_call_interval: float = 1.0,
) -> dict[str, list[dict]]:
    """Fetch daily OHLCV bars for ``symbols`` over ``[start, end]`` (inclusive).

    One batched request for all symbols. Each returned bar is Alpaca's raw
    dict: ``{"t","o","h","l","c","v","n","vw"}`` (``c`` = close, ``t`` = RFC-3339
    timestamp). Bars are returned oldest-first, as Alpaca sends them.

    Returns a mapping ``symbol -> [bar, ...]``. Symbols with no data are
    omitted. Any failure (missing creds, HTTP error, bad JSON, no requests
    lib) returns ``{}`` — callers treat empty as "no data" and degrade.

    Note: free plans must use ``feed="iex"``; ``feed="sip"`` needs a paid
    subscription and will 403 otherwise.
    """
    if not api_key_id or not api_secret_key:
        logger.info("Alpaca creds not set, skipping bar fetch")
        return {}
    clean = [s.strip().upper() for s in symbols if s and s.strip()]
    if not clean:
        return {}

    try:
        import requests
    except Exception as e:  # pragma: no cover — defensive; requests is transitive
        logger.warning(f"Alpaca fetch unavailable (requests import failed): {e}")
        return {}

    _enforce_rate_limit(min_call_interval)

    headers = {
        "APCA-API-KEY-ID": api_key_id,
        "APCA-API-SECRET-KEY": api_secret_key,
    }
    params = {
        "symbols": ",".join(clean),
        "timeframe": "1Day",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "feed": feed,
        "adjustment": "raw",
        "limit": 10000,
        "sort": "asc",
    }

    try:
        resp = requests.get(
            _DATA_BASE, headers=headers, params=params, timeout=_HTTP_TIMEOUT_SECS
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.warning(f"Alpaca daily-bar fetch failed for {clean}: {e}")
        return {}

    bars = payload.get("bars") if isinstance(payload, dict) else None
    if not isinstance(bars, dict):
        return {}

    # Keep only well-formed, non-empty series.
    return {
        sym: series
        for sym, series in bars.items()
        if isinstance(series, list) and series
    }


def trailing_window(days_back: int) -> tuple[date, date]:
    """Return ``(start, end)`` spanning roughly ``days_back`` calendar days up
    to today — used to over-fetch so weekends/holidays still yield enough
    trading bars. ``end`` is today; both are timezone-naive calendar dates.

    ``date.today()`` is called here (not at import) so tests can monkeypatch
    or pass explicit dates to the callers instead.
    """
    end = date.today()
    return end - timedelta(days=days_back), end
