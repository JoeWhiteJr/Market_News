"""Tests for the Overhype Detector (creative #5)."""

from market_mover.hype import HypeScore, score_hype


class TestScoreHype:
    def test_sober_headline_scores_zero(self):
        result = score_hype(
            "Fed holds rates steady, signals patience",
            "The central bank left its benchmark unchanged and reiterated a "
            "data-dependent stance.",
        )
        assert result.score == 0
        assert result.matched_terms == []
        assert result.band == "low"

    def test_hypey_headline_scores_high(self):
        result = score_hype(
            "Nvidia stock SKYROCKETS as AI demand explodes",
            "The chipmaker is a game-changer.",
        )
        assert result.score >= 7
        assert result.band == "high"
        # Distinct terms surfaced for the tooltip.
        assert any("skyrocket" in t for t in result.matched_terms)
        assert "explodes" in result.matched_terms

    def test_title_weighted_more_than_summary(self):
        in_title = score_hype("Stocks surge on earnings", "A quiet session.")
        in_summary = score_hype("Stocks rise on earnings", "Shares surge late.")
        assert in_title.score > in_summary.score

    def test_multiword_phrase_matches_with_hyphen_or_space(self):
        hyphen = score_hype("A real game-changer for chips", "")
        spaced = score_hype("A real game changer for chips", "")
        assert hyphen.score > 0
        assert spaced.score > 0
        assert hyphen.score == spaced.score

    def test_word_boundary_no_false_positive(self):
        # "surge" must not fire inside "insurgent"; "rips" not inside "trips".
        result = score_hype("Insurgent forces and travel trips dominate news", "")
        assert result.score == 0
        assert result.matched_terms == []

    def test_case_insensitive(self):
        assert score_hype("MASSIVE blowout quarter", "").score > 0
        assert score_hype("massive Blowout quarter", "").score > 0

    def test_score_capped_at_ten(self):
        spammy = "skyrockets soars explodes surges plunges craters collapses massive"
        result = score_hype(spammy, spammy)
        assert result.score == 10
        assert result.band == "high"

    def test_empty_inputs(self):
        assert score_hype("", "").score == 0
        assert score_hype("", "").matched_terms == []

    def test_summary_defaults_to_empty(self):
        # summary is optional
        result = score_hype("Markets soar")
        assert result.score > 0

    def test_matched_terms_deduped_across_fields(self):
        result = score_hype("Stocks surge", "More surge ahead")
        # "surge" present in both title and summary -> appears once.
        assert result.matched_terms.count("surge") == 1

    def test_bands(self):
        assert score_hype("calm markets", "").band == "low"          # 0
        assert score_hype("stocks surge", "").band in {"low", "medium"}
        very = score_hype("skyrockets explodes massive", "soars craters")
        assert very.band == "high"

    def test_label_format(self):
        result = score_hype("Nvidia soars", "")
        assert result.label == f"Hype {result.score}/10"


class TestHypeScoreModel:
    def test_is_frozen_dataclass(self):
        h = HypeScore(score=5, matched_terms=["surge"], band="medium")
        assert h.score == 5
        assert h.label == "Hype 5/10"


class TestHypeRendering:
    """Badge wiring through the email renderers (creative #5)."""

    @staticmethod
    def _articles():
        from market_mover.models import RankedArticle

        return [
            RankedArticle(
                rank=1,
                title="Nvidia stock SKYROCKETS as AI demand explodes",
                url="https://example.com/a",
                source_name="example.com",
                market_impact_summary="A game-changer quarter.",
                impact_score=9.1,
            ),
            RankedArticle(
                rank=2,
                title="Fed holds rates steady, signals patience",
                url="https://example.com/b",
                source_name="example.com",
                market_impact_summary="The benchmark was left unchanged.",
                impact_score=6.0,
            ),
        ]

    def _scores(self, articles):
        return {a.rank: score_hype(a.title, a.market_impact_summary) for a in articles}

    def test_html_badge_only_on_hyped_story(self):
        from market_mover.email_template import render_email_html

        articles = self._articles()
        html = render_email_html(articles, hype_scores=self._scores(articles))
        # The hyped #1 carries a badge; the sober #2 (score 0) does not.
        assert "Hype 9/10" in html
        # Exactly one badge label rendered (the sober story is clean).
        assert html.count("Hype 9/10") == 1
        # A clean "Hype 0/10" badge must never appear.
        assert "Hype 0/10" not in html

    def test_plaintext_badge_only_on_hyped_story(self):
        from market_mover.email_template import render_plain_text

        articles = self._articles()
        pt = render_plain_text(articles, hype_scores=self._scores(articles))
        line1 = next(line for line in pt.splitlines() if line.startswith("#1"))
        line2 = next(line for line in pt.splitlines() if line.startswith("#2"))
        assert "Hype 9/10" in line1
        assert "Hype" not in line2

    def test_no_badges_when_scores_absent(self):
        from market_mover.email_template import render_email_html

        articles = self._articles()
        # Feature disabled => no hype_scores passed => no badges at all.
        html = render_email_html(articles)
        assert "Hype" not in html
