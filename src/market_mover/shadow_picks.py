"""Shadow tracking of untraded runner-up stories (MM-T020).

Each run logs the stories ranked just below the top 3, plus a direction label for
every pick, made before the outcome is known. Prices score them later. This module
places no orders and runs after the top 3 are final, so it can't change the strategy.

Run a report with ``python -m market_mover.shadow_picks`` (needs Alpaca data keys).
"""

import logging
import os
import re
import statistics
import tempfile
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

from .paper_trading import is_after_market_open

logger = logging.getLogger("market_mover.shadow_picks")

_DIRECTIONS = {"BULLISH", "BEARISH", "MIXED"}
_CATEGORIES = {"macro", "single_name", "commodity", "crypto", "geopolitical", "other"}
_TICKER_RE = re.compile(r"^[A-Z][A-Z.]{0,9}$")
# Enough calendar days past the last record to reach the next trading day's bar.
_REPORT_PADDING_DAYS = 7

GROUP_TRADED = "traded"
GROUP_RUNNER_UP = "runner_up"


class ShadowPick(BaseModel):
    """One story, traded or not, with its direction as labeled at the time."""

    rank: int
    title: str
    ticker: str | None = None
    category: str = "other"
    impact_score: float
    direction: str | None = None
    url: str


class ShadowSelection(BaseModel):
    """What the LLM returned: directions for the top picks, plus runner-ups."""

    top_directions: dict[int, str] = {}
    runner_ups: list[ShadowPick] = []


class ShadowRecord(BaseModel):
    """One trading day's shadow-ledger row."""

    date: str
    run_started_at: str
    late_entry: bool
    traded: list[ShadowPick] = []
    runner_ups: list[ShadowPick] = []


class ScoredPick(BaseModel):
    """A pick's open-to-next-open return."""

    date: str
    group: str
    ticker: str
    category: str
    direction: str | None
    return_pct: float


def _clean_direction(value: object) -> str | None:
    direction = str(value or "").strip().upper()
    return direction if direction in _DIRECTIONS else None


def _clean_ticker(value: object) -> str | None:
    ticker = str(value or "").strip().upper()
    return ticker if _TICKER_RE.match(ticker) else None


def parse_shadow_selection(
    parsed: object, candidates: dict, count: int, first_rank: int
) -> ShadowSelection | None:
    """Validate the LLM's JSON. URLs must be real candidates, so titles can't be invented."""
    if not isinstance(parsed, dict):
        return None
    top_directions: dict[int, str] = {}
    for item in parsed.get("top_directions") or []:
        if not isinstance(item, dict):
            continue
        direction = _clean_direction(item.get("direction"))
        try:
            rank = int(item.get("rank"))
        except (TypeError, ValueError):
            continue
        if direction:
            top_directions[rank] = direction

    runner_ups: list[ShadowPick] = []
    seen: set[str] = set()
    for item in parsed.get("runner_ups") or []:
        if len(runner_ups) >= count:
            break
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        article = candidates.get(url)
        if article is None or url in seen:
            continue
        try:
            score = float(item.get("impact_score"))
        except (TypeError, ValueError):
            continue
        category = str(item.get("category", "")).strip().lower()
        runner_ups.append(
            ShadowPick(
                rank=first_rank + len(runner_ups),
                title=article.title,
                ticker=_clean_ticker(item.get("ticker")),
                category=category if category in _CATEGORIES else "other",
                impact_score=score,
                direction=_clean_direction(item.get("direction")),
                url=url,
            )
        )
        seen.add(url)
    if not runner_ups:
        logger.warning("Shadow picks: no valid runner-ups returned")
        return None
    return ShadowSelection(top_directions=top_directions, runner_ups=runner_ups)


def load_shadow_records(path: Path) -> list[ShadowRecord]:
    """Read the ledger (empty if absent). Bad rows are skipped with a warning."""
    if not path.exists():
        return []
    records: list[ShadowRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(ShadowRecord.model_validate_json(line))
        except ValueError as e:
            logger.warning(f"Shadow ledger: skipping bad row ({e})")
    return records


def _append_record(path: Path, record: ShadowRecord) -> None:
    """Atomically append one row (crash-safe rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_bytes() if path.exists() else b""
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False
    ) as tmp:
        tmp.write(existing)
        tmp.write(record.model_dump_json().encode("utf-8"))
        tmp.write(b"\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
    os.replace(tmp_name, str(path))


def record_shadow_picks(
    client,
    ranked: list,
    articles: list,
    settings,
    today: date,
    now: datetime | None = None,
) -> ShadowRecord | None:
    """Log today's shadow row. Never raises: the daily email must not depend on this."""
    if not settings.shadow_tracking_enabled:
        return None
    try:
        path = settings.shadow_picks_jsonl_full_path
        if any(r.date == today.isoformat() for r in load_shadow_records(path)):
            logger.info("Shadow picks: already recorded for %s, skipping", today)
            return None
        selection = client.generate_shadow_picks(ranked, articles, settings.shadow_runner_up_count)
        if not isinstance(selection, ShadowSelection):
            return None
        now = now or datetime.now(timezone.utc)
        traded = [
            ShadowPick(
                rank=a.rank, title=a.title, ticker=_clean_ticker(a.primary_ticker),
                category=a.category, impact_score=a.impact_score,
                direction=selection.top_directions.get(a.rank), url=a.url,
            )
            for a in ranked
        ]
        record = ShadowRecord(
            date=today.isoformat(),
            run_started_at=now.astimezone(timezone.utc).isoformat(),
            late_entry=is_after_market_open(now),
            traded=traded,
            runner_ups=selection.runner_ups,
        )
        _append_record(path, record)
        logger.info("Shadow picks: logged %d runner-ups", len(record.runner_ups))
        return record
    except Exception as e:
        logger.warning("Shadow tracking failed (%s), continuing", e)
        return None


def _open_to_next_open(bars: list[dict], day: str) -> float | None:
    for bar, next_bar in zip(bars, bars[1:]):
        if str(bar["t"])[:10] == day:
            return (float(next_bar["o"]) / float(bar["o"]) - 1) * 100
    return None


def score_shadow_records(records: list[ShadowRecord], bars: dict[str, list[dict]]) -> list[ScoredPick]:
    """Score picks open to next open.

    Late runs are skipped: their stories can already reflect that day's move, so
    measuring from the open would use information the run didn't have at the open.
    """
    scored: list[ScoredPick] = []
    for record in records:
        if record.late_entry:
            continue
        for group, picks in ((GROUP_TRADED, record.traded), (GROUP_RUNNER_UP, record.runner_ups)):
            for pick in picks:
                if not pick.ticker:
                    continue
                ret = _open_to_next_open(bars.get(pick.ticker, []), record.date)
                if ret is None:
                    continue
                scored.append(
                    ScoredPick(date=record.date, group=group, ticker=pick.ticker, category=pick.category,
                               direction=pick.direction, return_pct=ret)
                )
    return scored


def summarize(rows: list[ScoredPick], key: Callable[[ScoredPick], str]) -> dict[str, dict]:
    """Trade count, average return, and win rate for each group."""
    groups: dict[str, list[float]] = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row.return_pct)
    return {
        name: {
            "n": len(values),
            "mean_return": statistics.fmean(values),
            "win_rate": sum(v > 0 for v in values) / len(values) * 100,
        }
        for name, values in sorted(groups.items())
    }


def main() -> None:
    """Print the report: traded vs runner-ups, then direction by category."""
    from .config import MarketMoverSettings
    from .sources.alpaca_source import fetch_daily_bars

    settings = MarketMoverSettings()
    records = load_shadow_records(settings.shadow_picks_jsonl_full_path)
    if not records:
        print("No shadow records yet.")
        return
    symbols = sorted({p.ticker for r in records for p in r.traded + r.runner_ups if p.ticker})
    bars = fetch_daily_bars(
        symbols,
        date.fromisoformat(records[0].date),
        date.fromisoformat(records[-1].date) + timedelta(days=_REPORT_PADDING_DAYS),
        settings.alpaca_api_key_id,
        settings.alpaca_api_secret_key,
        feed=settings.alpaca_data_feed,
    )
    rows = score_shadow_records(records, bars)
    views: tuple[tuple[str, Callable[[ScoredPick], str]], ...] = (
        ("Traded vs runner-up", lambda r: r.group),
        ("Direction by category", lambda r: f"{r.category} / {r.direction}"),
    )
    for title, key in views:
        print(title)
        for name, stats in summarize(rows, key).items():
            print(f"  {name}: n={stats['n']} avg={stats['mean_return']:+.2f}% win={stats['win_rate']:.0f}%")


if __name__ == "__main__":
    main()
