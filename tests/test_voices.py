"""Tests for the briefing voice persona layer (Cycle 3)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from market_mover.llm_client import LLMClient, _build_system_prompt
from market_mover.models import RankedArticle
from market_mover.voices import (
    DEFAULT_VOICE,
    NEUTRAL_VOICE,
    available_voices,
    contains_profanity,
    get_voice,
    strip_profanity,
)


class TestVoiceConfig:
    def test_default_voice_is_vinny(self):
        assert DEFAULT_VOICE == "vinny"
        voice = get_voice(None)
        assert "Vinny" in voice["name"]
        assert voice["system_prompt_suffix"]
        assert voice["signoff"].startswith("—")

    def test_all_four_voices_supported(self):
        keys = available_voices()
        assert set(keys) >= {"vinny", "neutral", "terminal", "villain"}

    def test_neutral_voice_has_no_suffix(self):
        voice = get_voice("neutral")
        assert voice["system_prompt_suffix"] == ""
        assert voice["signoff"] == ""

    def test_terminal_voice_loaded(self):
        voice = get_voice("terminal")
        assert "Bloomberg-terminal" in voice["system_prompt_suffix"]
        assert voice["signoff"]

    def test_villain_voice_loaded(self):
        voice = get_voice("villain")
        assert "Bond-villain" in voice["system_prompt_suffix"]
        assert voice["signoff"]

    def test_unknown_voice_falls_back_to_default(self):
        voice = get_voice("doesnotexist")
        assert voice["name"] == get_voice(DEFAULT_VOICE)["name"]

    def test_voice_lookup_is_case_insensitive(self):
        v1 = get_voice("VINNY")
        v2 = get_voice("vinny")
        assert v1["name"] == v2["name"]


class TestSystemPromptBuilder:
    def test_empty_suffix_returns_base_prompt(self):
        neutral = get_voice(NEUTRAL_VOICE)
        out = _build_system_prompt("BASE", neutral)
        assert out == "BASE"

    def test_non_empty_suffix_is_appended(self):
        vinny = get_voice("vinny")
        out = _build_system_prompt("BASE", vinny)
        assert "BASE" in out
        assert "VOICE / TONE:" in out
        # Some characteristic Vinny phrase
        assert "Vinny" in out or "floor trader" in out
        # JSON contract reminder must still be there
        assert "JSON" in out


class TestProfanityFilter:
    def test_clean_text_has_no_profanity(self):
        assert not contains_profanity("Rate hikes pressure growth stocks.")
        assert strip_profanity("Rate hikes pressure growth stocks.") == "Rate hikes pressure growth stocks."

    def test_obvious_profanity_detected(self):
        assert contains_profanity("This market is shit today.")
        assert contains_profanity("What the fuck is the Fed doing")

    def test_word_boundary_protects_substrings(self):
        # 'assess' contains 'ass' but should NOT match
        assert not contains_profanity("Analysts assess the impact.")
        # 'classic' contains 'ass' as substring — must not match
        assert not contains_profanity("A classic risk-off day.")

    def test_strip_replaces_profanity(self):
        out = strip_profanity("This is a shit market.")
        assert "shit" not in out
        assert "[redacted]" in out

    def test_strip_is_idempotent(self):
        once = strip_profanity("a shit day for damn investors")
        twice = strip_profanity(once)
        assert once == twice

    @pytest.mark.parametrize(
        "phrase",
        [
            "fuck",
            "F U C K",     # spaced
            "shit",
            "BITCH",       # caps
            "asshole",
        ],
    )
    def test_obfuscation_variations_caught(self, phrase):
        assert contains_profanity(f"opening word {phrase} closing")


class TestProfanityGuardrailInClient:
    """The LLM client must scrub profanity, and (by default) flip the voice to neutral."""

    def _ranked(self, summary: str) -> list[dict]:
        return [
            RankedArticle(
                rank=1,
                title="x",
                url="https://www.reuters.com/y",
                source_name="reuters.com",
                market_impact_summary=summary,
                impact_score=8.0,
            )
        ]

    def test_clean_output_keeps_voice(self, mock_settings):
        client = LLMClient(mock_settings)
        clean = self._ranked("Rate cut shocks markets.")
        scrubbed, effective = client._enforce_profanity_guardrail(clean, get_voice("vinny"))
        assert effective["name"] == get_voice("vinny")["name"]
        assert scrubbed[0].market_impact_summary == "Rate cut shocks markets."

    def test_dirty_output_falls_back_to_neutral(self, mock_settings):
        client = LLMClient(mock_settings)
        dirty = self._ranked("This is a shit day for the long bond.")
        scrubbed, effective = client._enforce_profanity_guardrail(dirty, get_voice("vinny"))
        assert effective["name"] == get_voice(NEUTRAL_VOICE)["name"]
        # Stripped, not retained verbatim
        assert "shit" not in scrubbed[0].market_impact_summary

    def test_override_to_neutral_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("BRIEFING_VOICE_OVERRIDE_TO_NEUTRAL_ON_DETECT", "false")
        monkeypatch.setenv("CLAUDE_API_KEY_1", "test")
        from market_mover.config import MarketMoverSettings
        settings = MarketMoverSettings()
        client = LLMClient(settings)
        dirty = self._ranked("a shit day")
        scrubbed, effective = client._enforce_profanity_guardrail(dirty, get_voice("vinny"))
        # Voice kept, text still scrubbed
        assert effective["name"] == get_voice("vinny")["name"]
        assert "shit" not in scrubbed[0].market_impact_summary


class TestVoiceFlowsThroughAnalyzeArticles:
    """The voice argument must reach the Claude prompt unchanged."""

    def test_voice_suffix_appears_in_system_prompt(self, mock_settings):
        client = LLMClient(mock_settings)
        captured = {}

        valid_response = (
            '{"top_3":[{"rank":1,"title":"t","url":"https://example.com/x",'
            '"market_impact_summary":"s","impact_score":7.0,"is_video":false}]}'
        )

        def fake_call_claude(system_prompt, user_prompt):
            captured["system_prompt"] = system_prompt
            return valid_response

        with patch.object(LLMClient, "_call_claude", side_effect=fake_call_claude):
            from market_mover.models import RawArticle, SourceType
            articles = [
                RawArticle(
                    title="t", url="https://example.com/x",
                    source_name="x", source_type=SourceType.RSS,
                )
            ]
            ranked, _model, voice = client.analyze_articles(
                articles, voice=get_voice("vinny")
            )

        assert "Vinny" in captured["system_prompt"] or "floor trader" in captured["system_prompt"]
        assert voice["name"] == get_voice("vinny")["name"]
        assert len(ranked) == 1
