"""Briefing voice personas.

Each persona supplies a system-prompt suffix appended to the existing
ranking prompt, and a short signoff line rendered in the email footer.

Voice is style, not structure: the JSON output contract enforced by
``llm_client.RANKING_SYSTEM_PROMPT`` is non-negotiable. Personas only
influence the prose inside ``market_impact_summary``.

Cycle 3 — Joe's "make it have personality" pass.
"""

from __future__ import annotations

import logging
import re
from typing import TypedDict

logger = logging.getLogger("market_mover.voices")


class VoiceSpec(TypedDict):
    """A single voice persona configuration."""

    name: str
    system_prompt_suffix: str
    signoff: str


# ---------------------------------------------------------------------------
# Persona definitions
# ---------------------------------------------------------------------------

_VINNY_SUFFIX = (
    "You are Vinny, a former NYSE floor trader with 30 years on the Street. "
    "You've seen it all. You write the morning briefing in a salty, world-weary voice. "
    "Use Wall-Street slang sparingly (e.g., 'tape', 'bid', 'the long bond'). "
    "Be honest about what matters and what doesn't. "
    "Never recommend trades. Never insult specific companies or people. "
    "Family-friendly — no profanity. "
    "Keep summaries punchy: 2-3 sentences, the way you'd explain it to a junior at the coffee cart."
)

_TERMINAL_SUFFIX = (
    "Write in a dry, Bloomberg-terminal voice. Clipped sentences. Numbers first. "
    "No flourish, no opinion, no slang. Family-friendly — no profanity. "
    "Never recommend trades."
)

_VILLAIN_SUFFIX = (
    "Write in the voice of a Bond-villain hedge fund manager: theatrical, amused, "
    "faintly menacing in a tongue-in-cheek way. You find the markets entertaining. "
    "Never recommend trades. Never insult specific companies or people. "
    "Family-friendly — no profanity. Keep the bit tasteful."
)

_VOICES: dict[str, VoiceSpec] = {
    "vinny": {
        "name": "Vinny from the Floor",
        "system_prompt_suffix": _VINNY_SUFFIX,
        "signoff": "— Vinny, from the floor",
    },
    "neutral": {
        "name": "Neutral",
        "system_prompt_suffix": "",
        "signoff": "",
    },
    "terminal": {
        "name": "Terminal",
        "system_prompt_suffix": _TERMINAL_SUFFIX,
        "signoff": "— end of file —",
    },
    "villain": {
        "name": "The Chairman",
        "system_prompt_suffix": _VILLAIN_SUFFIX,
        "signoff": "— Yours in amusement, The Chairman",
    },
}


DEFAULT_VOICE = "vinny"
NEUTRAL_VOICE = "neutral"


def get_voice(voice_key: str | None) -> VoiceSpec:
    """Return the voice spec for ``voice_key`` (case-insensitive).

    Unknown / empty keys fall back to the default voice (``vinny``).
    """
    if not voice_key:
        return _VOICES[DEFAULT_VOICE]
    key = voice_key.strip().lower()
    if key not in _VOICES:
        logger.warning(
            "Unknown BRIEFING_VOICE=%r; falling back to %r", voice_key, DEFAULT_VOICE
        )
        return _VOICES[DEFAULT_VOICE]
    return _VOICES[key]


def available_voices() -> list[str]:
    """Return the supported voice keys (sorted for stable test output)."""
    return sorted(_VOICES.keys())


# ---------------------------------------------------------------------------
# Profanity guardrail
# ---------------------------------------------------------------------------

# Conservative word-boundary list. We don't try to catch every variation —
# Joe + Jared just need the obvious cases scrubbed so a salty model doesn't
# slip something past the persona prompt.
_PROFANITY_WORDS = (
    r"f[\W_]*u[\W_]*c[\W_]*k",
    r"s[\W_]*h[\W_]*i[\W_]*t",
    r"a[\W_]*s[\W_]*s[\W_]*h[\W_]*o[\W_]*l[\W_]*e",
    r"b[\W_]*i[\W_]*t[\W_]*c[\W_]*h",
    r"b[\W_]*a[\W_]*s[\W_]*t[\W_]*a[\W_]*r[\W_]*d",
    r"d[\W_]*a[\W_]*m[\W_]*n",
    r"c[\W_]*r[\W_]*a[\W_]*p",
    r"d[\W_]*i[\W_]*c[\W_]*k",
    r"p[\W_]*i[\W_]*s[\W_]*s",
    r"c[\W_]*u[\W_]*n[\W_]*t",
)
_PROFANITY_RE = re.compile(
    r"\b(?:" + "|".join(_PROFANITY_WORDS) + r")\b",
    flags=re.IGNORECASE,
)


def contains_profanity(text: str) -> bool:
    """Return True if ``text`` matches the conservative profanity list."""
    if not text:
        return False
    return bool(_PROFANITY_RE.search(text))


def strip_profanity(text: str, replacement: str = "[redacted]") -> str:
    """Replace any matched profanity in ``text`` with ``replacement``.

    Word-boundary matching keeps innocent substrings (e.g., "Scunthorpe",
    "assess") intact. Idempotent.
    """
    if not text:
        return text
    return _PROFANITY_RE.sub(replacement, text)
