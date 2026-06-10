"""Insider-transaction source (creative #16 — Insider / Form 4 Spotlight).

Surfaces notable *insider buying* on the day's pick tickers. Open-market
purchases by insiders (Form 4 transaction code ``P``) are one of the more
durable real-world signals — an executive putting their own money in.

We use Finnhub's free ``/stock/insider-transactions`` (structured JSON), which
avoids parsing raw SEC EDGAR Form 4 XML. Buys only — routine sells/grants/option
exercises are noise for this card.
"""

import logging
import time
from datetime import date, timedelta

from pydantic import BaseModel

logger = logging.getLogger("market_mover.sources.insider")

_HTTP_TIMEOUT_SECS = 20
_last_call_time: float = 0.0

# Form 4 transaction code for an open-market purchase.
_PURCHASE_CODE = "P"


class InsiderBuy(BaseModel):
    """A single notable insider open-market purchase."""

    ticker: str
    insider: str
    shares: int                # shares acquired (positive)
    price: float               # per-share transaction price
    value: float               # shares * price (approx dollars)
    transaction_date: str      # ISO date


def _enforce_rate_limit(min_interval: float) -> None:
    global _last_call_time
    now = time.monotonic()
    elapsed = now - _last_call_time
    if _last_call_time > 0 and elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_call_time = time.monotonic()


def fetch_insider_transactions(
    symbol: str, api_key: str, min_call_interval: float = 1.0
) -> list[dict]:
    """Fetch raw insider-transaction rows for ``symbol`` (empty on any failure)."""
    if not api_key or not symbol:
        return []

    try:
        import requests
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(f"Insider fetch unavailable (requests import failed): {e}")
        return []

    _enforce_rate_limit(min_call_interval)
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/stock/insider-transactions",
            params={"symbol": symbol.strip().upper(), "token": api_key},
            timeout=_HTTP_TIMEOUT_SECS,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.warning(f"Insider transactions fetch failed for {symbol}: {e}")
        return []

    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, list) else []


def notable_buys_for_ticker(
    rows: list[dict],
    ticker: str,
    today: date,
    lookback_days: int = 14,
    min_value: float = 100_000.0,
) -> list[InsiderBuy]:
    """Filter raw rows to recent, sizable open-market purchases.

    Keeps only Form 4 code ``P`` transactions within ``lookback_days`` whose
    dollar value clears ``min_value``. Sorted biggest-first.
    """
    cutoff = today - timedelta(days=lookback_days)
    buys: list[InsiderBuy] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("transactionCode", "")).upper() != _PURCHASE_CODE:
            continue
        change = _to_int(r.get("change"))
        price = _to_float(r.get("transactionPrice"))
        tx_date = str(r.get("transactionDate") or "")
        if change is None or change <= 0 or price is None or price <= 0:
            continue
        if not _within(tx_date, cutoff, today):
            continue
        value = change * price
        if value < min_value:
            continue
        buys.append(
            InsiderBuy(
                ticker=ticker.upper(), insider=str(r.get("name") or "Insider").title(),
                shares=change, price=price, value=value, transaction_date=tx_date,
            )
        )
    buys.sort(key=lambda b: b.value, reverse=True)
    return buys


def notable_insider_buys(
    picks: list,
    api_key: str,
    today: date,
    lookback_days: int = 14,
    min_value: float = 100_000.0,
    limit: int = 3,
    min_call_interval: float = 1.0,
) -> list[InsiderBuy]:
    """Aggregate notable insider buys across the day's pick tickers.

    Only single-name picks (those with a ``primary_ticker``) are checked.
    Returns the biggest ``limit`` buys across all of them.
    """
    if not api_key:
        return []
    seen: set[str] = set()
    all_buys: list[InsiderBuy] = []
    for p in picks:
        ticker = (getattr(p, "primary_ticker", None) or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        rows = fetch_insider_transactions(ticker, api_key, min_call_interval)
        all_buys.extend(
            notable_buys_for_ticker(rows, ticker, today, lookback_days, min_value)
        )
    all_buys.sort(key=lambda b: b.value, reverse=True)
    return all_buys[:limit]


def _within(iso_date: str, start: date, end: date) -> bool:
    try:
        d = date.fromisoformat(iso_date[:10])
    except (ValueError, TypeError):
        return False
    return start <= d <= end


def _to_int(v) -> int | None:
    try:
        return int(float(v)) if v is not None else None
    except (TypeError, ValueError):
        return None


def _to_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
