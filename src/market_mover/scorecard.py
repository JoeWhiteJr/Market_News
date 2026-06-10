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


def commit_daily_record(
    today_record: BriefingRecord,
    yesterday_judgments: list[Judgment] | None,
    path: Path,
    judge_model: str | None = None,
    judge_prompt_version: int | None = None,
    graded_at: str | None = None,
) -> None:
    """Atomically patch yesterday's row + append today's row in one rename.

    Phase B replaces the simple Phase A append with a single atomic write
    that does TWO things at once:

    1. If ``yesterday_judgments`` is provided AND a row exists in ``path``
       with ``date == today_record.date - 1 day`` (the previous record
       returned by :func:`load_yesterday`), patch its ``judgments``,
       ``graded_at``, ``judge_model`` and ``judge_prompt_version`` fields
       in-place.
    2. Append ``today_record`` as a fresh JSON line.

    Both changes ship in a single tempfile→fsync→rename. This is critical:
    if the daily run dies between updating yesterday and writing today, the
    NEXT day's "yesterday" lookup would return today's actual yesterday
    (now graded) instead of today's missing row, leading to a stale
    scorecard. The atomic single-rename prevents that split state.

    A re-judge is intentionally NOT performed: if the previous row already
    has ``judgments != None`` we skip the patch step (the LLM call was
    already made; re-running would be wasted spend). ``yesterday_judgments``
    coming in as ``None`` (judge failed today) is equivalent to "no patch".

    Args:
        today_record: Today's :class:`BriefingRecord` to append.
        yesterday_judgments: List of :class:`Judgment` from grading the
            previous row, or ``None`` if the judge didn't run / failed.
        path: Destination JSONL (typically ``data/briefings.jsonl``).
        judge_model: Anthropic model identifier used (e.g.
            ``"claude-sonnet-4-20250514"``). Stamped onto the patched row.
        judge_prompt_version: Locked integer per the ADR. Stamped onto the
            patched row alongside ``judge_model``.
        graded_at: ISO-8601 timestamp of when the judging completed.
            Defaults to the current UTC time when ``yesterday_judgments``
            is provided.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Read all existing rows (the file is small — one row per weekday).
    existing_lines: list[str] = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    stripped = raw.rstrip("\n")
                    if stripped.strip():
                        existing_lines.append(stripped)
        except OSError as e:
            logger.warning(
                "commit_daily_record: could not read %s (%s) — starting fresh",
                path,
                e,
            )
            existing_lines = []

    # Patch the most recent prior row if it matches and we have judgments.
    if yesterday_judgments and existing_lines:
        last_idx = len(existing_lines) - 1
        last_line = existing_lines[last_idx]
        try:
            prior = BriefingRecord.model_validate_json(last_line)
        except Exception as e:
            logger.warning(
                "commit_daily_record: last row malformed (%s) — leaving "
                "untouched, will still append today",
                e,
            )
            prior = None

        if prior is not None and prior.judgments is None:
            # Only patch if the row is actually older than today AND not
            # already graded. Same-day rows / future rows / already-graded
            # rows are left alone.
            if prior.date < today_record.date:
                graded_at_ts = graded_at or _graded_at_now()
                patched = prior.model_copy(
                    update={
                        "judgments": list(yesterday_judgments),
                        "graded_at": graded_at_ts,
                        "judge_model": judge_model,
                        "judge_prompt_version": judge_prompt_version,
                    }
                )
                existing_lines[last_idx] = patched.model_dump_json(exclude_none=False)

    today_line = today_record.model_dump_json(exclude_none=False)

    # Single atomic rename onto the target.
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        for line in existing_lines:
            tmp.write(line.encode("utf-8"))
            tmp.write(b"\n")
        tmp.write(today_line.encode("utf-8"))
        tmp.write(b"\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name

    os.replace(tmp_name, str(path))


def _graded_at_now() -> str:
    """Return the current UTC time as an ISO-8601 string (factored for tests)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


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

# Per-verdict visual styling for the scorecard badge. The light-mode colors
# pass WCAG AA (4.5:1) against the light card body (#f3f4f7); the dark-mode
# overrides ship via CSS classes (``mm-scorecard-verdict-{verdict}``) and the
# dark-mode @media block in ``email_template.py``.
#
# Reuses Cycle 2's contrast-corrected tokens where possible:
#   - HIT green derived from sparkline up_light (#0a6f38, ~6.1:1 on white)
#   - MISS red is the same #c0392b as rank #1 (~4.66:1 on white)
#   - PARTIAL amber is RANK_COLORS[2] (#a05a00, ~5.4:1 on white)
#   - TOO_EARLY / NOT_APPLICABLE use a neutral slate (#5a6068, ~5.6:1 on white)
_VERDICT_STYLES: dict[str, dict[str, str]] = {
    "HIT":            {"icon": "✓",  "label": "HIT",       "color": "#0a6f38", "bg": "#dff3e6"},
    "PARTIAL":        {"icon": "◐",  "label": "PARTIAL",   "color": "#a05a00", "bg": "#fbf0db"},
    "MISS":           {"icon": "✗",  "label": "MISS",      "color": "#c0392b", "bg": "#fbe0dc"},
    "TOO_EARLY":      {"icon": "⏱", "label": "TOO EARLY", "color": "#5a6068", "bg": "#e6e7ec"},
    "NOT_APPLICABLE": {"icon": "—",  "label": "N/A",       "color": "#5a6068", "bg": "#e6e7ec"},
}

# Plain-text verdict markers (rendered inside ``[ ]`` brackets).
_VERDICT_PLAIN_LABELS: dict[str, str] = {
    "HIT":            "HIT",
    "PARTIAL":        "PARTIAL",
    "MISS":           "MISS",
    "TOO_EARLY":      "TOO EARLY",
    "NOT_APPLICABLE": "N/A",
}


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

    # Build a per-rank lookup so each pick gets its matching judgment (if any).
    judgments_by_rank: dict[int, Judgment] = {}
    if yesterday.judgments is not None:
        for j in yesterday.judgments:
            judgments_by_rank[j.rank] = j

    pick_rows = "\n".join(
        _render_scorecard_pick_html(pick, judgments_by_rank.get(pick.rank))
        for pick in yesterday.picks
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


def _render_scorecard_pick_html(
    pick: ScorecardPick, judgment: Judgment | None
) -> str:
    """Render a single yesterday pick as a scorecard row.

    When ``judgment`` is ``None`` (Phase A row that hasn't been graded yet,
    or the Phase B judge failed for this pick today) we keep the Phase A
    "TBD" placeholder so the email still ships gracefully.
    """
    title = html_escape(pick.title)
    score = html_escape(f"{pick.impact_score:.1f}")
    rank = pick.rank
    ticker = html_escape(pick.primary_ticker) if pick.primary_ticker else "&mdash;"

    verdict_block, justification_block, snapshot_block = _render_verdict_blocks(
        judgment
    )

    return f"""        <tr>
          <td class="mm-scorecard-row" style="padding:8px 0;border-bottom:1px solid #e6e7ec;">
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
                </span>{justification_block}{snapshot_block}
              </td>
              <td valign="top" align="right" style="font-size:11px;color:#666;white-space:nowrap;padding-left:8px;">
                {verdict_block}
              </td>
            </tr>
            </table>
          </td>
        </tr>"""


def _render_verdict_blocks(
    judgment: Judgment | None,
) -> tuple[str, str, str]:
    """Return ``(verdict_badge_html, justification_html, snapshot_html)``.

    For ``judgment is None`` we fall back to the Phase A TBD placeholder
    badge (no justification / snapshot rendered).
    """
    if judgment is None:
        placeholder = html_escape(_SCORECARD_PLACEHOLDER)
        badge = (
            f'<span class="mm-scorecard-verdict mm-scorecard-verdict-tbd" '
            f'style="display:inline-block;background-color:#e6e7ec;'
            f'color:#3a3f4d;font-weight:600;padding:2px 8px;border-radius:10px;">'
            f'{placeholder}</span>'
        )
        return badge, "", ""

    style = _VERDICT_STYLES.get(judgment.verdict, _VERDICT_STYLES["NOT_APPLICABLE"])
    verdict_key = judgment.verdict.lower().replace("_", "-")
    icon = html_escape(style["icon"])
    label = html_escape(style["label"])

    badge = (
        f'<span class="mm-scorecard-verdict mm-scorecard-verdict-{verdict_key}" '
        f'aria-label="Verdict: {label}" '
        f'style="display:inline-block;background-color:{style["bg"]};'
        f'color:{style["color"]};font-weight:700;padding:3px 9px;'
        f'border-radius:10px;font-size:11px;letter-spacing:0.02em;">'
        f'<span aria-hidden="true">{icon}</span> {label}</span>'
    )

    justification = html_escape(judgment.justification)
    justification_block = (
        f'<br><span class="mm-scorecard-justification" '
        f'style="color:#666;font-size:11px;font-style:italic;">'
        f'{justification}</span>'
    )

    snapshot_block = _render_price_snapshot(judgment)
    return badge, justification_block, snapshot_block


def _render_price_snapshot(judgment: Judgment) -> str:
    """Render the inline price snapshot under a graded pick.

    Format: ``"SPY -1.1% · VIX +12%"`` (sized 10px, muted gray). Falls
    back to ``""`` if the price data is missing entirely.
    """
    pd = judgment.price_data
    parts: list[str] = []
    if pd.primary_ticker and pd.primary_pct_change_24h is not None:
        sign = "+" if pd.primary_pct_change_24h >= 0 else ""
        parts.append(
            f"{html_escape(pd.primary_ticker)} {sign}{pd.primary_pct_change_24h:.1f}%"
        )
    # Don't double-list SPY if the primary already IS SPY.
    primary_is_spy = (pd.primary_ticker or "").upper() == "SPY"
    if not primary_is_spy:
        spy_sign = "+" if pd.spy_pct >= 0 else ""
        parts.append(f"SPY {spy_sign}{pd.spy_pct:.1f}%")
    vix_sign = "+" if pd.vix_pct_change >= 0 else ""
    parts.append(f"VIX {vix_sign}{pd.vix_pct_change:.1f}%")
    # Skip the sector ETF when it's the same ticker we already listed as primary
    # (e.g. a macro/TLT pick whose sector proxy is also TLT) — no double-listing.
    sector_is_primary = (pd.sector_etf or "").upper() == (pd.primary_ticker or "").upper()
    if pd.sector_etf and pd.sector_pct is not None and not sector_is_primary:
        sector_sign = "+" if pd.sector_pct >= 0 else ""
        parts.append(
            f"{html_escape(pd.sector_etf)} {sector_sign}{pd.sector_pct:.1f}%"
        )

    if not parts:
        return ""

    snapshot = " &middot; ".join(parts)
    return (
        f'<br><span class="mm-scorecard-snapshot" '
        f'style="color:#888;font-size:10px;letter-spacing:0.02em;">'
        f'{snapshot}</span>'
    )


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
    judgments_by_rank: dict[int, Judgment] = {}
    if yesterday.judgments is not None:
        for j in yesterday.judgments:
            judgments_by_rank[j.rank] = j

    for pick in yesterday.picks:
        ticker = pick.primary_ticker or "—"
        lines.append(
            f"  #{pick.rank} [{pick.impact_score:.1f}/10] {pick.title}"
        )
        lines.append(f"      Ticker: {ticker}")
        judgment = judgments_by_rank.get(pick.rank)
        if judgment is None:
            lines.append(f"      Verdict: {_SCORECARD_PLACEHOLDER}")
        else:
            label = _VERDICT_PLAIN_LABELS.get(judgment.verdict, judgment.verdict)
            lines.append(f"      Verdict: [{label}] — {judgment.justification}")
            snapshot = _plain_text_price_snapshot(judgment)
            if snapshot:
                lines.append(f"      {snapshot}")
        lines.append("")
    return "\n".join(lines)


def _plain_text_price_snapshot(judgment: Judgment) -> str:
    """Return ``"SPY -1.1%  VIX +12%"`` style snapshot, or ``""`` if empty."""
    pd = judgment.price_data
    parts: list[str] = []
    if pd.primary_ticker and pd.primary_pct_change_24h is not None:
        sign = "+" if pd.primary_pct_change_24h >= 0 else ""
        parts.append(f"{pd.primary_ticker} {sign}{pd.primary_pct_change_24h:.1f}%")
    primary_is_spy = (pd.primary_ticker or "").upper() == "SPY"
    if not primary_is_spy:
        sign = "+" if pd.spy_pct >= 0 else ""
        parts.append(f"SPY {sign}{pd.spy_pct:.1f}%")
    sign = "+" if pd.vix_pct_change >= 0 else ""
    parts.append(f"VIX {sign}{pd.vix_pct_change:.1f}%")
    if pd.sector_etf and pd.sector_pct is not None:
        sign = "+" if pd.sector_pct >= 0 else ""
        parts.append(f"{pd.sector_etf} {sign}{pd.sector_pct:.1f}%")
    return "  ".join(parts)


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
