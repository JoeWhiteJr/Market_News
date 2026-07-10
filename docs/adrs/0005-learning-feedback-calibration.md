# ADR 0005 — Learning loop, Phase 1: soft calibration feedback

**Status**: **Accepted** — 2026-07-10 (Joe)
**Decides**: How the graded category track-record (ADR 0004) first feeds back
into the ranking, closing the measurement→decision loop.

## Context

ADR 0004 gave us Phase 0 — Bayesian-pooled per-category hit-quality, computed
and logged every run, but consumed by nothing. The dashboard (MM-T003) made that
signal visible. The obvious next step is to let the agent *act* on it.

But the data is still thin and noisy. At n≈53 graded picks the category 90%
credible intervals overlap heavily (geopolitical [22–62%] vs single_name
[14–43%] vs macro [10–39%]). We do **not** yet have statistical evidence that
category reliably predicts hit-quality. A mechanical score-weighting loop
(multiply impact by a category posterior) would therefore:

1. **Overfit noise** — a 41% geopolitical estimate off 12 picks regresses hard.
2. **Contaminate the measurement** — biasing picks toward a category changes the
   category mix, destroying the natural experiment that generates the signal.
3. **Collapse diversity** — fewer distinct kinds of picks, a worse product.
4. **Be circular** — tuning rankings on the judge and grading with the judge.

## Decision

### Soft, not mechanical (Phase 1)
Feed the track-record into the ranking **prompt** as *calibration context*. The
LLM reads how its past picks actually performed by category and tempers its own
confidence. There is **no arithmetic change** to `impact_score`; the model
reasons about the numbers qualitatively.

### Framing is calibration, NOT preference (LOCKED intent)
The block explicitly says: keep picking the genuinely most market-moving
stories; be more selective / lower-confidence where the hit-rate has been weak;
never let this override a clearly dominant story. This preserves pick diversity —
the feature adjusts *confidence*, not *category quotas*.

### Guardrail: `min_n` threshold
Only categories with ≥ `learning_feedback_min_n` (default **8**) graded picks
appear in the prompt. Thin categories (e.g. commodity n=3) are omitted rather
than presented as signal. If no category clears the bar, the block is empty and
the prompt is byte-for-byte the feedback-off baseline.

### Reversible + measurable (the point)
- Behind `learning_feedback_enabled` (default on). Flip off → baseline instantly.
- Every briefing record logs **`learning_feedback_active: bool`** (additive,
  backward-compatible; absent on all pre-2026-07 rows → defaults False). This is
  the baseline instrumentation: all ~53 historical picks are feedback-OFF, every
  pick from toggle-on forward is feedback-ON, so lift is measurable by splitting
  the ledger on this flag.

## How we'll know it worked (success criteria)
After the dataset roughly doubles (~4 weeks, ~100 graded picks), compare pooled
hit-quality of `learning_feedback_active=true` rows vs the feedback-off baseline:
- **Win**: overall pooled hit-quality rises (esp. the previously-weak categories
  becoming *more selective* — fewer but better picks there) without the category
  mix collapsing to a single type.
- **No effect / harm**: flip `learning_feedback_enabled` off; revisit whether the
  mechanical version is justified once CIs actually separate.

## Consequences
- The ranking prompt is now non-deterministic w.r.t. history — a given day's
  picks depend on the accumulated track-record. Acceptable; it's the whole point.
- Provenance is preserved: `learning_feedback_active` + the pooled report make
  every run auditable after the fact.
- Escalation path: **Phase 2** (mechanical weighting / confidence-scaled sizing)
  stays gated behind this ADR's success criteria being met on real data.

## Not in scope
- No mechanical impact-score adjustment (explicitly deferred).
- No change to the judge rubric/prompt (ADR 0001 stays locked).
- No paper-trade sizing change (ADR 0003 stays equal-weight).
