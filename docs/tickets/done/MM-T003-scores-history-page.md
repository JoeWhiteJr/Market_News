---
id: MM-T003
title: Browsable scores & grades history page
status: done
priority: medium
type: feature
owner: joe
assigned-team: Builder
created: 2026-07-09
updated: 2026-07-09
related-pr: "#27"
related-tickets: MM-T001
---

# Browsable scores & grades history page

## Problem
Every Yesterday-Index grade is stored permanently in `data/briefings.jsonl`
(append-only, committed to GitHub each run), but there is **no easy way to view
the history**. Reviewing past scores means reading raw JSON or running the
`python3 -m market_mover.learning` CLI. Joe (handwritten note on the June 2026
project overview: *"I want to be able to go back and look at ALL the scores and
grades"*) wants a browsable record of every pick and its verdict over time.

## Acceptance Criteria
- [x] A self-contained HTML page rendered from `data/briefings.jsonl`
- [x] Per-category pooled hit-quality summary (reuses the learning module)
- [x] Full daily history: date → each pick (rank, ticker, category, title) →
      verdict badge (HIT / PARTIAL / MISS / TOO_EARLY / N/A) + justification
- [x] Paper-trading track-record summary when present
- [x] Dark-mode aware, mobile responsive, no external assets (CSP-safe)
- [x] Regenerated every pipeline run and committed to `docs/scores.html`
- [x] Best-effort — page generation NEVER breaks the daily send
- [x] Published via GitHub Pages for a stable, easy-access URL
- [x] Unit tests for the renderer
- [x] `ruff` clean, full test suite green

## Context & Notes
- Data model: each briefing row has `picks[]` (rank, primary_ticker, category,
  title) and a parallel `judgments[]` (rank, verdict, justification, price_data).
- Reuse `learning.compute_category_performance` for the category summary so the
  page and the log readout never disagree.
- Repo is public, default branch `main`, `docs/` exists → Pages from `/docs`
  gives `https://joewhitejr.github.io/Market_News/scores.html` with no new
  exposure (scores are already public JSON in the repo).

## Retrospective
**Shipped 2026-07-09 (PR #27), published via GitHub Pages** at
`https://joewhitejr.github.io/Market_News/scores.html`.

**What went well:** reusing `learning.compute_category_performance` for the
category table means the page and the log readout can never drift apart. The
best-effort Step 7 writer keeps page generation from ever threatening the send.

**Bug caught in-flight:** the new Step 7 wrote to the *committed* `docs/scores.html`
during the test suite (an end-to-end `run_pipeline` test reads a tmp ledger but
had no way to redirect the new path). Fixed with an autouse `_sandbox_scores_page`
fixture mirroring the existing `_no_real_alpaca` guard, plus a regression test.
**Lesson:** any new pipeline step that writes a tracked repo file needs a
test-isolation guard from day one, or the suite silently clobbers real artifacts.
