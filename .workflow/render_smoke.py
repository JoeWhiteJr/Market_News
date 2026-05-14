"""Manual render smoke test for cycle-3 voice features.

Renders the email template with a Vinny voice + a mimicry-day subject + a
contrarian coda, then writes the HTML and the plain-text output to disk
for visual inspection.

Run from the project root:
    PYTHONPATH=src python3 .workflow/render_smoke.py
"""

from datetime import date

from market_mover.email_template import (
    build_subject,
    render_email_html,
    render_plain_text,
)
from market_mover.mimicry import mimicry_voice_for, mimicry_voice_to_voice_spec
from market_mover.models import ContrarianCoda, RankedArticle
from market_mover.voices import get_voice

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

# Pretend today is a Wednesday → mimicry day
mimicry = mimicry_voice_for(date(2026, 5, 13), 2)
voice = mimicry_voice_to_voice_spec(mimicry) if mimicry else get_voice("vinny")
mimicry_label = mimicry["name"] if mimicry else None

html = render_email_html(ranked, voice=voice, coda=coda)
text = render_plain_text(ranked, voice=voice, coda=coda)
subject = build_subject(ranked, "[Market Mover]", mimicry_label=mimicry_label)

print(f"SUBJECT: {subject}")
print()
print("--- PLAIN TEXT ---")
print(text)
print()

with open("/tmp/cycle3_manual_render.html", "w") as f:
    f.write(html)
print("HTML written to /tmp/cycle3_manual_render.html")

# Vinny-only render (no mimicry)
vinny_voice = get_voice("vinny")
html_vinny = render_email_html(ranked, voice=vinny_voice, coda=coda)
with open("/tmp/cycle3_vinny_render.html", "w") as f:
    f.write(html_vinny)
print("Vinny-only HTML written to /tmp/cycle3_vinny_render.html")

# Quick assertions
assert "data-block=\"contrarian\"" in html, "contrarian section missing"
assert "in the voice of" in subject, "mimicry label missing from subject"
assert "But: credit spreads" in html, "coda headline missing"
assert "Reuters" in html, "source attribution missing"
print()
print("All manual assertions passed.")
