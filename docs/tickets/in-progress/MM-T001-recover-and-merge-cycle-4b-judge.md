---
id: MM-T001
title: Recover and merge Cycle 4b Yesterday-Index Judge
status: in-progress
priority: high
type: feature
owner: joe
created: 2026-06-03
updated: 2026-06-03
related-pr: 8
related-tickets:
---

# Recover and merge Cycle 4b Yesterday-Index Judge

## Problem
The finished Cycle 4b judge feature was stranded uncommitted in a local agent worktree and has now been pushed as **PR #8** (JoeWhiteJr/Market_News). It needs review + merge.

The feature is complete and green:
- `src/market_mover/judge.py` (~497 lines)
- `src/market_mover/scorecard.py` (~735 lines)
- `tests/test_judge.py` + `tests/test_scorecard.py` (~1122 lines of tests)
- All passing — **261 tests total**, ruff clean.

Despite being done, the work never made it onto `main` because it lived only in a local agent worktree. PR #8 is the recovery vehicle.

## Acceptance Criteria
- [ ] PR #8 reviewed
- [ ] Confirm whether PR #8 should also bring in the stacked **cycle-4a (#7)** content (which appears absent from `main`)
- [ ] Drop briefing-artifact files that don't belong in source control:
  - [ ] `market_mover_2026-05-18.html`
  - [ ] `market_mover_2026-05-18.txt`
  - [ ] `.workflow/cycle-4a-render-*.html`
  - [ ] `.workflow/cycle_4a_render.py`
- [ ] Squash-merge with a clean commit message
- [ ] Delete the merged branch
- [ ] Delete the ~10 stale locked agent worktrees

## Context & Notes
- Discovered in a portfolio audit on 2026-06-03.
- The judge/scorecard feature is the Cycle 4b output of the `dev-cycle` 4-team loop; it evaluates the "Yesterday-Index" against a rubric (see `docs/adrs/0001-yesterday-index-rubric.md`).
- Open question on stacking: cycle-4a (#7) appears not to be on `main`. Decide whether #8 absorbs it or whether #7 lands first.
- The briefing-artifact files (rendered HTML/TXT briefings and one-off render scripts under `.workflow/`) are generated output, not source — they should not be committed.
- The ~10 stale worktrees are locked; they'll need `git worktree remove --force` (or unlock first) once the branch is merged.

## Implementation Plan
1. Review PR #8 diff against `main`; confirm the judge/scorecard + tests are the intended payload.
2. Diff #7 (cycle-4a) against `main` to determine whether its content is already present; decide stack vs. absorb.
3. Strip the briefing-artifact files from the PR (rebase/drop or follow-up commit).
4. Re-run the suite + ruff to confirm 261 tests still pass post-cleanup.
5. Squash-merge with a clean conventional-commit message.
6. Delete the merged branch (`git push origin --delete <branch>`).
7. Prune the ~10 stale locked agent worktrees (`git worktree list`, then unlock/remove).

## Retrospective
_Fill in when moving to done/: what went well, what didn't, what we'd do differently._
