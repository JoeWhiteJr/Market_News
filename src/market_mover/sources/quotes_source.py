"""Quotes source — fetches recent daily close prices from Finnhub for sparklines + judge."""

import logging
import time
from datetime import date, datetime, timedelta, timezone

from ..models import SparklineSeries

logger = logging.getLogger("market_mover.sources.quotes")

# Same 20s HTTP timeout convention used by the news sources since Cycle 1.
_HTTP_TIMEOUT_SECS = 20

# Anything within +/- this percent is rendered as "flat" rather than up/down.
_FLAT_THRESHOLD_PCT = 0.1

# Per-call rate limit shared across all sparkline requests in this process.
_last_call_time: float = 0.0

# Finnhub VIX symbol — they prefix non-equity indices with a caret. Used by the
# Phase B judge to pull the absolute VIX close level + 24h change.
_FINNHUB_VIX_SYMBOLS = ("^VIX", "VIX")


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


# ---------------------------------------------------------------------------
# Cycle 4 Phase B — judge price-data fetches
# ---------------------------------------------------------------------------


def _fetch_candle(
    ticker: str,
    from_ts: int,
    to_ts: int,
    api_key: str,
) -> dict | None:
    """Make a single Finnhub ``/stock/candle`` GET. Returns the parsed JSON
    payload, or ``None`` on any failure (HTTP error, no requests lib, bad
    JSON). The judge-side callers treat ``None`` as "no data — pass null
    through to the LLM."""
    try:
        import requests
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(f"Quote fetch unavailable (requests import failed): {e}")
        return None

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/stock/candle",
            params={
                "symbol": ticker,
                "resolution": "D",
                "from": from_ts,
                "to": to_ts,
                "token": api_key,
            },
            timeout=_HTTP_TIMEOUT_SECS,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Candle fetch failed for {ticker}: {e}")
        return None


def fetch_close_price(
    ticker: str,
    on_date: date,
    api_key: str,
    min_call_interval: float = 1.0,
) -> float | None:
    """Return the daily close price for ``ticker`` on ``on_date``.

    Returns ``None`` when the date has no trading data (weekend, holiday,
    illiquid ticker, API failure). The Phase B judge uses this for
    close-to-close 24h calculations — the caller is expected to advance the
    date manually for Friday→Monday windows.

    Args:
        ticker: Symbol to fetch (e.g. ``"SPY"``).
        on_date: Calendar date to look up.
        api_key: Finnhub API key. Empty -> ``None``.
        min_call_interval: Min seconds between Finnhub HTTP calls. Defaults
            to the project-wide 1.0s convention.
    """
    if not api_key or not ticker:
        return None

    _enforce_rate_limit(min_call_interval)

    # Finnhub /stock/candle returns a list — pad the window by a couple days
    # in case the exact date is a weekend/holiday and we want the user to
    # have advanced manually (we still return None here if the requested
    # date has no candle, but we ask for a small range so the API has data
    # to return rather than failing outright).
    day_start = datetime(on_date.year, on_date.month, on_date.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    from_ts = int(day_start.timestamp())
    to_ts = int(day_end.timestamp())

    payload = _fetch_candle(ticker.upper(), from_ts, to_ts, api_key)
    if not isinstance(payload, dict):
        return None
    if payload.get("s") != "ok":
        return None

    closes_raw = payload.get("c") or []
    if not isinstance(closes_raw, list) or not closes_raw:
        return None

    try:
        # The last close in the window is the one we want (handles APIs that
        # return adjacent days if our timestamp window straddles a boundary).
        return float(closes_raw[-1])
    except (TypeError, ValueError):
        return None


def _next_trading_day(
    ticker: str,
    after_date: date,
    api_key: str,
    min_call_interval: float,
    max_lookahead: int = 7,
) -> tuple[date, float] | None:
    """Find the next trading day's close after ``after_date`` for ``ticker``.

    Walks forward 1..max_lookahead calendar days and returns the first one
    with a close. Friday → Monday (3 calendar days) is the common case;
    Friday-into-a-Monday-holiday → Tuesday is 4 days. We cap at 7 to avoid
    surprising the API budget on a permanently delisted ticker.
    """
    for offset in range(1, max_lookahead + 1):
        candidate = after_date + timedelta(days=offset)
        close = fetch_close_price(ticker, candidate, api_key, min_call_interval)
        if close is not None:
            return candidate, close
    return None


def fetch_24h_close_change(
    ticker: str,
    briefing_date: date,
    api_key: str,
    min_call_interval: float = 1.0,
) -> tuple[float | None, float | None]:
    """Return ``(pct_change, vix_level_if_ticker_is_VIX_else_None)``.

    Implements the ADR's locked window: close on ``briefing_date`` vs close
    on the NEXT TRADING DAY. So a Friday briefing grades against Monday's
    close (3 calendar days, 1 trading day). Holidays slide one more day.

    For VIX (``"VIX"`` / ``"^VIX"``), also returns the absolute close level
    — the judge prompt needs both the level (``vix_close``) and the change
    (``vix_pct``).

    Returns ``(None, None)`` when the underlying data isn't available — the
    judge LLM still produces a verdict (typically ``NOT_APPLICABLE`` or
    ``TOO_EARLY``) when the prices are null.

    Args:
        ticker: Symbol to fetch (case-insensitive). VIX special-cased.
        briefing_date: The day the briefing was sent — close-to-close window
            starts here.
        api_key: Finnhub API key. Empty -> ``(None, None)``.
        min_call_interval: Min seconds between Finnhub HTTP calls.
    """
    if not api_key or not ticker:
        return None, None

    sym = ticker.strip().upper()
    is_vix = sym in {"VIX", "^VIX"}
    # Finnhub uses the ^VIX symbol for the index itself.
    finnhub_sym = "^VIX" if is_vix else sym

    start_close = fetch_close_price(
        finnhub_sym, briefing_date, api_key, min_call_interval
    )
    if start_close is None or start_close == 0:
        return None, None

    next_day = _next_trading_day(
        finnhub_sym, briefing_date, api_key, min_call_interval
    )
    if next_day is None:
        if is_vix:
            return None, start_close
        return None, None

    _, end_close = next_day
    pct_change = ((end_close - start_close) / start_close) * 100.0

    if is_vix:
        return pct_change, end_close
    return pct_change, None
