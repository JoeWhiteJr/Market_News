---
id: MM-T006
title: Email visuals pack — Gmail-safe charts + sparkline fix
status: in-progress
priority: medium
type: feature
owner: joe
assigned-team: Builder
created: 2026-07-10
updated: 2026-07-10
related-pr:
related-tickets: MM-T003
---

# Email visuals pack — Gmail-safe charts + sparkline fix

## Problem
Three creative-brainstorm agents converged: the briefing needs real *visuals*,
and — critically — the existing top-of-email sparklines are **inline `<svg>`,
which Gmail strips**, so Joe/Jared/Mia on Gmail likely see nothing there. Build
a pack of Gmail-safe visuals (colored table cells, the one technique that renders
everywhere) and fix the broken sparklines.

## Acceptance Criteria
- [x] New `visuals.py` module: shared red→neutral→green color ramp + 4 blocks,
      each with an HTML and a plain-text renderer, all colored-table-cell (no SVG)
- [x] **Fix sparklines** — replace the SVG polyline strip with a Gmail-safe
      "index strip" (ticker + %Δ on a shaded cell); delete the dead SVG code
- [x] **The Streak** — GitHub-style row of the last ~21 graded verdicts
- [x] **Category Report Card** — pooled hit-quality bars + 90% CI whisker
- [x] **Market Weather** — 11-cell sector-ETF heat-map (new `fetch_sector_moves`)
- [x] Each block hides itself with no data; all best-effort (never break the send)
- [x] Config flags: `sector_heatmap_enabled`, `streak_row_enabled`, `category_card_enabled`
- [x] Wired into `render_email_html` + `render_plain_text` + the pipeline
- [x] Unit tests for every renderer + the color ramp; obsolete SVG tests replaced
- [x] `ruff` clean, full suite green (405 passed)
- [ ] Joe eyeballs the Downloads preview in a real browser and on Gmail mobile

## Context & Notes
- Rendering hierarchy (verified): colored `<td bgcolor>` + hosted https PNGs render
  in Gmail; inline `<svg>` and `data:` URIs do NOT. Everything here uses the former.
- **Tradeoff flagged:** the index strip shows ticker + %Δ, not the 5-day *line
  shape* the old SVG drew. Gains: actually renders in Gmail + visual consistency
  with the heat-map. If Joe wants the line shape back, a hosted-PNG sparkline
  (matplotlib → Pages) is the upgrade path.
- Data all already on hand: Alpaca bars (sector ETFs), `briefings.jsonl` (streak),
  `learning.compute_category_performance` (report card).

## Retrospective
_Fill in when moving to done/._
