#!/usr/bin/env bash
# Source this to get the briefing day a run belongs to (MM-T022).
# GitHub can start scheduled runs many hours late, past midnight UTC, so a run's own
# UTC date can be the wrong day. A briefing day runs from 11:00 UTC to the next 11:00,
# which is before daisy's trigger (12:00 UTC summer, 13:00 winter) and every cron here.
# Sets BRIEFING_NOW (epoch), BRIEFING_DAY, and BRIEFING_CREATED (for gh run list --created).
readonly DAY_START_HOUR_UTC=11
readonly SECS_PER_HOUR=3600
readonly SECS_PER_DAY=86400

BRIEFING_NOW="${GUARD_NOW_EPOCH:-$(date -u +%s)}"
_day_epoch=$((BRIEFING_NOW - DAY_START_HOUR_UTC * SECS_PER_HOUR))
BRIEFING_DAY=$(date -u -d "@${_day_epoch}" +%Y-%m-%d)
_next_day=$(date -u -d "@$((_day_epoch + SECS_PER_DAY))" +%Y-%m-%d)
BRIEFING_CREATED="${BRIEFING_DAY}T${DAY_START_HOUR_UTC}:00:00Z..${_next_day}T$((DAY_START_HOUR_UTC - 1)):59:59Z"
