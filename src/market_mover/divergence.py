"""Sentiment vs Price Divergence flag (creative #15).

When a story's *narrative* is clearly bullish but the stock has been falling
(or clearly bearish while the stock rallies), that gap is the kind of
non-obvious setup a reader scrolling headlines would miss. We flag it at the
top of the briefing.

Both halves are deterministic and conservative:
- **Sentiment** comes from a curated bullish/bearish lexicon (same spirit as
  the Overhype Detector — testable, no LLM cost). Only a clear net lean counts.
- **Price** is the ticker's recent multi-day move from Alpaca bars.

A flag fires only when sentiment and price point *opposite* ways AND the price
move clears a threshold — so a tiny wiggle never trips it. False positives kill
trust in this feature faster than a missed signal, so the bar is intentionally
high.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .sources.alpaca_source import fetch_daily_bars

logger = logging.getLogger("market_mover.divergence")

BULLISH_TERMS: frozenset[str] = frozenset({
    "beats", "beat", "tops estimates", "surges", "surge", "soars", "soar",
    "jumps", "rallies", "rally", "upgrade", "upgraded", "raises guidance",
    "raised guidance", "record", "record high", "strong", "outperform",
    "buy rating", "blowout", "bullish", "gains", "climbs", "rebounds",
    "rebound", "recovery", "breakthrough", "wins", "approval", "approved",
    "expands", "boosts", "raises", "beats expectations", "all-time high",
})

BEARISH_TERMS: frozenset[str] = frozenset({
    "misses", "miss", "misses estimates", "plunges", "plunge", "falls",
    "drops", "slumps", "slump", "downgrade", "downgraded", "cuts guidance",
    "cut guidance", "weak", "warning", "warns", "lawsuit", "probe",
    "investigation", "recall", "layoffs", "bankruptcy", "default", "bearish",
    "decline", "declines", "tumbles", "tumble", "sinks", "slashes", "halts",
    "scandal", "fraud", "selloff", "sell-off", "misses expectations",
    "guidance cut", "shortfall", "delays", "delisting",
})

# Default: only flag a >= 2.0% opposing move over the lookback window.
_DEFAULT_THRESHOLD_PCT = 2.0
_DEFAULT_LOOKBACK = 5


@dataclass(frozen=True)
class Sentiment:
    """Net directional lean of a story's language."""

    label: str   # "bullish" | "bearish" | "neutral"
    score: int   # bullish_hits - bearish_hits


@dataclass(frozen=True)
class DivergenceFlag:
    """A detected narrative-vs-tape divergence for one pick."""

    ticker: str
    sentiment: str          # "bullish" | "bearish"
    price_pct: float        # recent move, signed
    lookback: int           # trading sessions measured
    headline: str           # the pick's title
    note: str               # human-readable explanation


def _normalize(text: str) -> str:
    lowered = (text or "").lower().replace("-", " ")
    return re.sub(r"\s+", " ", lowered).strip()


def _count(text: str, terms: frozenset[str]) -> int:
    norm = _normalize(text)
    if not norm:
        return 0
    return sum(
        1 for term in terms
        if re.search(rf"\b{re.escape(_normalize(term))}\b", norm)
    )


def score_sentiment(title: str, summary: str = "") -> Sentiment:
    """Net bullish/bearish lean from the headline + summary."""
    text = f"{title} {summary}"
    score = _count(text, BULLISH_TERMS) - _count(text, BEARISH_TERMS)
    label = "bullish" if score > 0 else "bearish" if score < 0 else "neutral"
    return Sentiment(label=label, score=score)


def _recent_change(
    ticker: str,
    api_key_id: str,
    api_secret_key: str,
    feed: str,
    lookback: int,
    today: date,
    min_call_interval: float,
) -> float | None:
    """Percent move over the last ``lookback`` sessions (oldest→latest close)."""
    start = today - timedelta(days=lookback * 3 + 7)
    bars = fetch_daily_bars(
        [ticker.upper()], start, today, api_key_id, api_secret_key, feed, min_call_interval
    ).get(ticker.upper()) or []
    closes: list[float] = []
    for b in bars:
        try:
            closes.append(float(b["c"]))
        except (KeyError, TypeError, ValueError):
            continue
    window = closes[-lookback:]
    if len(window) < 2 or window[0] == 0:
        return None
    return (window[-1] - window[0]) / window[0] * 100.0


def analyze_divergences(
    picks: list,
    api_key_id: str,
    api_secret_key: str,
    today: date,
    feed: str = "iex",
    threshold_pct: float = _DEFAULT_THRESHOLD_PCT,
    lookback: int = _DEFAULT_LOOKBACK,
    min_call_interval: float = 1.0,
) -> list[DivergenceFlag]:
    """Return divergence flags for picks whose narrative fights the tape.

    A flag fires only when sentiment is clearly bullish/bearish AND the recent
    price move is in the *opposite* direction by at least ``threshold_pct``.
    """
    if not (api_key_id and api_secret_key):
        return []

    flags: list[DivergenceFlag] = []
    for p in picks:
        ticker = (getattr(p, "primary_ticker", None) or "").strip().upper()
        if not ticker:
            continue
        sent = score_sentiment(
            getattr(p, "title", ""), getattr(p, "market_impact_summary", "")
        )
        if sent.label == "neutral":
            continue

        price_pct = _recent_change(
            ticker, api_key_id, api_secret_key, feed, lookback, today, min_call_interval
        )
        if price_pct is None:
            continue

        bullish_but_falling = sent.label == "bullish" and price_pct <= -threshold_pct
        bearish_but_rising = sent.label == "bearish" and price_pct >= threshold_pct
        if not (bullish_but_falling or bearish_but_rising):
            continue

        if bullish_but_falling:
            note = (
                f"Bullish coverage, but {ticker} is down {abs(price_pct):.1f}% "
                f"over the last {lookback} sessions — the tape isn't buying it."
            )
        else:
            note = (
                f"Bearish coverage, but {ticker} is up {price_pct:.1f}% over the "
                f"last {lookback} sessions — the tape is shrugging it off."
            )
        flags.append(
            DivergenceFlag(
                ticker=ticker, sentiment=sent.label, price_pct=price_pct,
                lookback=lookback, headline=getattr(p, "title", ""), note=note,
            )
        )

    logger.info("Divergence: %d flag(s) detected", len(flags))
    return flags
