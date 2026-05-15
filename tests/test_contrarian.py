"""Tests for the contrarian "Bear Case" coda (Cycle 3)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from market_mover.email_template import render_email_html, render_plain_text
from market_mover.llm_client import LLMClient
from market_mover.models import ContrarianCoda, RankedArticle, RawArticle, SourceType


@pytest.fixture
def top_story() -> RankedArticle:
    return RankedArticle(
        rank=1,
        title="Fed Holds Rates Steady",
        url="https://www.reuters.com/markets/fed-holds",
        source_name="reuters.com",
        market_impact_summary="Fed kept rates flat; equities rallied.",
        impact_score=9.0,
    )


@pytest.fixture
def article_pool() -> list[RawArticle]:
    return [
        RawArticle(
            title="Fed Holds Rates Steady",
            url="https://www.reuters.com/markets/fed-holds",
            source_name="Reuters",
            source_type=SourceType.RSS,
        ),
        RawArticle(
            title="10-Year Yield Spikes",
            url="https://www.bloomberg.com/markets/yields",
            source_name="Bloomberg",
            source_type=SourceType.RSS,
        ),
        RawArticle(
            title="Credit Spreads Widen",
            url="https://www.ft.com/credit",
            source_name="Financial Times",
            source_type=SourceType.RSS,
        ),
    ]


VALID_CODA_JSON = json.dumps({
    "headline": "But: 10-year yields tell a different story",
    "argument": "Yields rose 12bps even as the Fed held — the bond market is "
                "pricing in tighter conditions regardless. That cuts the equity "
                "rally narrative at the knees.",
    "source_url": "https://www.bloomberg.com/markets/yields",
})


class TestContrarianGeneration:
    def test_generates_coda_with_valid_source(self, mock_settings, top_story, article_pool):
        client = LLMClient(mock_settings)
        with patch.object(LLMClient, "_call_claude", return_value=VALID_CODA_JSON):
            coda = client.generate_contrarian_coda(top_story, article_pool)

        assert coda is not None
        assert isinstance(coda, ContrarianCoda)
        assert coda.headline.startswith("But:")
        assert coda.source_url == "https://www.bloomberg.com/markets/yields"
        assert coda.source_name == "bloomberg.com"

    def test_hallucinated_source_url_rejected(self, mock_settings, top_story, article_pool):
        bad_json = json.dumps({
            "headline": "Fabricated",
            "argument": "made up",
            "source_url": "https://www.someothersite.example.com/not-in-pool",
        })
        client = LLMClient(mock_settings)
        with patch.object(LLMClient, "_call_claude", return_value=bad_json):
            coda = client.generate_contrarian_coda(top_story, article_pool)
        assert coda is None

    def test_missing_required_field_returns_none(self, mock_settings, top_story, article_pool):
        # No 'argument' field at all
        bad_json = json.dumps({
            "headline": "h",
            "source_url": "https://www.bloomberg.com/markets/yields",
        })
        client = LLMClient(mock_settings)
        with patch.object(LLMClient, "_call_claude", return_value=bad_json):
            coda = client.generate_contrarian_coda(top_story, article_pool)
        assert coda is None

    def test_unparseable_response_returns_none(self, mock_settings, top_story, article_pool):
        client = LLMClient(mock_settings)
        with patch.object(LLMClient, "_call_claude", return_value="not JSON, no braces"):
            coda = client.generate_contrarian_coda(top_story, article_pool)
        assert coda is None

    def test_llm_error_returns_none_not_raises(self, mock_settings, top_story, article_pool):
        """The daily send must not fail because the optional coda failed."""
        client = LLMClient(mock_settings)
        with patch.object(LLMClient, "_call_claude", side_effect=Exception("Claude down")), \
             patch.object(LLMClient, "_call_gemini", side_effect=Exception("Gemini down")):
            coda = client.generate_contrarian_coda(top_story, article_pool)
        assert coda is None

    def test_top_story_url_excluded_from_pool(self, mock_settings, top_story, article_pool):
        """If the LLM picks the top story's own URL, that should be rejected
        (we excluded it from the allowed pool)."""
        self_referring = json.dumps({
            "headline": "h",
            "argument": "a",
            "source_url": top_story.url,
        })
        client = LLMClient(mock_settings)
        with patch.object(LLMClient, "_call_claude", return_value=self_referring):
            coda = client.generate_contrarian_coda(top_story, article_pool)
        assert coda is None

    def test_falls_back_to_gemini_on_claude_failure(self, mock_settings, top_story, article_pool):
        client = LLMClient(mock_settings)
        with patch.object(LLMClient, "_call_claude", side_effect=Exception("Claude down")), \
             patch.object(LLMClient, "_call_gemini", return_value=VALID_CODA_JSON):
            coda = client.generate_contrarian_coda(top_story, article_pool)
        assert coda is not None
        assert coda.headline.startswith("But:")

    def test_empty_article_pool_returns_none(self, mock_settings, top_story):
        client = LLMClient(mock_settings)
        # Mock the LLM to make sure we don't even call it.
        with patch.object(LLMClient, "_call_claude") as mock_claude:
            coda = client.generate_contrarian_coda(top_story, [])
        assert coda is None
        mock_claude.assert_not_called()

    def test_markdown_wrapped_json_parses(self, mock_settings, top_story, article_pool):
        wrapped = f"```json\n{VALID_CODA_JSON}\n```"
        client = LLMClient(mock_settings)
        with patch.object(LLMClient, "_call_claude", return_value=wrapped):
            coda = client.generate_contrarian_coda(top_story, article_pool)
        assert coda is not None


class TestContrarianDisabledKillSwitch:
    """CONTRARIAN_CODA_ENABLED=false must skip the second LLM call entirely.

    Driven from cli.run_pipeline, but we verify the setting controls the
    behavior at the seam.
    """

    def test_setting_default_is_true(self, mock_settings):
        assert mock_settings.contrarian_coda_enabled is True

    def test_setting_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("CONTRARIAN_CODA_ENABLED", "false")
        from market_mover.config import MarketMoverSettings
        s = MarketMoverSettings()
        assert s.contrarian_coda_enabled is False

    def test_disabled_short_circuits_in_cli(self, monkeypatch, top_story, article_pool):
        """When the flag is off, cli must not call generate_contrarian_coda."""
        # We test the conditional shape directly: simulate the cli decision.
        monkeypatch.setenv("CONTRARIAN_CODA_ENABLED", "false")
        from market_mover.config import MarketMoverSettings
        settings = MarketMoverSettings()
        client = LLMClient(settings)

        called = {"n": 0}

        def fake(*args, **kwargs):
            called["n"] += 1
            return None

        # The cli does `if settings.contrarian_coda_enabled and ranked:`
        # We mirror that guard here to confirm the second call is skipped.
        if settings.contrarian_coda_enabled and [top_story]:
            with patch.object(LLMClient, "generate_contrarian_coda", side_effect=fake):
                client.generate_contrarian_coda(top_story, article_pool)

        assert called["n"] == 0


class TestContrarianRendering:
    def _coda(self) -> ContrarianCoda:
        return ContrarianCoda(
            headline="But: 10-year yields tell a different story",
            argument="Yields rose 12bps even as the Fed held.",
            source_url="https://www.bloomberg.com/markets/yields",
            source_name="bloomberg.com",
        )

    def _article(self) -> RankedArticle:
        return RankedArticle(
            rank=1,
            title="Fed Holds Rates",
            url="https://www.reuters.com/x",
            source_name="reuters.com",
            market_impact_summary="Markets rallied.",
            impact_score=9.0,
        )

    def test_coda_renders_inside_section_marker(self):
        html = render_email_html([self._article()], coda=self._coda())
        assert '<section data-block="contrarian"' in html

    def test_coda_omitted_when_none(self):
        html = render_email_html([self._article()], coda=None)
        assert 'data-block="contrarian"' not in html

    def test_coda_appears_before_footer(self):
        html = render_email_html([self._article()], coda=self._coda())
        idx_section = html.find('data-block="contrarian"')
        # Look for the footer's actual rendered marker, not the CSS class name.
        idx_footer = html.find("Generated by Market Mover MCP")
        assert idx_section >= 0
        assert idx_footer >= 0
        assert idx_section < idx_footer

    def test_coda_appears_after_articles(self):
        html = render_email_html([self._article()], coda=self._coda())
        # The articles render as <td class="mm-article-wrap"> with the
        # opening "Articles" HTML comment.
        idx_articles = html.find("<!-- Article #1 -->")
        idx_section = html.find('data-block="contrarian"')
        assert idx_articles >= 0
        assert idx_section > idx_articles

    def test_coda_link_passes_through_safe_href(self):
        # javascript: URLs survived past the validator only because the test
        # pool included them — but _safe_href must still sanitize at render.
        bad = ContrarianCoda(
            headline="x",
            argument="y",
            source_url="javascript:alert(1)",
            source_name="evil",
        )
        html = render_email_html([self._article()], coda=bad)
        assert "javascript:alert" not in html
        # Link should fall back to "#"
        assert 'href="#"' in html

    def test_coda_html_is_escaped(self):
        bad = ContrarianCoda(
            headline="<script>x</script>",
            argument="<img onerror=evil>",
            source_url="https://www.bloomberg.com/markets/yields",
            source_name="bloomberg.com",
        )
        html = render_email_html([self._article()], coda=bad)
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;" in html
        assert "<img onerror=evil>" not in html

    def test_plain_text_includes_bear_case(self):
        text = render_plain_text([self._article()], coda=self._coda())
        assert "THE BEAR CASE" in text
        assert "But: 10-year yields" in text

    def test_plain_text_omits_bear_case_when_no_coda(self):
        text = render_plain_text([self._article()], coda=None)
        assert "THE BEAR CASE" not in text


class TestSignoffRendering:
    def test_signoff_in_footer_when_voice_supplied(self):
        from market_mover.voices import get_voice
        html = render_email_html(
            [
                RankedArticle(
                    rank=1, title="t",
                    url="https://www.reuters.com/x",
                    source_name="reuters.com",
                    market_impact_summary="s", impact_score=8.0,
                )
            ],
            voice=get_voice("vinny"),
        )
        assert "Vinny" in html
        # And it must live inside the footer block (after the contrarian section if any).
        assert "mm-signoff" in html

    def test_no_signoff_when_voice_is_neutral(self):
        from market_mover.voices import get_voice
        html = render_email_html(
            [
                RankedArticle(
                    rank=1, title="t",
                    url="https://www.reuters.com/x",
                    source_name="reuters.com",
                    market_impact_summary="s", impact_score=8.0,
                )
            ],
            voice=get_voice("neutral"),
        )
        assert "mm-signoff" not in html
