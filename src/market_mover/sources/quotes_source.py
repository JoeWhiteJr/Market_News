"""Quotes source — daily closes for sparklines + the judge, via Alpaca (ADR 0002).

The HTTP layer lives in :mod:`alpaca_source`; this module shapes Alpaca daily
bars into the two things the app needs:

- :func:`fetch_sparkline_data` — last N daily closes per ticker for the top strip.
- :func:`fetch_24h_close_change` — the briefing-day session's close-to-close move
  for the Yesterday-Index judge.

VIX handling (Joe's call, ADR 0002): the CBOE VIX *index* isn't on Alpaca, so we
proxy it with the ``VIXY`` ETF for **direction only**. The judge gets the proxy's
percent move as ``vix_pct`` but the VIX *level* stays ``None`` — we never feed the
ETF's price into the prompt's "VIX level" slot (it isn't the VIX level).
"""

import logging
from datetime import date, datetime, timedelta

from ..models import SparklineSeries
from .alpaca_source import fetch_daily_bars, trailing_window

logger = logging.getLogger("market_mover.sources.quotes")

# Anything within +/- this percent renders as "flat" rather than up/down.
_FLAT_THRESHOLD_PCT = 0.1

# VIX index isn't on Alpaca; proxy with this ETF for direction only.
_VIX_SYMBOLS = {"VIX", "^VIX"}
_VIX_PROXY_ETF = "VIXY"

# The 11 SPDR sector ETFs, in a fixed display order (MM-T006 "Market Weather").
SECTOR_ETFS: list[tuple[str, str]] = [
    ("XLK", "Tech"), ("XLF", "Financials"), ("XLE", "Energy"), ("XLV", "Health"),
    ("XLY", "Cons Disc"), ("XLI", "Industrials"), ("XLC", "Comms"), ("XLP", "Staples"),
    ("XLU", "Utilities"), ("XLB", "Materials"), ("XLRE", "Real Estate"),
]


def _bar_date(bar: dict) -> date | None:
    """Parse an Alpaca bar's ``t`` (RFC-3339) into a calendar date."""
    raw = bar.get("t") if isinstance(bar, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        # Bars are day-stamped, e.g. "2026-06-05T04:00:00Z" — the date prefix
        # is all we need.
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _closes(bars: list[dict]) -> list[float]:
    """Extract close prices from a list of Alpaca bars, oldest-first."""
    out: list[float] = []
    for b in bars:
        try:
            out.append(float(b["c"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _classify_direction(pct_change: float) -> str:
    """Classify a percent change as ``up`` / ``down`` / ``flat``.

    Flat is reserved for very small moves (default <0.1% in either direction)
    so a 0.02% drift doesn't render as a tiny arrow.
    """
    if abs(pct_change) < _FLAT_THRESHOLD_PCT:
        return "flat"
    return "up" if pct_change > 0 else "down"


def fetch_sparkline_data(
    tickers: list[str],
    days: int = 5,
    api_key_id: str = "",
    api_secret_key: str = "",
    feed: str = "iex",
    min_call_interval: float = 1.0,
) -> dict[str, SparklineSeries]:
    """Fetch the last ``days`` daily closes for each ticker as sparklines.

    One batched Alpaca request for all tickers. ``VIX`` is transparently
    fetched and labeled as ``VIXY`` (its tradeable proxy). Tickers with fewer
    than 2 closes are omitted; a total failure returns ``{}`` (callers treat
    empty as "skip the strip").

    Args:
        tickers: Symbols for the strip (e.g. ``["SPY","QQQ","DIA","VIX","IWM"]``).
        days: Trading days of history to keep. We over-fetch the calendar
            window so weekends/holidays still yield ``days`` real closes.
        api_key_id / api_secret_key: Alpaca data credentials. Missing -> ``{}``.
        feed: Alpaca data feed ("iex" on free plans).
        min_call_interval: Min seconds between Alpaca HTTP calls.
    """
    if not (api_key_id and api_secret_key) or not tickers:
        if not (api_key_id and api_secret_key):
            logger.info("Alpaca creds not set, skipping sparkline fetch")
        return {}

    # Map VIX -> VIXY for both the fetch and the displayed label.
    fetch_syms: list[str] = []
    for raw in tickers:
        t = (raw or "").strip().upper()
        if not t:
            continue
        fetch_syms.append(_VIX_PROXY_ETF if t in _VIX_SYMBOLS else t)
    if not fetch_syms:
        return {}

    start, end = trailing_window(max(days * 3, 12))
    bars_map = fetch_daily_bars(
        fetch_syms, start, end, api_key_id, api_secret_key, feed, min_call_interval
    )

    results: dict[str, SparklineSeries] = {}
    for sym, bars in bars_map.items():
        closes = _closes(bars)[-days:]
        if len(closes) < 2 or closes[0] == 0:
            continue
        pct_change = ((closes[-1] - closes[0]) / closes[0]) * 100.0
        results[sym] = SparklineSeries(
            ticker=sym,
            close_prices=closes,
            pct_change=pct_change,
            direction=_classify_direction(pct_change),
        )

    logger.info(f"Fetched sparkline data for {len(results)}/{len(fetch_syms)} tickers")
    return results


def fetch_sector_moves(
    api_key_id: str = "",
    api_secret_key: str = "",
    feed: str = "iex",
    min_call_interval: float = 1.0,
) -> list[tuple[str, str, float]]:
    """Last completed session's close-to-close % move for each sector ETF.

    Returns ``[(ticker, label, pct), ...]`` in :data:`SECTOR_ETFS` order,
    omitting any ETF Alpaca didn't return ≥2 closes for. Total failure or
    missing creds → ``[]`` (caller hides the heatmap).
    """
    if not (api_key_id and api_secret_key):
        logger.info("Alpaca creds not set, skipping sector heatmap fetch")
        return []
    symbols = [t for t, _ in SECTOR_ETFS]
    # Over-fetch the calendar window so weekends/holidays still yield 2 closes.
    start, end = trailing_window(12)
    bars_map = fetch_daily_bars(
        symbols, start, end, api_key_id, api_secret_key, feed, min_call_interval
    )
    out: list[tuple[str, str, float]] = []
    for ticker, label in SECTOR_ETFS:
        closes = _closes(bars_map.get(ticker, []))
        if len(closes) < 2 or closes[-2] == 0:
            continue
        pct = ((closes[-1] - closes[-2]) / closes[-2]) * 100.0
        out.append((ticker, label, pct))
    logger.info(f"Fetched sector moves for {len(out)}/{len(SECTOR_ETFS)} ETFs")
    return out


# ---------------------------------------------------------------------------
# Yesterday-Index judge price-data fetch (ADR 0001 window, ADR 0002 source)
# ---------------------------------------------------------------------------


def fetch_24h_close_change(
    ticker: str,
    briefing_date: date,
    api_key_id: str,
    api_secret_key: str,
    feed: str = "iex",
    min_call_interval: float = 1.0,
) -> tuple[float | None, float | None]:
    """Return ``(pct_change, vix_level_or_None)`` for the briefing-day session.

    Implements ADR 0001's locked close-to-close window using real Alpaca daily
    bars: the percent move from the trading day *before* ``briefing_date`` to
    ``briefing_date``'s close — i.e. the session that immediately followed the
    pre-market briefing. (Same window the Cycle 4c ``/quote`` path produced,
    now from true history instead of a snapshot.)

    VIX (``"VIX"``/``"^VIX"``) is proxied by the ``VIXY`` ETF for **direction
    only**: the proxy's percent move is returned as ``pct_change`` and the VIX
    *level* stays ``None`` (the ETF price is not the VIX level). For every
    other symbol the second element is always ``None``.

    Returns ``(None, None)`` when fewer than two usable bars are available — the
    judge LLM then sees ``null`` and typically returns ``NOT_APPLICABLE``.

    Args:
        ticker: Symbol (case-insensitive). VIX special-cased to its proxy.
        briefing_date: The day the briefing was sent — the session being graded.
        api_key_id / api_secret_key: Alpaca data credentials. Missing ->
            ``(None, None)``.
        feed: Alpaca data feed ("iex" on free plans).
        min_call_interval: Min seconds between Alpaca HTTP calls.
    """
    if not (api_key_id and api_secret_key) or not ticker:
        return None, None

    sym = ticker.strip().upper()
    fetch_sym = _VIX_PROXY_ETF if sym in _VIX_SYMBOLS else sym

    # Over-fetch ~10 calendar days so we always capture the briefing-day bar
    # plus the prior trading bar across weekends/holidays.
    start = briefing_date - timedelta(days=10)
    bars_map = fetch_daily_bars(
        [fetch_sym], start, briefing_date, api_key_id, api_secret_key, feed, min_call_interval
    )
    bars = bars_map.get(fetch_sym) or []

    # Keep bars on/before the briefing date (defensive: never grade with a bar
    # from after the session we're measuring), oldest-first.
    usable = [
        b for b in bars
        if (d := _bar_date(b)) is not None and d <= briefing_date
    ]
    if len(usable) < 2:
        return None, None

    try:
        prev_close = float(usable[-2]["c"])
        last_close = float(usable[-1]["c"])
    except (KeyError, TypeError, ValueError):
        return None, None
    if prev_close == 0:
        return None, None

    pct_change = ((last_close - prev_close) / prev_close) * 100.0
    # VIX proxy: direction only — the level stays null by design.
    return pct_change, None
