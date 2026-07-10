"""Gmail-safe email visuals (MM-T006).

Every renderer here uses **table cells with inline ``background-color``** and
plain HTML — the one graphic technique that renders reliably across email
clients including Gmail (which strips inline ``<svg>``, so the old sparkline
polylines were invisible for Gmail recipients). No SVG, no ``data:`` URIs, no JS.

Blocks:
  * ``render_index_strip_html``  — Gmail-safe replacement for the SVG sparkline
    strip: one shaded cell per index (ticker + % change).
  * ``render_streak_row_html``   — GitHub-contributions-style row of the last N
    graded verdicts (green HIT / amber PARTIAL / red MISS).
  * ``render_category_card_html``— per-category pooled hit-quality as ranked
    horizontal bars with a 90% CI whisker.
  * ``render_sector_heatmap_html``— an N-cell red/green sector-ETF grid.

Each has a plain-text sibling for the multipart email's text/plain part.
"""

from __future__ import annotations

from .learning import CategoryReport

# Verdicts that count as graded (mirrors learning.VERDICT_SCORE) plus the
# not-yet-resolved states we render as neutral/hollow cells.
_VERDICT_CELL: dict[str, tuple[str, str, str]] = {
    # verdict -> (bg, fg, glyph)
    "HIT": ("#1a7f37", "#ffffff", "H"),
    "PARTIAL": ("#bf8700", "#ffffff", "~"),
    "MISS": ("#cf222e", "#ffffff", "M"),
    "TOO_EARLY": ("#eaeef2", "#57606a", "·"),
    "NOT_APPLICABLE": ("#eaeef2", "#57606a", "·"),
}
_PENDING_CELL = ("#f6f8fa", "#8c959f", "·")

# Heat ramp endpoints for percent moves.
_NEG = (207, 34, 46)     # red   #cf222e
_MID = (246, 248, 250)   # near-white #f6f8fa
_POS = (26, 127, 55)     # green #1a7f37


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = _clamp(t, 0.0, 1.0)
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _legible_fg(bg: tuple[int, int, int]) -> str:
    """Black or white text, whichever is more legible on ``bg`` (WCAG luminance)."""
    r, g, b = (c / 255 for c in bg)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#111111" if lum > 0.55 else "#ffffff"


def heat_colors(pct: float, cap: float = 2.0) -> tuple[str, str]:
    """Map a percent move to (background, foreground) hex on a red→neutral→green
    ramp, saturating at ±``cap`` percent. 0% is neutral."""
    if pct >= 0:
        bg = _mix(_MID, _POS, pct / cap if cap else 0.0)
    else:
        bg = _mix(_MID, _NEG, -pct / cap if cap else 0.0)
    return _hex(bg), _legible_fg(bg)


def _fmt_pct(pct: float) -> str:
    return f"{pct:+.1f}%"


# ---------------------------------------------------------------------------
# 1. Index strip — Gmail-safe sparkline replacement
# ---------------------------------------------------------------------------

def render_index_strip_html(sparklines: dict) -> str:
    """One shaded cell per index (ticker + % change). Replaces the SVG strip.

    ``sparklines`` maps ticker -> object with ``.ticker`` and ``.pct_change``
    (a :class:`SparklineSeries`). Empty input → empty string (block hidden).
    """
    series = [s for s in sparklines.values()]
    if not series:
        return ""
    cells = []
    for s in series:
        bg, fg = heat_colors(getattr(s, "pct_change", 0.0))
        cells.append(
            f'<td align="center" bgcolor="{bg}" style="background-color:{bg};'
            f'color:{fg};padding:8px 10px;border-radius:6px;'
            f'font:600 13px/1.2 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
            f'white-space:nowrap;">'
            f'<span style="opacity:.85;font-weight:700;">{getattr(s, "ticker", "?")}</span>'
            f'&nbsp;{_fmt_pct(getattr(s, "pct_change", 0.0))}</td>'
        )
    gap = '<td style="width:6px;"></td>'
    row = gap.join(cells)
    return (
        '<section data-block="index-strip">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:0 auto 4px;"><tr>' + row + "</tr></table></section>"
    )


def render_index_strip_plain(sparklines: dict) -> str:
    series = [s for s in sparklines.values()]
    if not series:
        return ""
    parts = [
        f'{getattr(s, "ticker", "?")} {_fmt_pct(getattr(s, "pct_change", 0.0))}'
        for s in series
    ]
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# 2. The Streak — verdict spark-row
# ---------------------------------------------------------------------------

def _recent_verdicts(records: list[dict], limit: int) -> list[str | None]:
    """Flatten judgments oldest→newest; None marks an ungraded (pending) pick."""
    out: list[str | None] = []
    for rec in sorted(records, key=lambda r: r.get("date", "")):
        judgments = rec.get("judgments")
        picks = rec.get("picks") or []
        if not judgments:
            out.extend(None for _ in picks)
            continue
        by_rank = {j.get("rank"): j.get("verdict") for j in judgments if isinstance(j, dict)}
        for i, p in enumerate(picks):
            out.append(by_rank.get((p or {}).get("rank", i + 1)))
    return out[-limit:]


def render_streak_row_html(records: list[dict], limit: int = 21) -> str:
    """A row of colored squares for the last ``limit`` graded picks."""
    verdicts = _recent_verdicts(records, limit)
    if not verdicts:
        return ""
    graded = [v for v in verdicts if v in ("HIT", "PARTIAL", "MISS")]
    hits = sum(1 for v in graded if v == "HIT")
    cells = []
    for v in verdicts:
        bg, _fg, _g = _VERDICT_CELL.get(v or "", _PENDING_CELL)
        cells.append(
            f'<td width="13" height="13" bgcolor="{bg}" '
            f'style="background-color:{bg};width:13px;height:13px;border-radius:3px;"></td>'
            '<td width="3" style="width:3px;"></td>'
        )
    label = (
        f'{hits}/{len(graded)} HIT over the last {len(graded)} graded picks'
        if graded else "awaiting first grades"
    )
    return (
        '<section data-block="streak" style="margin:14px 0;">'
        '<div style="font:600 11px/1 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        'color:#57606a;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">'
        'Recent form</div>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        + "".join(cells)
        + "</tr></table>"
        f'<div style="font:400 12px/1.3 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        f'color:#57606a;margin-top:6px;">{label}</div>'
        "</section>"
    )


def render_streak_row_plain(records: list[dict], limit: int = 21) -> str:
    verdicts = _recent_verdicts(records, limit)
    if not verdicts:
        return ""
    glyphs = {"HIT": "#", "PARTIAL": "~", "MISS": ".", None: "·"}
    row = "".join(glyphs.get(v, "·") for v in verdicts)
    graded = [v for v in verdicts if v in ("HIT", "PARTIAL", "MISS")]
    hits = sum(1 for v in graded if v == "HIT")
    tail = f"  ({hits}/{len(graded)} HIT)" if graded else ""
    return f"Recent form: {row}{tail}"


# ---------------------------------------------------------------------------
# 3. Category Report Card — hit-quality bars
# ---------------------------------------------------------------------------

def render_category_card_html(report: CategoryReport, min_n: int = 3) -> str:
    """Ranked horizontal bars of pooled hit-quality per category, with CI whisker."""
    cats = [c for c in report.categories if c.n >= min_n]
    if not cats:
        return ""
    cats = sorted(cats, key=lambda c: -c.posterior_mean)
    rows = []
    for c in cats:
        pct = round(c.posterior_mean * 100)
        lo, hi = round(c.ci_low * 100), round(c.ci_high * 100)
        bar_bg, _ = heat_colors((c.posterior_mean - report.global_mean) * 100, cap=25)
        rows.append(
            '<tr>'
            f'<td style="padding:3px 8px 3px 0;font:600 12px/1.2 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
            f'color:#24292f;white-space:nowrap;">{c.category}</td>'
            '<td style="padding:3px 0;width:100%;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            'style="background:#eaeef2;border-radius:4px;"><tr>'
            f'<td width="{pct}%" bgcolor="{bar_bg}" '
            f'style="background-color:{bar_bg};height:16px;border-radius:4px;"></td>'
            f'<td></td></tr></table></td>'
            f'<td style="padding:3px 0 3px 8px;font:600 12px/1.2 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
            f'color:#24292f;white-space:nowrap;">{pct}%</td>'
            f'<td style="padding:3px 0 3px 6px;font:400 11px/1.2 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
            f'color:#8c959f;white-space:nowrap;">[{lo}–{hi}] n={c.n}</td>'
            '</tr>'
        )
    return (
        '<section data-block="category-card" style="margin:14px 0;">'
        '<div style="font:600 11px/1 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        'color:#57606a;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;">'
        f'Report card — hit-quality by category (global {round(report.global_mean * 100)}%)</div>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
        + "".join(rows)
        + "</table></section>"
    )


def render_category_card_plain(report: CategoryReport, min_n: int = 3) -> str:
    cats = [c for c in report.categories if c.n >= min_n]
    if not cats:
        return ""
    cats = sorted(cats, key=lambda c: -c.posterior_mean)
    lines = [f"Report card (global {round(report.global_mean * 100)}%):"]
    for c in cats:
        filled = round(c.posterior_mean * 10)
        bar = "#" * filled + "-" * (10 - filled)
        lines.append(f"  {c.category:13s} {bar} {round(c.posterior_mean*100)}% (n={c.n})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Sector heat-map — Market Weather
# ---------------------------------------------------------------------------

def render_sector_heatmap_html(moves: list[tuple[str, str, float]], cols: int = 4) -> str:
    """Grid of shaded cells. ``moves`` = [(ticker, label, pct), ...]."""
    if not moves:
        return ""
    cells = []
    for ticker, label, pct in moves:
        bg, fg = heat_colors(pct)
        cells.append(
            f'<td align="center" bgcolor="{bg}" style="background-color:{bg};color:{fg};'
            f'padding:10px 6px;border:2px solid #ffffff;border-radius:6px;'
            f'font:700 13px/1.2 -apple-system,Segoe UI,Roboto,Arial,sans-serif;">'
            f'{ticker}<br><span style="font-weight:400;font-size:12px;">{_fmt_pct(pct)}</span>'
            f'<br><span style="font-weight:400;font-size:10px;opacity:.8;">{label}</span></td>'
        )
    grid = []
    for i in range(0, len(cells), cols):
        grid.append("<tr>" + "".join(cells[i:i + cols]) + "</tr>")
    return (
        '<section data-block="sector-heatmap" style="margin:14px 0;">'
        '<div style="font:600 11px/1 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        'color:#57606a;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;">'
        'Market weather — sectors (yesterday\'s close)</div>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        'style="border-collapse:separate;border-spacing:0;">'
        + "".join(grid)
        + "</table></section>"
    )


def render_sector_heatmap_plain(moves: list[tuple[str, str, float]]) -> str:
    if not moves:
        return ""
    parts = [f"{t} {_fmt_pct(p)}" for t, _label, p in moves]
    return "Sectors (yesterday): " + "  ".join(parts)
