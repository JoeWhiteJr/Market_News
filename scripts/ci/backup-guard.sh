#!/usr/bin/env bash
# Decides whether a daily-briefing run should send. Writes skip=true|false to GITHUB_OUTPUT.
# Scheduled runs are only a backup for daisy's dispatched run (MM-T019, MM-T022).
set -euo pipefail

# After this, a run could cross midnight UTC and write tomorrow's ledger rows.
readonly SEND_CUTOFF_UTC="23:30"

# shellcheck source=scripts/ci/briefing-window.sh
source "$(dirname "${BASH_SOURCE[0]}")/briefing-window.sh"

set_skip() { echo "skip=$1" >> "$GITHUB_OUTPUT"; }

if [ "${DRY_RUN:-}" = "true" ]; then
  echo "Dry run: trigger received, skipping the briefing."
  set_skip true
  exit 0
fi

if [ "$GITHUB_EVENT_NAME" != "schedule" ]; then
  set_skip false
  exit 0
fi

# If this lookup fails, count zero runs: a duplicate email beats no email.
RUNS=$(gh run list --workflow daily-briefing.yml --event workflow_dispatch \
  --created "$BRIEFING_CREATED" --limit 50 --json status,conclusion,displayTitle) || RUNS='[]'
ACTIVE=$(python3 -c '
import json, sys
print(sum(r["displayTitle"] != "Dry run"
          and (r["conclusion"] == "success" or r["status"] != "completed")
          for r in json.load(sys.stdin)))' <<< "$RUNS")

if [ "$ACTIVE" -ge 1 ]; then
  echo "The dispatched run for ${BRIEFING_DAY} exists, so the backup has nothing to do."
  set_skip true
  exit 0
fi

today=$(date -u -d "@${BRIEFING_NOW}" +%Y-%m-%d)
clock=$(date -u -d "@${BRIEFING_NOW}" +%H:%M)
if [ "$today" != "$BRIEFING_DAY" ] || [[ ! "$clock" < "$SEND_CUTOFF_UTC" ]]; then
  echo "::error::No dispatched run for ${BRIEFING_DAY}, and this backup started too late to send (${today} ${clock} UTC)."
  set_skip true
  gh issue create --title "[daily-briefing MISSED] ${BRIEFING_DAY}" \
    --body "No successful dispatched run for ${BRIEFING_DAY}, and the backup started too late to send (${today} ${clock} UTC). Check daisy's market-mover-trigger." \
    || echo "::warning::Could not open the missed-briefing issue."
  exit 1
fi

echo "::warning::No dispatched run for ${BRIEFING_DAY}, so daisy's trigger may be down. Backup is sending the briefing."
set_skip false
