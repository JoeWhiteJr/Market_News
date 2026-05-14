"""Regression tests for the Claude -> Gemini fallback path.

Cycle 1's PR #1 introduced a sentinel return from
``_extract_text_from_anthropic_message`` when the Anthropic response had no
usable text content (empty completion or only ThinkingBlock / ToolUseBlock).
The sentinel then flowed into ``_parse_response`` and was re-raised as
``AnalysisParsingError`` BEFORE the Gemini fallback path ran — so an empty
Claude response would abort the briefing instead of falling through to Gemini.

Cycle 2 fix: the extractor raises ``EmptyLLMResponse`` (a subclass of
``LLMError``) which is caught by the broad ``except Exception`` in
``analyze_articles``, allowing the Gemini path to run.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from market_mover.exceptions import EmptyLLMResponse
from market_mover.llm_client import LLMClient
from market_mover.models import RawArticle, SourceType

VALID_GEMINI_RESPONSE = json.dumps({
    "top_3": [
        {
            "rank": 1,
            "title": "Fed Raises Rates",
            "url": "https://example.com/fed",
            "market_impact_summary": "Rate hikes affect all sectors.",
            "impact_score": 9.0,
            "is_video": False,
        },
    ]
})


@pytest.fixture
def sample_articles():
    return [
        RawArticle(
            title="Fed Raises Rates",
            url="https://example.com/fed",
            source_name="Reuters",
            source_type=SourceType.RSS,
            summary="Rate hike of 25bps",
        ),
    ]


class TestEmptyAnthropicResponseFallsBackToGemini:
    """Regression: a Claude response with no text content must fall through to Gemini."""

    def test_thinking_only_anthropic_response_falls_back_to_gemini(
        self, mock_settings, sample_articles
    ):
        client = LLMClient(mock_settings)
        thinking_only = SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="reasoning...")]
        )

        class _StubAnthropic:
            def __init__(self, *args, **kwargs):
                self.messages = SimpleNamespace(create=lambda **_: thinking_only)

        import anthropic as _anthropic_module

        with patch.object(_anthropic_module, "Anthropic", _StubAnthropic), patch.object(
            LLMClient, "_call_gemini", return_value=VALID_GEMINI_RESPONSE
        ) as mock_gemini:
            ranked, model, _voice = client.analyze_articles(sample_articles)

        assert mock_gemini.called, "Gemini fallback was not invoked"
        assert len(ranked) == 1
        assert ranked[0].title == "Fed Raises Rates"
        assert "gemini" in model.lower() or "flash" in model.lower()

    def test_empty_anthropic_content_falls_back_to_gemini(
        self, mock_settings, sample_articles
    ):
        client = LLMClient(mock_settings)
        empty_msg = SimpleNamespace(content=[])

        class _StubAnthropic:
            def __init__(self, *args, **kwargs):
                self.messages = SimpleNamespace(create=lambda **_: empty_msg)

        import anthropic as _anthropic_module

        with patch.object(_anthropic_module, "Anthropic", _StubAnthropic), patch.object(
            LLMClient, "_call_gemini", return_value=VALID_GEMINI_RESPONSE
        ) as mock_gemini:
            ranked, model, _voice = client.analyze_articles(sample_articles)

        assert mock_gemini.called
        assert len(ranked) == 1
        assert "gemini" in model.lower() or "flash" in model.lower()

    def test_empty_llm_response_is_a_subclass_of_llm_error(self):
        """``analyze_articles`` catches generic ``Exception`` for transient
        failures; ``EmptyLLMResponse`` must inherit from a base that is NOT
        ``AnalysisParsingError`` (which is re-raised) so the fallback runs."""
        from market_mover.exceptions import AnalysisParsingError, LLMError

        assert issubclass(EmptyLLMResponse, LLMError)
        assert not issubclass(EmptyLLMResponse, AnalysisParsingError)
