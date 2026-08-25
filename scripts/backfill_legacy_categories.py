"""One-shot migration: backfill category on legacy closed paper trades.

Context (MM-T015): category tracking on paper picks began 2026-06-23. The five
cycles opened before that (2026-06-09 .. 2026-06-15) carried no category, so the
trades that closed them landed in the dashboard's "unmapped" bucket (-$145.30,
10 trades). That made the per-category P&L attribution wrong: the two biggest
early single-name losers (SPCX -$150.76, ROKU -$58.39) were hidden in
"unmapped" instead of dragging down single_name.

Every unmapped trade's real category is recoverable from data/briefings.jsonl:
a closed trade on cycle N closed a position opened on cycle N-1, so we look up
the pick's category in the briefing dated N-1 by ticker.

This script is idempotent: it only fills closed trades whose category is null,
and it refuses to write a category it cannot verify against the briefing.

Run from the repo root:
    python scripts/backfill_legacy_categories.py            # dry run (default)
    python scripts/backfill_legacy_categories.py --apply    # write changes
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

LEDGER = Path("data/paper_trades.jsonl")
BRIEFINGS = Path("data/briefings.jsonl")


def _load_briefing_categories() -> dict[tuple[str, str], str]:
    """Map (briefing_date, TICKER) -> category from the briefings ledger."""
    out: dict[tuple[str, str], str] = {}
    for line in BRIEFINGS.read_text().splitlines():
        if not line.strip():
            continue
        b = json.loads(line)
        date = b.get("date")
        for pick in b.get("picks", []):
            ticker = pick.get("primary_ticker") or pick.get("ticker")
            category = pick.get("category")
            if date and ticker and category:
                out[(date, ticker.upper())] = category
    return out


def backfill(*, apply: bool) -> int:
    """Fill null categories on closed trades. Returns the number of edits."""
    rows = [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
    cats = _load_briefing_categories()

    # Cycle N closed positions were opened on cycle N-1.
    dates = [r["cycle_date"] for r in rows]
    prior = {dates[i]: dates[i - 1] for i in range(1, len(dates))}

    edits = 0
    unresolved: list[tuple[str, str]] = []
    for row in rows:
        close_date = row["cycle_date"]
        for trade in row.get("closed", []):
            if trade.get("category"):
                continue
            open_date = prior.get(close_date)
            ticker = (trade.get("ticker") or "").upper()
            category = cats.get((open_date, ticker)) if open_date else None
            if category is None:
                unresolved.append((close_date, ticker))
                continue
            print(f"  {close_date} {ticker:6} ${trade.get('pnl_abs', 0):8.2f} -> {category}")
            if apply:
                trade["category"] = category
            edits += 1

    if unresolved:
        raise SystemExit(
            f"ABORT: {len(unresolved)} unmapped trades could not be resolved "
            f"from briefings: {unresolved}. No changes written."
        )

    if apply:
        # Match the app's compact serialization so untouched rows stay
        # byte-identical and the diff shows only the trades we edited.
        with LEDGER.open("w") as fh:
            for row in rows:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(f"\nApplied {edits} edits to {LEDGER}.")
    else:
        print(f"\nDry run: {edits} trades would be updated. Re-run with --apply to write.")
    return edits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()
    backfill(apply=args.apply)


if __name__ == "__main__":
    main()
