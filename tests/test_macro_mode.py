"""Tests for Geographic / Macro Mode (creative #18)."""

import json
from unittest.mock import patch

from market_mover.llm_client import LLMClient
from market_mover.macro_mode import (
    MACRO_BIAS_INSTRUCTION,
    detect_macro_mode,
)
from market_mover.models import RankedArticle, RawArticle, SourceType


def _art(title, summary=""):
    return RawArticle(title=title, url="https://x.com", source_name="x",
                      source_type=SourceType.RSS, summary=summary)


class TestDetectMacroMode:
    def test_macro_heavy_day_active(self):
        arts = [
            _art("Fed holds rates steady"),
            _art("CPI inflation cools to 2%"),
            _art("ECB signals a rate cut"),
            _art("Apple unveils a new phone"),
        ]
        sig = detect_macro_mode(arts)
        assert sig.active is True
        assert sig.matched_count == 3
        assert "fed" in sig.themes

    def test_normal_day_inactive(self):
        arts = [
            _art("Apple earnings beat"),
            _art("Tesla announces recall"),
            _art("Nvidia launches chip"),
            _art("Fed official speaks at lunch"),  # 1 macro mention only
        ]
        assert detect_macro_mode(arts).active is False

    def test_empty_inactive(self):
        sig = detect_macro_mode([])
        assert sig.active is False
        assert sig.total == 0

    def test_below_min_count_inactive(self):
        # 2 macro of 3 clears the fraction but not the default min_count of 3.
        arts = [_art("Fed decision"), _art("CPI report"), _art("Apple news")]
        assert detect_macro_mode(arts, min_count=3).active is False

    def test_below_min_fraction_inactive(self):
        # 3 macro headlines but in a big pool -> fraction too low.
        arts = [_art("Fed"), _art("CPI"), _art("ECB")] + [_art(f"Company {i}") for i in range(20)]
        assert detect_macro_mode(arts, min_count=3, min_fraction=0.30).active is False

    def test_word_boundary(self):
        # "fed" must not match inside "fedex" / "federation"
        assert detect_macro_mode([_art("FedEx ships more"), _art("Sports federation news")]).matched_count == 0


class TestMacroPromptInjection:
    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_macro_instruction_appended_when_on(self, mock_claude, mock_settings):
        mock_claude.return_value = json.dumps({"top_3": [{
            "rank": 1, "title": "t", "url": "https://x.com", "source_name": "x",
            "market_impact_summary": "s", "impact_score": 8.0, "is_video": False,
        }]})
        client = LLMClient(mock_settings)
        client.analyze_articles([_art("Fed decision")], macro_mode=True)
        system_prompt = mock_claude.call_args.args[0]
        assert MACRO_BIAS_INSTRUCTION in system_prompt

    @patch("market_mover.llm_client.LLMClient._call_claude")
    def test_no_macro_instruction_when_off(self, mock_claude, mock_settings):
        mock_claude.return_value = json.dumps({"top_3": [{
            "rank": 1, "title": "t", "url": "https://x.com", "source_name": "x",
            "market_impact_summary": "s", "impact_score": 8.0, "is_video": False,
        }]})
        client = LLMClient(mock_settings)
        client.analyze_articles([_art("Apple earnings")], macro_mode=False)
        system_prompt = mock_claude.call_args.args[0]
        assert MACRO_BIAS_INSTRUCTION not in system_prompt


class TestMacroRendering:
    def _arts(self):
        return [RankedArticle(rank=1, title="X", url="https://x.com/a",
                              source_name="x", market_impact_summary="s", impact_score=8.0)]

    def test_html_badge_when_on(self):
        from market_mover.email_template import render_email_html
        assert "MACRO MODE" in render_email_html(self._arts(), macro_mode=True)

    def test_html_no_badge_when_off(self):
        from market_mover.email_template import render_email_html
        assert "MACRO MODE" not in render_email_html(self._arts(), macro_mode=False)

    def test_plain_text_tag(self):
        from market_mover.email_template import render_plain_text
        assert "[MACRO MODE]" in render_plain_text(self._arts(), macro_mode=True)
        assert "[MACRO MODE]" not in render_plain_text(self._arts(), macro_mode=False)
