---
id: MM-T014
title: Publish latest.json feed for the Wasden Watch /market page
status: in-progress
priority: medium
type: feature
owner: joe
created: 2026-08-16
updated: 2026-08-16
related-pr:
related-tickets:
assigned-team:
---

# Publish latest.json feed for the Wasden Watch /market page

## Problem
The Robinhood-Agentic (Wasden Watch) dashboard has a new read-only `/market` page (Jared's repo, PR #83) that shows Market Mover context: a headline feed plus a catalyst calendar. It needs the daily brief as structured JSON, but MM only publishes `scores.html` and emails the brief. Jared must not need MM's code, keys, or repo access, and his trading Postgres has no network port (ADR-001), so the handoff has to be an outbound pull from his side of a stable, public, zero-auth URL.

MM already serves GitHub Pages from `docs/` (that is how `scores.html` is live), so the cheapest handoff is to emit a JSON file into the same Pages folder every run.

## Acceptance Criteria
- [x] `write_latest_json` in `scores_page.py`: newest-N briefing records as JSON, best-effort (never raises), mirroring `write_scores_page`
- [x] Wired into `cli.py` Step 7 (7b) so it publishes next to `scores.html` each run
- [x] Tests: newest-first ordering, N-day cap, missing ledger = empty feed, undated record does not crash, unwritable path returns False without raising (`tests/test_latest_json.py`, 5 passing)
- [x] Smoke-tested against the real `data/briefings.jsonl` (5 records, ~28 KB, picks intact)
- [ ] Reviewed + committed + pushed; a run publishes `https://joewhitejr.github.io/Market_News/latest.json`
- [ ] Confirm GitHub Pages source is still `main` / `docs` (memory is ~37 days old); if the Pages path changed, the URL changes with it
- [ ] Hand Jared the final URL and the record shape (`picks[]` = headline feed)

## Context & Notes
- **Files:** `src/market_mover/scores_page.py` (+`write_latest_json`, `LATEST_JSON_DAYS = 5`), `src/market_mover/cli.py` (Step 7b + import), `tests/test_latest_json.py` (new).
- **Feed shape:** `{ schema_version, generated_at, count, briefings: [<newest-first records>] }`. Each briefing keeps its full record; `picks[]` (rank, title, summary, primary_ticker, impact_score, source_name, source_url) are the headline feed.
- **Not in the feed yet:** dated catalysts and per-ticker sentiment. v1 split: MM feeds headlines, Jared builds the catalyst calendar from FMP earnings. A `catalysts[]` + `macro_read` field on the brief schema is a possible follow-up if MM should own them.
- **Deploy:** takes effect only once committed and the daily Action runs (or a manual `python -m market_mover.scores_page` style run writes `docs/latest.json`).
- Built alongside the Wasden Watch Ut build-out (see the Robinhood-Agentic `docs/contracts/market-context-endpoint.md`).

## Retrospective
_(fill on move to done)_
