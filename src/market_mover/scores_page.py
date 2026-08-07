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


def render_scores_html(
    records: list[dict],
    *,
    today: date,
    generated_label: str = "",
    pnl_series: list[tuple[str, float]] | None = None,
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


def _load_pnl_series(paper_path: Path | None) -> list[tuple[str, float]]:
    """Best-effort cumulative-P&L series from the paper-trades ledger."""
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
    return _pnl_series(cycles)


def write_scores_page(
    jsonl_path: Path,
    out_path: Path,
    *,
    today: date,
    generated_label: str = "",
    paper_trades_path: Path | None = None,
) -> bool:
    """Render the scores page to ``out_path``. Best-effort — never raises.

    Returns True on success, False if anything went wrong (logged as a warning).
    """
    try:
        records = load_briefing_records(jsonl_path)
        html_doc = render_scores_html(
            records,
            today=today,
            generated_label=generated_label,
            pnl_series=_load_pnl_series(paper_trades_path),
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


def _main() -> None:  # pragma: no cover — thin CLI wrapper
    """Regenerate the page locally: ``python3 -m market_mover.scores_page``."""
    from datetime import date as _date

    from .config import MarketMoverSettings

    settings = MarketMoverSettings()
    out = Path(__file__).resolve().parents[2] / "docs" / "scores.html"
    ok = write_scores_page(
        settings.briefings_jsonl_full_path, out, today=_date.today(),
        paper_trades_path=settings.paper_trades_jsonl_full_path,
    )
    print(f"{'wrote' if ok else 'FAILED'} {out}")


if __name__ == "__main__":
    _main()
