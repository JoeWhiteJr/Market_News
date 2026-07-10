"""HTML email template for Market Mover briefings."""

import os
import re
import textwrap
from datetime import datetime
from html import escape as html_escape
from urllib.parse import quote as url_quote
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .divergence import DivergenceFlag
from .hype import HypeScore
from .learning import CategoryReport
from .models import ContrarianCoda, RankedArticle, SparklineSeries
from .sources.earnings_source import EarningsEntry
from .sources.insider_source import InsiderBuy
from .scorecard import (
    BriefingRecord,
    render_scorecard_html,
    render_scorecard_plain_text,
)
from .visuals import (
    render_category_card_html,
    render_category_card_plain,
    render_index_strip_html,
    render_index_strip_plain,
    render_sector_heatmap_html,
    render_sector_heatmap_plain,
    render_streak_row_html,
    render_streak_row_plain,
)

RANK_COLORS = {
    1: "#c0392b",  # Deep red — top impact (WCAG AA on white text)
    2: "#a05a00",  # Dark amber — was #f39c12, which failed 4.5:1 on #fff (~3.2:1)
    3: "#2470a8",  # Deep blue — WCAG AA on white text
}

# Sparkline up/down colors. Light-mode values must pass WCAG AA (4.5:1) against
# the white email body (#ffffff); dark-mode overrides (defined in the
# @prefers-color-scheme block below) must pass against the dark card bg
# (#1a1d24). The `flat` color is a neutral mid-gray that reads on both.
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


def render_email_html(
    articles: list[RankedArticle],
    sparklines: dict[str, SparklineSeries] | None = None,
    voice: dict | None = None,
    coda: ContrarianCoda | None = None,
    yesterday: BriefingRecord | None = None,
    hype_scores: dict[int, HypeScore] | None = None,
    paper_stats: dict | None = None,
    earnings: list["EarningsEntry"] | None = None,
    divergences: list["DivergenceFlag"] | None = None,
    insider_buys: list["InsiderBuy"] | None = None,
    macro_mode: bool = False,
    sector_moves: list[tuple[str, str, float]] | None = None,
    category_report: CategoryReport | None = None,
    streak_records: list[dict] | None = None,
) -> str:
    """Render top 3 ranked articles into an HTML email body.

    Args:
        articles: List of ranked articles (up to 3).
        sparklines: Optional mapping of ticker -> :class:`SparklineSeries`. When
            provided and non-empty, a sparkline strip renders at the top of the
            email body (right after the hidden preheader). Render order matches
            ``dict`` insertion order so callers control which ticker shows first.
        voice: Optional voice spec — its ``signoff`` is rendered in the footer.
        coda: Optional ``ContrarianCoda`` rendered at the bottom (just before
            the footer) inside ``<section data-block="contrarian">``.
        yesterday: Optional :class:`BriefingRecord` from the previous day. When
            present, a scorecard section is rendered between the sparkline
            strip (top) and the Top 3 articles. Phase A shows placeholder
            verdicts; Phase B will fill in real verdicts.
        hype_scores: Optional mapping of article rank -> :class:`HypeScore`
            (Overhype Detector, creative #5). When provided, an advisory
            hype-language badge renders next to each story's impact badge.

    Returns:
        Complete HTML string for the email body.
    """
    now_local = _now_local()
    date_str = html_escape(now_local.strftime("%B %d, %Y"))
    time_str = html_escape(now_local.strftime("%I:%M %p %Z"))
    hype_scores = hype_scores or {}
    article_blocks = "\n".join(
        _render_article_block(a, hype_scores.get(a.rank)) for a in articles[:3]
    )
    # Gmail-safe index strip (MM-T006): replaces the old inline-SVG sparklines,
    # which Gmail strips entirely. Colored table cells render everywhere.
    sparkline_block = render_index_strip_html(sparklines or {})

    # "Insights" visuals (MM-T006): sector heat-map, recent-form streak, and the
    # category report card — all colored-table-cell blocks. Wrapped as one card
    # row in the reference zone; each sub-block hides itself when it has no data.
    heatmap_html = render_sector_heatmap_html(sector_moves or [])
    streak_html = render_streak_row_html(streak_records or [])
    card_html = (
        render_category_card_html(category_report) if category_report is not None else ""
    )
    insights_inner = heatmap_html + streak_html + card_html
    insights_block = (
        f'<tr><td class="mm-darkcard" style="padding:8px 32px 4px;">{insights_inner}</td></tr>'
        if insights_inner
        else ""
    )

    scorecard_block = render_scorecard_html(yesterday, now_local.date())
    scorecard_block += _render_paper_block_html(paper_stats)
    earnings_block = _render_earnings_block_html(earnings or [])
    divergence_block = _render_divergence_block_html(divergences or [])
    insider_block = _render_insider_block_html(insider_buys or [])
    macro_badge = (
        '<span style="display:inline-block;margin-top:8px;background-color:#2d3a8c;'
        'color:#fff;font-size:11px;font-weight:700;padding:2px 10px;border-radius:10px;">'
        "&#127757; MACRO MODE</span>"
        if macro_mode else ""
    )

    if articles:
        preheader_raw = _first_sentence(articles[0].market_impact_summary) or articles[0].title
    else:
        preheader_raw = "Your daily 3-story market briefing."
    preheader = html_escape(preheader_raw)

    contrarian_block = _render_contrarian_block(coda) if coda is not None else ""

    signoff_raw = (voice or {}).get("signoff", "") if voice else ""
    if signoff_raw:
        signoff_html = (
            f'<p class="mm-footer-text mm-signoff" style="color:#888;font-size:12px;'
            f'margin:0 0 6px;font-style:italic;">{html_escape(signoff_raw)}</p>'
        )
    else:
        signoff_html = ""

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
    /* Contrarian "Bear Case" section. */
    .mm-contrarian-wrap {{ background-color: #1a1d24 !important; border-top-color: #232734 !important; }}
    .mm-contrarian-card {{ background-color: #232734 !important; border-left-color: #b9863d !important; }}
    .mm-contrarian-eyebrow {{ color: #b9863d !important; }}
    .mm-contrarian-headline {{ color: #e8ebf0 !important; }}
    .mm-contrarian-argument {{ color: #c8cdd6 !important; }}
    .mm-contrarian-source {{ color: #9aa0ad !important; }}
    /* Yesterday-Index scorecard (Cycle 4 Phase A). */
    .mm-scorecard-wrap {{ background-color: #1a1d24 !important; }}
    .mm-scorecard-card {{ background-color: #232734 !important; border-left-color: #8a93a8 !important; }}
    .mm-scorecard-eyebrow {{ color: #b3bcd1 !important; }}
    .mm-scorecard-sub {{ color: #9aa0ad !important; }}
    .mm-scorecard-row {{ border-bottom-color: #2c3140 !important; }}
    .mm-scorecard-title {{ color: #e8ebf0 !important; }}
    .mm-scorecard-meta {{ color: #9aa0ad !important; }}
    .mm-scorecard-verdict {{ background-color: #2c3140 !important; color: #b3bcd1 !important; }}
    /* Badges already use a dark-saturated background and #fff text — keep them. */
    /* Cycle 5/6 + creative cards: give the newer light cards real dark surfaces.
       Inline colors on children override parent classes, so each colored element
       carries its own class. */
    .mm-darkcard {{ background-color: #232734 !important; }}
    .mm-darktext {{ color: #c8cdd6 !important; }}
    .mm-darkticker {{ color: #e8ebf0 !important; }}
    .mm-darklabel-ref {{ color: #b3bcd1 !important; }}
    .mm-darklabel-warn {{ color: #e0944f !important; }}
    .mm-darklabel-bull {{ color: #6ee7a0 !important; }}
    .mm-darkborder-bull {{ border-left-color: #3fae6a !important; }}
  }}
</style>
</head>
<body class="mm-bg" style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#f4f4f4;opacity:0;">
{preheader}
</div>
{sparkline_block}
<table width="100%" cellpadding="0" cellspacing="0" class="mm-bg" style="background-color:#f4f4f4;padding:20px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" class="mm-card" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">

<!-- Header -->
<tr>
<td class="mm-header" style="background-color:#1a1a2e;padding:24px 32px;text-align:center;">
  <h1 class="mm-header-title" style="color:#ffffff;margin:0;font-size:24px;font-weight:700;">Market Mover</h1>
  <p class="mm-header-sub" style="color:#a0a0b0;margin:8px 0 0;font-size:14px;">Top 3 Market-Moving Stories &mdash; {date_str}</p>
  {macro_badge}
</td>
</tr>
<!-- Alerts: high-signal, act-on-today flags sit directly under the header -->
{divergence_block}
{insider_block}
<!-- Top 3 stories — the core, kept high so it's the first thing after alerts -->
<tr>
<td style="padding:24px 32px;">
{article_blocks}
</td>
</tr>

{contrarian_block}
<!-- Reference zone: retrospective + context, demoted below the stories -->
{insights_block}
{scorecard_block}
{earnings_block}
<!-- Footer -->
<tr>
<td class="mm-footer" style="background-color:#f8f8fa;padding:16px 32px;text-align:center;border-top:1px solid #eaeaea;">
  {signoff_html}
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


def render_plain_text(
    articles: list[RankedArticle],
    sparklines: dict[str, SparklineSeries] | None = None,
    voice: dict | None = None,
    coda: ContrarianCoda | None = None,
    yesterday: BriefingRecord | None = None,
    hype_scores: dict[int, HypeScore] | None = None,
    paper_stats: dict | None = None,
    earnings: list["EarningsEntry"] | None = None,
    divergences: list["DivergenceFlag"] | None = None,
    insider_buys: list["InsiderBuy"] | None = None,
    macro_mode: bool = False,
    sector_moves: list[tuple[str, str, float]] | None = None,
    category_report: CategoryReport | None = None,
    streak_records: list[dict] | None = None,
) -> str:
    """Render top 3 ranked articles as plain text fallback.

    Args:
        articles: List of ranked articles (up to 3).
        sparklines: Optional sparkline strip — rendered as a single line of
            ``TICKER +/-X.X%`` pairs above the date header when present.
        voice: Optional voice spec; its ``signoff`` is appended.
        coda: Optional contrarian coda — appended as a "Bear Case" section.
        yesterday: Optional previous-day record. When present, a scorecard
            section is appended between the sparkline strip and the Top 3.
        hype_scores: Optional mapping of article rank -> :class:`HypeScore`
            (Overhype Detector). When present, an advisory hype line is added
            under each story.

    Returns:
        Plain text string for the email body.
    """
    hype_scores = hype_scores or {}
    now_local = _now_local()
    date_str = now_local.strftime("%B %d, %Y")
    lines = []
    divergence_text = _render_divergence_block_plain(divergences or [])
    if divergence_text:
        lines.append(divergence_text)
        lines.append("")
    index_line = render_index_strip_plain(sparklines or {})
    if index_line:
        lines.append(index_line)
        lines.append("")

    scorecard_text = render_scorecard_plain_text(yesterday, now_local.date())
    if scorecard_text:
        lines.append(scorecard_text)
        lines.append("")

    paper_line = _render_paper_block_plain(paper_stats)
    if paper_line:
        lines.append(paper_line)
        lines.append("")

    # Insights (MM-T006): sector heatmap, recent-form streak, category card.
    for block_text in (
        render_sector_heatmap_plain(sector_moves or []),
        render_streak_row_plain(streak_records or []),
        render_category_card_plain(category_report) if category_report is not None else "",
    ):
        if block_text:
            lines.append(block_text)
            lines.append("")

    earnings_text = _render_earnings_block_plain(earnings or [])
    if earnings_text:
        lines.append(earnings_text)
        lines.append("")

    insider_text = _render_insider_block_plain(insider_buys or [])
    if insider_text:
        lines.append(insider_text)
        lines.append("")

    macro_tag = "  [MACRO MODE]" if macro_mode else ""
    lines.extend([
        f"MARKET MOVER — Top 3 Market-Moving Stories — {date_str}{macro_tag}",
        "=" * 60,
        "",
    ])

    for article in articles[:3]:
        action = "Watch" if article.is_video else "Read"
        source = _derive_source_name(article.url) or article.source_name
        hype = hype_scores.get(article.rank)
        impact_line = f"#{article.rank} — Impact: {article.impact_score}/10"
        if hype is not None and hype.score > 0:
            impact_line += f"  [! {hype.label}]"
        lines.extend([
            impact_line,
            f"  {article.title}",
            f"  Source: {source}",
            f"  {article.market_impact_summary}",
            f"  {action}: {article.url}",
            "",
        ])

    if coda is not None:
        coda_source = _derive_source_name(coda.source_url) or coda.source_name
        lines.extend([
            "THE BEAR CASE",
            "-" * 60,
            f"  {coda.headline}",
            f"  {coda.argument}",
            f"  Source: {coda_source}",
            f"  Read: {coda.source_url}",
            "",
        ])

    signoff = (voice or {}).get("signoff", "") if voice else ""
    if signoff:
        lines.append(signoff)

    return "\n".join(lines)


def build_subject(
    articles: list[RankedArticle],
    prefix: str = "[Market Mover]",
    mimicry_label: str | None = None,
) -> str:
    """Build the email subject line from the top article.

    Args:
        articles: List of ranked articles.
        prefix: Subject prefix.
        mimicry_label: When set, append ``" — in the voice of {mimicry_label}"``
            so it's obvious the day's prose is a parody bit. The label is
            *not* HTML-escaped because subject lines aren't HTML.

    Returns:
        Email subject string.
    """
    date_str = _now_local().strftime("%m/%d")
    if articles:
        # Truncate at a word boundary rather than mid-word.
        top_title = textwrap.shorten(articles[0].title, width=80, placeholder="…")
        subject = f"{prefix} {date_str}: {top_title}"
    else:
        subject = f"{prefix} {date_str}: Daily Market Briefing"
    if mimicry_label:
        subject = f"{subject} — in the voice of {mimicry_label}"
    return subject


# Overhype Detector badge colors, keyed by band. Muted on purpose — the badge
# is advisory and must not out-shout the rank/impact badge.
_HYPE_BAND_COLORS = {
    "low": "#6b7280",     # slate gray — measured language
    "medium": "#b45309",  # amber — getting punchy
    "high": "#b91c1c",    # red — breathless
}


def _render_hype_badge(hype: HypeScore | None) -> str:
    """Render the advisory Overhype badge as an inline HTML span.

    Returns an empty string when ``hype`` is ``None`` (feature disabled) or
    the score is 0 (nothing to flag — a clean headline shouldn't carry a
    "Hype 0/10" badge). Matched terms go in the ``title`` attribute as a
    hover tooltip and the ``aria-label`` for screen readers.
    """
    if hype is None or hype.score <= 0:
        return ""

    color = _HYPE_BAND_COLORS.get(hype.band, "#6b7280")
    safe_label = html_escape(hype.label)
    if hype.matched_terms:
        terms = ", ".join(hype.matched_terms)
        tooltip = html_escape(f"Hype language flagged: {terms}")
    else:
        tooltip = html_escape("Hype language score")

    return (
        f'<span role="img" aria-label="{tooltip}" title="{tooltip}" '
        f'style="display:inline-block;border:1px solid {color};color:{color};'
        "background-color:#fff;font-size:11px;font-weight:700;padding:1px 7px;"
        'border-radius:3px;margin-bottom:8px;margin-left:6px;">'
        f"&#9888; {safe_label}</span>"
    )


def _render_paper_block_html(paper_stats: dict | None) -> str:
    """Render the compact "Paper Portfolio" track-record line (Cycle 6).

    Returns an empty string until there's an equity snapshot to show. P&L and
    win-rate only appear once at least one trade has closed.
    """
    if not paper_stats or paper_stats.get("equity") is None:
        return ""

    equity = paper_stats["equity"]
    n = paper_stats.get("n_trades") or 0
    total_pnl = paper_stats.get("total_pnl") or 0.0
    win_rate = paper_stats.get("win_rate")
    wins = paper_stats.get("wins") or 0

    bits = [f"Equity ${equity:,.0f}"]
    if n > 0:
        pnl_color = "#1a7f37" if total_pnl >= 0 else "#b91c1c"
        sign = "+" if total_pnl >= 0 else "−"
        bits.append(
            f'<span style="color:{pnl_color};font-weight:700;">P&amp;L {sign}'
            f"${abs(total_pnl):,.0f}</span>"
        )
        if win_rate is not None:
            bits.append(f"Win {win_rate:.0f}% ({wins}/{n})")
    inner = " &nbsp;&bull;&nbsp; ".join(bits)

    return f"""
<tr>
<td style="padding:8px 32px 0;">
  <section data-block="paper">
  <table width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 4px;">
  <tr>
  <td class="mm-darkcard" style="padding:10px 14px;background-color:#f4f5f8;border-left:4px solid #8a93a8;border-radius:0 6px 6px 0;">
    <span class="mm-darklabel-ref" style="font-size:12px;font-weight:700;color:#5b6473;">&#128200; PAPER PORTFOLIO</span>
    <span style="font-size:11px;color:#9aa0ad;"> &mdash; paper money, picks auto-traded</span>
    <div class="mm-darktext" style="font-size:13px;color:#333;padding-top:4px;">{inner}</div>
  </td>
  </tr>
  </table>
  </section>
</td>
</tr>"""


def _render_paper_block_plain(paper_stats: dict | None) -> str:
    """Plain-text "Paper Portfolio" line (Cycle 6). Empty until equity exists."""
    if not paper_stats or paper_stats.get("equity") is None:
        return ""
    equity = paper_stats["equity"]
    n = paper_stats.get("n_trades") or 0
    parts = [f"PAPER PORTFOLIO (paper money) — Equity ${equity:,.0f}"]
    if n > 0:
        total_pnl = paper_stats.get("total_pnl") or 0.0
        win_rate = paper_stats.get("win_rate")
        sign = "+" if total_pnl >= 0 else "-"
        parts.append(f"P&L {sign}${abs(total_pnl):,.0f}")
        if win_rate is not None:
            parts.append(f"Win {win_rate:.0f}% ({paper_stats.get('wins') or 0}/{n})")
    return "  •  ".join(parts)


def _render_insider_block_html(buys: list[InsiderBuy]) -> str:
    """Render the Insider Spotlight card (creative #16). Empty if none."""
    if not buys:
        return ""
    rows = []
    for b in buys:
        ticker = html_escape(b.ticker)
        insider = html_escape(b.insider)
        value = _fmt_revenue(b.value)
        meta = html_escape(f"{insider} bought {value} ({b.transaction_date})")
        rows.append(
            f'<tr><td style="padding:3px 0;font-size:13px;color:#333;">'
            f'<strong class="mm-darklabel-bull" style="color:#1a6b3a;">{ticker}</strong>'
            f'<span class="mm-darktext" style="color:#555;"> &mdash; {meta}</span></td></tr>'
        )
    return f"""
<tr>
<td style="padding:0 32px 4px;">
  <section data-block="insider">
  <table width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 4px;">
  <tr>
  <td class="mm-darkcard mm-darkborder-bull" style="padding:12px 14px;background-color:#eef7f0;border-left:4px solid #1a6b3a;border-radius:0 6px 6px 0;">
    <div class="mm-darklabel-bull" style="font-size:12px;font-weight:700;color:#14582f;margin-bottom:6px;">&#128081; INSIDER BUYING</div>
    <table width="100%" cellpadding="0" cellspacing="0">
    {"".join(rows)}
    </table>
  </td>
  </tr>
  </table>
  </section>
</td>
</tr>"""


def _render_insider_block_plain(buys: list[InsiderBuy]) -> str:
    """Plain-text Insider Spotlight. Empty if none."""
    if not buys:
        return ""
    lines = ["INSIDER BUYING", "-" * 60]
    for b in buys:
        lines.append(f"  {b.ticker} — {b.insider} bought {_fmt_revenue(b.value)} ({b.transaction_date})")
    return "\n".join(lines)


def _render_divergence_block_html(flags: list[DivergenceFlag]) -> str:
    """Render the Narrative-vs-Tape divergence flag (creative #15). Empty if none."""
    if not flags:
        return ""
    rows = []
    for f in flags:
        ticker = html_escape(f.ticker)
        note = html_escape(f.note)
        rows.append(
            f'<tr><td style="padding:3px 0;font-size:13px;color:#333;">'
            f'<strong class="mm-darkticker" style="color:#7a3a00;">{ticker}</strong>'
            f'<span class="mm-darktext" style="color:#555;"> &mdash; {note}</span></td></tr>'
        )
    return f"""
<tr>
<td style="padding:0 32px 4px;">
  <section data-block="divergence">
  <table width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 4px;">
  <tr>
  <td class="mm-darkcard" style="padding:12px 14px;background-color:#fff4e8;border-left:4px solid #d2691e;border-radius:0 6px 6px 0;">
    <div class="mm-darklabel-warn" style="font-size:12px;font-weight:700;color:#a0480a;margin-bottom:6px;">&#9889; NARRATIVE vs TAPE</div>
    <table width="100%" cellpadding="0" cellspacing="0">
    {"".join(rows)}
    </table>
  </td>
  </tr>
  </table>
  </section>
</td>
</tr>"""


def _render_divergence_block_plain(flags: list[DivergenceFlag]) -> str:
    """Plain-text divergence flag. Empty if none."""
    if not flags:
        return ""
    lines = ["NARRATIVE vs TAPE", "-" * 60]
    for f in flags:
        lines.append(f"  {f.ticker} — {f.note}")
    return "\n".join(lines)


def _fmt_revenue(value: float | None) -> str:
    """Format a revenue estimate as ``$1.2B`` / ``$345M`` / ``$1.2K``."""
    if not value:
        return ""
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= div:
            return f"${value / div:.1f}{unit}"
    return f"${value:.0f}"


def _render_earnings_block_html(entries: list[EarningsEntry]) -> str:
    """Render the Pre-Market Earnings card (creative #14). Empty if no entries."""
    if not entries:
        return ""

    rows = []
    for e in entries:
        ticker = html_escape(e.symbol)
        meta_bits = [html_escape(e.when_label)]
        if e.eps_estimate is not None:
            meta_bits.append(f"EPS est {e.eps_estimate:.2f}")
        rev = _fmt_revenue(e.revenue_estimate)
        if rev:
            meta_bits.append(f"Rev est {rev}")
        meta = html_escape(" · ").join(meta_bits)
        rows.append(
            f'<tr><td style="padding:3px 0;font-size:13px;color:#333;">'
            f'<strong class="mm-darkticker" style="color:#1a1a2e;">{ticker}</strong>'
            f'<span class="mm-darktext" style="color:#777;"> &nbsp;{meta}</span></td></tr>'
        )

    return f"""
<tr>
<td style="padding:0 32px 4px;">
  <section data-block="earnings">
  <table width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 4px;">
  <tr>
  <td class="mm-darkcard" style="padding:12px 14px;background-color:#f4f5f8;border-left:4px solid #8a93a8;border-radius:0 6px 6px 0;">
    <div class="mm-darklabel-ref" style="font-size:12px;font-weight:700;color:#5b6473;margin-bottom:6px;">&#128197; REPORTING EARNINGS TODAY</div>
    <table width="100%" cellpadding="0" cellspacing="0">
    {"".join(rows)}
    </table>
  </td>
  </tr>
  </table>
  </section>
</td>
</tr>"""


def _render_earnings_block_plain(entries: list[EarningsEntry]) -> str:
    """Plain-text Pre-Market Earnings card. Empty if no entries."""
    if not entries:
        return ""
    lines = ["EARNINGS TODAY", "-" * 60]
    for e in entries:
        bits = [e.when_label]
        if e.eps_estimate is not None:
            bits.append(f"EPS est {e.eps_estimate:.2f}")
        rev = _fmt_revenue(e.revenue_estimate)
        if rev:
            bits.append(f"Rev est {rev}")
        lines.append(f"  {e.symbol} — {' · '.join(bits)}")
    return "\n".join(lines)


def _render_article_block(
    article: RankedArticle, hype: HypeScore | None = None
) -> str:
    """Render a single article block as HTML.

    When ``hype`` is provided (Overhype Detector, creative #5), an advisory
    hype-language badge is rendered next to the impact badge. It's purely
    informational — it never alters the story's prose or ranking.
    """
    color = RANK_COLORS.get(article.rank, "#888888")
    action_label = "Watch" if article.is_video else "Read"
    action_icon = "&#9654;" if article.is_video else "&#8594;"

    safe_url = _safe_href(article.url)
    safe_title = html_escape(article.title)
    safe_summary = html_escape(article.market_impact_summary)
    safe_source = html_escape(_derive_source_name(article.url) or article.source_name)
    safe_score = html_escape(f"{article.impact_score}")

    badge_aria = f"Rank {article.rank} story, impact score {safe_score} out of 10"
    hype_badge = _render_hype_badge(hype)

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
        </span>{hype_badge}
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


# Contrarian "Bear Case" accent color — desaturated amber. Distinct from the
# rank colors so the section reads as a separate beat, not a fourth ranked story.
_CONTRARIAN_ACCENT = "#a05a00"


def _render_contrarian_block(coda: ContrarianCoda) -> str:
    """Render the contrarian "Bear Case" section as an HTML table row.

    Wraps the visible content in ``<section data-block="contrarian">`` so
    diffs from the other dev (sparkline strip at the TOP of the email) don't
    overlap with this block. Rendered just before the footer in
    :func:`render_email_html`.

    Links are sanitized through :func:`_safe_href`.
    """
    safe_url = _safe_href(coda.source_url)
    safe_headline = html_escape(coda.headline)
    safe_argument = html_escape(coda.argument)
    safe_source = html_escape(_derive_source_name(coda.source_url) or coda.source_name)

    return f"""
<!-- Contrarian "Bear Case" coda — Cycle 3 -->
<tr>
<td class="mm-contrarian-wrap" style="padding:0 32px 24px;background-color:#ffffff;border-top:1px solid #eaeaea;">
  <section data-block="contrarian" aria-label="The Bear Case">
  <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:20px;">
  <tr>
  <td class="mm-contrarian-card" style="padding:16px;border-left:4px solid {_CONTRARIAN_ACCENT};background-color:#fbf6ee;border-radius:0 6px 6px 0;">
    <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td>
        <span class="mm-contrarian-eyebrow" style="display:inline-block;color:{_CONTRARIAN_ACCENT};font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;">
          The Bear Case
        </span>
      </td>
    </tr>
    <tr>
      <td style="padding-top:4px;">
        <p class="mm-contrarian-headline" style="color:#1a1a2e;font-size:15px;font-weight:600;line-height:1.3;margin:0;">
          {safe_headline}
        </p>
      </td>
    </tr>
    <tr>
      <td style="padding-top:8px;">
        <p class="mm-contrarian-argument" style="color:#444;font-size:14px;line-height:1.5;margin:0;">
          {safe_argument}
        </p>
      </td>
    </tr>
    <tr>
      <td style="padding-top:12px;">
        <a href="{safe_url}" class="mm-contrarian-source" style="color:{_CONTRARIAN_ACCENT};font-size:13px;font-weight:600;text-decoration:none;">
          &#8594; {safe_source}
        </a>
      </td>
    </tr>
    </table>
  </td>
  </tr>
  </table>
  </section>
</td>
</tr>"""
