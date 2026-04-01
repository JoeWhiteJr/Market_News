"""NewsAPI source — fetches general market news headlines."""

import logging
import time
from datetime import datetime, timedelta, timezone

from ..models import RawArticle, SourceType

logger = logging.getLogger("market_mover.sources.newsapi")

# Rate limiting
_last_call_time: float = 0.0


def fetch_newsapi_articles(
    api_key: str,
    min_call_interval: float = 1.0,
    max_articles: int = 50,
) -> list[RawArticle]:
    """Fetch general market news from NewsAPI.

    Args:
        api_key: NewsAPI API key.
        min_call_interval: Minimum seconds between API calls.
        max_articles: Maximum number of articles to return.

    Returns:
        List of RawArticle objects. Empty list on failure.
    """
    if not api_key:
        logger.info("NewsAPI key not set, skipping")
        return []

    global _last_call_time
    _enforce_rate_limit(min_call_interval)

    try:
        from newsapi import NewsApiClient

        api = NewsApiClient(api_key=api_key)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=1)

        response = api.get_everything(
            q="stock market OR economy OR federal reserve OR earnings OR Wall Street",
            from_param=start.strftime("%Y-%m-%d"),
            to=end.strftime("%Y-%m-%d"),
            language="en",
            sort_by="relevancy",
            page_size=min(max_articles, 100),
        )

        raw_articles = response.get("articles", [])
        if not raw_articles:
            logger.info("No NewsAPI articles found")
            return []

        articles = []
        for item in raw_articles[:max_articles]:
            published = None
            if item.get("publishedAt"):
                try:
                    published = datetime.fromisoformat(
                        item["publishedAt"].replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            articles.append(
                RawArticle(
                    title=item.get("title", "").strip(),
                    url=item.get("url", ""),
                    source_name=item.get("source", {}).get("name", "NewsAPI"),
                    source_type=SourceType.NEWSAPI,
                    published_at=published,
                    summary=item.get("description", "") or "",
                )
            )

        logger.info(f"Fetched {len(articles)} articles from NewsAPI")
        return articles

    except Exception as e:
        logger.warning(f"NewsAPI fetch failed: {e}")
        return []


def _enforce_rate_limit(min_interval: float) -> None:
    """Enforce minimum interval between API calls."""
    global _last_call_time
    now = time.monotonic()
    elapsed = now - _last_call_time
    if _last_call_time > 0 and elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_call_time = time.monotonic()
