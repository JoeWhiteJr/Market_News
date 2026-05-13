"""YouTube source — fetches recent financial videos via YouTube Data API v3."""

import logging
from datetime import datetime, timedelta, timezone

from ..models import RawArticle, SourceType

logger = logging.getLogger("market_mover.sources.youtube")


def fetch_youtube_videos(
    api_key: str,
    channel_ids: list[str],
    max_results_per_channel: int = 5,
    max_age_hours: int = 24,
) -> list[RawArticle]:
    """Fetch recent videos from financial YouTube channels.

    Args:
        api_key: YouTube Data API v3 key.
        channel_ids: List of YouTube channel IDs to search.
        max_results_per_channel: Max videos per channel.
        max_age_hours: Maximum video age in hours.

    Returns:
        List of RawArticle objects with is_video=True. Empty list on failure.
    """
    if not api_key:
        logger.info("YouTube API key not set, skipping")
        return []

    if not channel_ids:
        logger.info("No YouTube channels configured, skipping")
        return []

    try:
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning("google-api-python-client not installed, skipping YouTube")
        return []

    articles = []
    published_after = (
        datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    ).isoformat()

    # Build an httplib2.Http with an explicit 20s timeout so YouTube API calls
    # can't hang the pipeline. API-key auth doesn't require a credentialed http.
    try:
        import httplib2

        http = httplib2.Http(timeout=20)
    except ImportError:
        http = None

    try:
        youtube = build("youtube", "v3", developerKey=api_key, http=http)
    except Exception as e:
        logger.warning(f"Failed to build YouTube client: {e}")
        return []

    for channel_id in channel_ids:
        try:
            request = youtube.search().list(
                channelId=channel_id,
                order="date",
                type="video",
                publishedAfter=published_after,
                maxResults=max_results_per_channel,
                q="market OR stocks OR economy OR earnings OR Fed OR investing OR Wall Street OR S&P OR Dow OR Nasdaq",
                part="snippet",
            )
            response = request.execute()

            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                video_id = item.get("id", {}).get("videoId", "")

                if not video_id:
                    continue

                published = None
                if snippet.get("publishedAt"):
                    try:
                        published = datetime.fromisoformat(
                            snippet["publishedAt"].replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass

                articles.append(
                    RawArticle(
                        title=snippet.get("title", "").strip(),
                        url=f"https://www.youtube.com/watch?v={video_id}",
                        source_name=snippet.get("channelTitle", "YouTube"),
                        source_type=SourceType.YOUTUBE,
                        published_at=published,
                        summary=snippet.get("description", "") or "",
                        is_video=True,
                    )
                )

            logger.info(
                f"Fetched {len(response.get('items', []))} videos from channel {channel_id}"
            )

        except Exception as e:
            logger.warning(f"YouTube fetch failed for channel {channel_id}: {e}")
            continue

    logger.info(f"Total YouTube videos: {len(articles)}")
    return articles
