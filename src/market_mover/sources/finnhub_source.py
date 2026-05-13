"""Finnhub source — fetches general market news."""

import logging
import time
from datetime import datetime, timezone

from ..models import RawArticle, SourceType

logger = logging.getLogger("market_mover.sources.finnhub")

_last_call_time: float = 0.0


def fetch_finnhub_articles(
    api_key: str,
    min_call_interval: float = 1.0,
    max_articles: int = 50,
) -> list[RawArticle]:
    """Fetch general market news from Finnhub.

    Args:
        api_key: Finnhub API key.
        min_call_interval: Minimum seconds between API calls.
        max_articles: Maximum number of articles to return.

    Returns:
        List of RawArticle objects. Empty list on failure.
    """
    if not api_key:
        logger.info("Finnhub API key not set, skipping")
        return []

    _enforce_rate_limit(min_call_interval)

    try:
        import finnhub

        client = finnhub.Client(api_key=api_key)
        # Finnhub Client.DEFAULT_TIMEOUT is 10s class-wide; bump to 20s per task spec
        # and pin it as an instance attribute so it survives any class-level change.
        client.DEFAULT_TIMEOUT = 20
        news = client.general_news("general", min_id=0)

        if not news:
            logger.info("No Finnhub general news found")
            return []

        articles = []
        now = datetime.now(timezone.utc)

        for item in news[:max_articles]:
            # Finnhub timestamps are Unix epoch
            published = None
            if item.get("datetime"):
                try:
                    published = datetime.fromtimestamp(
                        item["datetime"], tz=timezone.utc
                    )
                    # Skip articles older than 24 hours
                    if (now - published).total_seconds() > 86400:
                        continue
                except (ValueError, TypeError, OSError):
                    pass

            articles.append(
                RawArticle(
                    title=item.get("headline", "").strip(),
                    url=item.get("url", ""),
                    source_name=item.get("source", "Finnhub"),
                    source_type=SourceType.FINNHUB,
                    published_at=published,
                    summary=item.get("summary", "") or "",
                )
            )

        logger.info(f"Fetched {len(articles)} articles from Finnhub")
        return articles

    except Exception as e:
        logger.warning(f"Finnhub fetch failed: {e}")
        return []


def _enforce_rate_limit(min_interval: float) -> None:
    """Enforce minimum interval between API calls."""
    global _last_call_time
    now = time.monotonic()
    elapsed = now - _last_call_time
    if _last_call_time > 0 and elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_call_time = time.monotonic()
