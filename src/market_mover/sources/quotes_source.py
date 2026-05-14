"""Quotes source — fetches recent daily close prices from Finnhub for sparklines."""

import logging
import time
from datetime import datetime, timedelta, timezone

from ..models import SparklineSeries

logger = logging.getLogger("market_mover.sources.quotes")

# Same 20s HTTP timeout convention used by the news sources since Cycle 1.
_HTTP_TIMEOUT_SECS = 20

# Anything within +/- this percent is rendered as "flat" rather than up/down.
_FLAT_THRESHOLD_PCT = 0.1

# Per-call rate limit shared across all sparkline requests in this process.
_last_call_time: float = 0.0


def fetch_sparkline_data(
    tickers: list[str],
    days: int = 5,
    api_key: str = "",
    min_call_interval: float = 1.0,
) -> dict[str, SparklineSeries]:
    """Fetch the last ``days`` daily close prices for each ticker.

    Args:
        tickers: Symbols to fetch (e.g. ``["SPY", "QQQ", "DIA", "VIX", "IWM"]``).
        days: How many trading days of data to request. We pad the calendar
            window to ``days * 3`` to cover weekends / holidays and then keep
            the last ``days`` closes returned.
        api_key: Finnhub API key. Empty -> returns ``{}``.
        min_call_interval: Minimum seconds between Finnhub HTTP calls.

    Returns:
        Mapping of ticker -> :class:`SparklineSeries`. Tickers with no data or
        an HTTP failure are silently omitted. A total fetch failure returns
        ``{}`` (callers should treat empty as "skip the strip").
    """
    if not api_key:
        logger.info("Finnhub API key not set, skipping sparkline fetch")
        return {}
    if not tickers:
        return {}

    try:
        import requests
    except Exception as e:  # pragma: no cover — defensive; requests is a transitive dep
        logger.warning(f"Sparkline fetch unavailable (requests import failed): {e}")
        return {}

    # Finnhub's /stock/candle endpoint expects Unix seconds. Pad the window
    # generously so weekends and holidays still yield ``days`` real closes.
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=max(days * 3, 10))
    params_from = int(window_start.timestamp())
    params_to = int(now.timestamp())

    results: dict[str, SparklineSeries] = {}

    for raw_ticker in tickers:
        ticker = (raw_ticker or "").strip().upper()
        if not ticker:
            continue

        _enforce_rate_limit(min_call_interval)

        try:
            resp = requests.get(
                "https://finnhub.io/api/v1/stock/candle",
                params={
                    "symbol": ticker,
                    "resolution": "D",
                    "from": params_from,
                    "to": params_to,
                    "token": api_key,
                },
                timeout=_HTTP_TIMEOUT_SECS,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.warning(f"Sparkline fetch failed for {ticker}: {e}")
            continue

        series = _build_series(ticker, payload, days)
        if series is not None:
            results[ticker] = series

    logger.info(f"Fetched sparkline data for {len(results)}/{len(tickers)} tickers")
    return results


def _build_series(
    ticker: str, payload: dict, days: int
) -> SparklineSeries | None:
    """Convert a Finnhub candle payload into a :class:`SparklineSeries`.

    Returns ``None`` if the payload has no usable data — Finnhub signals
    "no data" with ``{"s": "no_data"}``.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("s") != "ok":
        return None

    closes_raw = payload.get("c") or []
    if not isinstance(closes_raw, list) or len(closes_raw) < 2:
        return None

    # Keep only the last ``days`` closes; coerce to float defensively.
    try:
        closes = [float(c) for c in closes_raw[-days:]]
    except (TypeError, ValueError):
        return None

    if len(closes) < 2 or closes[0] == 0:
        return None

    pct_change = ((closes[-1] - closes[0]) / closes[0]) * 100.0
    direction = _classify_direction(pct_change)

    return SparklineSeries(
        ticker=ticker,
        close_prices=closes,
        pct_change=pct_change,
        direction=direction,
    )


def _classify_direction(pct_change: float) -> str:
    """Classify a percent change as ``up`` / ``down`` / ``flat``.

    Flat is reserved for very small moves (default <0.1% in either direction)
    so a 0.02% drift on VIX doesn't render as a tiny red arrow.
    """
    if abs(pct_change) < _FLAT_THRESHOLD_PCT:
        return "flat"
    return "up" if pct_change > 0 else "down"


def _enforce_rate_limit(min_interval: float) -> None:
    """Enforce minimum interval between Finnhub HTTP calls."""
    global _last_call_time
    now = time.monotonic()
    elapsed = now - _last_call_time
    if _last_call_time > 0 and elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_call_time = time.monotonic()
