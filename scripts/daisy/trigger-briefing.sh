#!/usr/bin/env bash
# Starts the Daily Market Briefing workflow on GitHub. Runs from the systemd timer.
# Usage: trigger-briefing.sh [--dry-run]
set -euo pipefail

readonly DISPATCH_URL="https://api.github.com/repos/JoeWhiteJr/Market_News/actions/workflows/daily-briefing.yml/dispatches"
readonly TOKEN_FILE="${HOME}/.config/market-mover/github-token"
readonly RETRIES=5
readonly RETRY_DELAY_SECS=20
readonly REQUEST_TIMEOUT_SECS=30

dry_run=false
if [ "${1:-}" = "--dry-run" ]; then
  dry_run=true
fi

token="$(tr -d '\r\n' < "${TOKEN_FILE}")"

# Pass the header through --config so the token never appears in the process list.
curl --fail --silent --show-error \
  --retry "${RETRIES}" --retry-all-errors --retry-delay "${RETRY_DELAY_SECS}" \
  --max-time "${REQUEST_TIMEOUT_SECS}" \
  --config <(printf 'header = "Authorization: Bearer %s"\n' "${token}") \
  --header "Accept: application/vnd.github+json" \
  --header "X-GitHub-Api-Version: 2022-11-28" \
  --data "{\"ref\":\"main\",\"inputs\":{\"dry_run\":\"${dry_run}\"}}" \
  "${DISPATCH_URL}"

echo "Dispatched daily-briefing.yml (dry_run=${dry_run})"
