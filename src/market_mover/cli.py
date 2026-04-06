"""CLI entry point for running the Market Mover pipeline end-to-end."""

import logging
import sys

from .config import MarketMoverSettings
from .email_sender import send_email
from .email_template import build_subject, render_email_html, render_plain_text
from .llm_client import LLMClient
from .models import RawArticle
from .sources.finnhub_source import fetch_finnhub_articles
from .sources.newsapi_source import fetch_newsapi_articles
from .sources.rss_source import fetch_rss_articles
from .sources.youtube_source import fetch_youtube_videos
from .server import _deduplicate_articles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("market_mover.cli")


def run_pipeline() -> None:
    """Run the full Market Mover pipeline: gather → analyze → format → send."""
    settings = MarketMoverSettings()

    # Step 1: Gather
    logger.info("Step 1: Gathering news from all sources...")
    all_articles: list[RawArticle] = []

    all_articles.extend(
        fetch_newsapi_articles(settings.newsapi_api_key, settings.min_call_interval_secs)
    )
    all_articles.extend(
        fetch_finnhub_articles(settings.finnhub_api_key, settings.min_call_interval_secs)
    )
    all_articles.extend(fetch_rss_articles(settings.rss_feed_list))
    all_articles.extend(
        fetch_youtube_videos(settings.youtube_api_key, settings.youtube_channel_list)
    )

    deduped = _deduplicate_articles(all_articles)
    logger.info(f"Gathered {len(deduped)} articles (from {len(all_articles)} raw)")

    if not deduped:
        logger.error("No articles gathered from any source. Aborting.")
        sys.exit(1)

    # Step 2: Analyze
    logger.info("Step 2: Analyzing with LLM...")
    client = LLMClient(settings)
    ranked, model_used = client.analyze_articles(deduped)
    logger.info(f"Top 3 ranked by {model_used}")

    for article in ranked:
        logger.info(f"  #{article.rank} [{article.impact_score}/10] {article.title[:60]}")

    # Step 3: Format
    logger.info("Step 3: Formatting email...")
    html_body = render_email_html(ranked)
    plain_text = render_plain_text(ranked)
    subject = build_subject(ranked, settings.email_subject_prefix)

    # Step 4: Send
    logger.info(f"Step 4: Sending to {settings.recipient_list}...")
    success = send_email(
        subject=subject,
        html_body=html_body,
        plain_text=plain_text,
        recipients=settings.recipient_list,
        settings=settings,
    )

    if success:
        logger.info("Pipeline complete — email sent successfully!")
    else:
        logger.error("Pipeline complete — email sending FAILED")
        sys.exit(1)


if __name__ == "__main__":
    run_pipeline()
