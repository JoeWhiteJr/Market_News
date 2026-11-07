---
id: MM-T021
title: Next paper edge review (progress check 2027-02-05, verdict after 600 trades)
status: open
priority: medium
type: analysis
owner: joe
created: 2026-11-07
updated: 2026-11-07
review-after: 2027-02-05
related-pr:
related-tickets: MM-T010, MM-T016, MM-T020
assigned-team: Scout
---

# Next paper edge review

## Problem
MM-T010 ended "too early": +0.28% per trade with a range that includes zero. It ran about
2 months late because nothing scheduled it. This ticket is the next check.

## Rules
- Count only on-time trades from 2026-11-09 on (`late_entry: false`), the new regime.
- Keep the strategy rules fixed until the verdict. Try ideas in replays first.
- Test on per-cycle changes and always rerun without the largest trade.
- 2027-02-05 is a progress check only. A verdict needs about 600 on-time trades, which is
  late 2027 or later, because carried repeat picks count as fewer trades.

## Acceptance Criteria
- [ ] MM-T016 reconciliation done first, or its effect on the numbers is stated
- [ ] Equity vs SPY and USO, with and without the largest trade
- [ ] Traded picks vs runner-ups (MM-T020)
- [ ] Single-stock lead: do bearish stories beat bullish ones on new data?
- [ ] Does USO beat simply holding USO?
- [ ] Explicit verdict: edge, no edge, or too early, and the next review date

## Baselines from the 2026-11-06 replays (open prices, in-sample, exploratory)
Entry time, 272 trades held to the next open: 9:30 AM +0.40%, 10:30 +0.24%, 12:30 PM +0.18%,
3:30 PM +0.14%. Moving later did not help.

Story direction, 281 trades, blind LLM labels, all bought:

| Direction | n | Avg | Win rate |
|---|---|---|---|
| Bullish | 113 | +0.48% | 58% |
| Bearish | 130 | +0.22% | 55% |
| Mixed | 38 | +0.50% | 63% |

Bullish minus bearish: +0.26% (range -0.65% to +1.23%), and 0.00% without MRNA. Following
direction with shorts averaged +0.10%, against +0.36% for only buying. Single stocks without
MRNA: bearish +0.58% vs bullish -0.79%, range includes zero.

## Retrospective
_Fill in when moving to done/._
