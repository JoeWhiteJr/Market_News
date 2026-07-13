# ADR 0006 — The Call · Beat the Bot (prediction game)

**Status**: **Accepted** — 2026-07-12 (Joe)
**Decides**: How the daily prediction game works — the bot's Call, how humans
play, how it's scored, and what state it persists.

## Context
A creative-brainstorm pass (three agents) converged on a daily *game* as the
single highest fun-per-effort addition, and it fits Market Mover's existing
"keeps score on itself" identity. Joe chose the full "Beat the Bot" v1: the bot
makes a graded call AND humans play, over a bot-only MVP.

The hard constraint: delivery is a one-way HTML email; email clients run no JS.
True interactivity needs an inbound channel, which we do not have.

## Decision

### The Call (fully automatic)
One LLM call turns today's Top 3 into a single falsifiable 24h prediction —
`{ticker, direction: UP|DOWN, confidence: 50-95, statement}`. It's resolved the
next morning against real close-to-close price action via the **existing judge
window** (`fetch_24h_close_change`), so the bot has a live, self-updating record
with zero new infrastructure. `resolve_outcome`: move beyond a ±0.05% PUSH band
in the predicted direction = HIT, against = MISS, inside = PUSH (nobody scores).

### Humans play by mailto + honor system (v1)
Each email renders one-tap **UP / DOWN `mailto:` buttons** that reply to the
shared group thread with a structured subject (`MM 2026-07-12 MU: UP`). All
three recipients are on one `To:` line, so Reply-All is already a group thread —
the cheapest interactive surface in the project.

Human votes are **honor-system**: they live in `data/predictions.jsonl`
(`human_calls`), and the pipeline renders whatever is there. The BOT's record is
always automatic, so the scoreboard is live even on days nobody logs a vote.
**Deliberately deferred:** an inbound Gmail-reading job that auto-tallies replies
(new auth + parser + dedup + state) — a clean Phase 2 once the game has legs.

### State (append-only, committed)
One `PredictionRecord` per day in `data/predictions.jsonl`: the Call, its later
`outcome`/`pct_change`, and `human_calls`. The workflow commits it back like the
other ledgers. Resolving yesterday patches its row in place (`rewrite_predictions`).

### Scoring
`season_stats` returns `{name: (wins, losses)}` for the Bot and every human who
has voted; PUSH and unresolved days score for nobody. Sorted by win rate.

## Consequences
- Fully functional day one on the bot's record alone; the human competition is
  opt-in and grows as people play (and as Joe logs votes).
- Best-effort throughout: any failure (LLM, price fetch, parse) hides the block
  and never touches the send.
- The honor-system step is the known rough edge; Phase 2 (auto-tally) removes it.

## Not in scope
- No inbound email parsing yet (Phase 2).
- No real-money or paper-trade linkage — this is a game, explicitly not advice.
- No change to the judge rubric (ADR 0001) — the Call uses the same *price
  window* but its own simple directional HIT/MISS, not the LLM judge.
