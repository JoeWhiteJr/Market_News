---
id: MM-T002
title: Turn the daily briefing into a 2-person stocks-club ritual with Jared
status: open
priority: low
type: feature
owner: joe
created: 2026-06-03
updated: 2026-06-03
related-pr:
related-tickets:
---

# Turn the daily briefing into a 2-person stocks-club ritual with Jared

## Problem
Jared is currently a passive co-recipient of the daily market-briefing email — he reads it, but there's no interaction loop. Open creative direction (captured from project memory): make the briefing an **interactive 2-person stocks-club ritual** so it becomes something Joe and Jared *do together* rather than just receive.

## Acceptance Criteria
- [ ] Define what the ritual actually is — pick a concrete mechanic:
  - reply-to-vote on the day's picks?
  - a weekly "pick of the week" each person commits to?
  - a running leaderboard / scorekeeping between the two?
- [ ] Spec the smallest viable first version (one mechanic, minimal infra)
- [ ] Implement that first version
- [ ] Keep this as an **idea ticket** until the ritual is scoped — do not start implementation before the mechanic is decided.

## Context & Notes
- Source: project memory note on the Market Mover MCP — Jared is a co-recipient and the stated creative goal is a 2-person stocks-club ritual.
- The briefing already sends to both Joe and Jared via GH Actions cron (6 AM MDT weekdays), so the delivery channel exists; the missing piece is the interaction loop.
- Reply-to-vote is attractive because it reuses email (no new surface), but capturing replies means inbound email parsing — weigh that cost when scoping.
- A leaderboard implies persistent state (where? `.workflow/state.json` pattern, a small store, or a committed scorecard file).
- Consider whether the existing judge/scorecard work (MM-T001) can be reused for ritual scorekeeping.

## Implementation Plan
1. Brainstorm the candidate mechanics with Jared; pick one.
2. Write a short spec (or an ADR if it drives an architecture decision) for the chosen ritual.
3. Identify the minimal infra needed (inbound parsing? state store?).
4. Build the smallest first version and ship it into one real daily/weekly cycle.
5. Gather feedback from one real round before expanding.

## Retrospective
_Fill in when moving to done/: what went well, what didn't, what we'd do differently._
