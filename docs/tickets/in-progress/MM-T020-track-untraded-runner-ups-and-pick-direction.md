---
id: MM-T020
title: Track untraded runner-up stories and each pick's direction
status: in-progress
priority: medium
type: feature
owner: joe
created: 2026-11-07
updated: 2026-11-07
related-pr:
related-tickets: MM-T021, MM-T010
assigned-team:
---

# Track untraded runner-up stories and each pick's direction

## Problem
Confirming an edge from 3 trades a day takes a year or more. We also can't tell whether
the top picks beat the stories the model passed over, because only the top 3 are stored.

## Approach
After the top 3 are final, one extra LLM call picks up to 5 runner-ups and labels every
pick BULLISH, BEARISH, or MIXED. `data/shadow_picks.jsonl` stores them, and prices score
them later, open to next open. It places no orders and can't change the picks or email.
Rows from late runs are stored but left out of scoring.

## Acceptance Criteria
- [x] Failures never affect the send, and a same-day rerun makes no extra call
- [x] Runner-ups must match a real candidate URL, so titles can't be invented
- [x] `python -m market_mover.shadow_picks` reports traded vs runner-ups and direction by category
- [x] Tests and ruff pass
- [ ] PR merged, after the 2026-11-09 run is verified
- [ ] First live row has runner-ups with tickers and directions on all picks
- [ ] Top 3 and email stay unaffected on the first live run

## Retrospective
_Fill in when moving to done/._
