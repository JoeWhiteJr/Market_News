---
id: MM-T017
title: Daily briefing down until Sep 1 - Anthropic usage cap hit, Gemini fallback truncates
status: open
priority: high
type: bug
owner: joe
created: 2026-08-26
updated: 2026-08-26
related-pr:
related-tickets: MM-T014
assigned-team:
---

# Daily briefing down until Sep 1 - Anthropic usage cap hit, Gemini fallback truncates

## Problem
The 2026-08-26 run failed (run 32967074472, auto-issue #42) and every weekday run
through Aug 31 will fail the same way unless fixed. Two stacked causes:

1. **Anthropic key hit its configured usage limit**: API returns 400
   "You have reached your specified API usage limits. You will regain access on
   2026-09-01 at 00:00 UTC." So `_call_claude` fails every run until Sep 1.
2. **The Gemini fallback produced unparseable output**: `analyze_articles` raised
   `AnalysisParsingError` at `llm_client.py:507`. The parser already handles markdown
   fences and brace extraction, so the fenced JSON itself never closed, i.e. the
   response was truncated. `gemini-2.5-flash` is a thinking model; its thought tokens
   count against `max_output_tokens`, which is the shared `max_tokens = 4096`
   (config.py:125) sized for Claude. The legacy `google.generativeai` SDK in use is
   deprecated (warning in the run log) and does not expose thinking-budget control.

Impact while broken: no briefing emails, no data ledger rows (briefings/paper trades/
predictions gap), no latest.json for the Wasden Watch handoff (blocks MM-T014
verification), auto-failure issues pile up.

## Acceptance Criteria
- [ ] Gemini fallback returns parseable JSON reliably: set
      `response_mime_type="application/json"` on the generation config, give the
      Gemini call its own output budget (e.g. 8192) instead of sharing Claude's
      `max_tokens`, and cap/disable thinking (requires migrating `_call_gemini` to
      `google.genai`, which also clears the deprecation warning)
- [ ] Regression test: truncated/fenced/unterminated responses surface a clear error
      identifying truncation (finish_reason MAX_TOKENS) rather than a generic parse fail
- [ ] Verified by a green run before 2026-09-01 (manual `workflow_dispatch` is fine)
- [ ] Joe decision (not code): raise or wait out the Anthropic usage cap; either way
      the fallback must actually work, that is its whole job
- [ ] Close stale auto-failure issues #22, #23 (June) and #42 once green

## Notes
Found 2026-08-26 by the scheduled MM-T014 verification (latest.json 404 because the
pipeline died at Step "analyze", well before publish Step 7). Not caused by the
latest.json code, which has not yet executed in production.

## Retrospective
(fill on close)
