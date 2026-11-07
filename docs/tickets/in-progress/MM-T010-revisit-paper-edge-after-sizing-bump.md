---
id: MM-T010
title: Revisit the paper-trading edge ~4 weeks after the $15k sizing bump
status: in-progress
priority: medium
type: analysis
owner: joe
assigned-team: Scout
created: 2026-08-12
updated: 2026-11-06
review-after: 2026-09-09
related-pr:
related-tickets: MM-T009, MM-T003
---

# Revisit the paper-trading edge ~4 weeks after the $15k sizing bump

## Problem
MM-T009 raised paper sizing $1k → $15k/pick and put the cumulative realized P&L on
the dashboard. That made the P&L **visible**, not **better** — the strategy was
breakeven going in (~57 trades, ~54% win, roughly flat net equity). The honest open
question is whether the picks actually pull ahead of zero once the noise is loud
enough to see. This ticket is the scheduled check-back so that question doesn't get
forgotten.

## Review after
**2026-09-09** (~4 weeks of $15k-era trades). Don't grade earlier — the sample is
too thin and the regime kink at 2026-08-12 dominates.

## What to look at
- Pull the P&L curve for **post-2026-08-12 cycles only** (ignore the $1k history —
  it's a different regime). Is the $15k-era slope meaningfully > 0, or still hugging
  zero with bigger swings?
- Win rate + average win/loss on the new-size trades vs the $1k baseline. Sizing
  shouldn't change the *rate* — if it did, that's a bug worth chasing, not edge.
- Worst single-day drawdown now that a -10% name costs ~$1.5k. Does it feel like
  something you'd tolerate with real money?
- Sanity: is the learning-feedback loop (ADR 0005) nudging category confidence in a
  direction the track record justifies, or is it noise?

## Decision this informs
Whether "go live someday" is even worth entertaining, or whether the strategy needs
a real edge (signal/selection change) before sizing means anything. Default posture:
**still paper, no live endpoint** (ADR 0003) until an edge is demonstrated — not just
a lucky month.

## Acceptance Criteria
- [x] Post-bump P&L slope + win-rate/avg-win-loss computed and written up here
- [x] Explicit verdict: edge / no edge / too-early-still, with the numbers behind it
- [x] If no edge: note candidate next levers (selection, holding period, category
      weighting) — don't act, just enumerate
- [x] Update or close based on findings

## Context & Notes
- Reminder: sizing amplifies variance, not edge. A good-looking month at $15k could
  be one lucky name. Weight the win *rate* and average trade over the headline dollar
  figure.
- All paper (ADR 0003). Nothing here touches real money.

## Review (2026-11-06, data through 2026-11-06)
**Verdict: too early. No edge shown, none ruled out. Stay on paper.**

| Since the $15k bump | Result |
|---|---|
| Account equity, 08-12 to 11-06 | +$7,498 (SPY -4.8%) |
| Without the MRNA day (+$5,500) | +$1,998 |
| Average change per cycle | +$132 (t=1.07, 57 cycles); +$36 without MRNA (t=0.46) |
| Median change per cycle | +$23; 29 of 57 cycles were up |
| Worst day | -$1,796 (08-21, the day after MRNA) |
| Max drawdown | -$2,413, 5.4% of the $45k deployed |

A straight line through equity gives +$94 per cycle, but one jump drives that line,
so the per-cycle average is the fairer test.

| Closed trades | n | Win rate | Avg | Avg win | Avg loss |
|---|---|---|---|---|---|
| $1k era | 81 | 55.6% | +0.11% | +2.20% | -2.50% |
| $15k era | 115 | 53.9% | +0.55% | +2.44% | -1.66% |
| $15k era without MRNA | 114 | 53.5% | +0.23% | +1.87% | -1.66% |

The win rate held, so sizing did not change behavior. Smaller average losses are the
one improvement, but the sample is too small to credit.

**Stress tests** (all intended trades rebuilt from public opening prices, n=287):
- Average trade +0.28%, 95% range -0.12% to +0.72%.
- Return after SPY and oil moves: +0.24% per cycle (p=0.13), or +0.15% without MRNA (p=0.27).
- Beats random picks from the same tickers at p=0.08, or p=0.17 without MRNA.
- Confirming +0.37% per trade takes about 600-750 trades, so about 2 more months of
  data will not settle this.

**Why the ledger overstates results:**
- Ledger P&L since 08-14 is +$9,922, against +$7,515 of equity. Use equity. (MM-T016)
- Labor Day double-logged closes, adding about +$135.
- 6 cycles in this era started late and account for $2,182 of ledger P&L.
- Failed orders skipped repeat picks until 11-07 (fixed, ADR 0003 amendment).

**Learning feedback (ADR 0005):** no evidence it helps. Win rate is 54.4% with it and
55.6% before it, and the higher average with it is mostly MRNA. Geopolitical picks rose
from 22% to 33% of the mix, which may be the oil news cycle, but ADR 0005 was not meant
to shift the mix, so it needs watching.

**Levers to consider (not acted on):**
- Selection: drop macro (-0.10% per trade). Impact score and rank do not predict returns.
- Holding period: not tested here.
- Category weighting: a filter using only past data did not beat keeping everything
  (+0.34% vs +0.37%).
- Oil: 38 of 50 logged geopolitical trades are USO, so test it against simply holding USO.

**What this means for real money:** not yet. At $2,000 fully invested, this period scales
to +$333, or +$89 without MRNA, with an -$80 worst day and a -$107 max drawdown.
