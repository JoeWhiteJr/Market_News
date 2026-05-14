"""CLI entry point for running the Market Mover pipeline end-to-end."""

import logging
import socket
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# Backstop network timeout for every socket-level operation in this process.
# Individual SDK calls also set per-call timeouts, but this protects against
# any code path we missed (the May 5 / Apr 28 2026 hangs went 15min without one).
socket.setdefaulttimeout(30)

from .config import MarketMoverSettings  # noqa: E402
from .email_sender import send_email  # noqa: E402
from .email_template import build_subject, render_email_html, render_plain_text  # noqa: E402
from .llm_client import LLMClient  # noqa: E402
from .models import RawArticle, SparklineSeries  # noqa: E402
from .server import _deduplicate_articles  # noqa: E402
from .sources.finnhub_source import fetch_finnhub_articles  # noqa: E402
from .sources.newsapi_source import fetch_newsapi_articles  # noqa: E402
from .sources.quotes_source import fetch_sparkline_data  # noqa: E402
from .sources.rss_source import fetch_rss_articles  # noqa: E402
from .sources.youtube_source import fetch_youtube_videos  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("market_mover.cli")


def _gather_articles(
    settings: MarketMoverSettings,
) -> tuple[list[RawArticle], dict[str, SparklineSeries], dict[str, str]]:
    """Fetch articles + sparkline data from all sources in parallel.

    Sources are fetched concurrently via a ThreadPoolExecutor (one worker per
    fetcher). The sparkline fetch is the 5th task, sharing the same pool so the
    pipeline doesn't pay for a second wave of network latency. Per-source rate
    limits are independent so parallel fetches are safe. The 30s
    ``socket.setdefaulttimeout`` backstop and each fetcher's per-call timeout
    still apply inside the worker threads.

    Returns:
        Tuple of (combined article list, sparkline data, dict mapping source
        name -> error message). A source with no error is omitted from the
        error dict; a source returning zero articles but no exception is also
        omitted (it succeeded, just empty). If the sparkline fetch fails, the
        sparkline dict is empty — callers should treat that as "skip the strip".
    """
    all_articles: list[RawArticle] = []
    sparklines: dict[str, SparklineSeries] = {}
    errors: dict[str, str] = {}

    fetchers: list[tuple[str, Callable[[], list[RawArticle]]]] = [
        (
            "NewsAPI",
            lambda: fetch_newsapi_articles(
                settings.newsapi_api_key, settings.min_call_interval_secs
            ),
        ),
        (
            "Finnhub",
            lambda: fetch_finnhub_articles(
                settings.finnhub_api_key, settings.min_call_interval_secs
            ),
        ),
        ("RSS", lambda: fetch_rss_articles(settings.rss_feed_list)),
        (
            "YouTube",
            lambda: fetch_youtube_videos(
                settings.youtube_api_key, settings.youtube_channel_list
            ),
        ),
    ]

    sparkline_task: Callable[[], dict[str, SparklineSeries]] | None = None
    if settings.sparkline_enabled and settings.finnhub_api_key:
        sparkline_task = lambda: fetch_sparkline_data(  # noqa: E731
            settings.sparkline_ticker_list,
            api_key=settings.finnhub_api_key,
            min_call_interval=settings.min_call_interval_secs,
        )

    total_workers = len(fetchers) + (1 if sparkline_task else 0)

    with ThreadPoolExecutor(max_workers=total_workers) as executor:
        future_to_source = {
            executor.submit(fetcher): source_name for source_name, fetcher in fetchers
        }
        sparkline_future = (
            executor.submit(sparkline_task) if sparkline_task is not None else None
        )

        for future in future_to_source:
            source_name = future_to_source[future]
            try:
                articles = future.result()
                all_articles.extend(articles)
            except Exception as e:
                # Each source is best-effort — capture errors so the degraded email can list them.
                logger.warning(f"{source_name} fetch raised at gather level: {e}")
                errors[source_name] = f"{type(e).__name__}: {e}"

        if sparkline_future is not None:
            try:
                sparklines = sparkline_future.result() or {}
            except Exception as e:
                # Sparkline failure is non-fatal — log and continue without the strip.
                logger.warning(f"Sparkline fetch raised at gather level: {e}")
                sparklines = {}

    return all_articles, sparklines, errors


def _send_degraded_email(
    settings: MarketMoverSettings, source_errors: dict[str, str]
) -> bool:
    """Send a minimal email noting that all sources failed today.

    Joe's hard requirement: never zero emails. A degraded notice beats silence.
    """
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    subject = f"{settings.email_subject_prefix} {datetime.now(timezone.utc).strftime('%m/%d')}: DEGRADED — no articles gathered"

    if source_errors:
        error_lines_text = "\n".join(
            f"  - {name}: {msg}" for name, msg in source_errors.items()
        )
        error_lines_html = "\n".join(
            f"<li><strong>{name}</strong>: {msg}</li>" for name, msg in source_errors.items()
        )
    else:
        error_lines_text = "  (no source raised an exception — all sources returned zero articles)"
        error_lines_html = "<li>No source raised an exception — all sources returned zero articles.</li>"

    plain_text = (
        f"MARKET MOVER — DEGRADED MODE — {date_str}\n"
        f"{'=' * 60}\n\n"
        "No articles were gathered from any source today.\n"
        "Source errors:\n"
        f"{error_lines_text}\n\n"
        "This is a placeholder email so you know the pipeline ran but failed to "
        "find content. Check the GitHub Actions logs for details.\n"
    )
    html_body = (
        "<!DOCTYPE html><html><body style=\"font-family:Arial,Helvetica,sans-serif;"
        "background:#fff4f4;padding:24px;\">"
        f"<h2 style=\"color:#c0392b;margin:0 0 8px;\">Market Mover — DEGRADED MODE</h2>"
        f"<p style=\"color:#555;margin:0 0 16px;\">{date_str}</p>"
        "<p>No articles were gathered from any source today.</p>"
        "<p><strong>Source errors:</strong></p>"
        f"<ul>{error_lines_html}</ul>"
        "<p style=\"color:#888;font-size:12px;\">This is a placeholder email so you "
        "know the pipeline ran but failed to find content. Check the GitHub Actions "
        "logs for details.</p>"
        "</body></html>"
    )

    return send_email(
        subject=subject,
        html_body=html_body,
        plain_text=plain_text,
        recipients=settings.recipient_list,
        settings=settings,
    )


def run_pipeline() -> None:
    """Run the full Market Mover pipeline: gather → analyze → format → send."""
    settings = MarketMoverSettings()

    # Step 1: Gather
    logger.info("Step 1: Gathering news from all sources...")
    all_articles, sparklines, source_errors = _gather_articles(settings)

    deduped = _deduplicate_articles(all_articles)
    logger.info(f"Gathered {len(deduped)} articles (from {len(all_articles)} raw)")
    if sparklines:
        logger.info(f"Sparkline data for {len(sparklines)} tickers: {list(sparklines)}")

    if not deduped:
        logger.error(
            "No articles gathered from any source. Sending degraded-mode email."
        )
        sent = _send_degraded_email(settings, source_errors)
        if not sent:
            logger.error("Degraded email also failed to send")
            sys.exit(1)
        return

    # Step 2: Analyze
    logger.info("Step 2: Analyzing with LLM...")
    client = LLMClient(settings)
    ranked, model_used = client.analyze_articles(deduped)
    logger.info(f"Top 3 ranked by {model_used}")

    for article in ranked:
        logger.info(f"  #{article.rank} [{article.impact_score}/10] {article.title[:60]}")

    # Step 3: Format
    logger.info("Step 3: Formatting email...")
    html_body = render_email_html(ranked, sparklines=sparklines)
    plain_text = render_plain_text(ranked, sparklines=sparklines)
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
