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
# Cycle 4 Phase B/C — judge price-data fetches
# ---------------------------------------------------------------------------
#
# NOTE on the data source (Cycle 4c): Finnhub's ``/stock/candle`` endpoint is
# premium-only on our current plan — it returns HTTP 403 ("You don't have
# access to this resource"). The free ``/quote`` endpoint works and returns a
# single snapshot: current close ``c``, previous close ``pc``, and the session
# percent change ``dp``. We grade against that snapshot's session.
#
# Why a snapshot is the right window: the daily cron grades YESTERDAY's
# briefing pre-market (~06:00 MDT, before the 09:30 ET open). At that moment
# the most-recent COMPLETED session is the one that followed yesterday's
# pre-market briefing — exactly the "close-to-close, ~24h after the briefing"
# window the ADR locks. ``dp`` is that session's move; ``c`` is its close
# (used as the VIX level). ``briefing_date`` is retained for signature
# stability + a staleness guard, not for historical lookup (``/quote`` has no
# history).


def _fetch_quote(
    ticker: str,
    api_key: str,
    min_call_interval: float,
) -> dict | None:
    """Make a single Finnhub ``/quote`` GET. Returns the parsed JSON payload,
    or ``None`` on any failure (HTTP error, no requests lib, bad JSON). The
    judge-side callers treat ``None`` as "no data — pass null through to the
    LLM."

    A successful ``/quote`` for a real symbol returns non-zero ``c`` and
    ``pc``. Finnhub returns ``c == 0`` (and ``pc == 0``) for an unknown
    symbol, which callers treat as "no data".
    """
    try:
        import requests
    except Exception as e:  # pragma: no cover — defensive; requests is transitive
        logger.warning(f"Quote fetch unavailable (requests import failed): {e}")
        return None

    _enforce_rate_limit(min_call_interval)

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker, "token": api_key},
            timeout=_HTTP_TIMEOUT_SECS,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Quote fetch failed for {ticker}: {e}")
        return None


def fetch_24h_close_change(
    ticker: str,
    briefing_date: date,
    api_key: str,
    min_call_interval: float = 1.0,
) -> tuple[float | None, float | None]:
    """Return ``(pct_change, vix_level_if_ticker_is_VIX_else_None)``.

    Implements the ADR's locked close-to-close window via Finnhub ``/quote``
    (see the module note above for why a snapshot is the correct window when
    graded pre-market the next day). ``pct_change`` is the session percent
    move (``dp``, falling back to ``(c - pc) / pc`` when ``dp`` is absent).

    For VIX (``"VIX"`` / ``"^VIX"``), also returns the absolute close level
    ``c`` — the judge prompt needs both the level (``vix_close``) and the
    change (``vix_pct``).

    Returns ``(None, None)`` when the underlying data isn't available — the
    judge LLM still produces a verdict (typically ``NOT_APPLICABLE`` or
    ``TOO_EARLY``) when the prices are null. For VIX with a usable level but
    no clean change, returns ``(None, level)`` so the prompt still shows the
    VIX level.

    Args:
        ticker: Symbol to fetch (case-insensitive). VIX special-cased.
        briefing_date: The day the briefing was sent. Retained for signature
            stability; ``/quote`` has no historical lookup so the fetch uses
            the latest completed session (see module note).
        api_key: Finnhub API key. Empty -> ``(None, None)``.
        min_call_interval: Min seconds between Finnhub HTTP calls.
    """
    if not api_key or not ticker:
        return None, None

    sym = ticker.strip().upper()
    is_vix = sym in {"VIX", "^VIX"}
    # Finnhub uses the ^VIX symbol for the index itself.
    finnhub_sym = "^VIX" if is_vix else sym

    quote = _fetch_quote(finnhub_sym, api_key, min_call_interval)
    if not isinstance(quote, dict):
        return None, None

    def _num(key: str) -> float | None:
        val = quote.get(key)
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    current_close = _num("c")
    prev_close = _num("pc")
    dp = _num("dp")

    # Finnhub returns c == 0 / pc == 0 for an unknown symbol — treat as no data.
    if not current_close:  # None or 0.0
        return None, None

    if dp is not None:
        pct_change = dp
    elif prev_close:  # not None and not 0.0
        pct_change = ((current_close - prev_close) / prev_close) * 100.0
    else:
        # We have a level but no usable change (e.g. pc missing). For VIX the
        # level is still useful to the prompt; for equities it isn't.
        return None, (current_close if is_vix else None)

    if is_vix:
        return pct_change, current_close
    return pct_change, None
