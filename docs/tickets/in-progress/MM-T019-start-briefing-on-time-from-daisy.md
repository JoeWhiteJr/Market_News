---
id: MM-T019
title: Start the daily briefing on time with a daisy trigger and GitHub backup
status: in-progress
priority: high
type: bug
owner: joe
created: 2026-11-06
updated: 2026-11-06
related-pr:
related-tickets: MM-T011, MM-T010
assigned-team:
---

# Start the daily briefing on time with a daisy trigger and GitHub backup

## Problem
GitHub starts the scheduled run late: 38 of the last 100 were 30+ minutes late, 15 were
2+ hours late, and Nov 3-6 were about 7 hours late. Late runs miss the morning and
enter trades at intraday prices, which distorts the paper results.

## Decisions (Joe, 2026-11-06)
- A daisy timer dispatches the existing workflow at 6 AM Denver year-round. Everything
  else stays on GitHub, so this replaces the full migration in MM-T011.
- GitHub's cron stays as a 13:30 UTC backup and skips if today's run exists.
- A run after the US open still trades, but its ledger row has `late_entry: true`.

## Acceptance Criteria
- [ ] PR merged
- [ ] Dry-run dispatch from daisy starts within about a minute, with no email or trades
- [ ] Timer enabled on daisy only after merge, so the old 12:00 UTC cron can't double-send
- [ ] 5 straight weekdays: dispatched run starts by 6:02 AM Denver, backup skips, one email
- [ ] Close MM-T011 as won't-fix if this holds

## Install on daisy (after merge)
1. Create a fine-grained token: repo `Market_News` only, Actions read and write. Save it
   to `~/.config/market-mover/github-token` with mode 600. It expires, and the backup
   covers that day, so watch for the backup's "daisy's trigger may be down" warning.
2. Copy `trigger-briefing.sh` to `~/market-mover-trigger/`, and the `.service` and
   `.timer` files to `~/.config/systemd/user/`.
3. Run `trigger-briefing.sh --dry-run` and confirm a "Dry run" appears in Actions.
4. `systemctl --user daemon-reload && systemctl --user enable --now market-mover-trigger.timer`

## Retrospective
_Fill in when moving to done/._
