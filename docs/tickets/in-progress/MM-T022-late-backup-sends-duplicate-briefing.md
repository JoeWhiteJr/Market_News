---
id: MM-T022
title: Late GitHub backup sends a duplicate briefing after midnight UTC
status: in-progress
priority: high
type: bug
owner: joe
created: 2026-11-24
updated: 2026-11-24
related-pr:
related-tickets: MM-T019, MM-T008, MM-T013
assigned-team:
---

# Late GitHub backup sends a duplicate briefing after midnight UTC

## Problem
Monday's backup (cron 13:30 UTC, 2026-11-23) started at 01:05 UTC on 11-24. The guard
looked up runs by the UTC date, found none for 11-24, and sent a second briefing to all
recipients. It also took the 11-24 paper-trading and shadow slots, so Tuesday's 6 AM run
skipped both. The run placed no new orders: NVDA, CRWD and SPY were all carried.
The watchdog had the same date bug (it ran at 01:56 UTC and checked the wrong day).

## Acceptance Criteria
- [x] Guard and watchdog use the day the run belongs to, not the UTC date it started
- [x] Regression test reproduces the incident: fails on the old logic, passes on the new
- [x] A backup too late to send (after 23:30 UTC) never sends. If that day's dispatched run
      also failed or never ran, it opens a MISSED issue and fails the job
- [x] Full test suite and ruff pass
- [ ] PR merged
- [ ] Next weekday: dispatched run sends, backup skips, watchdog passes

## Data
`data/briefings.jsonl` has two 2026-11-24 rows: the late run, then the 6 AM run. Both are
left in place. `load_yesterday` reads only the last row, so the next run grades the 6 AM
row and the late row stays ungraded. Paper-trade and shadow rows for 11-24 belong to the
late run and already have `late_entry: true`. MM-T021 should account for this.

## Retrospective
_Fill in when moving to done/._
