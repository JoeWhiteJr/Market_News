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
  A held ticker that is picked again is **carried** and resized to target rather
  than closed and re-bought, because the broker refuses a buy while a sell on the
  same ticker is pending (ADR 0003 amendment, MM-T016).

The ledger (``data/paper_trades.jsonl``) stores one :class:`PaperCycleRecord`
per trading day. The run is idempotent: a second run on the same calendar day
is a no-op (it returns the existing record).

PAPER ONLY — there is no live-trading path here.
"""

import logging
import os
import tempfile
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .alpaca_trading import AlpacaTradingClient
from .config import MarketMoverSettings

logger = logging.getLogger("market_mover.paper_trading")

# Categories whose picks are NOT paper-traded in v1 (no clean equity proxy).
_SKIP_CATEGORIES = {"crypto"}

# A carried position within this fraction of its target value is left alone, so
# ordinary daily drift doesn't generate churn orders.
_RESIZE_TOLERANCE = 0.10

# Orders placed at or after the opening bell fill at intraday prices, not the open
# ADR 0003 assumes, so those cycles are flagged for analysis to exclude or split.
_MARKET_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN = time(9, 30)

STATUS_SUBMITTED = "submitted"
STATUS_CARRIED = "carried"
STATUS_FAILED = "failed"


class ClosedPaperTrade(BaseModel):
    """A position closed this cycle, marked at close-submit time."""

    ticker: str
    qty: float
    entry_price: float
    exit_price: float
    pnl_abs: float
    pnl_pct: float
    # The pick's category (carried from when it was opened) so P&L pools by
    # category without a join back to briefings.jsonl. Null on legacy rows /
    # positions we can't map. (Phase 0 / ADR 0004.)
    category: str | None = None


class OpenedPaperPosition(BaseModel):
    """A position opened this cycle for one of today's picks."""

    ticker: str
    rank: int
    notional: float
    order_id: str | None = None
    category: str | None = None  # the pick's category (Phase 0 / ADR 0004)
    # submitted | carried | failed. Null on legacy rows, where a null order_id was
    # ambiguous. "carried" means the ticker was already held, so no opening order.
    status: str | None = None


class PaperCycleRecord(BaseModel):
    """One trading day's paper activity: equity snapshot + opens + closes."""

    cycle_date: str                       # ISO date the cycle ran
    equity: float | None = None           # Alpaca account equity after the cycle
    run_started_at: str | None = None     # UTC time the cycle ran (null on legacy rows)
    late_entry: bool | None = None        # True if it ran at/after the US open
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


def _mark_position(
    pos: dict, category: str | None, fraction: float = 1.0
) -> ClosedPaperTrade | None:
    """Mark ``fraction`` of a broker position as a closed trade (None if malformed)."""
    try:
        ticker = str(pos["symbol"])
        qty = float(pos.get("qty", 0) or 0)
        entry = float(pos.get("avg_entry_price", 0) or 0)
        exit_price = float(pos.get("current_price", entry) or entry)
        pnl_abs = float(pos.get("unrealized_pl", (exit_price - entry) * qty))
        pnl_pct = float(pos.get("unrealized_plpc", 0) or 0) * 100.0
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"paper: skipping malformed position {pos!r} ({e})")
        return None
    return ClosedPaperTrade(
        ticker=ticker, qty=qty * fraction, entry_price=entry, exit_price=exit_price,
        pnl_abs=pnl_abs * fraction, pnl_pct=pnl_pct, category=category,
    )


def _close_open_positions(
    client: AlpacaTradingClient,
    positions: list[dict],
    category_by_ticker: dict[str, str] | None = None,
) -> list[ClosedPaperTrade]:
    """Mark + liquidate ``positions``, logging only the closes the broker accepted.

    ``category_by_ticker`` (from the prior cycle's opened positions) lets each
    closed trade carry the pick's category so P&L pools by category.
    """
    category_by_ticker = category_by_ticker or {}
    closed: list[ClosedPaperTrade] = []
    for pos in positions:
        symbol = str(pos.get("symbol", ""))
        trade = _mark_position(pos, category_by_ticker.get(symbol.upper()))
        if trade is None:
            continue
        if not client.close_position(trade.ticker):
            logger.warning("paper: close of %s failed, position still open", trade.ticker)
            continue
        closed.append(trade)
    return closed


def _position_value(pos: dict) -> float | None:
    """Current dollar value of a broker position, or None if it can't be read."""
    try:
        if pos.get("market_value") not in (None, ""):
            return float(pos["market_value"])
        return float(pos["qty"]) * float(pos["current_price"])
    except (KeyError, TypeError, ValueError):
        return None


def _resize_carried(
    client: AlpacaTradingClient,
    pos: dict,
    target: float,
    category: str | None,
) -> tuple[str | None, ClosedPaperTrade | None]:
    """Move a carried position to ``target`` dollars if it drifted past tolerance.

    Returns ``(order_id, trade)``. ``trade`` is the realized slice of a trim, logged
    so the ledger keeps tracking broker equity.
    """
    ticker = str(pos["symbol"]).upper()
    value = _position_value(pos)
    if value is None or value <= 0:
        logger.warning("paper: can't value carried %s, leaving it unchanged", ticker)
        return None, None
    delta = target - value
    if abs(delta) <= target * _RESIZE_TOLERANCE:
        return None, None
    side = "buy" if delta > 0 else "sell"
    order = client.submit_notional_order(ticker, round(abs(delta), 2), side=side)
    if not isinstance(order, dict):
        logger.warning("paper: %s of carried %s was rejected", side, ticker)
        return None, None
    trade = _mark_position(pos, category, fraction=-delta / value) if delta < 0 else None
    return order.get("id"), trade


def run_paper_cycle(
    picks: list,
    settings: MarketMoverSettings,
    today: date,
    client: AlpacaTradingClient | None = None,
    now: datetime | None = None,
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
    now = now or datetime.now(timezone.utc)
    late_entry = now.astimezone(_MARKET_TZ).time() >= _MARKET_OPEN
    if late_entry:
        logger.warning("paper: cycle %s started after the US open, flagging as late", today)

    # 1) Close the prior run's positions (the ~24h exit). Carry each pick's
    # category so closed P&L pools. Walk the whole ledger, oldest first, because a
    # position can outlive one cycle when a market holiday delays its fill or close.
    prior_categories: dict[str, str] = {}
    for cycle in cycles:
        for op in cycle.opened:
            if op.category:
                prior_categories[op.ticker.upper()] = op.category
    #    A ticker picked again today is carried instead: closing it would leave a
    #    pending sell that makes the broker refuse today's buy.
    picks_by_ticker: dict[str, list] = {}
    for p in eligible_picks(picks):
        picks_by_ticker.setdefault(p.primary_ticker.strip().upper(), []).append(p)
    held = {str(pos.get("symbol", "")).upper(): pos for pos in client.list_positions()}
    closed = _close_open_positions(
        client,
        [pos for ticker, pos in held.items() if ticker not in picks_by_ticker],
        prior_categories,
    )

    # 2) Open today's eligible picks, equal-weight notional; resize carried ones.
    notional = settings.paper_notional_per_position
    opened: list[OpenedPaperPosition] = []
    for ticker, ticker_picks in picks_by_ticker.items():
        if ticker in held:
            order_id, trim = _resize_carried(
                client, held[ticker], notional * len(ticker_picks), prior_categories.get(ticker)
            )
            if trim is not None:
                closed.append(trim)
            for i, p in enumerate(ticker_picks):
                opened.append(
                    OpenedPaperPosition(
                        ticker=ticker, rank=int(p.rank), notional=notional,
                        order_id=order_id if i == 0 else None,
                        category=getattr(p, "category", None), status=STATUS_CARRIED,
                    )
                )
            continue
        for p in ticker_picks:
            order = client.submit_notional_order(ticker, notional, side="buy")
            order_id = order.get("id") if isinstance(order, dict) else None
            if order_id is None:
                logger.warning("paper: buy of %s (rank %s) failed", ticker, p.rank)
            opened.append(
                OpenedPaperPosition(
                    ticker=ticker, rank=int(p.rank), notional=notional, order_id=order_id,
                    category=getattr(p, "category", None),
                    status=STATUS_SUBMITTED if order_id else STATUS_FAILED,
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
        cycle_date=today.isoformat(), equity=equity, opened=opened, closed=closed,
        run_started_at=now.astimezone(timezone.utc).isoformat(), late_entry=late_entry,
    )
    _append_cycle(path, record)
    logger.info(
        "Paper cycle %s: closed %d, opened %d, equity=%s",
        today, len(closed), len(opened), equity,
    )
    return record
