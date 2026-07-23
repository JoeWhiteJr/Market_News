---
id: MM-T008
title: Watchdog alert when the daily briefing never runs
status: done
priority: medium
type: chore
owner: joe
assigned-team: Builder
created: 2026-07-15
updated: 2026-07-15
related-pr:
related-tickets:
note: "Renumbered from MM-T007 → MM-T008 on 2026-07-15 to resolve an ID collision with the prediction-game ticket (both grabbed MM-T007 in parallel sessions)."
---

# Watchdog alert when the daily briefing never runs

## Problem
On 2026-07-11 (a Friday) the daily briefing simply never happened: GitHub
dropped the scheduled run — no workflow run exists for that date, no data
commit, no email. Because the run never *started*, `daily-briefing.yml`'s
failure alerts (issue + operator email) never fired. The miss was only noticed
during a manual health check the next day. A silent-miss can currently go
unnoticed indefinitely — the same failure mode as the 2026-06-16..23
retired-model outage, but invisible even to the failure alerting added since.

## Acceptance Criteria
- [x] A separate scheduled workflow (`briefing-watchdog.yml`) checks each
      weekday whether a daily-briefing run exists for today (UTC)
- [x] Check fires well after the worst observed scheduled-start delay
      (15:41 UTC on 2026-06-29) → 17:30 UTC
- [x] **No run at all** → opens a `[daily-briefing MISSED]` issue AND emails
      the operator (same best-effort stdlib SMTP pattern as the failure alert)
- [x] Run exists but failed → stays silent (daily-briefing.yml's own failure
      alerts already fired; no duplicate noise)
- [x] Run still pending at check time → stays silent (defers to the briefing's
      own alerts)
- [x] Alert includes the manual-recovery command
      (`gh workflow run daily-briefing.yml`)
- [x] Reuses existing secrets only (`SMTP_USERNAME`, `SMTP_APP_PASSWORD`,
      `GITHUB_TOKEN`) — no new credentials

## Retrospective
**Shipped 2026-07-15.** The gap was structural: failure alerting can only fire
from inside a run, so a run that never starts is invisible to it. A watchdog is
itself a scheduled workflow and could also be dropped, but the two schedules
are 5.5 hours apart and both being dropped the same day is far less likely than
one. If GitHub-side drops become frequent, the escalation path is an external
monitor (e.g. healthchecks.io ping from the briefing run) — deliberately not
built now to avoid a new external dependency for a once-observed event.
