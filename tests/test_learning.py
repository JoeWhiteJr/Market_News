"""Tests for the Phase 0 learning loop (Bayesian-pooled category performance)."""

from datetime import date

from market_mover.learning import (
    VERDICT_SCORE,
    beta_ppf,
    betainc,
    compute_category_performance,
    format_category_readout,
    load_briefing_records,
)


def _record(d, *picks):
    """Build a graded briefing record from (rank, category, verdict) tuples."""
    return {
        "date": d,
        "picks": [
            {"rank": r, "category": c, "title": "t", "summary": "s",
             "impact_score": 8.0, "primary_ticker": "X", "source_url": "u",
             "source_name": "n"}
            for r, c, _ in picks
        ],
        "judgments": [{"rank": r, "verdict": v, "justification": "x",
                       "price_data": {}} for r, _, v in picks],
    }


class TestIncompleteBeta:
    def test_symmetric_median(self):
        assert abs(betainc(0.5, 2, 2) - 0.5) < 1e-6
        assert abs(betainc(0.5, 5, 5) - 0.5) < 1e-6

    def test_uniform_cdf(self):
        assert abs(betainc(0.3, 1, 1) - 0.3) < 1e-6

    def test_known_value(self):
        # I_0.3(2,5) = 0.579825 (hand-summed from the binomial form).
        assert abs(betainc(0.3, 2, 5) - 0.579825) < 1e-5

    def test_boundaries(self):
        assert betainc(0.0, 2, 3) == 0.0
        assert betainc(1.0, 2, 3) == 1.0

    def test_ppf_inverts_cdf(self):
        for q in (0.05, 0.25, 0.5, 0.75, 0.95):
            x = beta_ppf(q, 3, 4)
            assert abs(betainc(x, 3, 4) - q) < 1e-4

    def test_ppf_uniform(self):
        assert abs(beta_ppf(0.9, 1, 1) - 0.9) < 1e-6


class TestVerdictScore:
    def test_mapping(self):
        assert VERDICT_SCORE == {"HIT": 1.0, "PARTIAL": 0.5, "MISS": 0.0}

    def test_ungradeable_excluded(self):
        # TOO_EARLY / NOT_APPLICABLE are not in the score map.
        assert "TOO_EARLY" not in VERDICT_SCORE
        assert "NOT_APPLICABLE" not in VERDICT_SCORE


class TestComputeCategoryPerformance:
    TODAY = date(2026, 6, 19)

    def test_empty_returns_neutral_prior(self):
        rep = compute_category_performance([], self.TODAY)
        assert rep.total_gradeable == 0
        assert rep.global_mean == 0.25
        assert rep.categories == []

    def test_ungradeable_verdicts_excluded(self):
        recs = [_record("2026-06-18",
                        (1, "macro", "HIT"), (2, "macro", "NOT_APPLICABLE"),
                        (3, "macro", "TOO_EARLY"))]
        rep = compute_category_performance(recs, self.TODAY)
        macro = next(c for c in rep.categories if c.category == "macro")
        assert macro.n == 1  # only the HIT counts
        assert rep.total_gradeable == 1

    def test_pooling_shrinks_toward_global(self):
        # single_name 0/4, macro 2/2 -> global = 2/6 = 0.333.
        recs = [
            _record("2026-06-17", (1, "single_name", "MISS"), (2, "single_name", "MISS")),
            _record("2026-06-18", (1, "single_name", "MISS"), (2, "single_name", "MISS")),
            _record("2026-06-19", (1, "macro", "HIT"), (2, "macro", "HIT")),
        ]
        rep = compute_category_performance(recs, self.TODAY, prior_strength=4.0)
        sn = next(c for c in rep.categories if c.category == "single_name")
        mac = next(c for c in rep.categories if c.category == "macro")
        # single_name raw 0% -> pooled strictly above 0 (shrunk up toward global).
        assert sn.raw_mean == 0.0
        assert sn.posterior_mean > 0.0
        assert sn.posterior_mean < rep.global_mean
        # macro raw 100% -> pooled below 100% (shrunk down toward global).
        assert mac.raw_mean == 1.0
        assert mac.posterior_mean < 1.0
        assert mac.posterior_mean > rep.global_mean

    def test_partial_counts_half(self):
        recs = [_record("2026-06-18", (1, "macro", "PARTIAL"), (2, "macro", "PARTIAL"))]
        rep = compute_category_performance(recs, self.TODAY)
        assert rep.global_mean == 0.5  # two PARTIALs => mean 0.5

    def test_credible_interval_brackets_mean(self):
        recs = [_record("2026-06-18", (1, "macro", "HIT"), (2, "macro", "MISS"))]
        c = compute_category_performance(recs, self.TODAY).categories[0]
        assert 0.0 <= c.ci_low <= c.posterior_mean <= c.ci_high <= 1.0

    def test_window_filter_excludes_old(self):
        recs = [
            _record("2026-01-01", (1, "macro", "HIT")),   # far outside 7d
            _record("2026-06-18", (1, "macro", "MISS")),  # inside 7d
        ]
        rep = compute_category_performance(recs, self.TODAY, window_days=7)
        assert rep.total_gradeable == 1


class TestReadout:
    def test_empty(self):
        out = format_category_readout(compute_category_performance([], date(2026, 6, 19)))
        assert "no gradeable picks" in out

    def test_non_empty_has_categories(self):
        recs = [_record("2026-06-18", (1, "macro", "HIT"), (2, "geopolitical", "MISS"))]
        out = format_category_readout(compute_category_performance(recs, date(2026, 6, 19)))
        assert "macro" in out and "geopolitical" in out
        assert "pooled=" in out


class TestLoadRecords:
    def test_missing_file(self, tmp_path):
        assert load_briefing_records(tmp_path / "nope.jsonl") == []

    def test_skips_malformed_lines(self, tmp_path):
        p = tmp_path / "b.jsonl"
        p.write_text('{"date":"2026-06-18"}\nNOT JSON\n{"date":"2026-06-19"}\n')
        rows = load_briefing_records(p)
        assert len(rows) == 2
