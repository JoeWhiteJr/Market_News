"""Earnings calendar source (creative #14 — Pre-Market Earnings Card).

Finnhub's ``/calendar/earnings`` endpoint works on the free tier (unlike the
premium ``/stock/candle``). We use it to surface the notable companies
reporting earnings *today* in a small card at the top of the briefing.
"""

import logging
import time
from datetime import date, timedelta

from pydantic import BaseModel

logger = logging.getLogger("market_mover.sources.earnings")

_HTTP_TIMEOUT_SECS = 20
_last_call_time: float = 0.0


class EarningsEntry(BaseModel):
    """One company's scheduled earnings report."""

    symbol: str
    date: str                              # ISO date
    hour: str = ""                         # "bmo" | "amc" | "dmh" | ""
    eps_estimate: float | None = None
    revenue_estimate: float | None = None

    @property
    def when_label(self) -> str:
        """Human label for the report time."""
        return {
            "bmo": "Before open",
            "amc": "After close",
            "dmh": "Mid-day",
        }.get(self.hour, "Time TBD")


def _enforce_rate_limit(min_interval: float) -> None:
    global _last_call_time
    now = time.monotonic()
    elapsed = now - _last_call_time
    if _last_call_time > 0 and elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_call_time = time.monotonic()


def fetch_earnings_calendar(
    api_key: str,
    from_date: date,
    to_date: date,
    min_call_interval: float = 1.0,
) -> list[EarningsEntry]:
    """Fetch the earnings calendar over ``[from_date, to_date]`` (inclusive).

    Returns a list of :class:`EarningsEntry` (possibly empty). Any failure —
    missing key, HTTP error, bad JSON — returns ``[]`` (the card is optional).
    """
    if not api_key:
        logger.info("Finnhub key not set, skipping earnings calendar")
        return []

    try:
        import requests
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(f"Earnings fetch unavailable (requests import failed): {e}")
        return []

    _enforce_rate_limit(min_call_interval)

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "token": api_key,
            },
            timeout=_HTTP_TIMEOUT_SECS,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.warning(f"Earnings calendar fetch failed: {e}")
        return []

    raw = payload.get("earningsCalendar") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []

    out: list[EarningsEntry] = []
    for row in raw:
        if not isinstance(row, dict) or not row.get("symbol") or not row.get("date"):
            continue
        out.append(
            EarningsEntry(
                symbol=str(row["symbol"]).upper(),
                date=str(row["date"]),
                hour=str(row.get("hour") or ""),
                eps_estimate=_to_float(row.get("epsEstimate")),
                revenue_estimate=_to_float(row.get("revenueEstimate")),
            )
        )
    return out


def _to_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def notable_earnings_for(
    entries: list[EarningsEntry], on_date: date, limit: int = 5
) -> list[EarningsEntry]:
    """Pick the most notable reporters on ``on_date``.

    "Notable" is proxied by **revenue estimate** (bigger = more market-moving);
    entries without an estimate sort last. Returns at most ``limit``, ordered
    biggest-first.
    """
    todays = [e for e in entries if e.date == on_date.isoformat()]
    todays.sort(key=lambda e: (e.revenue_estimate or -1.0), reverse=True)
    return todays[:limit]


def default_window(today: date) -> tuple[date, date]:
    """The calendar window to request — today through the end of the week so a
    single fetch also primes any future use. Callers filter to the day they
    want via :func:`notable_earnings_for`."""
    return today, today + timedelta(days=2)
