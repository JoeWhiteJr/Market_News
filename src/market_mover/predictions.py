"""The Call + Beat the Bot — a daily prediction game (MM-T007).

Each morning the bot makes ONE falsifiable 24-hour prediction ("MU closes green
today") with a confidence %. The next morning it's resolved against real
close-to-close price action (reusing the judge's Alpaca window) and a running
scoreboard renders — bot vs. the humans, who play by tapping a one-tap
``mailto:`` UP/DOWN button that replies to the shared group thread.

Human calls are honor-system for v1: recipients reply UP/DOWN and their votes
are recorded in ``data/predictions.jsonl`` (``human_calls``); the pipeline renders
whatever is in that file. The BOT's record is always fully automatic, so the
game has a live scoreboard even on days nobody taps.

Persistence unit (one JSON line per day):
    {"date","call":{...},"resolved":bool,"outcome":"HIT|MISS|PUSH"|null,
     "pct_change":float|null,"human_calls":{"Joe":"UP",...}}
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date
from html import escape as html_escape
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, Field

from .sources.quotes_source import fetch_24h_close_change

logger = logging.getLogger("market_mover.predictions")

# Direction moves smaller than this (in %) resolve as PUSH — neither side wins a
# coin-flip-flat day. Keeps "closes green" honest without punishing ~0.0% noise.
_PUSH_BAND_PCT = 0.05


class DailyCall(BaseModel):
    """The bot's single 24h prediction for today's session."""

    ticker: str
    direction: str  # "UP" or "DOWN"
    confidence: int = Field(ge=0, le=100)
    statement: str


class PredictionRecord(BaseModel):
    """One day's Call plus its (later-filled) resolution and human votes."""

    date: date
    call: DailyCall
    resolved: bool = False
    outcome: str | None = None          # HIT | MISS | PUSH
    pct_change: float | None = None
    human_calls: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Persistence (append-only JSONL, mirrors scorecard.append_record)
# ---------------------------------------------------------------------------

def load_predictions(path: Path) -> list[PredictionRecord]:
    """Load all prediction records; skip malformed lines. Missing file → []."""
    if not path.exists():
        return []
    out: list[PredictionRecord] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(PredictionRecord.model_validate_json(line))
            except Exception:
                logger.warning("Skipping malformed predictions row")
    except OSError as e:
        logger.warning("Could not read predictions ledger: %s", e)
    return out


def append_prediction(record: PredictionRecord, path: Path) -> None:
    """Atomically append one record as a JSON line (crash-safe rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = record.model_dump_json()
    existing = path.read_bytes() if path.exists() else b""
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False
    ) as tmp:
        tmp.write(existing)
        tmp.write(line.encode("utf-8"))
        tmp.write(b"\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
    os.replace(tmp_name, str(path))


def rewrite_predictions(records: list[PredictionRecord], path: Path) -> None:
    """Rewrite the whole ledger (used to patch yesterday's resolution in place)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(r.model_dump_json() for r in records)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False
    ) as tmp:
        tmp.write(body.encode("utf-8"))
        if body:
            tmp.write(b"\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
    os.replace(tmp_name, str(path))


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_outcome(direction: str, pct_change: float) -> str:
    """HIT/MISS/PUSH for a predicted ``direction`` given the actual % move."""
    if abs(pct_change) < _PUSH_BAND_PCT:
        return "PUSH"
    went_up = pct_change > 0
    predicted_up = direction.strip().upper() == "UP"
    return "HIT" if went_up == predicted_up else "MISS"


def resolve_call(
    record: PredictionRecord,
    api_key_id: str,
    api_secret_key: str,
    feed: str = "iex",
    min_call_interval: float = 1.0,
) -> PredictionRecord:
    """Fill in ``outcome``/``pct_change`` from real close-to-close price action.

    Best-effort: if price data is unavailable the record stays unresolved
    (``resolved=False``) so a later run can try again. Returns the record
    (mutated copy) either way.
    """
    pct, _vix = fetch_24h_close_change(
        record.call.ticker, record.date, api_key_id, api_secret_key,
        feed=feed, min_call_interval=min_call_interval,
    )
    if pct is None:
        logger.info("The Call: no price data yet for %s on %s — leaving unresolved",
                    record.call.ticker, record.date)
        return record
    outcome = resolve_outcome(record.call.direction, pct)
    return record.model_copy(update={"resolved": True, "outcome": outcome, "pct_change": pct})


# ---------------------------------------------------------------------------
# Season stats
# ---------------------------------------------------------------------------

def season_stats(records: list[PredictionRecord]) -> dict[str, tuple[int, int]]:
    """Return ``{name: (wins, losses)}`` for the bot + every human who's played.

    PUSH days count for nobody. A human only scores on days they submitted a
    call that matches/misses the resolved outcome.
    """
    stats: dict[str, list[int]] = {"Bot": [0, 0]}
    for r in records:
        if not r.resolved or r.outcome not in ("HIT", "MISS"):
            continue
        bot_win = r.outcome == "HIT"
        stats["Bot"][0 if bot_win else 1] += 1
        for name, vote in (r.human_calls or {}).items():
            human_out = resolve_outcome(vote, r.pct_change or 0.0)
            if human_out not in ("HIT", "MISS"):
                continue
            stats.setdefault(name, [0, 0])[0 if human_out == "HIT" else 1] += 1
    return {k: (w, ll) for k, (w, ll) in stats.items()}


def _record_ymd(d: date) -> str:
    return d.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# mailto play buttons
# ---------------------------------------------------------------------------

def build_mailto(recipients: list[str], call_date: date, ticker: str, direction: str) -> str:
    """A ``mailto:`` link that replies to the whole group with a structured vote."""
    to = ",".join(recipients)
    subject = f"MM {_record_ymd(call_date)} {ticker}: {direction}"
    body = (
        f"My call: {ticker} closes {'GREEN' if direction == 'UP' else 'RED'} "
        f"today ({direction}).\n\n— sent from Market Mover"
    )
    return f"mailto:{quote(to, safe='@,')}?subject={quote(subject)}&body={quote(body)}"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _score_line(stats: dict[str, tuple[int, int]]) -> list[tuple[str, int, int]]:
    """Sort players by win rate desc, then wins desc; Bot always included."""
    def rate(wl: tuple[int, int]) -> float:
        w, ll = wl
        return w / (w + ll) if (w + ll) else 0.0
    items = [(name, w, ll) for name, (w, ll) in stats.items()]
    items.sort(key=lambda t: (-rate((t[1], t[2])), -t[1]))
    return items


def render_prediction_html(
    today: PredictionRecord | None,
    yesterday: PredictionRecord | None,
    stats: dict[str, tuple[int, int]],
    recipients: list[str],
) -> str:
    """Render the game block: today's Call + UP/DOWN buttons + yesterday's result."""
    if today is None:
        return ""
    call = today.call
    up_link = build_mailto(recipients, today.date, call.ticker, "UP")
    down_link = build_mailto(recipients, today.date, call.ticker, "DOWN")
    tkr = html_escape(call.ticker)
    stmt = html_escape(call.statement)

    # Yesterday's resolution line.
    resolved_html = ""
    if yesterday is not None and yesterday.resolved and yesterday.outcome in ("HIT", "MISS", "PUSH"):
        badge_color = {"HIT": "#1a7f37", "MISS": "#cf222e", "PUSH": "#8c959f"}[yesterday.outcome]
        move = f"{yesterday.pct_change:+.1f}%" if yesterday.pct_change is not None else ""
        chips = []
        for name, vote in (yesterday.human_calls or {}).items():
            ho = resolve_outcome(vote, yesterday.pct_change or 0.0)
            mark = {"HIT": "✓", "MISS": "✗", "PUSH": "–"}.get(ho, "·")
            chips.append(f'{html_escape(name)}&nbsp;{mark}')
        bot_mark = {"HIT": "✓", "MISS": "✗", "PUSH": "–"}.get(yesterday.outcome, "·")
        chips.append(f"Bot&nbsp;{bot_mark}")
        resolved_html = (
            f'<div class="mm-darktext" style="font:400 13px/1.4 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
            f'color:#57606a;margin-top:10px;">'
            f'Yesterday ({html_escape(yesterday.call.ticker)} {move}): '
            f'<span style="color:{badge_color};font-weight:700;">{yesterday.outcome}</span>'
            f'&nbsp;&nbsp;{"&nbsp;·&nbsp;".join(chips)}</div>'
        )

    # Season scoreboard.
    board_rows = "".join(
        f'<tr><td style="padding:2px 12px 2px 0;font:600 13px/1.3 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        f'color:#24292f;">{html_escape(name)}</td>'
        f'<td style="padding:2px 0;font:400 13px/1.3 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        f'color:#57606a;">{w}-{ll}'
        f'{f" ({round(100*w/(w+ll))}%)" if (w+ll) else ""}</td></tr>'
        for name, w, ll in _score_line(stats)
    )
    board_html = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:10px;">{board_rows}</table>'
        if board_rows else ""
    )

    return (
        '<tr><td class="mm-darkcard" style="padding:8px 32px 4px;">'
        '<section data-block="prediction" style="margin:14px 0;background:#f6f8fa;border-radius:10px;'
        'padding:16px 18px;border-left:4px solid #0969da;">'
        '<div style="font:700 11px/1 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#0969da;'
        'text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;">🎯 The Call · Beat the Bot</div>'
        f'<div class="mm-darktext" style="font:600 16px/1.4 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#24292f;">'
        f'{stmt}</div>'
        f'<div style="font:400 12px/1.3 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#8c959f;margin-top:4px;">'
        f'{tkr} · {call.direction} · {call.confidence}% confidence</div>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:12px;"><tr>'
        f'<td style="padding-right:8px;"><a href="{up_link}" '
        'style="display:inline-block;background:#1a7f37;color:#fff;text-decoration:none;'
        'font:700 14px/1 -apple-system,Segoe UI,Roboto,Arial,sans-serif;padding:10px 18px;border-radius:8px;">'
        '👍 I&#39;m UP</a></td>'
        f'<td><a href="{down_link}" '
        'style="display:inline-block;background:#cf222e;color:#fff;text-decoration:none;'
        'font:700 14px/1 -apple-system,Segoe UI,Roboto,Arial,sans-serif;padding:10px 18px;border-radius:8px;">'
        '👎 I&#39;m DOWN</a></td>'
        '</tr></table>'
        f'{resolved_html}'
        f'{board_html}'
        '</section></td></tr>'
    )


def render_prediction_plain(
    today: PredictionRecord | None,
    yesterday: PredictionRecord | None,
    stats: dict[str, tuple[int, int]],
    recipients: list[str],
) -> str:
    if today is None:
        return ""
    call = today.call
    lines = [
        "THE CALL · BEAT THE BOT",
        f"  {call.statement}",
        f"  ({call.ticker} · {call.direction} · {call.confidence}% confidence)",
        f"  Play: reply-all UP or DOWN  (subject: MM {_record_ymd(today.date)} {call.ticker}: UP/DOWN)",
    ]
    if yesterday is not None and yesterday.resolved and yesterday.outcome:
        move = f"{yesterday.pct_change:+.1f}%" if yesterday.pct_change is not None else ""
        lines.append(f"  Yesterday ({yesterday.call.ticker} {move}): {yesterday.outcome}")
    if stats:
        board = "  ".join(
            f"{name} {w}-{ll}" for name, w, ll in _score_line(stats)
        )
        lines.append(f"  Season: {board}")
    return "\n".join(lines)
