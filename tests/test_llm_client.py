"""Tests for the LLM client."""

import json
from unittest.mock import patch

import pytest

from market_mover.config import MarketMoverSettings
from market_mover.exceptions import AnalysisParsingError, LLMError
from market_mover.llm_client import LLMClient
from market_mover.models import RawArticle, SourceType


@pytest.fixture
def llm_client(mock_settings):
    return LLMClient(mock_settings)


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


VALID_LLM_RESPONSE = json.dumps({
    "top_3": [
        {
            "rank": 1,
            "title": "Fed Raises Rates",
            "url": "https://example.com/fed",
            "source_name": "Reuters",
            "market_impact_summary": "Rate hikes affect all sectors.",
            "impact_score": 9.0,
            "is_video": False,
        },
    ]
})


class TestLLMClient:
    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_analyze_articles_with_claude(self, mock_claude, llm_client, sample_articles):
        mock_claude.return_value = VALID_LLM_RESPONSE
        ranked, model, _voice = llm_client.analyze_articles(sample_articles)

        assert len(ranked) == 1
        assert ranked[0].rank == 1
        assert ranked[0].title == "Fed Raises Rates"
        assert ranked[0].impact_score == 9.0
        assert "claude" in model.lower() or "sonnet" in model.lower()

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_track_record_appended_to_system_prompt(
        self, mock_claude, llm_client, sample_articles
    ):
        """Phase 1 (ADR 0005): a non-empty track_record must reach the prompt."""
        mock_claude.return_value = VALID_LLM_RESPONSE
        block = "YOUR TRACK RECORD — macro: 22% over 19 graded picks"
        llm_client.analyze_articles(sample_articles, track_record=block)
        system_prompt = mock_claude.call_args.args[0]
        assert block in system_prompt

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_no_track_record_leaves_prompt_unchanged(
        self, mock_claude, llm_client, sample_articles
    ):
        """Feedback-off baseline: None/empty track_record adds nothing."""
        mock_claude.return_value = VALID_LLM_RESPONSE
        llm_client.analyze_articles(sample_articles, track_record=None)
        assert "YOUR TRACK RECORD" not in mock_claude.call_args.args[0]
        llm_client.analyze_articles(sample_articles, track_record="")
        assert "YOUR TRACK RECORD" not in mock_claude.call_args.args[0]

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_generate_daily_call_parses_and_clamps(self, mock_claude, llm_client, sample_ranked_articles):
        """MM-T007: The Call parses a valid prediction and clamps confidence."""
        import json as _json
        mock_claude.return_value = _json.dumps({
            "ticker": "mu", "direction": "up", "confidence": 140,
            "statement": "Micron closes green today.",
        })
        call = llm_client.generate_daily_call(sample_ranked_articles)
        assert call is not None
        assert call.ticker == "MU"        # upper-cased
        assert call.direction == "UP"
        assert call.confidence == 95      # clamped to [50, 95]
        assert "Micron" in call.statement

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_generate_daily_call_rejects_bad_direction(self, mock_claude, llm_client, sample_ranked_articles):
        import json as _json
        mock_claude.return_value = _json.dumps({
            "ticker": "MU", "direction": "SIDEWAYS", "confidence": 60, "statement": "x",
        })
        assert llm_client.generate_daily_call(sample_ranked_articles) is None

    def test_generate_daily_call_empty_articles_returns_none(self, llm_client):
        assert llm_client.generate_daily_call([]) is None

    @patch("market_mover.llm_client.LLMClient._call_gemini")
    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_falls_back_to_gemini(self, mock_claude, mock_gemini, llm_client, sample_articles):
        mock_claude.side_effect = Exception("Claude unavailable")
        mock_gemini.return_value = VALID_LLM_RESPONSE

        ranked, model, _voice = llm_client.analyze_articles(sample_articles)

        assert len(ranked) == 1
        assert "gemini" in model.lower() or "flash" in model.lower()

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_parses_markdown_wrapped_json(self, mock_claude, llm_client, sample_articles):
        mock_claude.return_value = f"```json\n{VALID_LLM_RESPONSE}\n```"
        ranked, _model, _voice = llm_client.analyze_articles(sample_articles)
        assert len(ranked) == 1

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_parses_json_embedded_in_text(self, mock_claude, llm_client, sample_articles):
        mock_claude.return_value = f"Here are the results:\n{VALID_LLM_RESPONSE}\nDone!"
        ranked, _model, _voice = llm_client.analyze_articles(sample_articles)
        assert len(ranked) == 1

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_raises_on_unparseable_response(self, mock_claude, llm_client, sample_articles):
        mock_claude.return_value = "This is not JSON at all, no braces here."
        with pytest.raises(AnalysisParsingError):
            llm_client.analyze_articles(sample_articles)

    def test_raises_without_api_keys(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_API_KEY_1", "")
        monkeypatch.setenv("CLAUDE_API_KEY_2", "")
        monkeypatch.setenv("GEMINI_API_KEY_1", "")
        monkeypatch.setenv("GEMINI_API_KEY_2", "")
        settings = MarketMoverSettings()
        client = LLMClient(settings)
        articles = [
            RawArticle(
                title="Test", url="https://x.com", source_name="X",
                source_type=SourceType.RSS, summary="test",
            )
        ]
        with pytest.raises(LLMError, match="No API keys configured"):
            client.analyze_articles(articles)
