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

from datetime import date  # noqa: E402

from .config import MarketMoverSettings  # noqa: E402
from .email_sender import send_email  # noqa: E402
from .email_template import build_subject, render_email_html, render_plain_text  # noqa: E402
from .hype import HypeScore, score_hype  # noqa: E402
from .llm_client import LLMClient  # noqa: E402
from .mimicry import mimicry_voice_for, mimicry_voice_to_voice_spec  # noqa: E402
from .models import RawArticle, SparklineSeries  # noqa: E402
from .judge import JUDGE_PROMPT_VERSION, judge_yesterday, now_iso_utc  # noqa: E402
from .scorecard import (  # noqa: E402
    build_record_from_pipeline,
    commit_daily_record,
    load_yesterday,
)
from .server import _deduplicate_articles  # noqa: E402
from .sources.finnhub_source import fetch_finnhub_articles  # noqa: E402
from .sources.newsapi_source import fetch_newsapi_articles  # noqa: E402
from .sources.quotes_source import fetch_sparkline_data  # noqa: E402
from .sources.rss_source import fetch_rss_articles  # noqa: E402
from .sources.youtube_source import fetch_youtube_videos  # noqa: E402
from .voices import get_voice  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("market_mover.cli")


# Map the full model id returned by LLMClient ("claude-sonnet-4-...",
# "gemini-2.5-flash") down to the literal stored in the JSONL ("claude" or
# "gemini"). Defensive — unknown names bucket as "claude" since that's the
# primary path; the scorecard still renders, the model field is informational.
def _model_family_label(model_used: str) -> str:
    """Return ``"claude"`` or ``"gemini"`` from a full SDK model identifier."""
    name = (model_used or "").lower()
    if "gemini" in name:
        return "gemini"
    return "claude"


# Map an effective voice's ``name`` field back to its persona key. The voice
# dict carries display names ("Vinny from the Floor", "The Chairman") but the
# JSONL schema's ``voice`` field is the persona key (vinny/neutral/...).
_VOICE_NAME_TO_KEY: dict[str, str] = {
    "Vinny from the Floor": "vinny",
    "Neutral": "neutral",
    "Terminal": "terminal",
    "The Chairman": "villain",
}


def _persona_voice_key(voice_spec: dict | None) -> str:
    """Return the persona key for a :class:`VoiceSpec`. Defaults to ``"neutral"``."""
    if not voice_spec:
        return "neutral"
    name = voice_spec.get("name", "")
    return _VOICE_NAME_TO_KEY.get(name, "neutral")


# Map the mimicry display label (subject suffix) back to the JSONL key.
# Kept in sync with mimicry.py's _MIMICRY_VOICES list.
_MIMICRY_NAME_TO_KEY: dict[str, str] = {
    "Jim Cramer": "cramer",
    "Warren Buffett (shareholder letter)": "buffett",
    "Matt Levine": "matt_levine",
    "Zerohedge": "zerohedge",
    "FT leader": "ft_leader",
}


def _mimicry_voice_key(mimicry_label: str | None) -> str | None:
    """Return the JSONL mimicry key for a display label, or ``None``."""
    if not mimicry_label:
        return None
    return _MIMICRY_NAME_TO_KEY.get(mimicry_label)


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
    if settings.sparkline_enabled and settings.has_alpaca_creds:
        sparkline_task = lambda: fetch_sparkline_data(  # noqa: E731
            settings.sparkline_ticker_list,
            api_key_id=settings.alpaca_api_key_id,
            api_secret_key=settings.alpaca_api_secret_key,
            feed=settings.alpaca_data_feed,
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

    # Voice resolution: mimicry day wins over the configured persona.
    mim = mimicry_voice_for(date.today(), settings.style_mimicry_weekday)
    if mim is not None:
        active_voice = mimicry_voice_to_voice_spec(mim)
        mimicry_label = mim["name"]
        logger.info(f"Mimicry day — voice override: {mimicry_label}")
    else:
        active_voice = get_voice(settings.briefing_voice)
        mimicry_label = None
        logger.info(f"Voice: {active_voice.get('name')}")

    ranked, model_used, effective_voice = client.analyze_articles(deduped, voice=active_voice)
    logger.info(f"Top 3 ranked by {model_used} (effective voice: {effective_voice.get('name')})")
    model_family = _model_family_label(model_used)

    for article in ranked:
        logger.info(f"  #{article.rank} [{article.impact_score}/10] {article.title[:60]}")

    # Optional: contrarian "Bear Case" coda — second LLM call.
    coda = None
    if settings.contrarian_coda_enabled and ranked:
        logger.info("Step 2b: Generating contrarian coda...")
        try:
            coda = client.generate_contrarian_coda(ranked[0], deduped)
            if coda is None:
                logger.info("Contrarian coda not produced for today (validation skipped)")
            else:
                logger.info(f"Contrarian coda: {coda.headline}")
        except Exception as e:
            # The daily send must not fail because the optional coda failed.
            logger.warning(f"Contrarian coda generation raised: {e}; continuing without it")
            coda = None
    else:
        logger.info("Contrarian coda disabled by config; skipping second LLM call")

    # If the voice was overridden to neutral by the profanity guardrail, also
    # drop the mimicry subject label — the bit doesn't land if the prose isn't there.
    if effective_voice.get("name") != active_voice.get("name"):
        mimicry_label = None

    # Step 3: Format
    logger.info("Step 3: Formatting email...")
    today = date.today()

    # Yesterday-Index: load the previous run's record (if any) so the scorecard
    # slot can render between the sparkline and Top 3. Failures here are
    # non-fatal — the email still ships if persistence has gone sideways.
    yesterday_record = None
    yesterday_judgments = None
    jsonl_path = settings.briefings_jsonl_full_path
    if settings.yesterday_index_enabled:
        try:
            yesterday_record = load_yesterday(jsonl_path, today)
            if yesterday_record is not None:
                logger.info(
                    "Yesterday-Index: rendering scorecard for %s (%d picks)",
                    yesterday_record.date,
                    len(yesterday_record.picks),
                )
            else:
                logger.info(
                    "Yesterday-Index: no prior record at %s — scorecard hidden",
                    jsonl_path,
                )
        except Exception as e:
            # Defense in depth — load_yesterday already swallows expected errors,
            # but if anything surprising bubbles up we still want today to ship.
            logger.warning(f"Yesterday-Index load raised unexpectedly: {e}")
            yesterday_record = None

        # Cycle 4 Phase B: if yesterday isn't already graded, run the LLM
        # judge against its 3 picks now. Wrapped in a broad try/except —
        # judge failures NEVER break the daily send (the scorecard falls
        # back to the Phase A "TBD" placeholder).
        if yesterday_record is not None and yesterday_record.judgments is None:
            logger.info(
                "Yesterday-Index: invoking judge for %s (%d picks)",
                yesterday_record.date,
                len(yesterday_record.picks),
            )
            try:
                yesterday_judgments = judge_yesterday(
                    yesterday_record, settings, client
                )
                if yesterday_judgments:
                    # Stamp the loaded record so the scorecard renders the
                    # real verdicts. The persisted patch in
                    # commit_daily_record happens below.
                    yesterday_record = yesterday_record.model_copy(
                        update={
                            "judgments": yesterday_judgments,
                            "graded_at": now_iso_utc(),
                            "judge_model": settings.judge_model,
                            "judge_prompt_version": JUDGE_PROMPT_VERSION,
                        }
                    )
                    logger.info(
                        "Yesterday-Index: judged %d/%d picks",
                        len(yesterday_judgments),
                        len(yesterday_record.picks),
                    )
                else:
                    logger.warning(
                        "Yesterday-Index: judge returned no judgments — "
                        "scorecard falls back to TBD placeholder"
                    )
            except Exception as e:
                logger.warning(
                    "Yesterday-Index: judge raised (%s) — continuing without "
                    "real verdicts",
                    e,
                )
                yesterday_judgments = None

    # Overhype Detector (creative #5): advisory per-story hype-language score.
    # Deterministic — no LLM call. Disabled => empty map => no badges render.
    hype_scores: dict[int, HypeScore] = {}
    if settings.hype_detector_enabled:
        hype_scores = {
            a.rank: score_hype(a.title, a.market_impact_summary) for a in ranked
        }
        flagged = sum(1 for h in hype_scores.values() if h.score > 0)
        logger.info(f"Overhype Detector: {flagged}/{len(hype_scores)} stories flagged")

    html_body = render_email_html(
        ranked,
        sparklines=sparklines,
        voice=effective_voice,
        coda=coda,
        yesterday=yesterday_record,
        hype_scores=hype_scores,
    )
    plain_text = render_plain_text(
        ranked,
        sparklines=sparklines,
        voice=effective_voice,
        coda=coda,
        yesterday=yesterday_record,
        hype_scores=hype_scores,
    )
    subject = build_subject(
        ranked,
        settings.email_subject_prefix,
        mimicry_label=mimicry_label,
    )

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

    # Step 5: Persist today's record AND patch yesterday's row with the
    # judgments we computed above. Cycle 4 Phase B uses a single atomic
    # rename for both writes — see ``commit_daily_record`` for the
    # crash-resilience reasoning.
    if settings.yesterday_index_enabled:
        try:
            mim_key = _mimicry_voice_key(mimicry_label)
            record = build_record_from_pipeline(
                today=today,
                ranked=ranked,
                coda=coda,
                model_used=model_family,
                voice=_persona_voice_key(effective_voice),
                mimicry_voice=mim_key,
            )
            commit_daily_record(
                today_record=record,
                yesterday_judgments=yesterday_judgments,
                path=jsonl_path,
                judge_model=settings.judge_model if yesterday_judgments else None,
                judge_prompt_version=(
                    JUDGE_PROMPT_VERSION if yesterday_judgments else None
                ),
            )
            logger.info(
                "Yesterday-Index: persisted today's record to %s (yesterday patched: %s)",
                jsonl_path,
                bool(yesterday_judgments),
            )
        except Exception as e:
            logger.warning(
                "Yesterday-Index: failed to persist today's record (%s) — "
                "email already sent, continuing",
                e,
            )


if __name__ == "__main__":
    run_pipeline()
