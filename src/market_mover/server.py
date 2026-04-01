"""Market Mover MCP Server — daily market-moving news briefings.

Exposes 3 tools:
  1. gather_news — Fetch from all configured sources, deduplicate
  2. analyze_and_rank — Use Claude/Gemini to rank by market impact
  3. format_email — Render top 3 into HTML email
"""

import json
import logging
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse

from fastmcp import FastMCP

from .config import MarketMoverSettings
from .email_template import build_subject, render_email_html, render_plain_text
from .llm_client import LLMClient
from .models import RankedArticle, RawArticle

logger = logging.getLogger("market_mover.server")
logging.basicConfig(level=logging.INFO)

mcp = FastMCP("Market Mover", instructions="Daily market-moving news briefing — top 3 articles")

# Lazy-initialized settings and LLM client
_settings: MarketMoverSettings | None = None
_llm_client: LLMClient | None = None


def _get_settings() -> MarketMoverSettings:
    global _settings
    if _settings is None:
        _settings = MarketMoverSettings()
    return _settings


def _get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient(_get_settings())
    return _llm_client


@mcp.tool()
def gather_news() -> dict:
    """Gather news from all configured sources (NewsAPI, Finnhub, RSS, YouTube).

    Fetches articles from the last 24 hours, deduplicates by URL and title
    similarity, and returns the combined list.

    Returns a dict with:
    - articles: list of raw articles (title, url, source, summary, etc.)
    - source_counts: breakdown of article count by source type
    - total: total number of deduplicated articles
    - gathered_at: ISO timestamp
    """
    from datetime import datetime, timezone

    from .sources.finnhub_source import fetch_finnhub_articles
    from .sources.newsapi_source import fetch_newsapi_articles
    from .sources.rss_source import fetch_rss_articles
    from .sources.youtube_source import fetch_youtube_videos

    settings = _get_settings()
    all_articles: list[RawArticle] = []

    # Fetch from all sources (graceful degradation — empty list on failure)
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

    # Deduplicate
    deduped = _deduplicate_articles(all_articles)

    # Count by source
    source_counts: dict[str, int] = {}
    for article in deduped:
        source_counts[article.source_type.value] = (
            source_counts.get(article.source_type.value, 0) + 1
        )

    logger.info(f"Gathered {len(deduped)} articles (from {len(all_articles)} raw)")

    return {
        "articles": [a.model_dump(mode="json") for a in deduped],
        "source_counts": source_counts,
        "total": len(deduped),
        "gathered_at": datetime.now(timezone.utc).isoformat(),
    }


@mcp.tool()
def analyze_and_rank(articles_json: str) -> dict:
    """Analyze articles using Claude/Gemini and rank by market impact.

    Takes the raw articles JSON from gather_news and sends them to an LLM
    for market impact analysis. Returns the top 3 most impactful articles
    with summaries explaining why they move markets.

    Args:
        articles_json: JSON string of raw articles from gather_news output.
            Pass the 'articles' array from gather_news result.

    Returns a dict with:
    - top_3: list of ranked articles with impact summaries and scores
    - model_used: which LLM performed the analysis
    - analyzed_at: ISO timestamp
    """
    from datetime import datetime, timezone

    articles_data = json.loads(articles_json)
    articles = [RawArticle(**a) for a in articles_data]

    if not articles:
        return {
            "top_3": [],
            "model_used": "none",
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "error": "No articles to analyze",
        }

    client = _get_llm_client()
    ranked, model_used = client.analyze_articles(articles)

    return {
        "top_3": [a.model_dump(mode="json") for a in ranked],
        "model_used": model_used,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


@mcp.tool()
def format_email(ranked_articles_json: str) -> dict:
    """Format the top 3 ranked articles into an HTML email.

    Takes the ranked articles JSON from analyze_and_rank and renders them
    into a polished HTML email ready to send via Gmail.

    Args:
        ranked_articles_json: JSON string of ranked articles from analyze_and_rank.
            Pass the 'top_3' array from analyze_and_rank result.

    Returns a dict with:
    - subject: email subject line
    - html_body: complete HTML email body
    - plain_text: plain text fallback
    - recipients: list of configured email recipients
    """
    settings = _get_settings()
    articles_data = json.loads(ranked_articles_json)
    articles = [RankedArticle(**a) for a in articles_data]

    return {
        "subject": build_subject(articles, settings.email_subject_prefix),
        "html_body": render_email_html(articles),
        "plain_text": render_plain_text(articles),
        "recipients": settings.recipient_list,
    }


def _normalize_url(url: str) -> str:
    """Normalize a URL for deduplication (strip query params, trailing slashes)."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _deduplicate_articles(articles: list[RawArticle]) -> list[RawArticle]:
    """Remove duplicate articles by URL and fuzzy title matching.

    Keeps the article with the longer summary when duplicates are found.
    """
    seen_urls: dict[str, RawArticle] = {}
    unique: list[RawArticle] = []

    for article in articles:
        if not article.title or not article.url:
            continue

        normalized_url = _normalize_url(article.url)

        # Check URL dedup
        if normalized_url in seen_urls:
            existing = seen_urls[normalized_url]
            if len(article.summary) > len(existing.summary):
                seen_urls[normalized_url] = article
                unique = [a for a in unique if _normalize_url(a.url) != normalized_url]
                unique.append(article)
            continue

        # Check fuzzy title dedup against existing articles
        is_duplicate = False
        for existing in unique:
            similarity = SequenceMatcher(
                None, article.title.lower(), existing.title.lower()
            ).ratio()
            if similarity > 0.80:
                is_duplicate = True
                if len(article.summary) > len(existing.summary):
                    unique.remove(existing)
                    unique.append(article)
                    seen_urls[_normalize_url(article.url)] = article
                break

        if not is_duplicate:
            seen_urls[normalized_url] = article
            unique.append(article)

    return unique


if __name__ == "__main__":
    mcp.run()
