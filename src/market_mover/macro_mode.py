"""Geographic / Macro Mode (creative #18).

On macro-event days (FOMC, ECB, a hot CPI print, a China data shock) Joe's focus
shifts from single-name corporate news to the big-picture drivers — so the
briefing should follow. Rather than a manual toggle (which gets forgotten), we
**auto-detect** a macro-heavy day from the candidate headlines and, when it
trips, bias the ranking toward macro/international stories.

Detection is deterministic and conservative: it takes a clear cluster of macro
headlines to flip the mode, so a single Fed mention on a normal day won't.
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("market_mover.macro_mode")

# Macro / central-bank / international-driver vocabulary.
MACRO_TERMS: frozenset[str] = frozenset({
    "fed", "fomc", "federal reserve", "powell", "rate cut", "rate hike",
    "interest rate", "interest rates", "rate decision", "ecb", "lagarde",
    "bank of japan", "boj", "bank of england", "central bank", "central banks",
    "inflation", "cpi", "ppi", "pce", "deflation", "stagflation",
    "jobs report", "nonfarm", "payrolls", "unemployment", "jobless claims",
    "gdp", "recession", "soft landing", "yield curve", "treasury yields",
    "10-year yield", "bond yields", "tariff", "tariffs", "trade war",
    "china pmi", "china gdp", "pboc", "yuan", "eurozone", "opec",
    "debt ceiling", "fiscal", "quantitative", "dollar index", "dxy",
})

_DEFAULT_MIN_COUNT = 3       # at least this many macro headlines
_DEFAULT_MIN_FRACTION = 0.30  # and at least this share of the pool


@dataclass(frozen=True)
class MacroSignal:
    """Result of macro-day detection."""

    active: bool
    matched_count: int
    total: int
    themes: list[str]        # distinct macro terms seen (for transparency)


def _normalize(text: str) -> str:
    lowered = (text or "").lower().replace("-", " ")
    return re.sub(r"\s+", " ", lowered).strip()


def _macro_terms_in(text: str) -> set[str]:
    norm = _normalize(text)
    if not norm:
        return set()
    found = set()
    for term in MACRO_TERMS:
        if re.search(rf"\b{re.escape(_normalize(term))}\b", norm):
            found.add(term)
    return found


def detect_macro_mode(
    articles: list,
    min_count: int = _DEFAULT_MIN_COUNT,
    min_fraction: float = _DEFAULT_MIN_FRACTION,
) -> MacroSignal:
    """Decide whether today is a macro-heavy day from the candidate articles.

    An article "counts" if its title or summary mentions any macro term. Mode is
    active when the count clears ``min_count`` AND the share of the pool clears
    ``min_fraction`` — both, so neither a couple of stray mentions nor a tiny
    article pool flips it.

    ``articles`` items expose ``title`` and (optionally) ``summary``.
    """
    total = len(articles)
    if total == 0:
        return MacroSignal(active=False, matched_count=0, total=0, themes=[])

    matched = 0
    themes: set[str] = set()
    for a in articles:
        text = f"{getattr(a, 'title', '')} {getattr(a, 'summary', '')}"
        hits = _macro_terms_in(text)
        if hits:
            matched += 1
            themes.update(hits)

    fraction = matched / total
    active = matched >= min_count and fraction >= min_fraction
    if active:
        logger.info(
            "Macro Mode ON — %d/%d macro headlines (%.0f%%); themes: %s",
            matched, total, fraction * 100, ", ".join(sorted(themes)[:6]),
        )
    return MacroSignal(
        active=active, matched_count=matched, total=total,
        themes=sorted(themes),
    )


# Instruction appended to the ranking system prompt when macro mode is active.
MACRO_BIAS_INSTRUCTION = (
    "MACRO MODE (today is a macro-event day): bias the ranking toward "
    "macro-economic and international stories — central-bank decisions, "
    "inflation/jobs/GDP data, rates, geopolitics, trade — over single-name "
    "corporate news. A market-moving macro story should outrank a routine "
    "single-company story today. The JSON schema and story count are unchanged."
)
