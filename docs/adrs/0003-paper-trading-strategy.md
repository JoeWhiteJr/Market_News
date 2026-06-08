# ADR 0003 — Paper-trading strategy (locked rules)

**Status**: **Accepted** — 2026-06-08 (Joe)
**Decides**: Exactly what the AI paper-trades each day, how much, and when it exits.
**Depends on**: ADR 0002 (Alpaca data + paper account).

## Context

We want a **dollar-denominated track record** of whether the briefing's picks
make money — the real backtest that must prove an edge **before any live money
moves** (Joe: paper-only to start; live trading is a later, gated cycle).

Like ADR 0001's judge rubric, the strategy rules must be **locked and
consistent** — otherwise the equity curve measures a moving target. Joe selected
the two core rules; this ADR formalizes them and fills the mechanics.

## Decision

### Universe (what it buys) — *Joe-approved*
The day's **ranked picks #1–#3** that have a **clean tradeable ticker**
(`primary_ticker` is non-null and is a real US-listed equity/ETF). Skip:
- macro/geopolitical picks with no clean single ticker,
- private companies / non-tradeable entities (e.g. SpaceX),
- anything the judge would mark `NOT_APPLICABLE` for lack of a ticker.

So 0–3 paper positions open per briefing day.

### Side
**Long only** in v1. No shorting, no options, no leverage. (The Bear-Case coda
is *not* traded in v1 — revisit later.)

### Position sizing
**Equal-weight, fixed notional per position**: `$1,000` paper per pick
(config `paper_notional_per_position`, default 1000). Max 3 positions/day →
≤ `$3,000` deployed/day against the Alpaca paper default `$100,000` equity.
Fractional shares allowed (Alpaca supports them) so the notional is exact.

### Entry / Exit — *Joe-approved: ~24h hold (single-cron model)*
The pipeline is a single pre-market cron, so it can't both open at the bell and
close at that day's close. The implemented model preserves the ~24h hold:
- **Each daily run**: first **close** the prior run's open positions (market
  orders, queued for the open), then **open** today's eligible picks (market
  notional orders, queued for the open).
- Net hold ≈ **open-to-open, ~24h** — mirrors the "grade yesterday, act today"
  rhythm of the Yesterday-Index. One position per pick, flat between cycles.
- P&L per closed position is marked at close-submit time (the position's
  Alpaca `unrealized_pl`); **Alpaca's account equity is the ground-truth curve**.
- *Refinement tracked, not built*: a second GitHub Actions workflow at 16:00 ET
  could liquidate intraday for a pure single-session hold (no overnight gap).

### Idempotency & safety
- Re-runs on the same day must **not** double-open positions (guard on a
  per-day trade-ledger key, mirroring `judge_yesterday`'s short-circuit).
- Paper account only; **no live endpoint** is wired in this cycle.
- Every paper order records the **pick + reasoning** that triggered it (audit
  trail, reused later for live trading).

### Recording
New append-only `data/paper_trades.jsonl` (separate from `briefings.jsonl`),
one record per position: `date, ticker, rank, side, notional, entry_price,
exit_price, pnl_abs, pnl_pct, briefing_date, alpaca_order_ids`. Running paper
equity + win-rate surfaced in the email scorecard (cosmetic, iterable).

## What's locked vs iterable
- **Locked**: long-only; universe = ranked #1–3 with a clean ticker;
  equal-weight fixed-notional sizing; 24h single-session hold; paper-only.
- **Iterable**: the notional amount, the scorecard display, whether to later
  add shorts/Bear-Case/multi-day holds (a NEW ADR — changing these resets the
  track record, same discipline as bumping `judge_prompt_version`).

## Verification
- Unit tests: universe filter (skips macro/private), sizing math, 24h exit,
  same-day re-run idempotency — all with a mocked Alpaca paper client.
- Live paper smoke: submit one tiny paper order against Alpaca paper, confirm
  fill + ledger row, then liquidate. No real money anywhere.

## Sign-off
- [x] Joe approves universe (ranked #1–3 with a clean ticker) — 2026-06-08
- [x] Joe approves long-only v1 (no shorts/options/leverage) — 2026-06-08
- [x] Joe approves $1,000/position equal-weight sizing — 2026-06-08
- [x] Joe approves ~24h hold, single-cron model (close prior run → open today) — 2026-06-08
- [x] Joe approves paper-only (live trading is a separate, future ADR) — 2026-06-08
