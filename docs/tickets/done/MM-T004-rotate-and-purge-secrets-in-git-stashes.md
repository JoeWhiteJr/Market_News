---
id: MM-T004
title: Rotate and purge secrets in git stashes
status: done
priority: medium
type: security
owner: joe
created: 2026-07-09
updated: 2026-07-09
related-pr:
related-tickets:
---

# Rotate and purge secrets in git stashes

> **2026-07-09 — credentials rotated; live exposure closed.** The values that
> were in the stash are now DEAD (see Acceptance Criteria). The cleartext
> secrets have been redacted from this ticket so it is safe to commit. Remaining
> work is hygiene only: drop the dangling stashes and add a `gitleaks` guard.

## Problem
The cycle-1 auditor report, captured in git stash object `438f764`
(`.workflow/history/cycle-1-auditor.md`, reachable via `stash@{0..1}` on the
`reliability-timeouts` branch), contained the full Gmail app password
(`SMTP_APP_PASSWORD=[REDACTED — rotated 2026-07-09]`) and a full-length Finnhub
key (`[REDACTED — rotated 2026-07-09]`), plus prefixes of the
Anthropic/Gemini/NewsAPI/YouTube keys. Verified NOT present in any commit
reachable from `origin/main` — exposure was local-disk only; stash objects are
never pushed. Rotation is now complete (2026-07-09), so those stash values no
longer authenticate against anything.

## Acceptance Criteria
- [x] Fully-exposed credentials rotated (Gmail app password, Finnhub) — done 2026-07-09; GitHub Actions secrets updated same day
- [x] Prefix-only credentials reviewed: Anthropic, Google AI Studio, YouTube rotated 2026-07-09; **NewsAPI intentionally NOT rotated** (free-tier key, prefix-only exposure — accepted risk, GH secret left current)
- [x] Cleartext secret values redacted from this ticket so it is safe to commit
- [x] All three stale git stashes are dropped — done 2026-07-09; the 2 non-secret WIP stashes were archived as patches to `/home/joe/market-mover-stash-archive-2026-07-09/` before dropping
- [x] `git gc --prune=now` is run to expire the now-unreferenced loose objects — done; the secret blob `425661ca6fe1a315a1bd2f0c776064e63fae83d7` is confirmed GONE from the object store
- [x] `gitleaks` added as a pre-commit hook (`.pre-commit-config.yaml`, pinned v8.30.0) — PR #28; baseline scan of the full tree passes clean

## Context & Notes
Source: `/home/joe/AUDIT_2026-07-09/market-mover-mcp.md`, Finding 1 ("[HIGH] secret — git stash object 438f764"). No Opus reconciliation report exists for this repo (`market-mover-mcp_opus.md` was not produced); treat the Fable-pass finding as-is. Pushed git history is confirmed clean of secret values by the same report.

## Implementation Plan
1. Check credential-provider dashboards (Gmail, Anthropic console, Google AI Studio, NewsAPI, Finnhub, YouTube) for the last rotation/creation date of each key referenced in the stash
2. Rotate any credential not confirmed rotated since 2026-05-13; update the working `.env` with new values
3. `git stash list` to confirm the 3 stashes, then `git stash drop` each
4. `git gc --prune=now` to expire the dangling objects
5. Add `gitleaks` as a pre-commit hook (and CI step) with a baseline scan of the current repo to confirm no secrets remain reachable

## Retrospective
**Resolved 2026-07-09.** Full remediation in one pass:
- Joe rotated 5 of 6 credentials (Gmail app password, Anthropic, Google AI Studio, Finnhub, YouTube) and updated the GitHub Actions secrets same day. NewsAPI intentionally left (free-tier, prefix-only exposure).
- The 3 dangling stashes were dropped after archiving the 2 non-secret WIP stashes to `/home/joe/market-mover-stash-archive-2026-07-09/`. `git gc --prune=now` expired the secret blob out of the object store (verified gone).
- `gitleaks` pre-commit guard added (PR #28) so this can't recur.

**What went well:** the values were already dead by the time we purged, so there was never a race. Archiving WIP-as-patches before dropping meant zero risk of losing unfinished work.

**Do differently:** the exposure sat unremediated from ~2026-05-13 to 2026-07-09 because the original cycle-1 finding lived only in a stashed report nobody re-read. The gitleaks hook + a tracked ticket close that gap — surface security findings as tickets immediately, never leave them in ephemeral workflow state.

**Note:** this ticket file only became safe to commit *after* the cleartext values were redacted (done this session). The `docs/tickets/` dir is still untracked in git — decide whether to start committing tickets now that MM-T004 is clean.
