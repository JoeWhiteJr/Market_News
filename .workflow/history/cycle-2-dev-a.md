# Cycle 2 — Developer A log

**Branch:** `fix/cycle-2-correctness`
**Date:** 2026-05-14

## Tasks shipped

### Task 1 — Gemini fallback regression (QA P0)
- Added `EmptyLLMResponse` exception in `src/market_mover/exceptions.py`
  (subclass of `LLMError`, deliberately not `AnalysisParsingError`).
- `_extract_text_from_anthropic_message()` in `src/market_mover/llm_client.py`
  now **raises** `EmptyLLMResponse` instead of returning the
  `NO_TEXT_SENTINEL` string. The sentinel constant was removed.
- `analyze_articles()`'s broad `except Exception` block now catches
  `EmptyLLMResponse` and falls through to Gemini. The Cycle 1 bug
  (sentinel -> `AnalysisParsingError` -> re-raised before Gemini ran)
  is fixed at the root.
- New test file `tests/test_llm_client_fallback.py` with 3 tests covering:
  thinking-only Anthropic response falls back to Gemini; empty-content
  Anthropic response falls back to Gemini; and an assertion that
  `EmptyLLMResponse` is a subclass of `LLMError` (not
  `AnalysisParsingError`).
- Updated `tests/test_email_polish.py::TestLlmClientGuard` to expect
  the raise behavior instead of sentinel returns.

### Task 2 — Parallel source fetches (Scout P2)
- `_gather_articles()` in `src/market_mover/cli.py` now uses
  `concurrent.futures.ThreadPoolExecutor(max_workers=4)` to fan the
  four source fetchers out in parallel.
- Per-source exception capture is preserved (errors dict still works
  for degraded-mode email).
- The 30s `socket.setdefaulttimeout` backstop propagates into worker
  threads automatically.
- New test `TestParallelGather::test_sources_are_fetched_in_parallel`
  in `tests/test_cli_reliability.py` mocks 4 sources to sleep 1s each
  and asserts gather finishes in &lt;1.5s.
- Manual dry-run confirmed: 4×0.5s sleeps complete in 0.50s
  (vs 2.0s sequential) — no deadlock.

### Task 3 — Dedupe refactor (auditor P1)
- `_deduplicate_articles()` in `src/market_mover/server.py` rewritten as a
  single-pass O(n) implementation using `seen_urls: set[str]` and
  `seen_titles: set[str]`. First-seen wins.
- Added a `_normalize_title()` helper that lower-cases, strips
  non-alphanumerics, and collapses whitespace — so titles that differ
  only in punctuation/capitalization collapse to one.
- Removed `difflib.SequenceMatcher` (and the dead URL-filter branch at
  the old line 195).
- Old `test_removes_exact_url_duplicates` updated: first-seen wins now
  (vs longer-summary-wins). Old `test_removes_similar_titles`
  replaced with `test_removes_titles_differing_only_by_punctuation`
  (the new exact-after-normalization semantic). Added the two
  spec-requested tests for the formerly-dead branches:
  `test_same_title_different_url_collapses_to_one` and
  `test_same_url_different_title_collapses_to_one`.

### Task 4 — GHA action bumps
- `actions/checkout@v4` -> `@v6` (latest stable per
  `gh api repos/actions/checkout/releases/latest`: v6.0.2).
- `actions/setup-python@v5` -> `@v6` (latest stable: v6.2.0).
- Both run on Node 24 so the June 2 2026 Node 20 deprecation is
  pre-empted.

## Test & lint
- `python3 -m pytest tests/ -x` — **73 passed**, 11 warnings, all pre-existing.
- `python3 -m ruff check` on touched files — **all checks passed**.
- One unrelated `I001` issue remains in `tests/test_sources.py` (not in
  Cycle 2 scope; pre-existing in main).

## Notes / decisions
- Added `pythonpath = ["src"]` to `pyproject.toml`'s pytest section.
  This was necessary because in worktree mode the package is installed
  editably from the main repo, so without `pythonpath` pytest imports
  the parent-repo `market_mover` instead of the worktree copy.
  This is a tiny test-config-only change, safe to keep upstream — it
  is the canonical pytest pattern for `src/` layouts.
- The `NO_TEXT_SENTINEL` constant is removed entirely (was unused
  outside the extractor and its tests). Anyone who imported it would
  see an ImportError — but nothing outside `llm_client.py` and its
  tests referenced it.
