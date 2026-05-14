# Market Mover MCP

## What it is
A personal MCP server that emails a daily Top-3 market-moving-news briefing,
ranked by Claude with a Gemini fallback. It pulls from NewsAPI, Finnhub, RSS
feeds (Bloomberg, CNBC, MarketWatch), and YouTube finance channels, dedupes,
asks an LLM to rank the three most market-moving stories with a short impact
summary, and emails the result to a configurable recipient list at 6 AM MDT
on weekdays.

## Why it exists
Reading three or four finance sites every morning to decide what actually
matters is a slow, distracting way to start the trading day. Market Mover
does the scroll-and-skim for you and lands a single short email in your
inbox: three stories, one paragraph each, ranked by market impact. If the
ranker fails, you still get a degraded-mode fallback so your morning routine
isn't broken silently.

## Architecture

```
  +-------------+
  |  NewsAPI    |\
  |  Finnhub    | \
  |  RSS feeds  |--+--> dedupe --> LLM ranker  --> email template --> SMTP --> Joe + Jared
  |  YouTube    | /     (URL)      (Claude/                              (Gmail
  +-------------+/                  Gemini)                              app pwd)
```

Fail-soft at every stage: each fetcher returns `[]` on failure, the ranker
falls back from Claude to Gemini, and a degraded-mode email is sent if
ranking fails outright. A GitHub Actions issue is auto-opened on workflow
failure.

## Local setup

```bash
git clone https://github.com/JoeWhiteJr/market-mover-mcp
cd market-mover-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e . -c requirements-lock.txt
cp .env.example .env
# fill in CLAUDE_API_KEY_1, GEMINI_API_KEY_1, NEWSAPI_API_KEY, FINNHUB_API_KEY,
# YOUTUBE_API_KEY, SMTP_USERNAME, SMTP_APP_PASSWORD, EMAIL_RECIPIENTS
market-mover
```

`market-mover` runs the full pipeline once and exits.

## Daily run

The `.github/workflows/daily-briefing.yml` workflow runs on cron
`0 12 * * 1-5` — that's 12:00 UTC, which is 6:00 AM MDT on weekdays. Logs
are visible under the repo's Actions tab. If the run fails, the workflow
opens an issue labeled `automated,daily-briefing-failure` so the failure
is impossible to miss.

## Configuration

All settings load from `.env` (or environment) via pydantic-settings.

| Var | Purpose |
| --- | --- |
| `CLAUDE_API_KEY_1` / `CLAUDE_API_KEY_2` | Anthropic keys, round-robin rotation |
| `GEMINI_API_KEY_1` / `GEMINI_API_KEY_2` | Google Gemini keys (fallback ranker) |
| `NEWSAPI_API_KEY` | NewsAPI.org key for top business headlines |
| `FINNHUB_API_KEY` | Finnhub market-news key |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key for channel video fetches |
| `RSS_FEEDS` | Semicolon-delimited RSS URLs (defaults cover Bloomberg / CNBC / MarketWatch) |
| `YOUTUBE_CHANNELS` | Semicolon-delimited YouTube channel IDs |
| `CLAUDE_MODEL` / `GEMINI_MODEL` | Override default models |
| `MAX_TOKENS` / `TEMPERATURE` | LLM generation knobs |
| `EMAIL_RECIPIENTS` | Comma-separated recipient list |
| `EMAIL_SUBJECT_PREFIX` | Subject-line prefix (default `[Market Mover]`) |
| `SMTP_USERNAME` / `SMTP_APP_PASSWORD` | Gmail address + app password for sending |
| `BRIEFING_TZ` | Display timezone for header date + footer timestamp (default `America/Denver`) |

## Status

Dev-cycle 2 in progress. Cycle 1 shipped HTML escaping, MDT timestamps,
HTTP timeouts, a degraded-mode email path, and an auto-issue alert on
workflow failure. Cycle 2 is adding scheme allow-listing for hrefs,
dark-mode email CSS, badge contrast a11y, and this README.
