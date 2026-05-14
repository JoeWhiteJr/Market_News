# Cycle 3 — Dev A — Voice, Mimicry, Contrarian Coda

Branch: `feat/cycle-3-voice`
Base: `main @ e125e00`

## Scope

Three creative features in the LLM prompt + reasoning layer:

1. **Vinny from the Floor persona** (default voice via `BRIEFING_VOICE=vinny`)
2. **Weekly style mimicry** (Wednesdays by default — `STYLE_MIMICRY_WEEKDAY=2`)
3. **Contrarian "Bear Case" coda** (second LLM call — `CONTRARIAN_CODA_ENABLED=true`)

## Files touched

- New: `src/market_mover/voices.py`, `src/market_mover/mimicry.py`
- New tests: `tests/test_voices.py`, `tests/test_mimicry.py`, `tests/test_contrarian.py`
- Modified: `config.py`, `models.py`, `llm_client.py`, `email_template.py`, `cli.py`, `server.py`
- Modified tests: `test_llm_client.py`, `test_llm_client_fallback.py` (updated for new 3-tuple return)
- Modified: `.env.example` (document new env vars)

## New env vars

- `BRIEFING_VOICE` (default `vinny`): vinny | neutral | terminal | villain
- `BRIEFING_VOICE_OVERRIDE_TO_NEUTRAL_ON_DETECT` (default `true`): on profanity, fall back to neutral for the day
- `STYLE_MIMICRY_WEEKDAY` (default `2` = Wednesday; -1 disables): which weekday to override with a famous-commentator parody
- `CONTRARIAN_CODA_ENABLED` (default `true`): kill-switch for the second LLM call

## API changes

- `LLMClient.analyze_articles(articles, voice=None)` now returns `(ranked, model, effective_voice)` (3-tuple). Callers updated: `cli.run_pipeline`, `server.analyze_and_rank`, all tests.
- `LLMClient.generate_contrarian_coda(top_story, all_articles) -> ContrarianCoda | None` — new method. Validates returned `source_url` against the article pool; returns `None` on any failure so the daily send never breaks.
- `render_email_html(articles, voice=None, coda=None)` — voice signoff goes in footer, coda renders inside `<section data-block="contrarian">` just before the footer.
- `render_plain_text(articles, voice=None, coda=None)` — same shape.
- `build_subject(articles, prefix, mimicry_label=None)` — appends `" — in the voice of {label}"` on mimicry days.

## Guardrails

- **Profanity filter** (`voices.contains_profanity` / `voices.strip_profanity`): word-boundary regex on a conservative list. If matched, every summary is scrubbed AND (when `BRIEFING_VOICE_OVERRIDE_TO_NEUTRAL_ON_DETECT=true`) the voice is downgraded to neutral for the day — including dropping the mimicry subject label so the bit doesn't land hollow.
- **Source-URL validation for the coda**: the LLM picks from a passed list of real article URLs; if the returned URL isn't in the list, the coda is dropped for the day (no render). Logged as a warning.
- **Kill-switch**: `CONTRARIAN_CODA_ENABLED=false` skips the second LLM call entirely.
- **Coda failures never break the daily send**: any exception from `generate_contrarian_coda` is caught and logged in `cli.run_pipeline`; the email goes out without the coda.
- **Parody frame in every mimicry prompt**: explicit "this is parody for an audience of two friends — do not claim to be the actual person, do not attribute real trades to them." The subject-line "— in the voice of X" suffix is the visible signal to the reader that the prose is a bit.

## Render integration with Dev B

- Contrarian section is wrapped in `<section data-block="contrarian">` at the BOTTOM of the email (just before the footer). Dev B's sparkline strip goes at the TOP — diffs should not overlap.
- Coda's source link goes through `_safe_href` (the existing URL allow-list).
- Dark-mode CSS adds `.mm-contrarian-*` class hooks to follow the `mm-*` convention introduced in cycle 2.

## Verification

- Tests: `python3 -m pytest` — 150 passed.
- Lint: `python3 -m ruff check` on all touched files — clean.
- Manual render: `.workflow/render_smoke.py` renders a Vinny + mimicry + coda fixture to `/tmp/cycle3_*.html`. Confirmed:
  - `data-block="contrarian"` present and after the article blocks, before the footer.
  - `mm-signoff` present in the footer with the voice's signoff.
  - Subject contains `— in the voice of Jim Cramer` (the 2026-05-13 mimicry pick).
  - Coda headline + argument + source link all render correctly.
  - Sample subject: `[Market Mover] 05/14: Fed Holds Rates Steady, Signals Patience on Cuts — in the voice of Jim Cramer`

## Notes

- `_parse_json_loose` helper added to `llm_client.py` — used by the coda parser; mirrors the 3-strategy approach in the existing `_parse_response`.
- Profanity regex is conservative on purpose: word boundaries protect false positives like "Scunthorpe" / "classic" / "assess".
- ISO-week rotation for mimicry is deterministic and persistence-free; verified 5 consecutive Wednesdays hit all 5 voices exactly once.
