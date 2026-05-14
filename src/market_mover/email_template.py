"""HTML email template for Market Mover briefings."""

import os
import re
import textwrap
from datetime import datetime
from html import escape as html_escape
from urllib.parse import quote as url_quote
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import RankedArticle

RANK_COLORS = {
    1: "#e74c3c",  # Red — top impact
    2: "#f39c12",  # Orange
    3: "#3498db",  # Blue
}

# Small whitelist mapping common netlocs to nicer display names.
# Anything not in the map falls back to the bare netloc (with leading "www." stripped).
_SOURCE_NAME_MAP = {
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "cnbc.com": "CNBC",
    "wsj.com": "WSJ",
    "ft.com": "Financial Times",
    "marketwatch.com": "MarketWatch",
    "finance.yahoo.com": "Yahoo Finance",
    "yahoo.com": "Yahoo",
    "ap.org": "Associated Press",
    "apnews.com": "Associated Press",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
}

# Safe characters allowed in href attribute values (per RFC 3986 unreserved
# + a permissive set of sub-delims commonly seen in real URLs).
_HREF_SAFE_CHARS = ":/?#[]@!$&'()*+,;=%"


def _get_tz() -> ZoneInfo:
    """Resolve the briefing display timezone.

    Checks ``BRIEFING_TZ`` in the environment first (so GitHub Actions / shell
    overrides work), then falls back to the value from :class:`MarketMoverSettings`
    (which reads ``.env``), and finally to ``America/Denver``.
    """
    tz_name = os.environ.get("BRIEFING_TZ")
    if not tz_name:
        try:
            from .config import MarketMoverSettings

            tz_name = MarketMoverSettings().briefing_tz
        except Exception:
            tz_name = "America/Denver"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _now_local() -> datetime:
    """Return the current time in the configured briefing timezone."""
    return datetime.now(_get_tz())


def _safe_href(url: str) -> str:
    """Return a URL safe to drop into an HTML href attribute.

    Articles arrive from RSS / NewsAPI with URLs that may already be percent-encoded,
    so we re-quote idempotently (safe chars include "%") and then HTML-escape the
    result to neutralize any stray quotes.
    """
    if not url:
        return ""
    quoted = url_quote(url, safe=_HREF_SAFE_CHARS)
    return html_escape(quoted, quote=True)


def _derive_source_name(url: str) -> str:
    """Derive a display source name from a URL's netloc.

    Strips a leading "www." and consults a small whitelist for common publishers.
    """
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.lower()
    except (ValueError, AttributeError):
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return _SOURCE_NAME_MAP.get(netloc, netloc)


def _first_sentence(text: str, max_chars: int = 140) -> str:
    """Pull the first sentence (or first ~max_chars) from a block of prose."""
    if not text:
        return ""
    match = re.search(r"(.+?[.!?])(?:\s|$)", text.strip(), flags=re.DOTALL)
    snippet = match.group(1).strip() if match else text.strip()
    if len(snippet) > max_chars:
        snippet = textwrap.shorten(snippet, width=max_chars, placeholder="…")
    return snippet


def render_email_html(articles: list[RankedArticle]) -> str:
    """Render top 3 ranked articles into an HTML email body.

    Args:
        articles: List of ranked articles (up to 3).

    Returns:
        Complete HTML string for the email body.
    """
    now_local = _now_local()
    date_str = html_escape(now_local.strftime("%B %d, %Y"))
    time_str = html_escape(now_local.strftime("%I:%M %p %Z"))
    article_blocks = "\n".join(_render_article_block(a) for a in articles[:3])

    if articles:
        preheader_raw = _first_sentence(articles[0].market_impact_summary) or articles[0].title
    else:
        preheader_raw = "Your daily 3-story market briefing."
    preheader = html_escape(preheader_raw)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#f4f4f4;opacity:0;">
{preheader}
</div>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f4;padding:20px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">

<!-- Header -->
<tr>
<td style="background-color:#1a1a2e;padding:24px 32px;text-align:center;">
  <h1 style="color:#ffffff;margin:0;font-size:24px;font-weight:700;">Market Mover</h1>
  <p style="color:#a0a0b0;margin:8px 0 0;font-size:14px;">Top 3 Market-Moving Stories &mdash; {date_str}</p>
</td>
</tr>

<!-- Articles -->
<tr>
<td style="padding:24px 32px;">
{article_blocks}
</td>
</tr>

<!-- Footer -->
<tr>
<td style="background-color:#f8f8fa;padding:16px 32px;text-align:center;border-top:1px solid #eaeaea;">
  <p style="color:#888;font-size:12px;margin:0;">
    Generated by Market Mover MCP &bull; {time_str}
  </p>
</td>
</tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def render_plain_text(articles: list[RankedArticle]) -> str:
    """Render top 3 ranked articles as plain text fallback.

    Args:
        articles: List of ranked articles (up to 3).

    Returns:
        Plain text string for the email body.
    """
    date_str = _now_local().strftime("%B %d, %Y")
    lines = [
        f"MARKET MOVER — Top 3 Market-Moving Stories — {date_str}",
        "=" * 60,
        "",
    ]

    for article in articles[:3]:
        action = "Watch" if article.is_video else "Read"
        source = _derive_source_name(article.url) or article.source_name
        lines.extend([
            f"#{article.rank} — Impact: {article.impact_score}/10",
            f"  {article.title}",
            f"  Source: {source}",
            f"  {article.market_impact_summary}",
            f"  {action}: {article.url}",
            "",
        ])

    return "\n".join(lines)


def build_subject(articles: list[RankedArticle], prefix: str = "[Market Mover]") -> str:
    """Build the email subject line from the top article.

    Args:
        articles: List of ranked articles.
        prefix: Subject prefix.

    Returns:
        Email subject string.
    """
    date_str = _now_local().strftime("%m/%d")
    if articles:
        # Truncate at a word boundary rather than mid-word.
        top_title = textwrap.shorten(articles[0].title, width=80, placeholder="…")
        return f"{prefix} {date_str}: {top_title}"
    return f"{prefix} {date_str}: Daily Market Briefing"


def _render_article_block(article: RankedArticle) -> str:
    """Render a single article block as HTML."""
    color = RANK_COLORS.get(article.rank, "#888888")
    action_label = "Watch" if article.is_video else "Read"
    action_icon = "&#9654;" if article.is_video else "&#8594;"

    safe_url = _safe_href(article.url)
    safe_title = html_escape(article.title)
    safe_summary = html_escape(article.market_impact_summary)
    safe_source = html_escape(_derive_source_name(article.url) or article.source_name)
    safe_score = html_escape(f"{article.impact_score}")

    return f"""
  <!-- Article #{article.rank} -->
  <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px;">
  <tr>
  <td style="padding:16px;border-left:4px solid {color};background-color:#fafafa;border-radius:0 6px 6px 0;">
    <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td>
        <span style="display:inline-block;background-color:{color};color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:3px;margin-bottom:8px;">
          #{article.rank} &bull; Impact: {safe_score}/10
        </span>
      </td>
    </tr>
    <tr>
      <td style="padding-top:8px;">
        <a href="{safe_url}" style="color:#1a1a2e;font-size:16px;font-weight:600;text-decoration:none;line-height:1.3;">
          {safe_title}
        </a>
      </td>
    </tr>
    <tr>
      <td style="padding-top:4px;">
        <span style="color:#888;font-size:12px;">{safe_source}</span>
      </td>
    </tr>
    <tr>
      <td style="padding-top:8px;">
        <p style="color:#444;font-size:14px;line-height:1.5;margin:0;">
          {safe_summary}
        </p>
      </td>
    </tr>
    <tr>
      <td style="padding-top:12px;">
        <a href="{safe_url}" style="color:{color};font-size:13px;font-weight:600;text-decoration:none;">
          {action_icon} {action_label} full article
        </a>
      </td>
    </tr>
    </table>
  </td>
  </tr>
  </table>"""
