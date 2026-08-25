"""Browsable scores & grades history page (MM-T003).

Renders ``data/briefings.jsonl`` into a single self-contained HTML file so the
full Yesterday-Index grading history can be reviewed at a glance instead of
reading raw JSON. Regenerated every pipeline run and published via GitHub Pages.

Design constraints:
  * **Self-contained** — inline CSS only, no external assets (works from a
    ``file://`` open, GitHub Pages, or an email preview).
  * **Best-effort** — :func:`write_scores_page` never raises; a broken render
    must never break the daily send.
  * **Single source of truth** — the category summary reuses
    :func:`market_mover.learning.compute_category_performance` so this page and
    the log readout can never disagree.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import date
from pathlib import Path

from .learning import (
    VERDICT_SCORE,
    compute_category_performance,
    load_briefing_records,
)

logger = logging.getLogger(__name__)

# Verdict → (label, CSS class). Order also drives the legend.
_VERDICT_META: dict[str, tuple[str, str]] = {
    "HIT": ("HIT", "hit"),
    "PARTIAL": ("PARTIAL", "partial"),
    "MISS": ("MISS", "miss"),
    "TOO_EARLY": ("TOO EARLY", "early"),
    "NOT_APPLICABLE": ("N/A", "na"),
}

_UNGRADED = ("PENDING", "pending")


def _verdict_badge(verdict: str | None) -> str:
    label, cls = _VERDICT_META.get(verdict or "", _UNGRADED)
    return f'<span class="badge badge-{cls}">{label}</span>'


def _fmt_pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _pick_rows(record: dict) -> list[str]:
    """Render each pick in a record as a table row, verdict joined by rank."""
    judgments_by_rank = {
        j.get("rank"): j for j in (record.get("judgments") or []) if isinstance(j, dict)
    }
    rows: list[str] = []
    picks = record.get("picks") or []
    for i, pick in enumerate(picks):
        if not isinstance(pick, dict):
            continue
        rank = pick.get("rank", i + 1)
        judgment = judgments_by_rank.get(rank, {})
        verdict = judgment.get("verdict")
        justification = judgment.get("justification") or ""
        ticker = pick.get("primary_ticker") or "—"
        category = pick.get("category") or "other"
        title = pick.get("title") or ""
        # Only the first pick of a day carries the date cell (rowspan look).
        date_cell = html.escape(record.get("date", "")) if i == 0 else ""
        date_class = "date-lead" if i == 0 else "date-cont"
        rows.append(
            "<tr>"
            f'<td class="{date_class}">{date_cell}</td>'
            f'<td class="rank">#{html.escape(str(rank))}</td>'
            f'<td class="ticker">{html.escape(str(ticker))}</td>'
            f'<td class="cat">{html.escape(str(category))}</td>'
            f'<td class="title" title="{html.escape(justification)}">'
            f"{html.escape(title)}</td>"
            f'<td class="verdict">{_verdict_badge(verdict)}</td>'
            "</tr>"
        )
    return rows


def _category_summary_html(report) -> str:
    if not report.categories:
        return '<p class="muted">No graded picks yet — check back after the first verdicts land.</p>'
    window = "all history" if report.window_days == 0 else f"last {report.window_days} days"
    header = (
        f'<p class="muted">Pooled hit-quality by category — {window}, '
        f"n={report.total_gradeable} graded picks, global {_fmt_pct(report.global_mean)} "
        f"(HIT=1, PARTIAL=½, MISS=0; TOO&nbsp;EARLY / N/A excluded).</p>"
    )
    body_rows = "".join(
        "<tr>"
        f'<td class="cat">{html.escape(c.category)}</td>'
        f'<td class="num">{c.n}</td>'
        f'<td class="num">{_fmt_pct(c.raw_mean)}</td>'
        f'<td class="num strong">{_fmt_pct(c.posterior_mean)}</td>'
        f'<td class="num muted">[{_fmt_pct(c.ci_low)}, {_fmt_pct(c.ci_high)}]</td>'
        "</tr>"
        for c in report.categories
    )
    return (
        header
        + '<table class="summary"><thead><tr>'
        '<th>Category</th><th class="num">n</th><th class="num">Raw</th>'
        '<th class="num">Pooled</th><th class="num">90% CI</th>'
        "</tr></thead><tbody>"
        + body_rows
        + "</tbody></table>"
    )


def _legend_html() -> str:
    chips = "".join(
        f'{_verdict_badge(v)} ' for v in _VERDICT_META
    )
    return f'<p class="legend">{chips}</p>'


def _overall_stats(records: list[dict]) -> tuple[int, int]:
    """Return (graded_picks, total_days) across all records."""
    graded = 0
    for r in records:
        for j in r.get("judgments") or []:
            if isinstance(j, dict) and j.get("verdict") in VERDICT_SCORE:
                graded += 1
    return graded, len(records)


def _pnl_series(cycles: list[dict]) -> list[tuple[str, float]]:
    """Cumulative realized P&L per cycle date, oldest→newest, starting at 0."""
    ordered = sorted(cycles, key=lambda c: c.get("cycle_date", ""))
    series: list[tuple[str, float]] = []
    running = 0.0
    for c in ordered:
        for closed in c.get("closed", []) or []:
            pnl = closed.get("pnl_abs")
            if isinstance(pnl, (int, float)):
                running += pnl
        series.append((c.get("cycle_date", ""), round(running, 2)))
    return series


def _render_pnl_chart(series: list[tuple[str, float]]) -> str:
    """A single-series line chart of cumulative realized paper P&L.

    Change-over-time with polarity (profit/loss around zero): one line, a zero
    baseline, colored green when the latest value is up and red when down, with
    native SVG ``<title>`` tooltips per point (no JS). Empty series → "".
    """
    if len(series) < 2:
        return ""
    W, H = 760, 240
    PAD_L, PAD_R, PAD_T, PAD_B = 8, 64, 16, 24
    xs = list(range(len(series)))
    ys = [v for _d, v in series]
    lo, hi = min(ys + [0.0]), max(ys + [0.0])
    span = (hi - lo) or 1.0
    # Add 8% headroom so the line/labels don't touch the frame.
    lo -= span * 0.08
    hi += span * 0.08
    span = hi - lo

    def px(i: int) -> float:
        return PAD_L + i * (W - PAD_L - PAD_R) / (len(series) - 1)

    def py(v: float) -> float:
        return PAD_T + (hi - v) * (H - PAD_T - PAD_B) / span

    final = ys[-1]
    cls = "pnl-pos" if final >= 0 else "pnl-neg"
    zero_y = py(0.0)
    line_pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in zip(xs, ys))
    area_pts = f"{px(0):.1f},{zero_y:.1f} " + line_pts + f" {px(xs[-1]):.1f},{zero_y:.1f}"
    dots = "".join(
        f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="2.5" class="{cls}-dot">'
        f'<title>{html.escape(d)}: ${v:+,.2f}</title></circle>'
        for i, (d, v) in enumerate(series)
    )
    end_x, end_y = px(xs[-1]), py(final)
    return f"""
    <div class="chartwrap">
      <svg viewBox="0 0 {W} {H}" width="100%" role="img"
           aria-label="Cumulative realized paper P&amp;L, latest ${final:+,.2f}">
        <line x1="{PAD_L}" y1="{zero_y:.1f}" x2="{W - PAD_R}" y2="{zero_y:.1f}" class="pnl-zero"/>
        <polygon points="{area_pts}" class="{cls}-area"/>
        <polyline points="{line_pts}" class="{cls}-line" fill="none"/>
        {dots}
        <circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="4" class="{cls}-dot"/>
        <text x="{end_x + 6:.1f}" y="{end_y + 4:.1f}" class="pnl-endlabel {cls}-fg">${final:+,.0f}</text>
        <text x="{PAD_L}" y="{py(hi) + 10:.1f}" class="pnl-axis">${hi:+,.0f}</text>
        <text x="{PAD_L}" y="{py(lo) - 3:.1f}" class="pnl-axis">${lo:+,.0f}</text>
      </svg>
      <div class="chartx"><span>{html.escape(series[0][0])}</span><span>{html.escape(series[-1][0])}</span></div>
    </div>"""


def _pick_return_series(cycles: list[dict]) -> list[tuple[str, float]]:
    """Per-cycle average realized *pct* return of that cycle's closed picks.

    One point per cycle that actually closed something, oldest→newest. This is a
    per-dollar figure (equal-weight mean of ``pnl_pct``), so it measures the
    picks' selection skill independent of how much cash sat idle.
    """
    ordered = sorted(cycles, key=lambda c: c.get("cycle_date", ""))
    out: list[tuple[str, float]] = []
    for c in ordered:
        pcts = [
            t.get("pnl_pct")
            for t in (c.get("closed") or [])
            if isinstance(t.get("pnl_pct"), (int, float))
        ]
        if pcts:
            out.append((c.get("cycle_date", ""), sum(pcts) / len(pcts)))
    return out


def _benchmark_pair(
    cycles: list[dict], spy_closes: dict[str, float]
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """Cumulative return %, picks vs SPY buy-and-hold, over shared trading days.

    Both curves are indexed to 0% at the first shared day. The picks line
    compounds each day's average realized pick return; the SPY line is
    ``close/base - 1`` over the same dates. Returns ``(picks_pts, spy_pts)``,
    each ``[(date, cum_pct), …]``, or ``([], [])`` if fewer than two days line
    up (best-effort — a missing SPY fetch just hides the chart).
    """
    picks = _pick_return_series(cycles)
    aligned = [(d, r) for d, r in picks if d in spy_closes]
    if len(aligned) < 2:
        return [], []
    base_spy = spy_closes.get(aligned[0][0]) or 0.0
    if base_spy <= 0:
        return [], []
    picks_pts = [(aligned[0][0], 0.0)]
    spy_pts = [(aligned[0][0], 0.0)]
    mult = 1.0
    for d, r in aligned[1:]:
        mult *= 1.0 + r / 100.0
        picks_pts.append((d, round((mult - 1.0) * 100.0, 3)))
        spy_pts.append((d, round((spy_closes[d] / base_spy - 1.0) * 100.0, 3)))
    return picks_pts, spy_pts


def _render_benchmark_chart(
    picks_pts: list[tuple[str, float]], spy_pts: list[tuple[str, float]]
) -> str:
    """Two-series line: cumulative pick return vs SPY buy-and-hold, both in %.

    One shared % axis (never a dual axis), a zero baseline, a legend (two
    series → identity is never color-alone), direct end-labels, and native
    ``<title>`` tooltips per point. Empty/short input → "".
    """
    if len(picks_pts) < 2 or len(spy_pts) < 2 or len(picks_pts) != len(spy_pts):
        return ""
    W, H = 760, 240
    PAD_L, PAD_R, PAD_T, PAD_B = 8, 78, 16, 24
    n = len(picks_pts)
    all_y = [v for _d, v in picks_pts] + [v for _d, v in spy_pts] + [0.0]
    lo, hi = min(all_y), max(all_y)
    span = (hi - lo) or 1.0
    lo -= span * 0.10
    hi += span * 0.10
    span = hi - lo

    def px(i: int) -> float:
        return PAD_L + i * (W - PAD_L - PAD_R) / (n - 1)

    def py(v: float) -> float:
        return PAD_T + (hi - v) * (H - PAD_T - PAD_B) / span

    def line(pts: list[tuple[str, float]]) -> str:
        return " ".join(f"{px(i):.1f},{py(v):.1f}" for i, (_d, v) in enumerate(pts))

    def dots(pts: list[tuple[str, float]], key: str) -> str:
        return "".join(
            f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="2.5" class="bm-{key}-dot">'
            f"<title>{html.escape(d)} · {key.upper()}: {v:+.2f}%</title></circle>"
            for i, (d, v) in enumerate(pts)
        )

    zero_y = py(0.0)
    pf, sf = picks_pts[-1][1], spy_pts[-1][1]
    ex = px(n - 1)
    # Nudge the two end-labels apart if they'd collide.
    py_p, py_s = py(pf), py(sf)
    if abs(py_p - py_s) < 13:
        if py_p <= py_s:
            py_p, py_s = min(py_p, py_s) - 2, max(py_p, py_s) + 11
        else:
            py_s, py_p = min(py_p, py_s) - 2, max(py_p, py_s) + 11
    alpha = pf - sf
    acls = "bm-pos-fg" if alpha >= 0 else "bm-neg-fg"
    return f"""
    <div class="bm-legend">
      <span class="k"><span class="sw bm-picks-sw"></span>Picks (per-dollar)</span>
      <span class="k"><span class="sw bm-spy-sw"></span>SPY buy &amp; hold</span>
      <span class="k">Edge vs SPY: <strong class="{acls}">{alpha:+.2f}%</strong></span>
    </div>
    <div class="chartwrap">
      <svg viewBox="0 0 {W} {H}" width="100%" role="img"
           aria-label="Cumulative pick return {pf:+.2f}% versus SPY {sf:+.2f}%">
        <line x1="{PAD_L}" y1="{zero_y:.1f}" x2="{W - PAD_R}" y2="{zero_y:.1f}" class="pnl-zero"/>
        <polyline points="{line(spy_pts)}" class="bm-spy-line" fill="none"/>
        <polyline points="{line(picks_pts)}" class="bm-picks-line" fill="none"/>
        {dots(spy_pts, "spy")}
        {dots(picks_pts, "picks")}
        <circle cx="{ex:.1f}" cy="{py(pf):.1f}" r="4" class="bm-picks-dot"/>
        <circle cx="{ex:.1f}" cy="{py(sf):.1f}" r="4" class="bm-spy-dot"/>
        <text x="{ex + 6:.1f}" y="{py_p + 4:.1f}" class="pnl-endlabel bm-picks-fg">{pf:+.1f}%</text>
        <text x="{ex + 6:.1f}" y="{py_s + 4:.1f}" class="pnl-endlabel bm-spy-fg">{sf:+.1f}%</text>
      </svg>
      <div class="chartx"><span>{html.escape(picks_pts[0][0])}</span><span>{html.escape(picks_pts[-1][0])}</span></div>
    </div>"""


def _category_pnl(cycles: list[dict]) -> list[dict]:
    """Realized P&L pooled by category across every closed trade in the ledger.

    Returns rows sorted by total P&L descending, each with trade count, win
    rate, total dollars, and average pct per trade. Legacy rows with no
    category fall into ``"unmapped"``.
    """
    agg: dict[str, dict] = {}
    for c in cycles:
        for t in c.get("closed", []) or []:
            pnl = t.get("pnl_abs")
            if not isinstance(pnl, (int, float)):
                continue
            cat = t.get("category") or "unmapped"
            a = agg.setdefault(
                cat, {"category": cat, "total": 0.0, "trades": 0, "wins": 0, "pct_sum": 0.0}
            )
            a["total"] += pnl
            a["trades"] += 1
            if pnl > 0:
                a["wins"] += 1
            pct = t.get("pnl_pct")
            if isinstance(pct, (int, float)):
                a["pct_sum"] += pct
    rows = []
    for a in agg.values():
        n = a["trades"]
        rows.append(
            {
                "category": a["category"],
                "total": round(a["total"], 2),
                "trades": n,
                "win_rate": (a["wins"] / n) if n else 0.0,
                "avg_pct": (a["pct_sum"] / n) if n else 0.0,
            }
        )
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


def _render_category_pnl(rows: list[dict]) -> str:
    """Diverging bars (green profit ▶ / ◀ red loss around zero) + a numeric table."""
    if not rows:
        return ""
    maxabs = max((abs(r["total"]) for r in rows), default=0.0) or 1.0
    BW, CX, HALF = 172, 86, 78
    body = ""
    for r in rows:
        t = r["total"]
        w = min(abs(t) / maxabs * HALF, HALF)
        if t >= 0:
            rect = f'<rect x="{CX}" y="4" width="{w:.1f}" height="12" rx="3" class="cat-pos"/>'
        else:
            rect = f'<rect x="{CX - w:.1f}" y="4" width="{w:.1f}" height="12" rx="3" class="cat-neg"/>'
        bar = (
            f'<svg viewBox="0 0 {BW} 20" width="{BW}" height="20" role="img" '
            f'aria-label="{html.escape(r["category"])} total ${t:+,.2f}">'
            f'<line x1="{CX}" y1="1" x2="{CX}" y2="19" class="cat-zero"/>{rect}</svg>'
        )
        tcls = "cat-pos-fg" if t >= 0 else "cat-neg-fg"
        body += (
            f'<tr><td class="ticker">{html.escape(r["category"])}</td>'
            f'<td class="num">{r["trades"]}</td>'
            f'<td class="num">{r["win_rate"] * 100:.0f}%</td>'
            f'<td class="num strong {tcls}">${t:+,.2f}</td>'
            f'<td class="num">{r["avg_pct"]:+.2f}%</td>'
            f'<td class="catbar">{bar}</td></tr>'
        )
    return (
        '<div class="scroll"><table><thead><tr>'
        '<th>Category</th><th class="num">Trades</th><th class="num">Win%</th>'
        '<th class="num">Total P&amp;L</th><th class="num">Avg/trade</th>'
        "<th>◀ Loss · Profit ▶</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>"
    )


def render_scores_html(
    records: list[dict],
    *,
    today: date,
    generated_label: str = "",
    pnl_series: list[tuple[str, float]] | None = None,
    benchmark: tuple[list[tuple[str, float]], list[tuple[str, float]]] | None = None,
    category_pnl: list[dict] | None = None,
) -> str:
    """Render the full scores-history page as a self-contained HTML string."""
    report = compute_category_performance(records, today)
    graded, total_days = _overall_stats(records)

    # Newest day first — that's what you want when you open the page.
    ordered = sorted(records, key=lambda r: r.get("date", ""), reverse=True)
    history_rows = "".join(row for r in ordered for row in _pick_rows(r))
    gen = html.escape(generated_label) if generated_label else html.escape(str(today))

    series = pnl_series or []
    pnl_chart = _render_pnl_chart(series)
    pnl_card = (
        '<div class="card">'
        '<h2 style="margin-top:0">Paper P&amp;L — cumulative realized</h2>'
        f'<p class="muted" style="margin:0">Latest: '
        f'<strong>${series[-1][1]:+,.2f}</strong> over {len(series)} trading days · '
        'paper money, $15k/pick · not investment advice.</p>'
        f'{pnl_chart}</div>'
        if pnl_chart
        else ""
    )

    # Benchmark: do the picks beat just holding SPY? (per-dollar, cash-drag out)
    picks_pts, spy_pts = benchmark or ([], [])
    bm_chart = _render_benchmark_chart(picks_pts, spy_pts)
    bm_card = (
        '<div class="card">'
        '<h2 style="margin-top:0">Picks vs. the market</h2>'
        '<p class="muted" style="margin:0 0 4px">Cumulative return per dollar in a pick '
        'vs. buying &amp; holding SPY over the same trading days — the honest "is there '
        'an edge?" test (cash drag removed). Above the SPY line = the picks are adding '
        'value; at or below = you\'d have done as well owning the index.</p>'
        f'{bm_chart}</div>'
        if bm_chart
        else ""
    )

    # Which categories actually make money (realized $ pooled by category).
    cat_rows = category_pnl or []
    cat_pnl_html = _render_category_pnl(cat_rows)
    cat_pnl_card = (
        '<div class="card">'
        '<h2 style="margin-top:0">Where the money comes from</h2>'
        '<p class="muted" style="margin:0 0 8px">Realized paper P&amp;L pooled by pick '
        'category. Small trade counts are noisy — read the direction, not the decimals.</p>'
        f'{cat_pnl_html}</div>'
        if cat_pnl_html
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Market Mover — Scores &amp; Grades</title>
<style>
  :root {{
    --bg:#f7f8fa; --card:#ffffff; --ink:#1a1d21; --muted:#6b7280;
    --line:#e5e7eb; --accent:#0f62fe;
    --hit-bg:#d1fae5; --hit-fg:#065f46; --partial-bg:#fef3c7; --partial-fg:#92400e;
    --miss-bg:#fee2e2; --miss-fg:#991b1b; --early-bg:#e0e7ff; --early-fg:#3730a3;
    --na-bg:#f3f4f6; --na-fg:#6b7280; --pending-bg:#f3f4f6; --pending-fg:#9ca3af;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg:#0d1117; --card:#161b22; --ink:#e6edf3; --muted:#8b949e;
      --line:#30363d; --accent:#58a6ff;
      --hit-bg:#0f2f22; --hit-fg:#4ade80; --partial-bg:#3a2e0a; --partial-fg:#fbbf24;
      --miss-bg:#3a1414; --miss-fg:#f87171; --early-bg:#1e1b4b; --early-fg:#a5b4fc;
      --na-bg:#21262d; --na-fg:#8b949e; --pending-bg:#21262d; --pending-fg:#6e7681;
    }}
  }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; padding:24px 16px 64px; background:var(--bg); color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  h2 {{ font-size:16px; margin:28px 0 10px; }}
  .sub {{ color:var(--muted); margin:0 0 20px; font-size:13px; }}
  .card {{
    background:var(--card); border:1px solid var(--line); border-radius:12px;
    padding:16px 18px; margin-bottom:20px;
  }}
  .stat-row {{ display:flex; gap:24px; flex-wrap:wrap; }}
  .stat {{ min-width:120px; }}
  .stat .n {{ font-size:26px; font-weight:700; }}
  .stat .l {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
  .muted {{ color:var(--muted); font-size:13px; }}
  .scroll {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th, td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:600; }}
  td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  td.strong {{ font-weight:700; }}
  td.rank {{ color:var(--muted); font-variant-numeric:tabular-nums; }}
  td.ticker {{ font-weight:600; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  td.cat {{ color:var(--muted); white-space:nowrap; }}
  td.title {{ max-width:420px; }}
  td.date-lead {{ font-weight:600; white-space:nowrap; }}
  td.date-cont {{ border-bottom:none; }}
  tr:has(td.date-lead) td {{ border-top:2px solid var(--line); }}
  .badge {{
    display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px;
    font-weight:700; letter-spacing:.02em; white-space:nowrap;
  }}
  .badge-hit {{ background:var(--hit-bg); color:var(--hit-fg); }}
  .badge-partial {{ background:var(--partial-bg); color:var(--partial-fg); }}
  .badge-miss {{ background:var(--miss-bg); color:var(--miss-fg); }}
  .badge-early {{ background:var(--early-bg); color:var(--early-fg); }}
  .badge-na {{ background:var(--na-bg); color:var(--na-fg); }}
  .badge-pending {{ background:var(--pending-bg); color:var(--pending-fg); }}
  .legend {{ margin:10px 0 0; }}
  footer {{ color:var(--muted); font-size:12px; margin-top:28px; text-align:center; }}
  /* Paper P&L chart — single series, polarity around zero. */
  .chartwrap {{ margin-top:8px; }}
  .chartx {{ display:flex; justify-content:space-between; color:var(--muted); font-size:11px; margin-top:2px; }}
  .pnl-zero {{ stroke:var(--line); stroke-width:1; stroke-dasharray:3 3; }}
  .pnl-axis {{ fill:var(--muted); font-size:11px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .pnl-endlabel {{ font-size:13px; font-weight:700; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .pnl-pos-line {{ stroke:#1a7f37; stroke-width:2; }}
  .pnl-neg-line {{ stroke:#cf222e; stroke-width:2; }}
  .pnl-pos-area {{ fill:#1a7f37; opacity:.10; }}
  .pnl-neg-area {{ fill:#cf222e; opacity:.10; }}
  .pnl-pos-dot {{ fill:#1a7f37; }}
  .pnl-neg-dot {{ fill:#cf222e; }}
  .pnl-pos-fg {{ fill:#1a7f37; }}
  .pnl-neg-fg {{ fill:#cf222e; }}
  @media (prefers-color-scheme: dark) {{
    .pnl-pos-line, .pnl-pos-dot {{ stroke:#4ade80; }}
    .pnl-pos-dot, .pnl-pos-area {{ fill:#4ade80; }}
    .pnl-pos-fg {{ fill:#4ade80; }}
    .pnl-neg-line, .pnl-neg-dot {{ stroke:#f87171; }}
    .pnl-neg-dot, .pnl-neg-area {{ fill:#f87171; }}
    .pnl-neg-fg {{ fill:#f87171; }}
  }}
  /* Picks-vs-SPY benchmark — two series, identity by color + legend. */
  .bm-legend {{ display:flex; flex-wrap:wrap; gap:16px; align-items:center;
    color:var(--muted); font-size:12px; margin:6px 0 2px; }}
  .bm-legend .k {{ display:inline-flex; align-items:center; gap:6px; }}
  .bm-legend .sw {{ width:16px; height:0; border-top-width:3px; border-top-style:solid; border-radius:2px; }}
  .bm-picks-sw {{ border-top-color:var(--accent); }}
  .bm-spy-sw {{ border-top-color:var(--muted); border-top-style:dashed; }}
  .bm-picks-line {{ stroke:var(--accent); stroke-width:2; }}
  .bm-picks-dot, .bm-picks-fg {{ fill:var(--accent); }}
  .bm-spy-line {{ stroke:var(--muted); stroke-width:2; stroke-dasharray:5 3; }}
  .bm-spy-dot, .bm-spy-fg {{ fill:var(--muted); }}
  .bm-pos-fg {{ color:#1a7f37; }}
  .bm-neg-fg {{ color:#cf222e; }}
  /* Category P&L — diverging bars around a zero midpoint. */
  td.catbar {{ width:180px; }}
  td.catbar svg {{ display:block; }}
  .cat-zero {{ stroke:var(--line); stroke-width:1; }}
  .cat-pos {{ fill:#1a7f37; }}
  .cat-neg {{ fill:#cf222e; }}
  .cat-pos-fg {{ color:#1a7f37; }}
  .cat-neg-fg {{ color:#cf222e; }}
  @media (prefers-color-scheme: dark) {{
    .bm-pos-fg {{ color:#4ade80; }}
    .bm-neg-fg {{ color:#f87171; }}
    .cat-pos {{ fill:#4ade80; }}
    .cat-neg {{ fill:#f87171; }}
    .cat-pos-fg {{ color:#4ade80; }}
    .cat-neg-fg {{ color:#f87171; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Market Mover — Scores &amp; Grades</h1>
  <p class="sub">Every daily pick, graded by the Yesterday-Index judge. Newest first.
    Hover a story for the judge's reasoning.</p>

  <div class="card">
    <div class="stat-row">
      <div class="stat"><div class="n">{total_days}</div><div class="l">Days recorded</div></div>
      <div class="stat"><div class="n">{graded}</div><div class="l">Graded picks</div></div>
      <div class="stat"><div class="n">{_fmt_pct(report.global_mean)}</div><div class="l">Global hit-quality</div></div>
    </div>
  </div>

  {pnl_card}

  {bm_card}

  {cat_pnl_card}

  <div class="card">
    <h2 style="margin-top:0">By category</h2>
    {_category_summary_html(report)}
  </div>

  <div class="card">
    <h2 style="margin-top:0">Full history</h2>
    {_legend_html()}
    <div class="scroll">
      <table>
        <thead><tr>
          <th>Date</th><th>Rank</th><th>Ticker</th><th>Category</th>
          <th>Story</th><th>Verdict</th>
        </tr></thead>
        <tbody>{history_rows}</tbody>
      </table>
    </div>
  </div>

  <footer>Generated {gen} · Market Mover · verdicts are the judge's, not investment advice.</footer>
</div>
</body>
</html>"""


def _read_cycles(paper_path: Path | None) -> list[dict]:
    """Best-effort load of the paper-trades ledger as a list of raw dicts."""
    if paper_path is None or not paper_path.exists():
        return []
    cycles: list[dict] = []
    try:
        for line in paper_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                cycles.append(json.loads(line))
    except Exception as e:
        logger.warning("Scores page: could not read paper ledger (%s)", e)
        return []
    return cycles


def fetch_spy_closes(
    cycles: list[dict],
    *,
    api_key_id: str,
    api_secret_key: str,
    feed: str = "iex",
    min_call_interval: float = 1.0,
) -> dict[str, float]:
    """Daily SPY closes over the ledger's date span, as ``{iso_date: close}``.

    One batched Alpaca call for the whole window. Best-effort: no creds, no
    ledger, or any fetch failure returns ``{}`` and the benchmark chart is
    simply hidden — the page never depends on the network.
    """
    dates = sorted(c.get("cycle_date", "") for c in cycles if c.get("cycle_date"))
    if len(dates) < 2 or not (api_key_id and api_secret_key):
        return {}
    try:
        start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    except ValueError:
        return {}
    from .sources.alpaca_source import fetch_daily_bars

    bars = fetch_daily_bars(
        ["SPY"], start, end, api_key_id, api_secret_key,
        feed=feed, min_call_interval=min_call_interval,
    )
    out: dict[str, float] = {}
    for bar in bars.get("SPY") or []:
        t, c = bar.get("t"), bar.get("c")
        if isinstance(t, str) and isinstance(c, (int, float)):
            out[t[:10]] = float(c)
    return out


def write_scores_page(
    jsonl_path: Path,
    out_path: Path,
    *,
    today: date,
    generated_label: str = "",
    paper_trades_path: Path | None = None,
    spy_closes: dict[str, float] | None = None,
) -> bool:
    """Render the scores page to ``out_path``. Best-effort — never raises.

    Returns True on success, False if anything went wrong (logged as a warning).
    """
    try:
        records = load_briefing_records(jsonl_path)
        cycles = _read_cycles(paper_trades_path)
        html_doc = render_scores_html(
            records,
            today=today,
            generated_label=generated_label,
            pnl_series=_pnl_series(cycles),
            benchmark=_benchmark_pair(cycles, spy_closes or {}),
            category_pnl=_category_pnl(cycles),
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html_doc, encoding="utf-8")
        logger.info(
            "Scores page: wrote %d days to %s", len(records), out_path
        )
        return True
    except Exception as e:  # pragma: no cover — defensive; must never break send
        logger.warning("Scores page: render failed (%s) — skipping", e)
        return False


# The market-context feed the Robinhood-Agentic /market page consumes (WW integration, 2026-08-16).
# Published as JSON next to scores.html on GitHub Pages, so the trading backend can GET a stable URL
# once a day: no repo access, no keys, and its DB port stays closed (an outbound pull, not an inbound
# connection). The picks[] on each record are the headline feed; the consumer derives its own
# catalyst calendar from its earnings data. Kept small: only the most recent few days.
LATEST_JSON_DAYS = 5


def _pick_to_headline(pick: dict, brief_date: str | None) -> dict:
    """Map a briefing pick to the headline shape the dashboard's market-context route reads
    (id, title, source, url, published_at, summary, tickers, sentiment)."""
    ticker = (pick.get("primary_ticker") or "").strip().upper() or None
    return {
        "id": f"mm-{brief_date or 'undated'}-{pick.get('rank')}",
        "title": pick.get("title"),
        "source": pick.get("source_name") or "Market Mover",
        "url": pick.get("source_url"),
        "published_at": f"{brief_date}T12:00:00Z" if brief_date else None,
        "summary": pick.get("summary"),
        "tickers": [ticker] if ticker else [],
        # The brief scores impact, not direction, so no sentiment is asserted. null renders neutral.
        "sentiment": None,
    }


def _pick_to_mover(pick: dict) -> dict:
    """Map a briefing pick to the Top Movers shape (the brief's ranked picks, verbatim)."""
    ticker = (pick.get("primary_ticker") or "").strip().upper() or None
    return {
        "rank": pick.get("rank"),
        "ticker": ticker,
        "category": pick.get("category"),
        "title": pick.get("title"),
        "justification": pick.get("summary"),
        # The brief carries no per-pick directional verdict; leave it null rather than infer one.
        "verdict": None,
    }


def write_latest_json(
    jsonl_path: Path,
    out_path: Path,
    *,
    generated_label: str = "",
    days: int = LATEST_JSON_DAYS,
) -> bool:
    """Write the newest brief to ``out_path`` in the shape the Wasden Watch market-context route reads.

    The route serves ONE brief, so this derives top-level ``generated_at`` / ``brief_date`` /
    ``macro_read`` / ``headlines`` (mapped from the newest brief's picks) plus ``top_movers`` (the
    ranked picks) for the Market page's Top Movers card. Best-effort: returns True on success, False
    if anything went wrong (logged), and never raises, mirroring ``write_scores_page``.

    ``days`` bounds how far back to look for the newest dated record; a row missing a date sorts last
    rather than crashing the sort.
    """
    try:
        records = load_briefing_records(jsonl_path)
        recent = sorted(records, key=lambda r: r.get("date", ""), reverse=True)[:days]
        latest = recent[0] if recent else {}
        brief_date = latest.get("date")
        picks = sorted(
            (latest.get("picks") or []),
            key=lambda p: p.get("rank") if isinstance(p.get("rank"), int) else 1_000_000,
        )
        payload = {
            "schema_version": 2,
            "generated_at": generated_label,
            "brief_date": brief_date,
            # The brief carries no single macro read today; the route serves null and the page hides
            # the banner rather than showing an empty one. Flows through if a brief later adds one.
            "macro_read": latest.get("macro_read"),
            "headlines": [_pick_to_headline(p, brief_date) for p in picks],
            "top_movers": [_pick_to_mover(p) for p in picks],
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Latest JSON: wrote %d headline(s) to %s", len(payload["headlines"]), out_path)
        return True
    except Exception as e:  # pragma: no cover  (defensive; must never break the send)
        logger.warning("Latest JSON: write failed (%s), skipping", e)
        return False


def _main() -> None:  # pragma: no cover — thin CLI wrapper
    """Regenerate the page locally: ``python3 -m market_mover.scores_page``."""
    from datetime import date as _date

    from .config import MarketMoverSettings

    settings = MarketMoverSettings()
    out = Path(__file__).resolve().parents[2] / "docs" / "scores.html"
    cycles = _read_cycles(settings.paper_trades_jsonl_full_path)
    spy = fetch_spy_closes(
        cycles,
        api_key_id=settings.alpaca_api_key_id,
        api_secret_key=settings.alpaca_api_secret_key,
        feed=settings.alpaca_data_feed,
        min_call_interval=settings.min_call_interval_secs,
    )
    ok = write_scores_page(
        settings.briefings_jsonl_full_path, out, today=_date.today(),
        paper_trades_path=settings.paper_trades_jsonl_full_path,
        spy_closes=spy,
    )
    print(f"{'wrote' if ok else 'FAILED'} {out}")


if __name__ == "__main__":
    _main()
