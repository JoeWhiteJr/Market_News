"""Tests for the P1 email polish bundle.

Covers:
- HTML escaping of titles / summaries / URLs in the rendered template
- Timestamp rendering in the configured ``BRIEFING_TZ`` (not UTC)
- Subject line: word-boundary truncation + plain-text preheader presence
- URL-derived source attribution (not LLM-provided)
- ``LLMClient`` guard against Anthropic responses with no text blocks
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from market_mover.email_template import (
    _derive_source_name,
    _get_tz,
    _safe_href,
    build_subject,
    render_email_html,
)
from market_mover.llm_client import (
    NO_TEXT_SENTINEL,
    LLMClient,
    _extract_text_from_anthropic_message,
)
from market_mover.models import RankedArticle, RawArticle, SourceType


def _make_article(
    title: str = "Fed Holds Rates Steady",
    url: str = "https://www.reuters.com/markets/fed-holds",
    summary: str = "Fed kept rates flat. Markets reacted modestly.",
    rank: int = 1,
    impact: float = 8.5,
) -> RankedArticle:
    return RankedArticle(
        rank=rank,
        title=title,
        url=url,
        source_name="ignored — derived from URL",
        market_impact_summary=summary,
        impact_score=impact,
    )


class TestHtmlEscaping:
    def test_title_with_script_tag_is_escaped(self):
        article = _make_article(title="<script>alert(1)</script>")
        html = render_email_html([article])
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_summary_with_html_is_escaped(self):
        article = _make_article(summary='Bad <img src=x onerror="evil()">')
        html = render_email_html([article])
        assert 'onerror="evil()"' not in html
        assert "&lt;img" in html

    def test_href_attribute_neutralizes_quote_injection(self):
        # Attacker tries to break out of the href attribute.
        evil_url = 'https://example.com/" onmouseover="alert(1)'
        article = _make_article(url=evil_url)
        html = render_email_html([article])
        # The raw injection should not appear unescaped inside an href.
        assert 'onmouseover="alert(1)"' not in html
        assert 'href="https://example.com/' in html

    def test_normal_urls_remain_clickable(self):
        article = _make_article(url="https://www.reuters.com/markets/fed-holds")
        html = render_email_html([article])
        assert 'href="https://www.reuters.com/markets/fed-holds"' in html


class TestTimezone:
    def test_default_tz_is_america_denver(self, monkeypatch):
        monkeypatch.delenv("BRIEFING_TZ", raising=False)
        tz = _get_tz()
        # ZoneInfo("America/Denver") — key is exposed on the instance.
        assert str(tz) == "America/Denver"

    def test_env_override_changes_tz(self, monkeypatch):
        monkeypatch.setenv("BRIEFING_TZ", "Europe/London")
        tz = _get_tz()
        assert str(tz) == "Europe/London"

    def test_invalid_tz_falls_back_to_utc(self, monkeypatch):
        monkeypatch.setenv("BRIEFING_TZ", "Not/A/Real/Zone")
        tz = _get_tz()
        assert str(tz) == "UTC"

    def test_rendered_date_uses_local_tz_not_utc(self, monkeypatch):
        # Pick a fixed UTC instant that crosses the date line for Denver.
        # 2026-05-13 02:00 UTC == 2026-05-12 20:00 Denver (MDT).
        # If the template uses UTC the date renders as May 13; if it uses
        # America/Denver it renders as May 12.
        monkeypatch.setenv("BRIEFING_TZ", "America/Denver")
        from datetime import timezone as _tz

        fixed_utc = datetime(2026, 5, 13, 2, 0, 0, tzinfo=_tz.utc)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D401 - signature must match datetime.now
                return fixed_utc.astimezone(tz) if tz else fixed_utc

        with patch("market_mover.email_template.datetime", _FixedDatetime):
            html = render_email_html([_make_article()])

        assert "May 12, 2026" in html
        assert "May 13, 2026" not in html


class TestSubjectAndPreheader:
    def test_subject_truncates_at_word_boundary(self):
        long_title = (
            "Federal Reserve announces surprise emergency interest rate cut citing "
            "deteriorating macroeconomic conditions across global markets"
        )
        subject = build_subject([_make_article(title=long_title)])
        # Should not slice mid-word and should be reasonably bounded.
        assert "…" in subject or len(subject) <= 110
        # No mid-word truncation: the last non-prefix word should not be a fragment.
        truncated = subject.split(": ", 1)[-1]
        last_word = truncated.rstrip("…").rstrip().split()[-1]
        # If textwrap.shorten ran, the last word should be a real word from the title.
        assert last_word in long_title.split()

    def test_preheader_div_present_in_html(self):
        article = _make_article(
            summary="Rate cut surprises markets. Equities rallied 2% on the news."
        )
        html = render_email_html([article])
        assert "display:none" in html
        # First sentence of the summary should appear inside the preheader.
        assert "Rate cut surprises markets." in html

    def test_preheader_falls_back_to_title_when_no_summary(self):
        article = _make_article(summary="")
        html = render_email_html([article])
        # Title appears both in preheader and main body — at least one occurrence.
        assert article.title in html


class TestUrlBasedSourceAttribution:
    def test_reuters_netloc_renders_as_reuters(self):
        article = _make_article(url="https://www.reuters.com/markets/fed-holds")
        html = render_email_html([article])
        assert "Reuters" in html

    def test_unknown_netloc_renders_bare_domain(self):
        article = _make_article(url="https://obscure-blog.example.com/post/42")
        html = render_email_html([article])
        assert "obscure-blog.example.com" in html

    def test_www_prefix_stripped(self):
        assert _derive_source_name("https://www.example.com/x") == "example.com"

    def test_empty_url_returns_empty(self):
        assert _derive_source_name("") == ""

    def test_llm_parser_does_not_use_provided_source_name(self, mock_settings):
        """The LLM's source_name should be ignored — we derive from URL."""
        client = LLMClient(mock_settings)
        raw_response = (
            '{"top_3":[{"rank":1,"title":"x",'
            '"url":"https://www.bloomberg.com/news/x",'
            '"source_name":"Motley Fool / Cleveland Fed",'
            '"market_impact_summary":"y","impact_score":7.0,"is_video":false}]}'
        )
        ranked = client._parse_response(raw_response)
        assert ranked[0].source_name == "bloomberg.com"
        assert "Motley Fool" not in ranked[0].source_name


class TestLlmClientGuard:
    def test_extract_text_handles_thinking_only_response(self):
        # ThinkingBlock-like object — has .type="thinking" and no .text.
        thinking_block = SimpleNamespace(type="thinking", thinking="reasoning...")
        msg = SimpleNamespace(content=[thinking_block])
        result = _extract_text_from_anthropic_message(msg)
        assert result == NO_TEXT_SENTINEL

    def test_extract_text_handles_empty_content(self):
        msg = SimpleNamespace(content=[])
        result = _extract_text_from_anthropic_message(msg)
        assert result == NO_TEXT_SENTINEL

    def test_extract_text_skips_tool_use_returns_text_block(self):
        tool_block = SimpleNamespace(type="tool_use", name="x", input={})
        text_block = SimpleNamespace(type="text", text="hello world")
        msg = SimpleNamespace(content=[tool_block, text_block])
        result = _extract_text_from_anthropic_message(msg)
        assert result == "hello world"

    def test_extract_text_returns_first_text_block(self):
        block_a = SimpleNamespace(type="text", text="first")
        block_b = SimpleNamespace(type="text", text="second")
        msg = SimpleNamespace(content=[block_a, block_b])
        assert _extract_text_from_anthropic_message(msg) == "first"

    def test_call_claude_does_not_crash_on_thinking_only(self, mock_settings):
        """Integration-ish: the full _call_claude path should not raise on a
        ThinkingBlock-only response. It should return the sentinel, which the
        upstream parser will then surface as an AnalysisParsingError."""
        client = LLMClient(mock_settings)
        thinking_only = SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="...")]
        )

        class _StubAnthropic:
            def __init__(self, *args, **kwargs):  # accept any client kwargs (timeout, etc.)
                self.messages = SimpleNamespace(create=lambda **_: thinking_only)

        import anthropic as _anthropic_module

        with patch.object(_anthropic_module, "Anthropic", _StubAnthropic):
            # Should not raise IndexError / AttributeError.
            out = client._call_claude("sys", "user")
        assert out == NO_TEXT_SENTINEL


class TestSafeHrefSchemeAllowList:
    """``_safe_href`` must reject any scheme outside http/https.

    A malicious article URL of ``javascript:alert(1)`` would otherwise render as
    a clickable XSS payload in clients that support inline JS in mail (rare, but
    we still defense-in-depth this).
    """

    def test_javascript_scheme_is_rejected(self):
        assert _safe_href("javascript:alert(1)") == "#"

    def test_javascript_scheme_mixed_case_is_rejected(self):
        assert _safe_href("JaVaScRiPt:alert(1)") == "#"

    def test_data_uri_is_rejected(self):
        assert _safe_href("data:text/html,<script>alert(1)</script>") == "#"

    def test_vbscript_scheme_is_rejected(self):
        assert _safe_href("vbscript:msgbox(1)") == "#"

    def test_file_scheme_is_rejected(self):
        assert _safe_href("file:///etc/passwd") == "#"

    def test_protocol_relative_url_is_rejected(self):
        # //example.com has no scheme — unsafe in an email context where there
        # is no "current scheme" to inherit from.
        assert _safe_href("//example.com/x") == "#"

    def test_relative_url_is_rejected(self):
        assert _safe_href("/just/a/path") == "#"

    def test_bare_path_is_rejected(self):
        assert _safe_href("example.com/x") == "#"

    def test_empty_string_returns_empty(self):
        assert _safe_href("") == ""

    def test_whitespace_only_returns_empty(self):
        assert _safe_href("   ") == ""

    def test_http_url_passes_through(self):
        out = _safe_href("http://example.com/x")
        assert out == "http://example.com/x"

    def test_https_url_passes_through(self):
        out = _safe_href("https://example.com/x")
        assert out == "https://example.com/x"

    def test_https_with_query_and_fragment_passes_through(self):
        out = _safe_href("https://example.com/path?q=1&r=2#frag")
        # & must be HTML-escaped inside an href attribute.
        assert "https://example.com/path?q=1" in out
        assert "&amp;r=2" in out
        assert "#frag" in out

    def test_template_falls_back_on_javascript_url(self):
        """End-to-end: rendering an article with a javascript: URL must not
        emit a clickable ``href="javascript:..."`` anchor."""
        article = _make_article(url="javascript:alert(1)")
        html = render_email_html([article])
        assert "javascript:alert" not in html
        assert 'href="#"' in html

    def test_template_renders_rank_aria_label(self):
        """Badge has an aria-label announcing rank + impact score."""
        article = _make_article(rank=1, impact=9.0)
        html = render_email_html([article])
        assert 'aria-label="Rank 1 story, impact score 9.0 out of 10"' in html


class TestUnusedImportShim:
    """Keep static-analysis happy about test-only imports."""

    def test_raw_article_import(self):
        # RawArticle is imported for completeness of test surface.
        assert RawArticle is not None
        assert SourceType.RSS == SourceType("rss")
