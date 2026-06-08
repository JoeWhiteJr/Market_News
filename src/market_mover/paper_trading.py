"""Paper-trading engine (Cycle 6 / ADR 0003).

Builds a dollar-denominated track record of the briefing's own picks against the
Alpaca **paper** account. Per ADR 0003 the rules are locked:

- **Universe**: the day's ranked picks (#1–#3) that carry a clean tradeable
  ticker. Macro/geopolitical picks with no ticker and private companies are
  skipped; crypto is out of v1 (equities/ETFs only).
- **Side**: long only.
- **Sizing**: equal-weight, fixed ``paper_notional_per_position`` per pick.
- **Hold**: ~24h. Each daily run **closes** the prior run's positions, then
  **opens** today's picks (single pre-market cron, so open-to-open ≈ 24h).

The ledger (``data/paper_trades.jsonl``) stores one :class:`PaperCycleRecord`
per trading day. The run is idempotent: a second run on the same calendar day
is a no-op (it returns the existing record).

PAPER ONLY — there is no live-trading path here.
"""

import logging
import os
import tempfile
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from .alpaca_trading import AlpacaTradingClient
from .config import MarketMoverSettings

logger = logging.getLogger("market_mover.paper_trading")

# Categories whose picks are NOT paper-traded in v1 (no clean equity proxy).
_SKIP_CATEGORIES = {"crypto"}


class ClosedPaperTrade(BaseModel):
    """A position closed this cycle, marked at close-submit time."""

    ticker: str
    qty: float
    entry_price: float
    exit_price: float
    pnl_abs: float
    pnl_pct: float


class OpenedPaperPosition(BaseModel):
    """A position opened this cycle for one of today's picks."""

    ticker: str
    rank: int
    notional: float
    order_id: str | None = None


class PaperCycleRecord(BaseModel):
    """One trading day's paper activity: equity snapshot + opens + closes."""

    cycle_date: str                       # ISO date the cycle ran
    equity: float | None = None           # Alpaca account equity after the cycle
    opened: list[OpenedPaperPosition] = []
    closed: list[ClosedPaperTrade] = []


def eligible_picks(picks: list) -> list:
    """Filter picks to those we'll paper-trade (clean, tradeable ticker).

    ``picks`` may be ``RankedArticle`` or ``ScorecardPick`` — both expose
    ``rank``, ``primary_ticker`` and ``category``. Returns at most 3.
    """
    out = []
    for p in picks:
        ticker = (getattr(p, "primary_ticker", None) or "").strip()
        category = getattr(p, "category", "other")
        if ticker and category not in _SKIP_CATEGORIES:
            out.append(p)
    return out[:3]


def load_cycles(path: Path) -> list[PaperCycleRecord]:
    """Read all cycle records from the ledger (empty list if absent/corrupt)."""
    if not path.exists():
        return []
    records: list[PaperCycleRecord] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(PaperCycleRecord.model_validate_json(line))
                except Exception as e:
                    logger.warning(f"paper ledger: skipping bad row ({e})")
    except OSError as e:
        logger.warning(f"paper ledger: could not read {path} ({e})")
    return records


def _append_cycle(path: Path, record: PaperCycleRecord) -> None:
    """Atomically append one cycle record (tempfile + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            existing = [ln.rstrip("\n") for ln in fh if ln.strip()]
    existing.append(record.model_dump_json())
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp",
        delete=False,
    ) as tmp:
        for ln in existing:
            tmp.write(ln.encode("utf-8"))
            tmp.write(b"\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
    os.replace(tmp_name, str(path))


def compute_paper_stats(cycles: list[PaperCycleRecord]) -> dict:
    """Aggregate the ledger into display stats.

    Returns ``{n_trades, wins, win_rate, total_pnl, equity, last_cycle_date}``.
    ``equity`` is the most recent non-null equity snapshot.
    """
    all_closed = [t for c in cycles for t in c.closed]
    n = len(all_closed)
    wins = sum(1 for t in all_closed if t.pnl_abs > 0)
    total_pnl = sum(t.pnl_abs for t in all_closed)
    equity = next((c.equity for c in reversed(cycles) if c.equity is not None), None)
    return {
        "n_trades": n,
        "wins": wins,
        "win_rate": (wins / n * 100.0) if n else None,
        "total_pnl": total_pnl,
        "equity": equity,
        "last_cycle_date": cycles[-1].cycle_date if cycles else None,
    }


def _close_open_positions(client: AlpacaTradingClient) -> list[ClosedPaperTrade]:
    """Mark + liquidate every open paper position (the prior run's opens)."""
    closed: list[ClosedPaperTrade] = []
    for pos in client.list_positions():
        try:
            ticker = str(pos["symbol"])
            qty = float(pos.get("qty", 0) or 0)
            entry = float(pos.get("avg_entry_price", 0) or 0)
            exit_price = float(pos.get("current_price", entry) or entry)
            pnl_abs = float(pos.get("unrealized_pl", (exit_price - entry) * qty))
            pnl_pct = float(pos.get("unrealized_plpc", 0) or 0) * 100.0
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"paper: skipping malformed position {pos!r} ({e})")
            continue
        closed.append(
            ClosedPaperTrade(
                ticker=ticker, qty=qty, entry_price=entry, exit_price=exit_price,
                pnl_abs=pnl_abs, pnl_pct=pnl_pct,
            )
        )
        client.close_position(ticker)
    return closed


def run_paper_cycle(
    picks: list,
    settings: MarketMoverSettings,
    today: date,
    client: AlpacaTradingClient | None = None,
) -> PaperCycleRecord | None:
    """Run one paper-trading cycle: close prior positions, open today's picks.

    Idempotent — if a record already exists for ``today``, returns it without
    touching the broker. Returns ``None`` when paper trading is disabled or
    credentials are missing.
    """
    if not settings.paper_trading_enabled or not settings.has_alpaca_creds:
        logger.info("Paper trading disabled or Alpaca creds missing — skipping")
        return None

    path = settings.paper_trades_jsonl_full_path
    cycles = load_cycles(path)
    if cycles and cycles[-1].cycle_date == today.isoformat():
        logger.info("Paper trading: already ran for %s — skipping (idempotent)", today)
        return cycles[-1]

    client = client or AlpacaTradingClient(settings)

    # 1) Close the prior run's positions (the ~24h exit).
    closed = _close_open_positions(client)

    # 2) Open today's eligible picks, equal-weight notional.
    opened: list[OpenedPaperPosition] = []
    for p in eligible_picks(picks):
        ticker = p.primary_ticker.strip().upper()
        order = client.submit_notional_order(
            ticker, settings.paper_notional_per_position, side="buy"
        )
        opened.append(
            OpenedPaperPosition(
                ticker=ticker,
                rank=int(p.rank),
                notional=settings.paper_notional_per_position,
                order_id=(order or {}).get("id") if isinstance(order, dict) else None,
            )
        )

    # 3) Equity snapshot (Alpaca is the source of truth for the curve).
    account = client.get_account()
    equity = None
    if isinstance(account, dict):
        try:
            equity = float(account.get("equity"))
        except (TypeError, ValueError):
            equity = None

    record = PaperCycleRecord(
        cycle_date=today.isoformat(), equity=equity, opened=opened, closed=closed
    )
    _append_cycle(path, record)
    logger.info(
        "Paper cycle %s: closed %d, opened %d, equity=%s",
        today, len(closed), len(opened), equity,
    )
    return record
