"""Yesterday-Index LLM judge — Cycle 4 Phase B.

Grades yesterday's 3 picks against 24h close-to-close price action and the
ADR's frozen rubric. The judge prompt is locked verbatim in
``docs/adrs/0001-yesterday-index-rubric.md`` and reproduced here as
:data:`JUDGE_PROMPT_TEMPLATE`. If anything about the prompt or the locked
verdict thresholds changes, bump :data:`JUDGE_PROMPT_VERSION` AND re-run
all historical judgments from scratch (Joe's call, not automated).

Phase B contract:
- ``judge_pick``: fetch price data + call the LLM for ONE pick. Returns
  ``Judgment`` (or ``None`` on irrecoverable failure).
- ``judge_yesterday``: orchestrate per-pick judging in a 3-worker thread
  pool. Skips entirely if yesterday's row is already graded.

Failure semantics: a judge failure NEVER breaks the daily send. The CLI
wraps the call in a try/except that defaults to ``None`` (the scorecard
falls back to its Phase A "TBD" placeholder).
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from .scorecard import (
    BriefingRecord,
    Judgment,
    PriceData,
    ScorecardPick,
)
from .sources.quotes_source import fetch_24h_close_change

if TYPE_CHECKING:  # pragma: no cover — typing only
    from .config import MarketMoverSettings
    from .llm_client import LLMClient

logger = logging.getLogger("market_mover.judge")


# ---------------------------------------------------------------------------
# Locked constants — see ADR 0001
# ---------------------------------------------------------------------------

# Bump this and re-grade history if the prompt below ever changes.
JUDGE_PROMPT_VERSION: int = 1

# Verbatim from the ADR. DO NOT edit without bumping JUDGE_PROMPT_VERSION
# and re-running every historical judgment. The ``{...}`` placeholders are
# substituted in :func:`_build_judge_prompt`.
JUDGE_PROMPT_TEMPLATE: str = (
    "You are grading yesterday's market briefing predictions. Your verdict will be\n"
    "persisted and compared over months — be CONSISTENT, not generous.\n"
    "\n"
    "YESTERDAY'S PREDICTION (one of 3 picks):\n"
    "- Title: {title}\n"
    "- Summary: {summary}\n"
    "- Impact score (0–10): {impact_score}\n"
    "- Primary ticker or category: {ticker_or_category}\n"
    "\n"
    "PRICE DATA — 24 hours after the briefing (close to close):\n"
    "- Primary ticker {ticker}: {primary_pct}%\n"
    "- SPY: {spy_pct}%\n"
    "- VIX level: {vix_close} (change: {vix_pct}%)\n"
    "- Sector ETF (if applicable, {sector_etf}): {sector_pct}%\n"
    "\n"
    "VERDICT RULES:\n"
    "- HIT: The predicted impact materialized in the expected direction with a\n"
    "  meaningful magnitude. For high-impact predictions (impact_score ≥ 8.0), this\n"
    "  means ≥1.5% absolute move in the right asset. For lower-impact predictions\n"
    "  (5.0 ≤ score < 8.0), ≥0.7% move.\n"
    "- PARTIAL: Direction was right, but magnitude was muted; OR the right asset\n"
    "  moved but a related one didn't.\n"
    "- MISS: Direction was wrong (asset moved opposite way), OR no material\n"
    "  movement at all for a high-impact (≥8.0) prediction.\n"
    "- TOO_EARLY: The story is a multi-day setup (e.g., \"Fed meeting next week,\"\n"
    "  \"earnings season begins\") — too early to grade in 24h.\n"
    "- NOT_APPLICABLE: Story has no obvious market connection or the relevant\n"
    "  asset class has no clean ticker proxy.\n"
    "\n"
    "WHEN IN DOUBT:\n"
    "- Prefer PARTIAL over HIT.\n"
    "- Prefer TOO_EARLY over MISS.\n"
    "\n"
    "Return JSON only:\n"
    "{{\n"
    "  \"verdict\": \"HIT\" | \"PARTIAL\" | \"MISS\" | \"TOO_EARLY\" | \"NOT_APPLICABLE\",\n"
    "  \"justification\": \"<one short sentence citing the numbers>\"\n"
    "}}\n"
)

# Locked verdict literal set. Anything else from the LLM gets one retry and
# then the judgment is dropped (logged warning, daily send unaffected).
_ALLOWED_VERDICTS: frozenset[str] = frozenset(
    {"HIT", "PARTIAL", "MISS", "TOO_EARLY", "NOT_APPLICABLE"}
)


# ---------------------------------------------------------------------------
# Category → asset map (resolves ADR open question §1)
# ---------------------------------------------------------------------------

# Per the ADR's open question about macro/single-name/etc tickers, this is
# the Phase B mapping. For ``macro`` and ``geopolitical`` the primary asset
# defaults to SPY (broad-market proxy); ``geopolitical`` adds VIX so the
# judge sees flight-to-safety moves. Single-name/commodity/crypto require
# the pick to carry an explicit ``primary_ticker`` from the ranker.
#
# Phase C may revisit (e.g. adding per-sector ETFs). For now this is locked.
JUDGE_ASSETS_BY_CATEGORY: dict[str, dict[str, list[str] | str | None]] = {
    "macro":         {"primary_or_default": "SPY", "extras": ["TLT", "DXY"]},
    "single_name":   {"primary_or_default": None,  "extras": []},
    "commodity":     {"primary_or_default": None,  "extras": []},
    "crypto":        {"primary_or_default": None,  "extras": []},
    "geopolitical":  {"primary_or_default": "SPY", "extras": ["VIX"]},
    "other":         {"primary_or_default": "SPY", "extras": []},
}


def _resolve_primary_ticker(pick: ScorecardPick) -> str | None:
    """Return the ticker we should fetch close-to-close data for.

    Priority:
    1. ``pick.primary_ticker`` if the ranker set one.
    2. ``JUDGE_ASSETS_BY_CATEGORY[pick.category]["primary_or_default"]``
       when defined for that category.

    Returns ``None`` when the category has no default and the ranker
    didn't supply a ticker — the judge then sees a ``null`` primary and
    typically produces a NOT_APPLICABLE verdict.
    """
    if pick.primary_ticker:
        return pick.primary_ticker
    mapping = JUDGE_ASSETS_BY_CATEGORY.get(pick.category, {})
    return mapping.get("primary_or_default")  # type: ignore[return-value]


def _resolve_sector_etf(pick: ScorecardPick) -> str | None:
    """Return the first non-VIX extra ticker for the pick's category.

    The judge prompt only has one ``{sector_etf}`` slot — when a category
    defines multiple extras we send the first one that isn't VIX (VIX is
    rendered separately via the ``vix_close`` / ``vix_pct`` slots).
    """
    mapping = JUDGE_ASSETS_BY_CATEGORY.get(pick.category, {})
    extras = mapping.get("extras") or []
    for sym in extras:  # type: ignore[union-attr]
        if isinstance(sym, str) and sym.upper() != "VIX":
            return sym
    return None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _format_pct(value: float | None) -> str:
    """Format a percent change for the prompt; ``None`` -> ``"null"``."""
    if value is None:
        return "null"
    return f"{value:+.2f}"


def _format_level(value: float | None) -> str:
    """Format an absolute level for the prompt; ``None`` -> ``"null"``."""
    if value is None:
        return "null"
    return f"{value:.2f}"


def _build_judge_prompt(
    pick: ScorecardPick,
    primary_pct: float | None,
    spy_pct: float | None,
    vix_close: float | None,
    vix_pct: float | None,
    sector_etf: str | None,
    sector_pct: float | None,
) -> str:
    """Substitute the pick + price data into the frozen prompt template."""
    primary_ticker_display = _resolve_primary_ticker(pick) or "N/A"
    ticker_or_category = pick.primary_ticker or pick.category
    sector_etf_display = sector_etf or "N/A"
    return JUDGE_PROMPT_TEMPLATE.format(
        title=pick.title,
        summary=pick.summary,
        impact_score=f"{pick.impact_score:.1f}",
        ticker_or_category=ticker_or_category,
        ticker=primary_ticker_display,
        primary_pct=_format_pct(primary_pct),
        spy_pct=_format_pct(spy_pct),
        vix_close=_format_level(vix_close),
        vix_pct=_format_pct(vix_pct),
        sector_etf=sector_etf_display,
        sector_pct=_format_pct(sector_pct),
    )


# ---------------------------------------------------------------------------
# LLM call + response parsing
# ---------------------------------------------------------------------------


def _parse_judge_response(raw: str) -> tuple[str, str] | None:
    """Parse the LLM's JSON response into ``(verdict, justification)``.

    Returns ``None`` if the JSON is unparseable OR if the verdict isn't in
    the locked literal set. The caller does one retry on ``None``; a second
    ``None`` results in the judgment being dropped (logged warning).
    """
    text = (raw or "").strip()
    if not text:
        return None

    parsed: object | None = None
    # Strategy 1: direct JSON.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: markdown code-fenced JSON.
    if parsed is None:
        code_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if code_match:
            try:
                parsed = json.loads(code_match.group(1).strip())
            except json.JSONDecodeError:
                pass

    # Strategy 3: first brace-delimited object.
    if parsed is None:
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                parsed = json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

    if not isinstance(parsed, dict):
        return None

    verdict = str(parsed.get("verdict", "")).strip().upper()
    justification = str(parsed.get("justification", "")).strip()

    if verdict not in _ALLOWED_VERDICTS:
        return None
    if not justification:
        # No justification text — render a stub rather than dropping the
        # verdict. Joe's policy: ship the verdict if we have it.
        justification = "(no justification provided)"
    return verdict, justification


def _call_judge_llm(
    settings: MarketMoverSettings,
    llm_client: LLMClient,
    prompt: str,
) -> str | None:
    """Call Anthropic with ``temperature=0.0`` per the ADR.

    The judge intentionally bypasses the dual Claude/Gemini fallback used
    by ``LLMClient.analyze_articles``: we want the ``judge_model`` field
    to be deterministic per row. Falling back to Gemini would mean two
    different models could appear in the historical record, breaking the
    apples-to-apples comparison Joe wants.
    """
    try:
        import anthropic
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(f"Judge unavailable (anthropic import failed): {e}")
        return None

    keys = settings.claude_api_keys
    if not keys:
        logger.warning("Judge skipped: no Claude API keys configured")
        return None

    # Mirror the LLMClient round-robin behavior by reading the next key off
    # the cycle. We don't have the cycle here, so use the FIRST key — judge
    # calls are once per pick per day (low volume).
    key = keys[0]
    client = anthropic.Anthropic(api_key=key, timeout=45)

    try:
        message = client.messages.create(
            model=settings.judge_model,
            max_tokens=512,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
            timeout=45,
        )
    except Exception as e:
        logger.warning(f"Judge Anthropic call failed: {e}")
        return None

    # Extract first text block, defensive against thinking/tool blocks.
    content = getattr(message, "content", None) or []
    for block in content:
        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if block_type == "text" and isinstance(text, str):
            return text
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            return text
    return None


# ---------------------------------------------------------------------------
# Per-pick judge
# ---------------------------------------------------------------------------


def judge_pick(
    pick: ScorecardPick,
    briefing_date: date,
    settings: MarketMoverSettings,
    llm_client: LLMClient,
) -> Judgment | None:
    """Grade a single pick's 24h price action against the rubric.

    Steps:
    1. Resolve the primary ticker (pick override or category default).
    2. Fetch close-to-close pct change for primary, SPY, VIX (+ optional
       sector ETF). Missing data is passed to the LLM as ``null``.
    3. Build the frozen judge prompt and send it at ``temperature=0.0``.
    4. Parse the JSON response. If the verdict isn't in the locked set,
       retry ONCE. If still bad, log a warning and return ``None``.

    Returns:
        :class:`Judgment` on success, or ``None`` on irrecoverable failure
        (the daily send doesn't fail because the judge did).
    """
    primary_ticker = _resolve_primary_ticker(pick)
    sector_etf = _resolve_sector_etf(pick)

    # ---- Fetch price data (Alpaca daily bars — ADR 0002) ----
    akid = settings.alpaca_api_key_id
    asec = settings.alpaca_api_secret_key
    feed = settings.alpaca_data_feed
    mci = settings.min_call_interval_secs

    primary_pct: float | None = None
    if primary_ticker:
        primary_pct, _ = fetch_24h_close_change(
            primary_ticker, briefing_date, akid, asec, feed=feed, min_call_interval=mci
        )

    spy_pct: float | None = None
    # Avoid a redundant call when primary IS SPY.
    if primary_ticker and primary_ticker.upper() == "SPY":
        spy_pct = primary_pct
    else:
        spy_pct, _ = fetch_24h_close_change(
            "SPY", briefing_date, akid, asec, feed=feed, min_call_interval=mci
        )

    # VIX is proxied by the VIXY ETF for direction only — vix_close stays None.
    vix_pct, vix_close = fetch_24h_close_change(
        "VIX", briefing_date, akid, asec, feed=feed, min_call_interval=mci
    )

    sector_pct: float | None = None
    if sector_etf:
        sector_pct, _ = fetch_24h_close_change(
            sector_etf, briefing_date, akid, asec, feed=feed, min_call_interval=mci
        )

    prompt = _build_judge_prompt(
        pick=pick,
        primary_pct=primary_pct,
        spy_pct=spy_pct,
        vix_close=vix_close,
        vix_pct=vix_pct,
        sector_etf=sector_etf,
        sector_pct=sector_pct,
    )

    # ---- Call the LLM (with one retry on bad verdict) ----
    raw = _call_judge_llm(settings, llm_client, prompt)
    parsed = _parse_judge_response(raw or "")
    if parsed is None:
        logger.warning(
            "Judge response invalid on first call for pick #%d (%r); retrying",
            pick.rank,
            pick.title[:60],
        )
        raw = _call_judge_llm(settings, llm_client, prompt)
        parsed = _parse_judge_response(raw or "")
        if parsed is None:
            logger.warning(
                "Judge response still invalid after retry for pick #%d (%r) — "
                "dropping judgment",
                pick.rank,
                pick.title[:60],
            )
            return None

    verdict, justification = parsed

    # spy_pct / vix_close / vix_pct are required (non-Optional) on PriceData
    # per the ADR schema. When Finnhub is down for SPY/VIX entirely, fall
    # back to 0.0 with the field still populated (the judge already saw
    # ``null`` in the prompt so the verdict reflects the uncertainty).
    return Judgment(
        rank=pick.rank,
        verdict=verdict,  # type: ignore[arg-type]
        justification=justification,
        price_data=PriceData(
            primary_ticker=primary_ticker,
            primary_pct_change_24h=primary_pct,
            spy_pct=spy_pct if spy_pct is not None else 0.0,
            vix_close=vix_close if vix_close is not None else 0.0,
            vix_pct_change=vix_pct if vix_pct is not None else 0.0,
            sector_etf=sector_etf,
            sector_pct=sector_pct,
        ),
    )


# ---------------------------------------------------------------------------
# Day-level orchestrator
# ---------------------------------------------------------------------------


def judge_yesterday(
    yesterday: BriefingRecord,
    settings: MarketMoverSettings,
    llm_client: LLMClient,
) -> list[Judgment] | None:
    """Grade every pick in ``yesterday`` in parallel.

    Re-run safe: if ``yesterday.judgments`` is already populated, returns
    those existing judgments unchanged — we do NOT spend another LLM call
    on a row we've already graded. This keeps the daily pipeline idempotent
    if a workflow re-run happens after a partial commit.

    Returns ``None`` only when every per-pick judge call returned ``None``
    (LLM/network is completely broken). A mixed success (e.g. 2 of 3 picks
    judged) returns a list of those that succeeded.
    """
    if yesterday.judgments is not None:
        # Already graded — short-circuit (supports workflow re-runs).
        return yesterday.judgments

    if not yesterday.picks:
        return None

    # 3 picks → 3 workers. Per-pick judge calls fetch 4 quotes serially
    # inside the thread, then one Anthropic call — running picks in parallel
    # keeps the total latency to roughly the slowest single-pick call.
    judgments: list[Judgment] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(
                judge_pick, pick, yesterday.date, settings, llm_client
            ): pick
            for pick in yesterday.picks
        }
        for future, pick in futures.items():
            try:
                result = future.result()
            except Exception as e:
                logger.warning(
                    "Judge raised for pick #%d (%r): %s — dropping",
                    pick.rank,
                    pick.title[:60],
                    e,
                )
                continue
            if result is not None:
                judgments.append(result)

    if not judgments:
        return None

    # Keep rank order stable so the rendered scorecard matches the picks.
    judgments.sort(key=lambda j: j.rank)
    return judgments


def now_iso_utc() -> str:
    """ISO8601 ``graded_at`` timestamp in UTC — pinned for reproducibility."""
    return datetime.now(timezone.utc).isoformat()
