---
id: MM-T005
title: Close the learning loop (Phase 1) — soft calibration feedback
status: done
priority: medium
type: feature
owner: joe
assigned-team: Builder
created: 2026-07-10
updated: 2026-07-10
related-pr:
related-tickets: MM-T003
---

# Close the learning loop (Phase 1) — soft calibration feedback

## Problem
The learning loop (ADR 0004 / MM-T003) *measures* per-category hit-quality but
nothing consumes it — the agent doesn't yet learn from its own track record.
Joe: "is it learning?" → it was measuring, not adjusting. Close the loop, but
without overfitting the thin, overlapping-CI data (n≈53).

## Decision (ADR 0005)
**Soft, not mechanical.** Feed the graded category track-record into the ranking
prompt as *calibration* context so the LLM tempers its own confidence — no
arithmetic change to impact scores. Chosen over mechanical score-weighting
because at n≈53 the category CIs overlap and a hard tilt would overfit noise,
contaminate the measurement, and collapse pick diversity.

## Acceptance Criteria
- [x] `format_track_record_for_prompt()` builds a calibration block from the
      pooled report (reuses `compute_category_performance`)
- [x] `min_n` guardrail excludes thin categories (default 8; commodity n=3 out)
- [x] Framed as calibration, NOT category preference (preserves diversity)
- [x] Behind `learning_feedback_enabled` flag (reversible / A-B)
- [x] Threaded into `analyze_articles(track_record=...)` (mirrors macro-bias)
- [x] **`learning_feedback_active` logged per briefing record** — baseline
      instrumentation so lift is measurable (historical rows default False)
- [x] Empty block ⇒ byte-for-byte feedback-off baseline prompt
- [x] Best-effort — any failure falls back to baseline, never breaks the send
- [x] ADR 0005 written with explicit success criteria + Phase 2 escalation gate
- [x] Unit tests (learning block, prompt threading, record flag, legacy default)
- [x] `ruff` clean, full suite green (400 passed)

## Measurement plan
All ~53 historical picks are feedback-OFF (flag absent → False). From toggle-on
forward, picks are feedback-ON. After ~4 weeks (~100 graded picks) split the
ledger on `learning_feedback_active` and compare pooled hit-quality. Win =
overall hit-quality rises without the category mix collapsing. No effect ⇒ flip
the flag off; mechanical Phase 2 stays gated on CIs actually separating.

## Retrospective
**Shipped 2026-07-10.** Deliberately resisted the tempting mechanical loop —
the honest read of the CIs said we'd be tuning on noise. The soft version is
reversible, measurable, and preserves the natural experiment. The real
deliverable is arguably the `learning_feedback_active` flag: without that
baseline marker we'd never be able to prove the feature helped.
