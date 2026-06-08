"""Overhype Detector — a deterministic "sober second read" of each story.

Creative pick #5. Before the briefing ships, every ranked story gets a
**hype-language score** (0–10) computed from a curated lexicon of breathless
market clichés ("skyrockets", "explodes", "game-changer", "to the moon"). The
score is rendered as an *advisory badge* next to the impact score — it never
rewrites or suppresses a story (per the brainstorm: advisory, not editorial).

Why deterministic (not an LLM pass): it's fully unit-testable, costs nothing,
adds no latency, and is transparent — the badge can name the exact words that
tripped it. It measures hype *language density*, which is precisely what the
badge claims to measure (not a subjective judgment of whether a story is
"really" a big deal).
"""

import re
from dataclasses import dataclass, field

# Curated hype lexicon. Grouped only for readability; all terms are treated
# equally. Multi-word phrases are matched with flexible whitespace/hyphenation
# (see ``_normalize``). Keep entries lowercase — matching is case-insensitive.
HYPE_TERMS: frozenset[str] = frozenset(
    {
        # explosive up-moves
        "skyrockets", "skyrocket", "soars", "soar", "soaring", "explodes",
        "explode", "exploding", "rockets", "rocket", "surges", "surge",
        "surging", "moonshot", "to the moon", "parabolic", "blasts off",
        "goes vertical", "rips", "ripping", "melt-up", "melt up",
        # explosive down-moves
        "plunges", "plunge", "plummets", "plummet", "craters", "cratering",
        "collapses", "collapse", "tanks", "tanking", "nosedive", "freefall",
        "bloodbath", "carnage", "meltdown",
        # superlatives / clickbait
        "game-changer", "game changer", "gamechanger", "game-changing",
        "unprecedented", "historic", "stunning", "shocking", "jaw-dropping",
        "mind-blowing", "epic", "massive", "monster", "blowout", "blockbuster",
        "you won't believe", "you wont believe", "the next big thing",
        "once in a lifetime", "once-in-a-lifetime", "must-see", "insane",
        "crazy", "wild", "frenzy", "mania", "euphoria", "panic", "fomo",
        "supercharge", "supercharged", "red-hot", "red hot", "on fire",
        "smashes", "smash", "obliterates", "crushing it", "crushes",
        # certainty / urgency clickbait
        "guaranteed", "can't-miss", "cant miss", "no-brainer", "slam dunk",
        "this changes everything", "act now", "before it's too late",
    }
)

# Title hype is louder than body hype — headlines are where overhype lives.
_TITLE_WEIGHT = 3.0
_SUMMARY_WEIGHT = 1.5
_MAX_SCORE = 10

# Score bands for the advisory badge label/color.
_LOW_MAX = 3   # 0–3  : measured
_MED_MAX = 6   # 4–6  : punchy


@dataclass(frozen=True)
class HypeScore:
    """Result of scoring one story's hype-language density."""

    score: int                                  # 0–10, higher = more hype
    matched_terms: list[str] = field(default_factory=list)
    band: str = "low"                           # "low" | "medium" | "high"

    @property
    def label(self) -> str:
        """Short badge text, e.g. ``"Hype 6/10"``."""
        return f"Hype {self.score}/10"


def _normalize(text: str) -> str:
    """Lowercase and collapse hyphens/whitespace so ``game-changer``,
    ``game changer`` and ``game  changer`` all match one lexicon entry."""
    lowered = (text or "").lower()
    # Treat hyphens as spaces, then collapse runs of whitespace.
    lowered = lowered.replace("-", " ")
    return re.sub(r"\s+", " ", lowered).strip()


def _count_hits(text: str) -> list[str]:
    """Return the list of distinct hype terms found in ``text`` (whole-word /
    whole-phrase matches only, so ``surge`` doesn't fire inside ``insurgent``)."""
    norm = _normalize(text)
    if not norm:
        return []
    hits: list[str] = []
    for term in HYPE_TERMS:
        norm_term = _normalize(term)
        # \b around a normalized (space-joined) phrase gives whole-word match.
        if re.search(rf"\b{re.escape(norm_term)}\b", norm):
            hits.append(term)
    return hits


def _band_for(score: int) -> str:
    if score <= _LOW_MAX:
        return "low"
    if score <= _MED_MAX:
        return "medium"
    return "high"


def score_hype(title: str, summary: str = "") -> HypeScore:
    """Score a story's hype-language density on a 0–10 scale.

    Title matches are weighted more heavily than summary matches. A term that
    appears in both the title and the summary counts once per field. The score
    is capped at 10. ``matched_terms`` is the de-duplicated union of terms found
    across both fields (handy for the badge tooltip).

    Args:
        title: The story headline.
        summary: The market-impact summary (optional).

    Returns:
        A :class:`HypeScore`.
    """
    title_hits = _count_hits(title)
    summary_hits = _count_hits(summary)

    raw = len(title_hits) * _TITLE_WEIGHT + len(summary_hits) * _SUMMARY_WEIGHT
    score = min(_MAX_SCORE, int(round(raw)))

    matched = list(dict.fromkeys(title_hits + summary_hits))  # ordered de-dupe
    return HypeScore(score=score, matched_terms=matched, band=_band_for(score))
