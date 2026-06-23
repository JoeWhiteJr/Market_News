"""Learning loop — Phase 0: Bayesian-pooled performance by category.

The Yesterday-Index judge grades each pick HIT/PARTIAL/MISS/TOO_EARLY/N_A. With
only a handful of graded days, raw per-category hit-rates are wild (e.g. a
``single_name`` rate of 0/5 = 0% that shouldn't read as "worthless"). We pool
them with a Beta-Binomial model: each category's success rate shrinks toward the
global rate, by an amount that depends on how little data the category has.

This is pure-Python conjugate Bayes — no numpy/scipy. The methodology (verdict→
score mapping, prior) is documented in ADR 0004 so comparisons stay consistent.

Phase 0 is **measurement only**: it computes and reports. Nothing here feeds back
into ranking or sizing yet (that's a later, gated phase).
"""

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger("market_mover.learning")


def load_briefing_records(path: Path) -> list[dict]:
    """Read all briefing rows from the JSONL ledger as plain dicts.

    Returns ``[]`` if the file is absent or unreadable. Malformed lines are
    skipped (best-effort — a learning readout must never break the pipeline).
    """
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning("learning: could not read %s (%s)", path, e)
    return rows

# Verdict -> success score. PARTIAL counts as half a hit. TOO_EARLY and
# NOT_APPLICABLE are NOT gradeable outcomes — they're excluded from the
# denominator entirely (the pick couldn't be scored, so it isn't evidence
# about category quality). Locked in ADR 0004.
VERDICT_SCORE: dict[str, float] = {"HIT": 1.0, "PARTIAL": 0.5, "MISS": 0.0}
_GRADEABLE = set(VERDICT_SCORE)


@dataclass(frozen=True)
class CategoryStat:
    """Pooled performance for one category."""

    category: str
    n: int                 # gradeable picks (HIT/PARTIAL/MISS)
    raw_mean: float        # unpooled mean success (n==0 -> 0.0)
    posterior_mean: float  # Beta-Binomial posterior mean (shrunk toward global)
    ci_low: float          # 90% equal-tailed credible interval
    ci_high: float


@dataclass(frozen=True)
class CategoryReport:
    """Pooled per-category performance across the graded history."""

    total_gradeable: int
    global_mean: float
    prior_strength: float
    window_days: int       # 0 == all history
    categories: list[CategoryStat] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure-Python regularized incomplete beta + inverse (for credible intervals)
# ---------------------------------------------------------------------------


def _betacf(x: float, a: float, b: float) -> float:
    """Continued fraction for the incomplete beta (Numerical Recipes ``betacf``)."""
    MAXIT, EPS, FPMIN = 200, 3.0e-12, 1.0e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def betainc(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta function I_x(a, b) — the Beta(a,b) CDF at x."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(ln_beta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(x, a, b) / a
    return 1.0 - bt * _betacf(1.0 - x, b, a) / b


def beta_ppf(q: float, a: float, b: float) -> float:
    """Inverse Beta(a,b) CDF (quantile) via bisection on :func:`betainc`."""
    if q <= 0.0:
        return 0.0
    if q >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if betainc(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-10:
            break
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Pooling
# ---------------------------------------------------------------------------


def _gradeable_scores_by_category(
    records: list, today: date, window_days: int
) -> dict[str, list[float]]:
    """Collect success scores per category from graded briefing records.

    ``records`` are dicts (parsed JSONL rows). Only rows with ``judgments`` and
    only gradeable verdicts (HIT/PARTIAL/MISS) contribute.
    """
    cutoff = today - timedelta(days=window_days) if window_days > 0 else None
    out: dict[str, list[float]] = {}
    for r in records:
        judgments = r.get("judgments")
        if not judgments:
            continue
        if cutoff is not None:
            try:
                if date.fromisoformat(str(r.get("date"))[:10]) < cutoff:
                    continue
            except (ValueError, TypeError):
                continue
        picks_by_rank = {p.get("rank"): p for p in r.get("picks", [])}
        for j in judgments:
            verdict = j.get("verdict")
            if verdict not in _GRADEABLE:
                continue
            pick = picks_by_rank.get(j.get("rank"))
            category = (pick or {}).get("category") or "other"
            out.setdefault(category, []).append(VERDICT_SCORE[verdict])
    return out


def compute_category_performance(
    records: list,
    today: date,
    prior_strength: float = 4.0,
    window_days: int = 0,
) -> CategoryReport:
    """Beta-Binomial pooled success rate per category.

    Each category's posterior shrinks toward the **global** success rate by
    ``prior_strength`` pseudo-observations (empirical-Bayes-lite: the prior is
    centered on the data's own global mean). Small-n categories shrink hard;
    well-sampled ones barely move.

    Returns a :class:`CategoryReport`. With no gradeable data, ``global_mean``
    falls back to a neutral 0.25 and ``categories`` is empty.
    """
    by_cat = _gradeable_scores_by_category(records, today, window_days)
    total_n = sum(len(v) for v in by_cat.values())
    total_score = sum(sum(v) for v in by_cat.values())

    if total_n == 0:
        return CategoryReport(
            total_gradeable=0, global_mean=0.25, prior_strength=prior_strength,
            window_days=window_days, categories=[],
        )

    global_mean = total_score / total_n
    # Prior centered on the global mean with ``prior_strength`` pseudo-obs.
    alpha0 = max(global_mean * prior_strength, 1e-6)
    beta0 = max((1.0 - global_mean) * prior_strength, 1e-6)

    stats: list[CategoryStat] = []
    for category, scores in by_cat.items():
        n = len(scores)
        s = sum(scores)
        a_post = alpha0 + s
        b_post = beta0 + (n - s)
        post_mean = a_post / (a_post + b_post)
        stats.append(
            CategoryStat(
                category=category,
                n=n,
                raw_mean=s / n if n else 0.0,
                posterior_mean=post_mean,
                ci_low=beta_ppf(0.05, a_post, b_post),
                ci_high=beta_ppf(0.95, a_post, b_post),
            )
        )

    # Most-sampled first, then by posterior mean — stable, readable ordering.
    stats.sort(key=lambda c: (-c.n, -c.posterior_mean))
    return CategoryReport(
        total_gradeable=total_n, global_mean=global_mean,
        prior_strength=prior_strength, window_days=window_days, categories=stats,
    )


def format_category_readout(report: CategoryReport) -> str:
    """One-line-per-category plain-text readout for logs / CLI."""
    if not report.categories:
        return (
            "Learning: no gradeable picks yet "
            f"(global prior {report.global_mean:.0%})."
        )
    window = "all history" if report.window_days == 0 else f"last {report.window_days}d"
    lines = [
        f"Learning — pick hit-quality by category ({window}, "
        f"n={report.total_gradeable}, global {report.global_mean:.0%}, "
        f"prior κ={report.prior_strength:g}):"
    ]
    for c in report.categories:
        lines.append(
            f"  {c.category:13s} n={c.n:<3d} raw={c.raw_mean:.0%}  "
            f"pooled={c.posterior_mean:.0%}  "
            f"90% CI [{c.ci_low:.0%}, {c.ci_high:.0%}]"
        )
    return "\n".join(lines)


def _main() -> None:  # pragma: no cover — thin CLI wrapper
    """Print the current category readout: ``python3 -m market_mover.learning``."""
    from datetime import date as _date

    from .config import MarketMoverSettings

    settings = MarketMoverSettings()
    report = compute_category_performance(
        load_briefing_records(settings.briefings_jsonl_full_path),
        _date.today(),
        prior_strength=settings.learning_prior_strength,
        window_days=settings.learning_window_days,
    )
    print(format_category_readout(report))


if __name__ == "__main__":
    _main()
