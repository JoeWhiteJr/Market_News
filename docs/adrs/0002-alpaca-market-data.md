# ADR 0002 — Migrate market data to Alpaca

**Status**: **Accepted** — 2026-06-08 (Joe)
**Decides**: Where Market Mover gets price data (judge close-to-close, sparklines, future features).
**Supersedes**: the Finnhub price-data path locked in ADR 0001's Cycle 4c note.

## Context

Finnhub's `/stock/candle` is premium-only on our plan (HTTP 403). Cycle 4c
worked around it with Finnhub `/quote`, but `/quote` is a **single snapshot**:
no history, no arbitrary-date lookup, and no VIX index. That blocks:

- precise close-to-close grading for delayed/backfilled rows,
- the 5-day sparkline strip (silently empty since Cycle 3),
- any feature needing real historical bars (multi-day trends, paper-trading
  fills in ADR 0003).

Joe has an Alpaca account. Alpaca's **Market Data API v2** offers free
historical + recent **daily/▮minute bars** (IEX feed) and is the data backbone
for the paper-trading track record (ADR 0003) and eventual live trading.

## Decision

Add **Alpaca** as the primary market-data source; retire the Finnhub
quote/candle price path. **Finnhub stays for news** (`finnhub_source.py`) — only
the quotes path moves.

### Scope
- New `src/market_mover/sources/alpaca_source.py`:
  - `fetch_daily_bars(symbols, start, end) -> dict[str, list[Bar]]` via
    `GET https://data.alpaca.markets/v2/stocks/bars?timeframe=1Day&...`
    (auth headers `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY`).
- Reimplement the existing public functions on top of bars, keeping signatures
  stable so `judge.py` and `cli.py` barely change:
  - `fetch_24h_close_change(ticker, briefing_date, ...)` → real close-to-close
    using the bar on `briefing_date` vs the next trading bar (history now
    available, so the original ADR 0001 window is honored precisely — no more
    snapshot approximation).
  - `fetch_sparkline_data(tickers, days=5, ...)` → last 5 daily bars.
- Config (`config.py`): `alpaca_api_key_id`, `alpaca_api_secret_key`,
  `alpaca_paper: bool = True`, `alpaca_data_feed: str = "iex"`.

### VIX (still a gap)
Alpaca does **not** carry the CBOE VIX **index** spot (proprietary). Options:
keep `vix_*` null (current behavior, judge grades on SPY + primary), or feed a
VIX **ETF** proxy (`VIXY`/`VXX`) **change** while leaving the prompt's "VIX
level" null (never a fake level). **Proposed**: ETF proxy for *direction only*,
level stays null. Tracked as an open question; not blocking.

### Data-quality note
Free Alpaca data is the **IEX** feed, not full consolidated SIP — daily closes
can differ marginally from the official print. Acceptable for grading and paper
trading; revisit if we ever need exact official closes.

## What's locked vs iterable
- **Locked**: Alpaca is the price-data source; Finnhub remains news-only;
  function signatures (`fetch_24h_close_change`, `fetch_sparkline_data`) stay
  stable for callers.
- **Iterable**: data feed (IEX→SIP if we upgrade), VIX proxy decision, bar
  granularity for future intraday features.

## Verification
- Unit tests with mocked Alpaca bar payloads (mirror the Cycle 4c test style).
- Live read-only smoke: real close-to-close for SPY/GOOGL/USO matches the
  Cycle 4c `/quote` `dp` within rounding.
- Sparkline strip renders 5 real points in a dispatched email.

## Sign-off
- [x] Joe approves Alpaca as the price-data source (Finnhub stays news-only) — 2026-06-08
- [x] Joe approves the VIX-proxy-direction-only decision (`VIXY` change, level stays null) — 2026-06-08
- [x] Joe confirms paper keys are in `.env` (account `PA338…`, verified read-only) — 2026-06-08
