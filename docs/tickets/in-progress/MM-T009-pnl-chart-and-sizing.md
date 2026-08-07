---
id: MM-T009
title: Paper P&L chart on the dashboard + bump position sizing to $15k
status: in-progress
priority: medium
type: feature
owner: joe
assigned-team: Builder
created: 2026-08-02
updated: 2026-08-02
related-pr:
related-tickets: MM-T003
---

# Paper P&L chart on the dashboard + bump position sizing to $15k

## Problem
Joe asked two things after seeing the paper track record was flat: (1) put the
P&L on the Pages dashboard so it's watchable over time instead of computed ad
hoc, and (2) be more aggressive — the account was only deploying ~$3k of $100k
($1k × 3 picks), so the dollar P&L was invisible noise.

## Decision
- **Sizing → $15k/pick** (chosen from a 3-way: full-equity / half / keep-small).
  ~$45k deployed across 3 picks, ~55% cash: visible, meaningful swings without a
  single -15% name gutting the account. Explicitly flagged to Joe that sizing
  **amplifies variance, not edge** — a louder breakeven until an edge is proven.
- **P&L chart**: cumulative *realized* P&L (starts at 0), single-series line with
  a zero baseline, green when up / red when down, native SVG `<title>` tooltips,
  light+dark palette matching the existing dashboard. Built per the dataviz
  method (change-over-time + polarity; single series → no legend).

## Acceptance Criteria
- [x] `_pnl_series` (cumulative realized P&L per cycle) + `_render_pnl_chart` (SVG)
- [x] P&L card on the scores dashboard, above the category table
- [x] `write_scores_page` loads the paper ledger; wired through cli + `_main`
- [x] `paper_notional_per_position` 1000 → 15000 with an honest comment
- [x] Best-effort — chart hidden with <2 points; never breaks page/send
- [x] Geometry bounds-checked (no viewBox overflow); light+dark
- [x] Tests: cumulative/order, empty-hides, sign→color, zero-baseline+tooltips, card
- [x] `ruff` clean, full suite green (432 passed)
- [ ] Joe eyeballs `Downloads/Market Mover - Dashboard.html`

## Context & Notes
- Historical trades stay at $1k, so the P&L curve shows a regime change at the
  sizing-bump date — expect the wiggles to get ~15× bigger going forward.
- Sizing change is PAPER ONLY (ADR 0003); no live endpoint exists anywhere.

## Retrospective
_Fill in when moving to done/._
