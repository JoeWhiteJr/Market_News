---
id: MM-T010
title: Revisit the paper-trading edge ~4 weeks after the $15k sizing bump
status: open
priority: medium
type: analysis
owner: joe
assigned-team: Scout
created: 2026-08-12
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
- [ ] Post-bump P&L slope + win-rate/avg-win-loss computed and written up here
- [ ] Explicit verdict: edge / no edge / too-early-still, with the numbers behind it
- [ ] If no edge: note candidate next levers (selection, holding period, category
      weighting) — don't act, just enumerate
- [ ] Update or close based on findings

## Context & Notes
- Reminder: sizing amplifies variance, not edge. A good-looking month at $15k could
  be one lucky name. Weight the win *rate* and average trade over the headline dollar
  figure.
- All paper (ADR 0003). Nothing here touches real money.
