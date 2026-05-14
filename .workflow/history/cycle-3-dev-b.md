# Cycle 3 — Dev B Log (Sparkline Strip)

**Branch:** `feat/cycle-3-sparkline`
**Base:** `main` @ `e125e00`
**Scope:** Add a 5-cell sparkline strip (SPY, QQQ, DIA, VIX, IWM) at the top of the email body.

## What shipped

1. **`SparklineSeries` model** (`src/market_mover/models.py`) — pydantic dataclass:
   `ticker`, `close_prices: list[float]`, `pct_change: float`, `direction: Literal["up","down","flat"]`.
2. **`sources/quotes_source.py`** — new module with `fetch_sparkline_data(tickers, days=5, api_key, min_call_interval)`.
   - Hits Finnhub `/stock/candle?resolution=D`, pads the date window to `days * 3` to cover weekends.
   - 20s HTTP timeout (project convention since Cycle 1).
   - Per-ticker failure isolation: a bad ticker is silently dropped; total failure returns `{}`.
   - Reuses the same `_enforce_rate_limit` shape as the other sources.
   - `_classify_direction` flattens anything within ±0.1% so a 0.02% VIX drift doesn't render as a tiny red arrow.
3. **Config** (`src/market_mover/config.py`):
   - `SPARKLINE_ENABLED` (default `True`)
   - `SPARKLINE_TICKERS` (default `"SPY,QQQ,DIA,VIX,IWM"`)
   - `sparkline_ticker_list` property splits/normalizes (`.strip().upper()`).
4. **Email template** (`src/market_mover/email_template.py`):
   - New `<section data-block="sparkline">` block injected **right after the hidden preheader `<div>`** — line range distinct from Dev A's contrarian block at the bottom.
   - One `<table>` row, 5 `<td>` cells (`width=20%`), each with inline `<svg>` + `<polyline>` (~80×24px).
   - `SPARKLINE_COLORS` palette extends the Cycle 2 WCAG-corrected approach: light + dark variants, all passing 4.5:1 against `#ffffff` (light) and `#1a1d24` (`.mm-card` dark bg).
   - Dark-mode overrides in `@media (prefers-color-scheme: dark)`: swap to brighter `mm-spark-*` tones.
   - Mobile (`@media (max-width: 600px)`): cells switch to `display:block` so they stack vertically.
   - Outlook desktop fallback: `<!--[if mso]>` conditional emits a plain-text strip like `SPY +1.3%  QQQ -0.4%  DIA +0.6%  VIX -2.1%  IWM +0.1%`. Modern clients hide it; Outlook ignores the SVG branch.
   - `render_email_html(articles, sparklines=...)` and `render_plain_text(articles, sparklines=...)` both accept the new kwarg; `None`/`{}` skips the strip cleanly.
5. **Pipeline integration** (`src/market_mover/cli.py`):
   - `_gather_articles` now returns `(articles, sparklines, errors)`. Sparkline fetch is a 5th task in the existing `ThreadPoolExecutor` (only added when `sparkline_enabled` and `finnhub_api_key` are present).
   - Sparkline failure is non-fatal — `run_pipeline` logs and proceeds with `sparklines={}`, the template skips the block.
6. **Tests** (`tests/test_sparkline.py`) — 36 new tests:
   - Direction thresholds (flat <0.1%, exact edges).
   - `_build_series` happy path, `no_data`, single-close, non-dict, truncation to last N days.
   - `fetch_sparkline_data` empty inputs, full success (asserts endpoint + 20s timeout), bad ticker isolation, total failure → `{}`.
   - SVG `<polyline>` carries 5 points, monotonic-up series ends with smaller Y (visually up), flat series renders horizontal.
   - End-to-end render: block before date header, missing data ⇒ no block, plain-text strip, MSO conditional present, mobile `@media` present.
   - WCAG AA contrast verified in-test via the standard relative-luminance formula for all 6 colors (up/down/flat × light/dark).
7. **Test patch** (`tests/test_cli_reliability.py`): updated to unpack the new 3-tuple and patch `cli.fetch_sparkline_data` so the existing reliability tests don't make real Finnhub HTTP calls.

## Verification

- `python3 -m pytest` → **124 passed** (36 new + 88 pre-existing).
- `python3 -m ruff check` on all touched files → clean. (One pre-existing I001 in `tests/test_sources.py` is out of scope.)
- Manual render via fixture data with 5 tickers (up/down/up/down/flat) inspected — sparkline strip sits above the date header, polylines correctly shaped (up series ends visually-high), colors match the WCAG-tested palette.

## Out of scope

Dev A's blocks (Vinny persona, contrarian coda) — those go in `<section data-block="contrarian">` at the bottom of the body.

## Notes

- No new dependencies added — `requests` is already a transitive dep via `newsapi-python`. The fetch imports it inside the try-block so a missing install still degrades gracefully.
- Sparkline data uses `dict` insertion order (Python 3.7+ guarantee) so render order matches the ticker config.
