# ADR 0001 — Yesterday-Index Rubric

**Status**: **Accepted** — 2026-05-15 (Joe)
**Decides**: How we grade yesterday's market-briefing picks against actual price action.

## Context

Each weekday morning we send a "Top 3 Market-Moving Stories" briefing. We want to know, over time, whether the briefing's high-impact predictions actually predict anything. The data must be persisted in a form that lets us compare hit-rates across models (Claude vs Gemini), voices, and rubric tweaks.

The original brainstorm flagged: *"Rubric design is the whole ballgame. If we pick the wrong metric and accumulate months of data against it, retroactively changing it invalidates history."* This ADR locks the rubric.

## Decision

### 1. Rubric type: **Hybrid LLM-judge**

For each of yesterday's 3 picks:
1. Fetch raw 24h close-to-close price data for the relevant asset(s).
2. Pass both the original prediction and the price data to a frozen judge prompt.
3. Receive a verdict in `{HIT, PARTIAL, MISS, TOO_EARLY, NOT_APPLICABLE}` + a one-sentence justification.
4. Persist the raw price data alongside the verdict, so the judge prompt can be retired/replaced later without losing fidelity.

### 2. Storage: `data/briefings.jsonl` committed back by the GHA bot

Append-only JSONL, one record per day, git-tracked. The daily-briefing workflow already has `permissions: contents: write`. The workflow is not triggered by `push`, so no CI loop.

### 3. Phasing

- **Phase A (this cycle)**: Persistence + scorecard render with placeholder verdicts. Starts collecting today's picks. Schema locked.
- **Phase B (next cycle)**: Add LLM judge + price-data fetch. Real verdicts appear.
- **Phase C (after ≥2 weeks of data)**: Running stats display (Claude vs Gemini hit-rates over rolling 30-day window).

## The Frozen Judge Prompt (Phase B will use this verbatim)

```
You are grading yesterday's market briefing predictions. Your verdict will be
persisted and compared over months — be CONSISTENT, not generous.

YESTERDAY'S PREDICTION (one of 3 picks):
- Title: {title}
- Summary: {summary}
- Impact score (0–10): {impact_score}
- Primary ticker or category: {ticker_or_category}

PRICE DATA — 24 hours after the briefing (close to close):
- Primary ticker {ticker}: {primary_pct}%
- SPY: {spy_pct}%
- VIX level: {vix_close} (change: {vix_pct}%)
- Sector ETF (if applicable, {sector_etf}): {sector_pct}%

VERDICT RULES:
- HIT: The predicted impact materialized in the expected direction with a
  meaningful magnitude. For high-impact predictions (impact_score ≥ 8.0), this
  means ≥1.5% absolute move in the right asset. For lower-impact predictions
  (5.0 ≤ score < 8.0), ≥0.7% move.
- PARTIAL: Direction was right, but magnitude was muted; OR the right asset
  moved but a related one didn't.
- MISS: Direction was wrong (asset moved opposite way), OR no material
  movement at all for a high-impact (≥8.0) prediction.
- TOO_EARLY: The story is a multi-day setup (e.g., "Fed meeting next week,"
  "earnings season begins") — too early to grade in 24h.
- NOT_APPLICABLE: Story has no obvious market connection or the relevant
  asset class has no clean ticker proxy.

WHEN IN DOUBT:
- Prefer PARTIAL over HIT.
- Prefer TOO_EARLY over MISS.

Return JSON only:
{
  "verdict": "HIT" | "PARTIAL" | "MISS" | "TOO_EARLY" | "NOT_APPLICABLE",
  "justification": "<one short sentence citing the numbers>"
}
```

**Frozen parameters:**
- `temperature`: 0.0 (reproducibility)
- `model`: locked to whichever judge model is in effect; logged per-row as `judge_model`
- `judge_prompt_version`: integer, incremented if prompt ever changes (and judgments re-run from scratch)

## Frozen JSONL Schema (v1)

One record per day, appended at end-of-pipeline. `graded_at` and `judgments` are populated by Phase B (filled in by the NEXT day's run grading yesterday).

```jsonc
{
  "date": "YYYY-MM-DD",                  // ISO date — when the briefing was sent
  "schema_version": 1,
  "model_used": "claude" | "gemini",      // which model produced today's ranking
  "voice": "vinny" | "neutral" | "terminal" | "villain",
  "mimicry_voice": null | "cramer" | "buffett" | "matt_levine" | "zerohedge" | "ft_leader",
  "picks": [                              // length 3
    {
      "rank": 1 | 2 | 3,
      "title": "<string>",
      "summary": "<string>",
      "impact_score": 0.0–10.0,
      "primary_ticker": "<string>" | null,    // null for pure-macro stories
      "category": "macro" | "single_name" | "commodity" | "crypto" | "geopolitical" | "other",
      "source_url": "<string>",
      "source_name": "<string>"
    },
    ...
  ],
  "contrarian": null | {
    "headline": "<string>",
    "argument": "<string>",
    "source_url": "<string>",
    "source_name": "<string>"
  },
  // Phase B fills these:
  "graded_at": null | "<ISO timestamp>",
  "judge_model": null | "<string>",
  "judge_prompt_version": null | 1,
  "judgments": null | [
    {
      "rank": 1 | 2 | 3,
      "verdict": "HIT" | "PARTIAL" | "MISS" | "TOO_EARLY" | "NOT_APPLICABLE",
      "justification": "<string>",
      "price_data": {
        "primary_ticker": "<string>" | null,
        "primary_pct_change_24h": <float> | null,
        "spy_pct": <float>,
        "vix_close": <float>,
        "vix_pct_change": <float>,
        "sector_etf": "<string>" | null,
        "sector_pct": <float> | null
      }
    },
    ...
  ]
}
```

## What's NOT locked (free to iterate without invalidating history)

- The rendered scorecard's visual design — colors, layout, copy.
- Which stats we display in the email (we could show 30-day hit-rate today, 90-day later, etc.).
- Whether we display the running Claude-vs-Gemini split or hide it.
- The Phase A "placeholder verdict" copy (e.g., "TBD — judging launches in Phase B").
- Adding new optional fields to the schema (`schema_version` bumps to 2; existing rows remain readable).

## What IS locked (changing these invalidates historical comparisons)

- The verdict categories: `{HIT, PARTIAL, MISS, TOO_EARLY, NOT_APPLICABLE}`.
- The judge prompt text above.
- The HIT/PARTIAL/MISS magnitude thresholds (≥1.5% for high-impact, ≥0.7% for medium-impact).
- The price-data window (close-to-close 24h after the briefing was sent).
- The "prefer PARTIAL over HIT, TOO_EARLY over MISS" tie-breaking rules.
- The schema's required fields.

If any locked item must change, bump `judge_prompt_version` and re-run all historical judgments from scratch. Document the change here.

## Open questions

- **Macro story tickers**: For "inflation surges" or "Fed cuts rates," the natural ticker is SPY + TLT + DXY rather than a single asset. Phase B's category→tickers mapping will be defined in a follow-up section of this ADR before Phase B ships.
- **Earnings stories**: A "TSLA earnings beat" story's 24h window crosses the earnings release itself. Phase B may need to use a different window (T+0 close to T+1 close where T = release day, not briefing day) for these. Tracked in the spec; not yet locked.

## Implementation note — price source (Cycle 4c, 2026-06-05)

Phase B originally fetched the close-to-close window via Finnhub `/stock/candle`
(close on `briefing_date` vs close on the next trading day). **That endpoint is
premium-only on our current plan** — it returns HTTP 403, so every judgment came
back `NOT_APPLICABLE` for want of data.

Resolution: the judge now reads the free Finnhub `/quote` endpoint, which returns
a single snapshot — close `c`, previous close `pc`, session % change `dp`. This
is the **correct** close-to-close window because grading runs **pre-market the
next day** (~06:00 MDT, before the 09:30 ET open): at that moment the most-recent
*completed* session is exactly the one that followed the briefing. `dp` is that
session's move; `c` is its close.

- This changes the **mechanism**, not the locked conceptual window. **No
  `judge_prompt_version` bump** — prompt text and thresholds are untouched.
- **VIX gap**: the `^VIX`/`VIX` index is also premium on `/quote` (null), so VIX
  level/change render as `null` for now and the judge grades on SPY + the primary
  ticker. VIX ETFs (`VIXY`, `VXX`) are available but their *price* is not the VIX
  *level*, so we do **not** substitute them into the frozen prompt's "VIX level"
  slot. Proper VIX + true historical bars are deferred to the planned Alpaca
  market-data integration.
- The sparkline strip shares the same `/stock/candle` 403 and is deferred
  separately.

## Sign-off

- [x] Joe approves judge prompt text (verbatim above) — 2026-05-15
- [x] Joe approves verdict thresholds (≥1.5% / ≥0.7%) — 2026-05-15
- [x] Joe approves storage choice (`data/briefings.jsonl` in repo) — 2026-05-15
- [x] Joe approves phasing (A → B → C) — 2026-05-15
- [x] Joe approves 5-verdict scheme (no `SURPRISE` 6th category) — 2026-05-15
- [x] Joe approves public hit-rate display in email (Claude vs Gemini split visible) — 2026-05-15
