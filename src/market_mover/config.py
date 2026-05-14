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
