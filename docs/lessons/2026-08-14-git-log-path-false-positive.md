# Lesson: a "done" feature whose trigger never deployed

**Date**: 2026-08-14 (Joe)
**Severity**: medium (a safety net was silently absent, not a live outage)
**Related**: MM-T008 (original watchdog), MM-T013 (the deploy fix), MM-T012 (where it surfaced)

## What happened
MM-T008 ("watchdog alert when the daily briefing never runs") was marked **done**
and its PR merged. Weeks later, during MM-T012, we found the actual GitHub Actions
trigger, `.github/workflows/briefing-watchdog.yml`, had **never landed on `main`**.
It sat untracked in the working tree the whole time. The watchdog was not running,
so from the day MM-T008 "shipped" until 2026-08-14 there was **no alert** if GitHub
silently dropped the scheduled briefing (the exact failure mode that happened on
2026-07-11).

## Root cause
The deployment was verified with:

```bash
git log origin/main --oneline -1 -- .github/workflows/briefing-watchdog.yml && echo "committed"
```

`git log <path>` **exits 0 even when it matches zero commits** (empty output,
success status). Chained with `&&`, the `echo "committed"` fired on an empty
result. An absence of history was read as presence of the file. The feature looked
shipped while its only artifact never left the working tree.

Contributing: the workflow was self-contained YAML with no Python module to import,
so nothing else failed loudly to signal the gap. A silent safety net gives no
feedback when it is missing, by definition.

## The fix
Deployed the file (MM-T013, PR #38) and verified the right way:

```bash
git ls-tree -r origin/main --name-only | grep briefing-watchdog   # lists it
git cat-file -e origin/main:.github/workflows/briefing-watchdog.yml # exit 0 iff present
```

Then confirmed behavior end to end with a live `workflow_dispatch` run: verdict
`ok`, issue and email steps correctly skipped, no false alarm.

## Takeaways
1. **To check whether a file is on a branch, ask the tree, not the log.** Use
   `git ls-tree` / `git cat-file -e <ref>:<path>`. Never rely on `git log <path>`
   alone: an empty match still exits 0 and reads as success.
2. **Verify against the remote ref, not a local checkout.** The bug was over-trust
   in local state; the fix had to avoid the same trap by checking `origin/main`.
3. **Silent safety nets need an explicit liveness check.** A monitor that only acts
   on failure looks identical whether it is deployed or absent. Prove it runs (a
   manual dispatch, a heartbeat) at deploy time, not the next time it is needed.
4. **A guard `&&`-chained to an `echo` is not a test.** If the command can succeed
   with empty output, the echo lies. Assert on the output, or use a command whose
   exit code actually encodes presence.

## Guard for next time
When closing any ticket whose deliverable is a deployed file (workflow, config,
migration, cron), the definition of done includes: confirmed on the target branch
via `git ls-tree`/`git cat-file`, and (for anything runnable) one real execution
observed green.
