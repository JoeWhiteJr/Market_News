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
    1: "#c0392b",  # Deep red — top impact (WCAG AA on white text)
    2: "#a05a00",  # Dark amber — was #f39c12, which failed 4.5:1 on #fff (~3.2:1)
    3: "#2470a8",  # Deep blue — WCAG AA on white text
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

# URL schemes that are safe to emit inside an email href.
# Anything else (javascript:, data:, vbscript:, file:, relative/protocol-relative
# URLs) is rejected and replaced with a benign fallback. Article URLs in this
# project always come from RSS / NewsAPI / YouTube and should be absolute http(s).
_ALLOWED_HREF_SCHEMES = frozenset({"http", "https"})
_SAFE_HREF_FALLBACK = "#"


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

    Only ``http`` and ``https`` schemes are allowed. ``javascript:``, ``data:``,
    relative URLs, and protocol-relative URLs (``//example.com/x``) all fall back
    to ``"#"`` — a malicious article URL must not produce an executable link in
    a mail client. Empty / whitespace-only input returns ``""`` so empty hrefs
    stay empty (the surrounding template treats that as a no-op anchor).
    """
    if not url or not url.strip():
        return ""
    try:
        scheme = urlparse(url).scheme.lower()
    except (ValueError, AttributeError):
        return _SAFE_HREF_FALLBACK
    if scheme not in _ALLOWED_HREF_SCHEMES:
        return _SAFE_HREF_FALLBACK
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
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<style>
  /* Dark-mode overrides for clients that honor prefers-color-scheme
     (Apple Mail, iOS Mail, Outlook.com web, recent Gmail web).
     Gmail iOS still auto-inverts; the meta tags above opt us out where supported. */
  @media (prefers-color-scheme: dark) {{
    body, .mm-bg {{ background-color: #0f1116 !important; }}
    .mm-card {{ background-color: #1a1d24 !important; box-shadow: 0 2px 8px rgba(0,0,0,0.6) !important; }}
    .mm-header {{ background-color: #11131a !important; }}
    .mm-header-title {{ color: #e8ebf0 !important; }}
    .mm-header-sub {{ color: #9aa0ad !important; }}
    .mm-article-wrap {{ background-color: #232734 !important; }}
    .mm-title {{ color: #e8ebf0 !important; }}
    .mm-source {{ color: #9aa0ad !important; }}
    .mm-summary {{ color: #c8cdd6 !important; }}
    .mm-footer {{ background-color: #11131a !important; border-top-color: #232734 !important; }}
    .mm-footer-text {{ color: #7a8090 !important; }}
    /* Badges already use a dark-saturated background and #fff text — keep them. */
  }}
</style>
</head>
<body class="mm-bg" style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#f4f4f4;opacity:0;">
{preheader}
</div>
<table width="100%" cellpadding="0" cellspacing="0" class="mm-bg" style="background-color:#f4f4f4;padding:20px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" class="mm-card" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">

<!-- Header -->
<tr>
<td class="mm-header" style="background-color:#1a1a2e;padding:24px 32px;text-align:center;">
  <h1 class="mm-header-title" style="color:#ffffff;margin:0;font-size:24px;font-weight:700;">Market Mover</h1>
  <p class="mm-header-sub" style="color:#a0a0b0;margin:8px 0 0;font-size:14px;">Top 3 Market-Moving Stories &mdash; {date_str}</p>
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
<td class="mm-footer" style="background-color:#f8f8fa;padding:16px 32px;text-align:center;border-top:1px solid #eaeaea;">
  <p class="mm-footer-text" style="color:#888;font-size:12px;margin:0;">
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

    badge_aria = f"Rank {article.rank} story, impact score {safe_score} out of 10"

    return f"""
  <!-- Article #{article.rank} -->
  <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px;">
  <tr>
  <td class="mm-article-wrap" style="padding:16px;border-left:4px solid {color};background-color:#fafafa;border-radius:0 6px 6px 0;">
    <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td>
        <span role="img" aria-label="{badge_aria}" style="display:inline-block;background-color:{color};color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:3px;margin-bottom:8px;">
          #{article.rank} &bull; Impact: {safe_score}/10
        </span>
      </td>
    </tr>
    <tr>
      <td style="padding-top:8px;">
        <a href="{safe_url}" class="mm-title" style="color:#1a1a2e;font-size:16px;font-weight:600;text-decoration:none;line-height:1.3;">
          {safe_title}
        </a>
      </td>
    </tr>
    <tr>
      <td style="padding-top:4px;">
        <span class="mm-source" style="color:#888;font-size:12px;">{safe_source}</span>
      </td>
    </tr>
    <tr>
      <td style="padding-top:8px;">
        <p class="mm-summary" style="color:#444;font-size:14px;line-height:1.5;margin:0;">
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
