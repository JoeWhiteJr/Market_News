"""Weekly style-mimicry rotation.

On a configurable weekday (default Wednesday), the briefing's persona is
overridden with a rotating famous-commentator voice. This is an explicit
"bit" — the email subject is suffixed with "— in the voice of {name}" so
the reader knows it's parody, not plagiarism.

Rotation: ``ISO week-number-of-year % len(voices)`` — deterministic, no
persistence needed.

Cycle 3 — Joe's "make it funny on Wednesdays" pass.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TypedDict

from .voices import VoiceSpec

logger = logging.getLogger("market_mover.mimicry")


class MimicryVoice(TypedDict):
    """A single mimicry persona configuration."""

    name: str               # public display name (e.g., "Matt Levine")
    system_prompt_suffix: str
    signoff: str


# Shared guardrail prepended to every mimicry suffix. The LLM must understand
# this is parody for a private audience of two, not impersonation.
_PARODY_FRAME = (
    "This briefing is parody for a private audience of two friends. "
    "Do NOT claim to be the actual person. "
    "Do NOT attribute real trades, positions, or recommendations to them. "
    "Capture only their PROSE STYLE. "
    "Never recommend trades. Family-friendly — no profanity. "
    "The structural JSON contract (top_3 with ranked stories) is non-negotiable; "
    "the style only applies to the prose inside market_impact_summary. "
)


_MIMICRY_VOICES: list[MimicryVoice] = [
    {
        "name": "Jim Cramer",
        "system_prompt_suffix": (
            _PARODY_FRAME
            + "Style: high-energy, exclamatory, sound-effect adjectives. "
            "Use 'Boo-yah' SPARINGLY (at most once total). "
            "Short punchy sentences. Capitalize for emphasis occasionally. "
            "Treat each story like it's the most exciting thing of the week."
        ),
        "signoff": "— in the voice of Jim Cramer",
    },
    {
        "name": "Warren Buffett (shareholder letter)",
        "system_prompt_suffix": (
            _PARODY_FRAME
            + "Style: folksy Omaha shareholder-letter voice. Long-form sentences. "
            "Baseball and farming metaphors. Self-deprecating asides. "
            "Patient, avuncular, slightly amused at Wall Street's hurry."
        ),
        "signoff": "— in the voice of Warren Buffett",
    },
    {
        "name": "Matt Levine",
        "system_prompt_suffix": (
            _PARODY_FRAME
            + "Style: long parenthetical asides (often nested), structurally amused. "
            "The vibe is 'isn't this kind of funny if you think about it for a second.' "
            "Numbered conditional structure where appropriate "
            "('one, this happened; two, somehow this also happened…')."
        ),
        "signoff": "— in the voice of Matt Levine",
    },
    {
        "name": "Zerohedge",
        "system_prompt_suffix": (
            _PARODY_FRAME
            + "Style: grim, contrarian, conspiratorial-edge. "
            "ALL CAPS for emphasis on a few key words per story (not whole sentences). "
            "Treat consensus narratives with suspicion. "
            "Stay tasteful — no slurs, no real defamation, no political potshots."
        ),
        "signoff": "— in the voice of Zerohedge",
    },
    {
        "name": "FT leader",
        "system_prompt_suffix": (
            _PARODY_FRAME
            + "Style: dry, transatlantic Financial Times leader column. "
            "Use phrases like 'this newspaper has long argued…' once if it fits. "
            "Measured, faintly disapproving, institutional voice."
        ),
        "signoff": "— in the voice of the FT leader column",
    },
]


def mimicry_voice_for(today: date, weekday: int) -> MimicryVoice | None:
    """Return the mimicry voice for ``today`` if it matches ``weekday``, else None.

    Args:
        today: the date to check (typically ``date.today()``).
        weekday: 0=Mon … 6=Sun. Anything outside 0..6 disables mimicry.

    Returns:
        A ``MimicryVoice`` dict on a match, or ``None`` to indicate "use the
        regular persona today."
    """
    if not (0 <= weekday <= 6):
        return None
    if today.weekday() != weekday:
        return None

    # ISO week number — stable across years, rotates 5 distinct voices per cycle.
    week_number = today.isocalendar()[1]
    idx = week_number % len(_MIMICRY_VOICES)
    voice = _MIMICRY_VOICES[idx]
    logger.info(
        "Mimicry day: rotating to %r (ISO week %d, idx %d)",
        voice["name"],
        week_number,
        idx,
    )
    return voice


def mimicry_voice_to_voice_spec(mim: MimicryVoice) -> VoiceSpec:
    """Adapt a ``MimicryVoice`` into the ``VoiceSpec`` shape used by ``llm_client``.

    Lets the LLM client treat mimicry and personas through the same interface.
    """
    return {
        "name": mim["name"],
        "system_prompt_suffix": mim["system_prompt_suffix"],
        "signoff": mim["signoff"],
    }


def all_mimicry_voices() -> list[MimicryVoice]:
    """Return the rotation list (for tests / debugging)."""
    return list(_MIMICRY_VOICES)
