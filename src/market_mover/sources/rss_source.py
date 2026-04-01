"""RSS source — fetches market news from configurable RSS feeds."""

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..models import RawArticle, SourceType

logger = logging.getLogger("market_mover.sources.rss")


def fetch_rss_articles(
    feed_urls: list[str],
    max_age_hours: int = 24,
) -> list[RawArticle]:
    """Parse RSS feeds and return articles from the specified time window.

    Args:
        feed_urls: List of RSS feed URLs to parse.
        max_age_hours: Maximum article age in hours.

    Returns:
        List of RawArticle objects. Empty list on failure.
    """
    if not feed_urls:
        logger.info("No RSS feed URLs configured, skipping")
        return []

    try:
        import feedparser
    except ImportError:
        logger.warning("feedparser not installed, skipping RSS")
        return []

    articles = []
    now = datetime.now(timezone.utc)

    for feed_url in feed_urls:
        try:
            feed = feedparser.parse(feed_url)
            feed_title = feed.feed.get("title", feed_url)

            for entry in feed.entries:
                published = _parse_published_date(entry)

                # Skip articles older than max_age_hours
                if published:
                    age_hours = (now - published).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        continue

                title = entry.get("title", "").strip()
                link = entry.get("link", "")
                summary = entry.get("summary", entry.get("description", "")) or ""

                if not title or not link:
                    continue

                articles.append(
                    RawArticle(
                        title=title,
                        url=link,
                        source_name=feed_title,
                        source_type=SourceType.RSS,
                        published_at=published,
                        summary=summary[:500],
                    )
                )

            logger.info(f"Fetched {len(feed.entries)} entries from RSS: {feed_title}")

        except Exception as e:
            logger.warning(f"RSS feed failed ({feed_url}): {e}")
            continue

    logger.info(f"Total RSS articles (within {max_age_hours}h): {len(articles)}")
    return articles


def _parse_published_date(entry: dict) -> datetime | None:
    """Extract and parse the published date from an RSS entry."""
    # Try published_parsed (struct_time from feedparser)
    if entry.get("published_parsed"):
        try:
            from calendar import timegm

            ts = timegm(entry["published_parsed"])
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, TypeError, OverflowError):
            pass

    # Try raw published string
    if entry.get("published"):
        try:
            return parsedate_to_datetime(entry["published"]).astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass

    # Try updated_parsed as fallback
    if entry.get("updated_parsed"):
        try:
            from calendar import timegm

            ts = timegm(entry["updated_parsed"])
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, TypeError, OverflowError):
            pass

    return None
