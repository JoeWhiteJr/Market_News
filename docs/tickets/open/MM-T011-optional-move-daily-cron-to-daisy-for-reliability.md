---
id: MM-T011
title: "Optional: move the daily-briefing cron to daisy (reliability, not cost)"
status: open
priority: low
type: infrastructure
owner: joe
created: 2026-08-13
updated: 2026-08-13
related-pr:
related-tickets: MM-T008
assigned-team:
---

# Optional: move the daily-briefing cron to daisy (reliability, not cost)

## Problem
**This migration saves $0** — the repo is public, so GitHub Actions minutes, Pages, and secret storage are all free. The only argument is schedule determinism: GitHub delayed the 12:00 UTC run to 15:41 on 2026-06-29 and silently never started it on 2026-07-11 (documented in `briefing-watchdog.yml:5-8`; MM-T008 exists purely to detect this). A systemd timer on daisy fires on time, every time, and the watchdog becomes unnecessary. Decide whether the reliability is worth the migration work; closing this as won't-fix is a legitimate outcome.

## Approach
If done: clone repo on daisy, `.env` with all 12 secrets, systemd timer at 12:00 UTC weekdays. The pipeline itself (80–130 s, pure outbound HTTPS + Gmail SMTP:587) ports with zero changes. The work is entirely in three GitHub-coupled seams:

1. **State round-trip** — Actions commits `data/*.jsonl` back to `main` each run. On daisy the working dir is persistent, so the push is only needed to keep **GitHub Pages** (`docs/scores.html`) updating. Either keep pushing (deploy key with `contents: write`) or serve the scores page from daisy's nginx instead.
2. **Failure alerting** — the SMTP alert code lives as inline heredoc Python *inside the workflow YAML*, not in `src/`; it must be lifted into a wrapper script or it's silently lost. The GitHub-issue-on-failure path needs a PAT or gets dropped in favor of email.
3. **Watchdog** — `gh run list`-based check is meaningless off Actions; replace with healthchecks.io or a second timer, or delete.

## Acceptance Criteria
- [ ] Go/no-go decided (won't-fix acceptable — record why)
- [ ] If go: timer runs green 5 consecutive weekdays; email arrives at 6:00 AM MDT sharp
- [ ] If go: scores page still updates (push from daisy, or served locally)
- [ ] If go: failure alert path tested (kill a run on purpose); Actions workflows disabled to avoid double-sends
- [ ] Note: local `.env` is missing `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY` (they exist only as GitHub secrets, which can't be read back) — fetch fresh from Alpaca when provisioning

## Context & Notes
From the 2026-08-13 server-consolidation review. Counterweight: daisy is a single point of failure with no SLA, and this is the one system with an unbroken daily-delivery streak to Joe + Jared — moving it trades a rare-lateness problem for a new-single-point-of-failure problem. That's why this is optional/low.

## Retrospective
_Fill in when moving to done/._
