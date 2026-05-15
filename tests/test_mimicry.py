"""Tests for the weekly style-mimicry rotation (Cycle 3)."""

from __future__ import annotations

from datetime import date

import pytest

from market_mover.email_template import build_subject
from market_mover.mimicry import (
    all_mimicry_voices,
    mimicry_voice_for,
    mimicry_voice_to_voice_spec,
)
from market_mover.models import RankedArticle


def _ranked() -> list[RankedArticle]:
    return [
        RankedArticle(
            rank=1,
            title="Fed Holds Rates",
            url="https://www.reuters.com/x",
            source_name="reuters.com",
            market_impact_summary="Markets reacted.",
            impact_score=8.0,
        )
    ]


class TestMimicryVoiceSet:
    def test_five_voices_in_rotation(self):
        assert len(all_mimicry_voices()) == 5

    def test_all_voices_have_required_fields(self):
        for v in all_mimicry_voices():
            assert v["name"]
            assert v["system_prompt_suffix"]
            assert v["signoff"].startswith("—")

    def test_parody_frame_in_every_suffix(self):
        for v in all_mimicry_voices():
            assert "parody" in v["system_prompt_suffix"].lower()
            assert "do not claim" in v["system_prompt_suffix"].lower() or "do not attribute" in v["system_prompt_suffix"].lower()

    def test_expected_personas_present(self):
        names = {v["name"] for v in all_mimicry_voices()}
        # Order-independent — just confirm the curated five are in there.
        assert "Jim Cramer" in names
        assert any("Buffett" in n for n in names)
        assert "Matt Levine" in names
        assert "Zerohedge" in names
        assert any("FT" in n for n in names)


class TestMimicryRotation:
    def test_disabled_returns_none(self):
        # 2026-05-13 is a Wednesday — but with weekday=-1, mimicry is disabled.
        assert mimicry_voice_for(date(2026, 5, 13), -1) is None

    def test_invalid_weekday_returns_none(self):
        assert mimicry_voice_for(date(2026, 5, 13), 7) is None
        assert mimicry_voice_for(date(2026, 5, 13), 99) is None

    def test_non_mimicry_day_returns_none(self):
        # 2026-05-12 is a Tuesday. Weekday=2 (Wednesday) → no match.
        assert mimicry_voice_for(date(2026, 5, 12), 2) is None

    def test_mimicry_day_returns_voice(self):
        # 2026-05-13 is a Wednesday → weekday=2 matches.
        voice = mimicry_voice_for(date(2026, 5, 13), 2)
        assert voice is not None
        assert voice["name"] in {v["name"] for v in all_mimicry_voices()}

    def test_rotation_changes_across_weeks(self):
        # Consecutive Wednesdays should differ in voice (5 voices, mod 5).
        voices = []
        for d in [date(2026, 5, 6), date(2026, 5, 13), date(2026, 5, 20), date(2026, 5, 27), date(2026, 6, 3)]:
            v = mimicry_voice_for(d, 2)
            assert v is not None
            voices.append(v["name"])
        # 5 consecutive Wednesdays should hit all 5 voices exactly once.
        assert len(set(voices)) == 5

    def test_rotation_is_deterministic(self):
        v_a = mimicry_voice_for(date(2026, 5, 13), 2)
        v_b = mimicry_voice_for(date(2026, 5, 13), 2)
        assert v_a == v_b

    @pytest.mark.parametrize(
        "test_date,iso_week_expected_idx",
        [
            (date(2026, 5, 13), date(2026, 5, 13).isocalendar()[1] % 5),
            (date(2026, 5, 20), date(2026, 5, 20).isocalendar()[1] % 5),
        ],
    )
    def test_index_matches_iso_week_modulo(self, test_date, iso_week_expected_idx):
        voice = mimicry_voice_for(test_date, 2)
        assert voice is not None
        assert voice["name"] == all_mimicry_voices()[iso_week_expected_idx]["name"]


class TestMimicryVoiceSpecAdapter:
    def test_adapter_preserves_fields(self):
        mim = all_mimicry_voices()[0]
        spec = mimicry_voice_to_voice_spec(mim)
        assert spec["name"] == mim["name"]
        assert spec["system_prompt_suffix"] == mim["system_prompt_suffix"]
        assert spec["signoff"] == mim["signoff"]


class TestSubjectMimicryLabel:
    def test_no_label_subject_unchanged(self):
        subj = build_subject(_ranked(), prefix="[MM]")
        assert "in the voice of" not in subj

    def test_label_appended_when_set(self):
        subj = build_subject(_ranked(), prefix="[MM]", mimicry_label="Matt Levine")
        assert "in the voice of Matt Levine" in subj
        # Original headline still present
        assert "Fed Holds Rates" in subj

    def test_label_empty_string_treated_as_disabled(self):
        subj = build_subject(_ranked(), prefix="[MM]", mimicry_label="")
        assert "in the voice of" not in subj

    def test_label_appears_after_headline(self):
        subj = build_subject(_ranked(), prefix="[MM]", mimicry_label="Jim Cramer")
        idx_headline = subj.find("Fed Holds Rates")
        idx_label = subj.find("in the voice of")
        assert idx_headline >= 0 and idx_label > idx_headline
