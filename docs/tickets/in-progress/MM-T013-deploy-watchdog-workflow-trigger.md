---
id: MM-T013
title: Deploy the briefing-watchdog workflow trigger (MM-T008 shipped code, not the trigger)
status: in-progress
priority: high
type: fix
owner: joe
assigned-team: Builder
created: 2026-08-14
updated: 2026-08-14
related-pr: 38
related-tickets: MM-T008, MM-T012
---

# Deploy the briefing-watchdog workflow trigger

## Problem
MM-T008 ("Watchdog alert when the daily briefing never runs") was marked **done**
and PR #35 merged — but the actual GitHub Actions trigger,
`.github/workflows/briefing-watchdog.yml`, **never landed on `main`**. It sat
untracked in the working tree across sessions. A stale `git log origin/main -- <path>`
check returned exit 0 with *empty* output (git log succeeds even when it matches
no commits), which was misread as "committed on main" — a false positive that hid
the gap until MM-T012.

**Impact:** the watchdog has not been running. If GitHub silently drops the
scheduled briefing (as it did on 2026-07-11) — no run, no failure, no logs —
**nothing currently alerts anyone.** That's the exact failure mode MM-T008 was
built to catch, and it's been unguarded.

## Decision
Commit the existing, complete workflow file to `main` (no code changes needed —
it's self-contained). Verify it's actually tracked on `origin/main` this time
with `git ls-tree`, not `git log`.

## What the workflow does (unchanged from MM-T008)
- Runs at **17:30 UTC weekdays** (11:30 AM MDT) — well after the 12:00 UTC
  briefing and any plausible late GitHub start.
- Uses `gh run list --workflow daily-briefing.yml --created <today>` to classify:
  `ok` (≥1 success) / `missed` (zero runs exist) / `pending` (late run in flight)
  / `failed-elsewhere` (ran, none succeeded — briefing's own alerts fire).
- On **`missed`** only: opens a GitHub issue *and* emails the operator via stdlib
  SMTP (same best-effort pattern as `daily-briefing.yml`). Never duplicates the
  briefing's own failure alerts.

## Acceptance Criteria
- [x] `.github/workflows/briefing-watchdog.yml` committed to the feature branch
- [x] Polls the correct workflow filename (`daily-briefing.yml`) — verified present
- [x] Reuses secrets that already exist (`SMTP_USERNAME`, `SMTP_APP_PASSWORD`,
      auto `GITHUB_TOKEN`) — same names `daily-briefing.yml` uses
- [x] YAML validates (`yaml.safe_load`)
- [ ] Merged to `main` and confirmed tracked via `git ls-tree -r origin/main`
      (NOT `git log`)
- [ ] `workflow_dispatch` manual run triggers green (or a clean `ok`/`missed`
      verdict) from the Actions tab

## Context & Notes
- Root-cause lesson also logged: verify file-on-branch presence with
  `git ls-tree`/`git cat-file`, never `git log <path>` alone (empty match still
  exits 0). Worth a post-mortem via `/ticketing:lesson`.
- No Python module is involved — MM-T008's watchdog is entirely this YAML. So the
  only artifact that was missing is the trigger itself.

## Retrospective
_Fill in when moving to done/._
