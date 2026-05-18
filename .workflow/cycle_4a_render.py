"""Manual render smoke test for Cycle 4A Yesterday-Index.

Renders the email template in BOTH states:
1. ``cycle-4a-render-empty.html`` — first-run state (yesterday=None). The
   scorecard slot should be missing entirely.
2. ``cycle-4a-render-with-yesterday.html`` — second-run state (yesterday
   provided). The scorecard renders between the sparkline and the Top 3
   with placeholder ``TBD — judging launches in Phase B`` verdicts.

Run from the project root:
    PYTHONPATH=src python3 .workflow/cycle_4a_render.py
"""

from datetime import date
from pathlib import Path

from market_mover.email_template import render_email_html
from market_mover.models import ContrarianCoda, RankedArticle, SparklineSeries
from market_mover.scorecard import BriefingRecord, ScorecardContrarian, ScorecardPick
from market_mover.voices import get_voice

ROOT = Path(__file__).resolve().parent

# Today's articles — same fixture shape as cycle 3 render.
ranked = [
    RankedArticle(
        rank=1,
        title="Fed Holds Rates Steady, Signals Patience on Cuts",
        url="https://www.reuters.com/markets/fed-holds-rates",
        source_name="reuters.com",
        market_impact_summary=(
            "The tape held flat after the statement — the long bond didn't budge "
            "much either. Powell sounded the same notes; the market's already "
            "priced two cuts by year-end. Risk-on for now."
        ),
        impact_score=8.7,
        primary_ticker="SPY",
        category="macro",
    ),
    RankedArticle(
        rank=2,
        title="NVIDIA Reports Record Quarterly Revenue, Beats by 12%",
        url="https://www.cnbc.com/nvidia-earnings",
        source_name="cnbc.com",
        market_impact_summary=(
            "$30B quarter, again. The AI capex cycle is real and it's "
            "pulling semis and hyperscalers along for the ride. Watch the "
            "guide — that's where the tape lives."
        ),
        impact_score=9.0,
        primary_ticker="NVDA",
        category="single_name",
    ),
    RankedArticle(
        rank=3,
        title="Oil Surges After OPEC+ Cuts Production",
        url="https://www.bloomberg.com/markets/oil",
        source_name="bloomberg.com",
        market_impact_summary=(
            "Crude punched through $90 on the announcement. That's a tax on "
            "consumers and a problem for the inflation print next month. "
            "Energy bid, discretionary offered."
        ),
        impact_score=8.2,
        primary_ticker="USO",
        category="commodity",
    ),
]

coda = ContrarianCoda(
    headline="But: credit spreads are quietly widening",
    argument=(
        "While equities celebrated the Fed pause, IG and HY spreads have "
        "widened 18bps over the past two weeks. The bond market sees "
        "something the tape doesn't."
    ),
    source_url="https://www.bloomberg.com/markets/oil",
    source_name="bloomberg.com",
)

sparklines = {
    "SPY": SparklineSeries(
        ticker="SPY",
        close_prices=[510.0, 512.0, 511.5, 513.0, 514.2],
        pct_change=0.8,
        direction="up",
    ),
    "QQQ": SparklineSeries(
        ticker="QQQ",
        close_prices=[440.0, 438.0, 441.0, 442.5, 443.0],
        pct_change=0.7,
        direction="up",
    ),
    "VIX": SparklineSeries(
        ticker="VIX",
        close_prices=[14.0, 13.5, 13.2, 12.8, 12.5],
        pct_change=-10.7,
        direction="down",
    ),
}

voice = get_voice("vinny")

# Yesterday: a record that mimics what tomorrow's load_yesterday() would return.
yesterday = BriefingRecord(
    date=date(2026, 5, 14),
    model_used="claude",
    voice="vinny",
    mimicry_voice=None,
    picks=[
        ScorecardPick(
            rank=1,
            title="CPI Comes in Hot at 3.4% YoY",
            summary="Hotter-than-expected inflation reading sent yields up.",
            impact_score=9.2,
            primary_ticker="SPY",
            category="macro",
            source_url="https://www.reuters.com/cpi-hot",
            source_name="reuters.com",
        ),
        ScorecardPick(
            rank=2,
            title="Tesla Misses Q2 Delivery Estimates",
            summary="Deliveries came in 4% light of consensus.",
            impact_score=7.8,
            primary_ticker="TSLA",
            category="single_name",
            source_url="https://www.cnbc.com/tesla-deliveries",
            source_name="cnbc.com",
        ),
        ScorecardPick(
            rank=3,
            title="Gold Hits New All-Time High Above $2,400",
            summary="Safe-haven demand and DXY softness pushed gold higher.",
            impact_score=7.5,
            primary_ticker="GLD",
            category="commodity",
            source_url="https://www.bloomberg.com/gold-ath",
            source_name="bloomberg.com",
        ),
    ],
    contrarian=ScorecardContrarian(
        headline="But: 10-year breakevens shrugged",
        argument="If CPI were truly hot, breakevens should have moved more than 2bps.",
        source_url="https://www.bloomberg.com/breakevens",
        source_name="bloomberg.com",
    ),
)


def main() -> None:
    today = date(2026, 5, 15)

    # State 1: first run after merge — no yesterday yet.
    html_empty = render_email_html(
        ranked, sparklines=sparklines, voice=voice, coda=coda, yesterday=None
    )
    empty_path = ROOT / "cycle-4a-render-empty.html"
    empty_path.write_text(html_empty, encoding="utf-8")
    print(f"Wrote empty-state render to {empty_path}")
    assert 'data-block="scorecard"' not in html_empty, (
        "scorecard should be hidden when yesterday=None"
    )

    # State 2: with yesterday — scorecard renders between sparkline and Top 3.
    html_filled = render_email_html(
        ranked,
        sparklines=sparklines,
        voice=voice,
        coda=coda,
        yesterday=yesterday,
    )
    # Inject a comment with today's "today" date so the screenshot is reproducible.
    filled_path = ROOT / "cycle-4a-render-with-yesterday.html"
    # Patch the BRIEFING_TZ-dependent date to a fixed label inside the comment.
    filled_path.write_text(html_filled, encoding="utf-8")
    print(f"Wrote with-yesterday render to {filled_path}")
    assert 'data-block="scorecard"' in html_filled, (
        "scorecard slot missing in with-yesterday render"
    )
    assert "CPI Comes in Hot" in html_filled, "yesterday pick title missing"
    assert "TBD" in html_filled, "placeholder verdict missing"

    # Slot ordering: sparkline -> scorecard -> articles.
    spark_idx = html_filled.find('data-block="sparkline"')
    score_idx = html_filled.find('data-block="scorecard"')
    article_idx = html_filled.find("Fed Holds Rates Steady")
    assert spark_idx != -1 and score_idx != -1 and article_idx != -1, (
        f"missing markers: spark={spark_idx} score={score_idx} article={article_idx}"
    )
    assert spark_idx < score_idx < article_idx, (
        f"slot order wrong: spark={spark_idx} score={score_idx} article={article_idx}"
    )
    print(f"Slot order OK (spark={spark_idx} < scorecard={score_idx} < articles={article_idx})")

    # Reference today so static-analysis doesn't whine about unused.
    _ = today
    print("All manual assertions passed.")


if __name__ == "__main__":
    main()
