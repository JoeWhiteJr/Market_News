# ADR 0004 — Learning loop, Phase 0: Bayesian-pooled performance

**Status**: **Accepted** — 2026-06-19 (Joe)
**Decides**: How we estimate per-category pick performance from the graded
history so the agent can eventually learn from it.

## Context

The Yesterday-Index judge grades every pick (ADR 0001) and the paper engine
records P&L (ADR 0003), but **nothing consumes that history** to improve future
decisions. With only a handful of graded days, raw per-category hit-rates are
useless — e.g. `single_name` at 0/5 = 0% reads as "worthless" when it's really a
tiny, unlucky sample. We need estimates that are honest about uncertainty and
that don't over-react to small n.

Phase 0 is **measurement only**: compute and report. No feedback into ranking or
sizing yet (those are later, separately-gated phases).

## Decision

### Verdict → success score (LOCKED)
`HIT = 1.0`, `PARTIAL = 0.5`, `MISS = 0.0`. `TOO_EARLY` and `NOT_APPLICABLE` are
**excluded from the denominator** — the pick couldn't be scored, so it's not
evidence about category quality. Changing this mapping re-bases all historical
comparisons (treat like a `judge_prompt_version` bump).

### Model: Beta-Binomial partial pooling (LOCKED shape)
Per category *k*, the success rate `θ_k ~ Beta(α_k, β_k)` with a prior centered
on the **global** success rate `m` (empirical-Bayes-lite) at `prior_strength`
(κ) pseudo-observations:
- `α₀ = m·κ`, `β₀ = (1−m)·κ`
- posterior: `α_k = α₀ + Σscores_k`, `β_k = β₀ + (n_k − Σscores_k)`
- report: posterior mean `α_k/(α_k+β_k)` + a 90% equal-tailed credible interval
  (pure-Python regularized incomplete beta + bisection inverse — no numpy/scipy).

Small-n categories shrink hard toward `m`; well-sampled ones barely move. This
is the right tool *because* data is scarce.

### Implementation
- `src/market_mover/learning.py` — the pooling, the incomplete-beta math, and a
  plain-text readout. Standalone CLI: `python3 -m market_mover.learning`.
- Logged once per pipeline run (`cli.py`, after persistence) — never affects the
  send.
- Paper ledger now carries `category` on opened + closed records
  (`paper_trading.py`) so P&L pools without a join. **Additive metadata — the
  paper *strategy* (ADR 0003) is unchanged, so no track-record reset.**

## What's locked vs iterable
- **Locked**: the verdict→score mapping; excluding TOO_EARLY/N/A; the
  Beta-Binomial partial-pooling shape; the credible-interval definition.
- **Iterable**: `prior_strength` (κ, default 4.0), `window_days` (default 0 =
  all history), the readout's wording/where it's shown, and which *additional*
  features get pooled later (model, voice, event-type, direction).

## Out of scope (future, separately gated)
- **Feedback into ranking** — inject a calibration note into the ranking prompt
  (the `MACRO_BIAS_INSTRUCTION` pattern; not locked). Gated on the estimates
  becoming informative.
- **Per-category position sizing** — collides with ADR 0003's equal-weight lock;
  needs its own ADR + a paper track-record reset.
- **Richer multi-axis classification** (event type, direction, horizon) — the
  foundation for finer pooling; its own taxonomy ADR.

## Sign-off
- [x] Joe approves Beta-Binomial partial pooling as the estimator — 2026-06-19
- [x] Joe approves the HIT=1 / PARTIAL=0.5 / MISS=0, exclude-TOO_EARLY/N/A scoring — 2026-06-19
- [x] Joe approves Phase 0 = measurement only (no feedback yet) — 2026-06-19
- [x] Joe approves adding `category` to the paper ledger (additive, no reset) — 2026-06-19
