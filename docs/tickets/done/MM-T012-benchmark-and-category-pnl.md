---
id: MM-T012
title: SPY benchmark chart + per-category P&L attribution on the dashboard
status: done
priority: medium
type: feature
owner: joe
assigned-team: Builder
created: 2026-08-13
updated: 2026-08-13
related-pr: 37
related-tickets: MM-T009, MM-T010, MM-T003
---

# SPY benchmark chart + per-category P&L attribution on the dashboard

## Problem
"How do we make the model do better?" — but you can't improve what you can't
attribute. The dashboard showed one blended realized-$ P&L line, which is flat
mostly because the account sits ~95% in cash (cash drag), not because the picks
are bad. We needed to (1) separate *selection skill* from cash drag with a real
benchmark, and (2) see **which categories** actually make money before tuning
anything.

## Decision
Two new dashboard cards, both computed from the paper ledger (SPY needs one
best-effort Alpaca call; failure just hides that one chart):

- **Picks vs. the market** — two-series line, **cumulative return per dollar** of
  a pick vs **SPY buy-and-hold** over the same trading days, both indexed to 0%.
  This removes cash drag and answers the real edge question. Per dataviz: two
  series → legend required (blue "Picks" vs muted-gray dashed "SPY"), one shared
  % axis (never dual-axis), zero baseline, direct end-labels, `<title>` tooltips,
  a headline "Edge vs SPY" delta colored green/red.
- **Where the money comes from** — realized $ pooled by pick category as diverging
  bars (green profit ▶ / ◀ red loss around a zero midpoint) + a numeric table
  (trades, win%, total $, avg/trade), sorted by total P&L.

## First read from the live ledger (38 aligned days, as of 2026-08-12)
- **Picks +8.72% vs SPY +6.47% per dollar → +2.25% edge.** Modest, small sample,
  could be noise — but *directionally* the picks are beating the index once cash
  drag is removed. That's a very different story than the ~flat dollar P&L, and
  it's exactly what MM-T010 (Sept 9 review) should test for significance.
- **Category attribution:** single_name **+$182.92** (30 trades) carries the book;
  geopolitical +$74.87; macro +$27.15; commodity +$5.83 (n=2, noise);
  **unmapped −$145.30** (10 legacy/uncategorized trades) is the biggest drag —
  worth investigating whether those are a real losing bucket or just mislabeled.

## Acceptance Criteria
- [x] `_pick_return_series`, `_benchmark_pair`, `_render_benchmark_chart` (pure, tested)
- [x] `_category_pnl`, `_render_category_pnl` (pure, tested)
- [x] `fetch_spy_closes` — best-effort one-call SPY fetch; {} → chart hidden
- [x] Wired through `write_scores_page`, `_main`, and cli Step 7
- [x] Both cards light+dark; benchmark geometry bounds-checked (x≤760, y in [0,240])
- [x] Tests: per-cycle avg, zero-indexing/compounding, 2-day guard, legend+alpha,
      category pooling/sort/unmapped, sign colors, card show/hide (+13, 445 total)
- [x] `ruff` clean, full suite green
- [x] Joe eyeballs `Downloads/Market Mover - Dashboard (benchmark + category).html`

## Context & Notes
- **Methodology (honest):** the picks line compounds each day's *equal-weight mean*
  `pnl_pct`; both curves index to 0% at the first shared day (first day's return is
  the baseline). It's a directional edge tool, not a research-grade backtest — small
  n, no transaction costs beyond what's already in the fills, ~1-day holds aligned to
  SPY close-to-close. Read the trend, not the third decimal.
- SPY closes come from Alpaca daily bars (IEX). No creds / no network → `{}` → the
  benchmark card is simply absent; the rest of the page is unaffected.
- The **unmapped −$145** bucket is the most actionable lead this surfaced — feeds the
  "make the model better" thread and MM-T010.

## Retrospective
Shipped in a **single focused PR** (#37, squash-merged first try) — held the
one-feature-one-PR discipline from the MM-T007 stacking mishap.

**What went well**
- The benchmark reframed the whole "is there edge?" question. The blended
  realized-\$ line read flat; the per-dollar view showed **picks +8.72% vs SPY
  +6.47% (+2.25% edge)** over 38 days. Same data, opposite gut read — cash drag
  was hiding it. Exactly why "measure before you tune" was the right first move.
- Category attribution immediately surfaced the **unmapped −\$145** bucket as the
  top actionable lead, plus single_name (+\$183) as the clear breadwinner.
- Best-effort SPY fetch kept the network out of the critical path: no creds → the
  card just vanishes, page/send unaffected. Same contract as the P&L chart.
- Followed the dataviz method (two series → legend, one % axis, diverging bars
  with a neutral zero). Geometry bounds-checked before ship.

**What surprised us**
- **ID collision:** the parallel daisy-cron session had already taken MM-T011.
  Renumbered mine → MM-T012 (third time this pattern has bitten — T007/T008, now
  this). A shared next_id across concurrent sessions is the root cause; worth a
  guard someday, but not urgent.
- **Caught a latent bug while here:** MM-T008's watchdog was marked done but its
  `.github/workflows/briefing-watchdog.yml` trigger is **not on main** — a false
  "committed on main" from `git log` exiting 0 on no matches hid it. Filed as a
  follow-up; the Python shipped but the cron trigger never did.

**Follow-ups spun out**
- Investigate the unmapped −\$145 bucket (real losing bucket vs mislabeled).
- Deploy the missing watchdog trigger (separate from this ticket).
- MM-T010 (Sept 9) now has the exact tooling to judge whether the +2.25% edge is
  signal or noise.

**Lesson reused:** attribution before optimization; one feature = one PR off main.
