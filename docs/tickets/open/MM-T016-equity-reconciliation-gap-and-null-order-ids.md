---
id: MM-T016
title: Reconcile $1,499 equity gap (2026-08-21) and handle null order_id opens
status: open
priority: high
type: bug
owner: joe
created: 2026-08-25
updated: 2026-08-25
related-pr:
related-tickets: MM-T010
assigned-team:
---

# Reconcile $1,499 equity gap (2026-08-21) and handle null order_id opens

## Problem
Broker-reported `equity` in `data/paper_trades.jsonl` and the cumulative sum of logged
`closed[].pnl_abs` tracked each other within ~$90 (normal open-position mark-to-market)
until 2026-08-21. On that date a **-$1,521 gap appeared and has stayed constant**
(-$1,498.92 on 08-24 and 08-25), which means a one-time realized loss hit the account
that the ledger never recorded. Also on 08-21, only 2 of 3 positions opened and both
carry `order_id: null` (rank-3 slot missing entirely); another `order_id: null` open
appears on 08-25 (TLT). Failed/unlogged orders are the prime suspect.

Consequence: "how is the strategy doing" currently has two answers that disagree by
1.5 points (ledger equity +2.5% vs realized-P&L replay +4.0%), and neither is cleanly
right. All learning-loop / edge analysis depends on this ledger being trustworthy.

## Acceptance Criteria
- [ ] Root-cause the 2026-08-21 gap (broker fill history vs ledger; check the failed
      rank-3 open and both null order_ids)
- [ ] Ledger correction entry or documented explanation committed (append-only; match
      exact serialization `separators=(",", ":")` per LESSONS.md)
- [ ] cli.py logs a warning (and records the failure in the ledger row) whenever an
      order comes back without an order_id, instead of writing `order_id: null` silently
- [ ] A reconciliation check (equity vs cumulative realized P&L within open-position
      MTM tolerance) runs each cycle and warns on drift > $500
- [ ] Regression test for the null-order_id path

## Notes
Found 2026-08-25 during a stats pass. Same pass also showed total realized P&L is
+$4,043 but **+$5,534 of that is a single trade** (MRNA +36.9% on 2026-08-20, at $15k
sizing); ex-MRNA the book is -$1,491 and mean per-trade return is slightly negative.
Relevant context for MM-T010 (revisit edge after sizing bump).

## Retrospective
(fill on close)
