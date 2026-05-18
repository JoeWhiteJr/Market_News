"""Yesterday-Index persistence + scorecard rendering.

Cycle 4 Phase A — the "Memory & Accountability" foundation. We persist each
day's briefing as one JSON line in ``data/briefings.jsonl`` (schema locked in
``docs/adrs/0001-yesterday-index-rubric.md``) and render a placeholder
scorecard section above the Top 3 that shows yesterday's picks with a
``TBD — judging launches in Phase B`` verdict next to each one.

Phase B will replace the placeholder with a real LLM judge verdict and price-
data lookup. Phase C will surface running stats (Claude-vs-Gemini hit rates).
The schema is frozen at v1 so historical comparisons survive future iterations.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date
from html import escape as html_escape
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .models import ContrarianCoda, RankedArticle

logger = logging.getLogger("market_mover.scorecard")

SCHEMA_VERSION: int = 1

Verdict = Literal["HIT", "PARTIAL", "MISS", "TOO_EARLY", "NOT_APPLICABLE"]
Category = Literal[
    "macro", "single_name", "commodity", "crypto", "geopolitical", "other"
]
Voice = Literal["vinny", "neutral", "terminal", "villain"]
MimicryVoiceName = Literal[
    "cramer", "buffett", "matt_levine", "zerohedge", "ft_leader"
]


# ---------------------------------------------------------------------------
# Pydantic models — these MUST match the ADR's frozen v1 schema verbatim.
# ---------------------------------------------------------------------------


class ScorecardPick(BaseModel):
    """One of the 3 ranked picks, persisted alongside its briefing."""

    rank: int
    title: str
    summary: str
    impact_score: float
    primary_ticker: str | None = None
    category: Category = "other"
    source_url: str
    source_name: str


class ScorecardContrarian(BaseModel):
    """Optional bear-case coda — null when no coda was produced that day."""

    headline: str
    argument: str
    source_url: str
    source_name: str


class PriceData(BaseModel):
    """24h close-to-close price snapshot used by the Phase B judge.

    Phase A never writes this — it's defined here so Phase B can drop in.
    """

    primary_ticker: str | None = None
    primary_pct_change_24h: float | None = None
    spy_pct: float
    vix_close: float
    vix_pct_change: float
    sector_etf: str | None = None
    sector_pct: float | None = None


class Judgment(BaseModel):
    """Phase B verdict for a single pick. Phase A always persists ``null``."""

    rank: int
    verdict: Verdict
    justification: str
    price_data: PriceData


class BriefingRecord(BaseModel):
    """One day's briefing record — the append-only unit in briefings.jsonl.

    Fields below ``picks`` / ``contrarian`` are Phase-B-populated. In Phase A
    they are written as ``null`` and filled in by tomorrow's run grading
    today's record.
    """

    date: date
    schema_version: int = SCHEMA_VERSION
    model_used: Literal["claude", "gemini"]
    voice: Voice
    mimicry_voice: MimicryVoiceName | None = None
    picks: list[ScorecardPick] = Field(min_length=1, max_length=3)
    contrarian: ScorecardContrarian | None = None
    # Phase B fills these. Stay nullable forever so the schema doesn't bump.
    graded_at: str | None = None
    judge_model: str | None = None
    judge_prompt_version: int | None = None
    judgments: list[Judgment] | None = None


class RunningStats(BaseModel):
    """Aggregate hit-rate stats over a rolling window. Phase C consumer.

    Defined now so the signature is stable across cycles; Phase A always
    returns ``None`` from :func:`compute_running_stats`.
    """

    window_days: int
    total_records: int
    by_model: dict[str, dict[str, int]]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def append_record(record: BriefingRecord, path: Path) -> None:
    """Append one record as a single JSON line to ``path``.

    Creates the parent directory and file if they don't exist. The append is
    atomic-ish: we read the existing file, append the new line, and rename a
    temp file over the original. That way a crashed run mid-write can't leave
    the JSONL truncated.

    Args:
        record: The day's briefing record.
        path: Destination file (typically ``data/briefings.jsonl``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize once. ``mode="json"`` makes ``date`` -> ``"YYYY-MM-DD"``.
    line = record.model_dump_json(exclude_none=False)

    # Atomic-ish append: write existing bytes + new line to a temp file in the
    # same directory, then rename it onto the target. ``os.replace`` is atomic
    # on the same filesystem (POSIX + Windows).
    existing = path.read_bytes() if path.exists() else b""
    if existing and not existing.endswith(b"\n"):
        # Defensive: if a previous run died mid-write without a newline,
        # add one before appending so we don't produce a fused-line entry.
        existing = existing + b"\n"

    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(existing)
        tmp.write(line.encode("utf-8"))
        tmp.write(b"\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name

    os.replace(tmp_name, str(path))


def load_yesterday(path: Path, today: date) -> BriefingRecord | None:
    """Return the last record in ``path`` if it pre-dates ``today``, else None.

    Returns ``None`` for:
    - Missing file (the "first run ever" case — no scorecard to render).
    - Last line's ``date == today`` (pipeline already ran today; don't show
      today's row as if it were yesterday's).
    - Last line's ``date > today`` (clock skew; refuse to render rather than
      show a future-dated scorecard).
    - Malformed last line (truncated JSON, missing fields). Logs a warning
      and returns ``None`` rather than raising — the send must not fail.

    Args:
        path: Source JSONL.
        today: The date of the current run (typically ``date.today()``).
    """
    if not path.exists():
        return None

    last_line: str | None = None
    try:
        # Walk the file from the bottom — the last non-empty line is the most
        # recent record. The file is small (one row per weekday for a single
        # MCP server) so a simple read-all-and-tail is fine.
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                stripped = raw.strip()
                if stripped:
                    last_line = stripped
    except OSError as exc:
        logger.warning("Could not read briefings.jsonl at %s: %s", path, exc)
        return None

    if last_line is None:
        return None

    try:
        record = BriefingRecord.model_validate_json(last_line)
    except Exception as exc:
        # Truncated JSON, missing fields, schema mismatch — all bucket here.
        logger.warning(
            "Last line of %s did not parse as BriefingRecord: %s",
            path,
            exc,
        )
        return None

    if record.date >= today:
        # Either we already ran today (don't grade ourselves) or the system
        # clock disagrees with the persisted data (don't render the future).
        logger.info(
            "Yesterday-Index: last record date %s is not before today %s; "
            "skipping scorecard render",
            record.date,
            today,
        )
        return None

    return record


def compute_running_stats(
    path: Path, window_days: int = 30
) -> RunningStats | None:
    """Aggregate hit-rate stats over the last ``window_days``.

    Phase A: returns ``None`` always. The signature is defined now so Phase C
    can fill it in without churning callers.

    Args:
        path: Source JSONL.
        window_days: Rolling-window size in days.
    """
    _ = path, window_days  # Phase C will use them.
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Color palette mirrors the contrarian block's amber accent so the scorecard
# reads as its own beat — distinct from the rank colors (#c0392b / #a05a00 /
# #2470a8) and the sparkline strip. WCAG-AA verified against #fafafa
# (light card body) and #232734 (dark mode override).
_SCORECARD_ACCENT = "#3a3f4d"
_SCORECARD_PLACEHOLDER = "TBD — judging launches in Phase B"


def render_scorecard_html(
    yesterday: BriefingRecord | None, today: date
) -> str:
    """Render the scorecard section as an HTML table row.

    Returns ``""`` when ``yesterday is None`` — the email template treats that
    as "skip the slot entirely" (first run after merge ships without it).
    Otherwise renders each of yesterday's 3 picks with the placeholder
    verdict ``"TBD — judging launches in Phase B"`` next to it.

    The block is wrapped in ``<section data-block="scorecard">`` so the email
    layout slots it cleanly between the sparkline and the Top 3.

    Args:
        yesterday: Yesterday's persisted record, or ``None`` for first-run.
        today: Today's date (used only to label the section header).
    """
    if yesterday is None:
        return ""

    yesterday_label = html_escape(yesterday.date.strftime("%B %d"))
    today_label = html_escape(today.strftime("%B %d"))
    model_label = html_escape(yesterday.model_used.title())

    pick_rows = "\n".join(
        _render_scorecard_pick_html(pick) for pick in yesterday.picks
    )

    return f"""
<!-- Yesterday-Index scorecard — Cycle 4 Phase A -->
<tr>
<td class="mm-scorecard-wrap" style="padding:24px 32px 0;background-color:#ffffff;">
  <section data-block="scorecard" aria-label="Yesterday's picks scorecard">
  <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px;">
  <tr>
  <td class="mm-scorecard-card" style="padding:16px;border-left:4px solid {_SCORECARD_ACCENT};background-color:#f3f4f7;border-radius:0 6px 6px 0;">
    <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td>
        <span class="mm-scorecard-eyebrow" style="display:inline-block;color:{_SCORECARD_ACCENT};font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px;">
          Yesterday&apos;s Scorecard &mdash; {yesterday_label}
        </span>
      </td>
    </tr>
    <tr>
      <td style="padding-bottom:8px;">
        <span class="mm-scorecard-sub" style="color:#666;font-size:12px;">
          Grading {model_label}&rsquo;s picks against {today_label}&rsquo;s tape.
        </span>
      </td>
    </tr>
    <tr>
      <td>
        <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
{pick_rows}
        </table>
      </td>
    </tr>
    </table>
  </td>
  </tr>
  </table>
  </section>
</td>
</tr>"""


def _render_scorecard_pick_html(pick: ScorecardPick) -> str:
    """Render a single yesterday pick as a scorecard row."""
    title = html_escape(pick.title)
    score = html_escape(f"{pick.impact_score:.1f}")
    rank = pick.rank
    placeholder = html_escape(_SCORECARD_PLACEHOLDER)
    ticker = html_escape(pick.primary_ticker) if pick.primary_ticker else "&mdash;"

    return f"""        <tr>
          <td class="mm-scorecard-row" style="padding:6px 0;border-bottom:1px solid #e6e7ec;">
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
            <tr>
              <td valign="top" style="font-size:12px;color:#333;font-weight:600;width:32px;">
                #{rank}
              </td>
              <td valign="top" style="font-size:13px;color:#1a1a2e;line-height:1.35;">
                <span class="mm-scorecard-title" style="font-weight:600;">{title}</span>
                <br>
                <span class="mm-scorecard-meta" style="color:#666;font-size:11px;">
                  Impact {score}/10 &middot; Ticker: {ticker}
                </span>
              </td>
              <td valign="top" align="right" style="font-size:11px;color:#666;white-space:nowrap;padding-left:8px;">
                <span class="mm-scorecard-verdict" style="display:inline-block;background-color:#e6e7ec;color:#3a3f4d;font-weight:600;padding:2px 8px;border-radius:10px;">
                  {placeholder}
                </span>
              </td>
            </tr>
            </table>
          </td>
        </tr>"""


def render_scorecard_plain_text(
    yesterday: BriefingRecord | None, today: date
) -> str:
    """Plain-text fallback for the scorecard. Returns ``""`` on first-run.

    Matches the HTML shape so a reader who only sees text gets the same
    information (picks + impact scores + placeholder verdict).
    """
    if yesterday is None:
        return ""

    yesterday_label = yesterday.date.strftime("%B %d")
    today_label = today.strftime("%B %d")
    lines = [
        f"YESTERDAY'S SCORECARD — {yesterday_label}",
        "-" * 60,
        f"Grading {yesterday.model_used.title()}'s picks against {today_label}'s tape.",
        "",
    ]
    for pick in yesterday.picks:
        ticker = pick.primary_ticker or "—"
        lines.append(
            f"  #{pick.rank} [{pick.impact_score:.1f}/10] {pick.title}"
        )
        lines.append(f"      Ticker: {ticker}")
        lines.append(f"      Verdict: {_SCORECARD_PLACEHOLDER}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Conveniences for the CLI
# ---------------------------------------------------------------------------


def build_record_from_pipeline(
    today: date,
    ranked: list[RankedArticle],
    coda: ContrarianCoda | None,
    model_used: Literal["claude", "gemini"],
    voice: str,
    mimicry_voice: str | None,
) -> BriefingRecord:
    """Compose a :class:`BriefingRecord` from the pipeline's outputs.

    Mapping rules:
    - ``model_used``: the ranking model that produced today's picks.
    - ``voice``: the persona key (``vinny``/``neutral``/``terminal``/``villain``)
      that actually shipped (post-guardrail).
    - ``mimicry_voice``: ``None`` on a non-mimicry day; otherwise one of
      ``cramer``/``buffett``/``matt_levine``/``zerohedge``/``ft_leader``.

    Args:
        today: The date the briefing was sent.
        ranked: Top 3 ranked articles after LLM analysis.
        coda: Optional contrarian coda (may be ``None``).
        model_used: Which LLM produced ``ranked`` — mapped to ``"claude"``/``"gemini"``.
        voice: The shipping persona key.
        mimicry_voice: Mimicry voice key, or ``None``.
    """
    picks = [
        ScorecardPick(
            rank=a.rank,
            title=a.title,
            summary=a.market_impact_summary,
            impact_score=a.impact_score,
            primary_ticker=a.primary_ticker,
            category=a.category,
            source_url=a.url,
            source_name=a.source_name,
        )
        for a in ranked[:3]
    ]
    contrarian = (
        ScorecardContrarian(
            headline=coda.headline,
            argument=coda.argument,
            source_url=coda.source_url,
            source_name=coda.source_name,
        )
        if coda is not None
        else None
    )
    return BriefingRecord(
        date=today,
        schema_version=SCHEMA_VERSION,
        model_used=model_used,
        voice=voice,  # type: ignore[arg-type]  # validator enforces the literal
        mimicry_voice=mimicry_voice,  # type: ignore[arg-type]
        picks=picks,
        contrarian=contrarian,
        graded_at=None,
        judge_model=None,
        judge_prompt_version=None,
        judgments=None,
    )
