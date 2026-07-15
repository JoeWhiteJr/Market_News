---
id: MM-T007
title: The Call · Beat the Bot — daily prediction game
status: done
priority: medium
type: feature
owner: joe
assigned-team: Builder
created: 2026-07-12
updated: 2026-07-15
related-pr: "#32, #33"
related-tickets: MM-T002, MM-T006
---

# The Call · Beat the Bot — daily prediction game

## Problem
A creative-brainstorm pass converged on a daily *game* as the top fun-per-effort
add. Joe picked the full "Beat the Bot" v1: the bot makes a graded 24h call AND
the three recipients play via one-tap buttons, with a running human-vs-bot
scoreboard. Turns the one-way email into something the group does together
(the MM-T002 "stocks-club ritual" north star).

## Decision (ADR 0006)
Soft, infra-light. Bot's Call is fully automatic (LLM generates, existing judge
price-window resolves). Humans play via `mailto:` UP/DOWN buttons (Reply-All to
the shared thread); votes are honor-system in `data/predictions.jsonl` for v1.
Inbound auto-tally deferred to Phase 2.

## Acceptance Criteria
- [x] `predictions.py`: `DailyCall` + `PredictionRecord` models, JSONL
      persistence (append/load/rewrite), `resolve_outcome`/`resolve_call`,
      `season_stats`, `build_mailto`, HTML + plain renderers
- [x] `LLMClient.generate_daily_call()` — structured, validated (direction ∈
      {UP,DOWN}, confidence clamped 50–95, ticker upper-cased), Claude→Gemini
- [x] Resolution reuses `fetch_24h_close_change` (ADR 0001 window); ±0.05% PUSH band
- [x] One-tap UP/DOWN `mailto:` buttons reply-all with a structured subject
- [x] Season scoreboard (bot + humans), rendered under the Top 3 stories
- [x] Ledger committed by the workflow; yesterday's row patched in place
- [x] Behind `prediction_game_enabled`; all best-effort (never breaks the send)
- [x] Double-run guard (one Call per date); conftest sandboxes the ledger path
- [x] Tests: resolution, stats, persistence, mailto, render, LLM parse/clamp
- [x] `ruff` clean, full suite green (425 passed)
- [ ] Joe eyeballs the Downloads full preview; plays one real round with Jared/Mia

## Context & Notes
- All three recipients share one `To:` line → Reply-All is a live group thread.
- Bot record is automatic, so the scoreboard lives even if nobody logs votes.
- Known rough edge: honor-system vote entry. Phase 2 = inbound Gmail auto-tally.
- Stacked on MM-T006 (shares email_template/cli/config edits).

## Retrospective
**Shipped 2026-07-15.** Reused the existing judge price-window (`fetch_24h_close_
change`) as the free automatic referee — the whole game rides infrastructure we
already trusted, so it was cheap to build. The bot-always-plays design means the
scoreboard is alive on day one even before any human votes.

**Process miss (fixed):** PR #32 was stacked on the #31 (visuals) branch, and
got merged into that branch instead of `main` — so the game briefly didn't reach
main. Recovered by cherry-picking the identical commit onto main as #33 (zero
conflicts). **Lesson:** don't leave a stacked PR's base pointing at the parent
branch when merging — retarget to `main` first, or don't stack; the 20-second
window between merging #31 and #32 was enough to lose the work.
