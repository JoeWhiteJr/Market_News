"""Configuration for Market Mover using pydantic-settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Default RSS feeds for market news
_DEFAULT_RSS_FEEDS = ";".join([
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
])

# Default YouTube channel IDs (CNBC, Bloomberg, Yahoo Finance, WSJ)
_DEFAULT_YOUTUBE_CHANNELS = ";".join([
    "UCvJJ_dzjViJCoLf5uKUTwoA",   # CNBC
    "UC-EnprmCZ3OXyAoG7539ESA",   # Bloomberg Television
    "UCIALMKvObZNtJ68-sMkiRA",    # Yahoo Finance
    "UCK7tptUDHh-RYDsdxO1-5QQ",   # WSJ
])


class MarketMoverSettings(BaseSettings):
    """Configuration loaded from .env file with sensible defaults."""

    # LLM API keys (round-robin rotation)
    claude_api_key_1: str = ""
    claude_api_key_2: str = ""
    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""

    # News source API keys
    newsapi_api_key: str = ""
    finnhub_api_key: str = ""
    youtube_api_key: str = ""

    # Alpaca market data (Cycle 5 / ADR 0002) — replaces Finnhub for prices.
    # Paper keys are fine for data; the data API is the same for paper + live.
    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_paper: bool = True
    # Free plans get the IEX feed; "sip" requires a paid subscription.
    alpaca_data_feed: str = "iex"

    @property
    def has_alpaca_creds(self) -> bool:
        """True when both Alpaca data credentials are configured."""
        return bool(self.alpaca_api_key_id and self.alpaca_api_secret_key)

    # Paper-trading track record (Cycle 6 / ADR 0003). PAPER ONLY — there is no
    # live-trading endpoint wired anywhere in this codebase.
    paper_trading_enabled: bool = True
    # Equal-weight notional per position, in paper dollars.
    paper_notional_per_position: float = 1000.0
    # Append-only ledger, one cycle record per trading day.
    paper_trades_jsonl_path: str = "data/paper_trades.jsonl"
    # Paper trading API base — hard-coded to the paper host as a safety rail.
    alpaca_paper_base_url: str = "https://paper-api.alpaca.markets"

    @property
    def paper_trades_jsonl_full_path(self) -> Path:
        """Absolute path to the paper-trades ledger."""
        p = Path(self.paper_trades_jsonl_path)
        return p if p.is_absolute() else _PROJECT_ROOT / p

    # LLM config
    claude_model: str = "claude-sonnet-4-20250514"
    gemini_model: str = "gemini-2.5-flash"
    max_tokens: int = 4096
    temperature: float = 0.2

    # RSS feeds (semicolon-delimited)
    rss_feeds: str = _DEFAULT_RSS_FEEDS

    # YouTube channels (semicolon-delimited channel IDs)
    youtube_channels: str = _DEFAULT_YOUTUBE_CHANNELS

    # Email config
    email_recipients: str = ""
    email_subject_prefix: str = "[Market Mover]"

    # SMTP config (Gmail app password)
    smtp_username: str = ""
    smtp_app_password: str = ""

    # Rate limiting
    min_call_interval_secs: float = 1.0

    # Display timezone for rendered timestamps (header date, footer "generated at")
    briefing_tz: str = "America/Denver"

    # Sparkline strip at the top of the email (Cycle 3)
    sparkline_enabled: bool = True
    sparkline_tickers: str = "SPY,QQQ,DIA,VIX,IWM"

    @property
    def sparkline_ticker_list(self) -> list[str]:
        """Return list of tickers for the top-of-email sparkline strip."""
        return [t.strip().upper() for t in self.sparkline_tickers.split(",") if t.strip()]

    # --- Cycle 3 voice + reasoning layer ------------------------------------
    # Which persona to use day-to-day. Supported: vinny | neutral | terminal | villain.
    briefing_voice: str = "vinny"
    # If the LLM output trips the profanity guardrail, fall back to neutral for
    # today's send (and log a warning). Default on — safer than shipping a
    # potentially-off-tone briefing.
    briefing_voice_override_to_neutral_on_detect: bool = True
    # Weekly mimicry rotation (Mon=0 … Sun=6). -1 disables. Default Wednesday.
    style_mimicry_weekday: int = 2
    # Contrarian "Bear Case" coda — a second LLM call after the ranking.
    # Off = skip the coda entirely (no second LLM call).
    contrarian_coda_enabled: bool = True

    # Overhype Detector (creative #5) — advisory per-story hype-language badge.
    # Deterministic lexicon score, no extra LLM call. Off = hide the badges.
    hype_detector_enabled: bool = True

    # Pre-Market Earnings Card (creative #14) — notable companies reporting
    # earnings today, via Finnhub's free /calendar/earnings. Off = hide it.
    earnings_card_enabled: bool = True
    earnings_card_max: int = 5

    # Sentiment vs Price Divergence flag (creative #15) — flag picks whose
    # narrative fights the tape. Conservative thresholds avoid false positives.
    divergence_flag_enabled: bool = True
    divergence_threshold_pct: float = 2.0
    divergence_lookback_days: int = 5

    # --- Cycle 4A Yesterday-Index ------------------------------------------
    # Path to the append-only JSONL of daily briefing records. Relative paths
    # are resolved against the repo root via :attr:`briefings_jsonl_full_path`.
    briefings_jsonl_path: str = "data/briefings.jsonl"
    # Kill-switch — set to False to hide the scorecard and skip persistence.
    # Joe asked for a single env flag so we can turn the feature off without
    # a redeploy if it ever causes trouble.
    yesterday_index_enabled: bool = True

    # --- Cycle 4B Yesterday-Index judge ------------------------------------
    # Anthropic model used by the Phase B judge. Logged per-row as
    # ``judge_model`` so historical comparisons stay apples-to-apples.
    # IMPORTANT: per ADR 0001 the rubric+prompt are LOCKED. If you change
    # this model, also bump ``JUDGE_PROMPT_VERSION`` in judge.py and
    # manually re-grade history (no automated migration).
    judge_model: str = "claude-sonnet-4-20250514"

    @property
    def briefings_jsonl_full_path(self) -> Path:
        """Resolve :attr:`briefings_jsonl_path` against the repo root.

        Absolute paths are returned untouched so deployments that mount a
        persistent volume can override with e.g. ``/data/briefings.jsonl``.
        """
        p = Path(self.briefings_jsonl_path)
        if p.is_absolute():
            return p
        return _PROJECT_ROOT / p

    @property
    def claude_api_keys(self) -> list[str]:
        """Return list of non-empty Claude API keys."""
        return [k for k in [self.claude_api_key_1, self.claude_api_key_2] if k]

    @property
    def gemini_api_keys(self) -> list[str]:
        """Return list of non-empty Gemini API keys."""
        return [k for k in [self.gemini_api_key_1, self.gemini_api_key_2] if k]

    @property
    def rss_feed_list(self) -> list[str]:
        """Return list of RSS feed URLs."""
        return [url.strip() for url in self.rss_feeds.split(";") if url.strip()]

    @property
    def youtube_channel_list(self) -> list[str]:
        """Return list of YouTube channel IDs."""
        return [ch.strip() for ch in self.youtube_channels.split(";") if ch.strip()]

    @property
    def recipient_list(self) -> list[str]:
        """Return list of email recipients."""
        return [r.strip() for r in self.email_recipients.split(",") if r.strip()]

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_prefix="",
        extra="ignore",
    )
