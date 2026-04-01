"""Tests for the MCP server tools and deduplication logic."""



from market_mover.email_template import build_subject, render_email_html, render_plain_text
from market_mover.models import RankedArticle, RawArticle, SourceType
from market_mover.server import _deduplicate_articles, _normalize_url


class TestDeduplication:
    def test_removes_exact_url_duplicates(self):
        articles = [
            RawArticle(
                title="Same Article",
                url="https://example.com/article",
                source_name="Source A",
                source_type=SourceType.NEWSAPI,
                summary="Short",
            ),
            RawArticle(
                title="Same Article",
                url="https://example.com/article",
                source_name="Source B",
                source_type=SourceType.RSS,
                summary="Longer summary here",
            ),
        ]
        result = _deduplicate_articles(articles)
        assert len(result) == 1
        assert result[0].summary == "Longer summary here"

    def test_removes_similar_titles(self):
        articles = [
            RawArticle(
                title="Fed Raises Interest Rates by 25 Basis Points",
                url="https://reuters.com/fed",
                source_name="Reuters",
                source_type=SourceType.RSS,
                summary="Short",
            ),
            RawArticle(
                title="Fed Raises Interest Rates by 25 Basis Points Today",
                url="https://cnbc.com/fed",
                source_name="CNBC",
                source_type=SourceType.NEWSAPI,
                summary="Longer summary about the Fed decision",
            ),
        ]
        result = _deduplicate_articles(articles)
        assert len(result) == 1
        assert result[0].summary == "Longer summary about the Fed decision"

    def test_keeps_different_articles(self):
        articles = [
            RawArticle(
                title="Fed Raises Rates",
                url="https://example.com/fed",
                source_name="Reuters",
                source_type=SourceType.RSS,
            ),
            RawArticle(
                title="NVIDIA Earnings Beat",
                url="https://example.com/nvidia",
                source_name="CNBC",
                source_type=SourceType.NEWSAPI,
            ),
        ]
        result = _deduplicate_articles(articles)
        assert len(result) == 2

    def test_skips_articles_without_title_or_url(self):
        articles = [
            RawArticle(
                title="", url="https://example.com/a",
                source_name="X", source_type=SourceType.RSS,
            ),
            RawArticle(
                title="Good Article", url="",
                source_name="X", source_type=SourceType.RSS,
            ),
            RawArticle(
                title="Valid Article", url="https://example.com/valid",
                source_name="X", source_type=SourceType.RSS,
            ),
        ]
        result = _deduplicate_articles(articles)
        assert len(result) == 1
        assert result[0].title == "Valid Article"


class TestNormalizeUrl:
    def test_strips_query_params(self):
        assert _normalize_url("https://example.com/article?ref=twitter") == "https://example.com/article"

    def test_strips_trailing_slash(self):
        assert _normalize_url("https://example.com/article/") == "https://example.com/article"

    def test_preserves_path(self):
        assert _normalize_url("https://example.com/news/fed-rates") == "https://example.com/news/fed-rates"


class TestEmailTemplate:
    def test_render_html_contains_article_titles(self, sample_ranked_articles):
        html = render_email_html(sample_ranked_articles)
        assert "Fed Raises Interest Rates" in html
        assert "NVIDIA Reports Record" in html
        assert "Oil Prices Surge" in html

    def test_render_html_contains_links(self, sample_ranked_articles):
        html = render_email_html(sample_ranked_articles)
        assert "https://example.com/fed-rates" in html
        assert "https://example.com/nvidia-earnings" in html

    def test_render_html_contains_impact_scores(self, sample_ranked_articles):
        html = render_email_html(sample_ranked_articles)
        assert "9.5/10" in html
        assert "8.7/10" in html

    def test_render_plain_text(self, sample_ranked_articles):
        text = render_plain_text(sample_ranked_articles)
        assert "Fed Raises Interest Rates" in text
        assert "NVIDIA Reports Record" in text
        assert "https://example.com/fed-rates" in text

    def test_video_article_shows_watch_label(self):
        video = RankedArticle(
            rank=1,
            title="Market Analysis Video",
            url="https://youtube.com/watch?v=abc",
            source_name="CNBC",
            market_impact_summary="Analysis of today's market.",
            impact_score=7.0,
            is_video=True,
        )
        html = render_email_html([video])
        assert "Watch" in html

        text = render_plain_text([video])
        assert "Watch" in text

    def test_build_subject_includes_top_title(self, sample_ranked_articles):
        subject = build_subject(sample_ranked_articles)
        assert "[Market Mover]" in subject
        assert "Fed Raises" in subject

    def test_build_subject_fallback_without_articles(self):
        subject = build_subject([])
        assert "[Market Mover]" in subject
        assert "Daily Market Briefing" in subject
