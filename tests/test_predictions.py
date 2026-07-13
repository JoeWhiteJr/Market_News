"""Tests for The Call · Beat the Bot prediction game (MM-T007)."""

from datetime import date
from unittest.mock import patch

from market_mover.predictions import (
    DailyCall,
    PredictionRecord,
    append_prediction,
    build_mailto,
    load_predictions,
    render_prediction_html,
    render_prediction_plain,
    resolve_call,
    resolve_outcome,
    rewrite_predictions,
    season_stats,
)

D1, D2 = date(2026, 7, 11), date(2026, 7, 12)


def _call(ticker="MU", direction="UP", conf=68, stmt="MU closes green today."):
    return DailyCall(ticker=ticker, direction=direction, confidence=conf, statement=stmt)


class TestResolveOutcome:
    def test_up_call_up_move_is_hit(self):
        assert resolve_outcome("UP", 2.3) == "HIT"

    def test_up_call_down_move_is_miss(self):
        assert resolve_outcome("UP", -1.4) == "MISS"

    def test_down_call_down_move_is_hit(self):
        assert resolve_outcome("DOWN", -0.9) == "HIT"

    def test_flat_move_is_push(self):
        assert resolve_outcome("UP", 0.01) == "PUSH"
        assert resolve_outcome("DOWN", -0.02) == "PUSH"

    def test_direction_case_insensitive(self):
        assert resolve_outcome("up", 1.0) == "HIT"


class TestResolveCall:
    def test_resolves_from_price(self):
        rec = PredictionRecord(date=D1, call=_call(direction="UP"))
        with patch("market_mover.predictions.fetch_24h_close_change", return_value=(1.8, None)):
            out = resolve_call(rec, "k", "s", min_call_interval=0.0)
        assert out.resolved is True
        assert out.outcome == "HIT"
        assert abs(out.pct_change - 1.8) < 1e-9

    def test_no_price_leaves_unresolved(self):
        rec = PredictionRecord(date=D1, call=_call())
        with patch("market_mover.predictions.fetch_24h_close_change", return_value=(None, None)):
            out = resolve_call(rec, "k", "s", min_call_interval=0.0)
        assert out.resolved is False
        assert out.outcome is None


class TestSeasonStats:
    def test_counts_bot_and_humans(self):
        recs = [
            PredictionRecord(date=D1, call=_call(direction="UP"), resolved=True,
                             outcome="HIT", pct_change=2.0,
                             human_calls={"Joe": "UP", "Jared": "DOWN"}),
            PredictionRecord(date=D2, call=_call(direction="DOWN"), resolved=True,
                             outcome="MISS", pct_change=1.1,
                             human_calls={"Joe": "UP", "Jared": "DOWN"}),
        ]
        stats = season_stats(recs)
        assert stats["Bot"] == (1, 1)     # HIT then MISS
        assert stats["Joe"] == (2, 0)     # UP right both days (both moved up)
        assert stats["Jared"] == (0, 2)   # DOWN wrong both days

    def test_push_and_unresolved_ignored(self):
        recs = [
            PredictionRecord(date=D1, call=_call(), resolved=True, outcome="PUSH",
                             pct_change=0.0, human_calls={"Joe": "UP"}),
            PredictionRecord(date=D2, call=_call(), resolved=False),
        ]
        stats = season_stats(recs)
        assert stats["Bot"] == (0, 0)
        assert "Joe" not in stats  # PUSH day scores for nobody


class TestPersistence:
    def test_append_load_roundtrip(self, tmp_path):
        p = tmp_path / "predictions.jsonl"
        r1 = PredictionRecord(date=D1, call=_call())
        r2 = PredictionRecord(date=D2, call=_call(ticker="USO", direction="DOWN"))
        append_prediction(r1, p)
        append_prediction(r2, p)
        loaded = load_predictions(p)
        assert [r.date for r in loaded] == [D1, D2]
        assert loaded[1].call.ticker == "USO"

    def test_rewrite_patches_in_place(self, tmp_path):
        p = tmp_path / "predictions.jsonl"
        recs = [PredictionRecord(date=D1, call=_call())]
        rewrite_predictions(recs, p)
        recs[0] = recs[0].model_copy(update={"resolved": True, "outcome": "HIT", "pct_change": 1.0})
        rewrite_predictions(recs, p)
        loaded = load_predictions(p)
        assert len(loaded) == 1 and loaded[0].outcome == "HIT"

    def test_missing_file_is_empty(self, tmp_path):
        assert load_predictions(tmp_path / "nope.jsonl") == []

    def test_malformed_line_skipped(self, tmp_path):
        p = tmp_path / "predictions.jsonl"
        append_prediction(PredictionRecord(date=D1, call=_call()), p)
        with p.open("a") as fh:
            fh.write("NOT JSON\n")
        assert len(load_predictions(p)) == 1


class TestMailto:
    def test_builds_reply_all_link(self):
        link = build_mailto(["joe@x.com", "jared@x.com"], D2, "USO", "DOWN")
        assert link.startswith("mailto:")
        assert "joe@x.com" in link and "jared@x.com" in link
        assert "USO" in link and "DOWN" in link


class TestRender:
    def _stats(self):
        return {"Bot": (3, 1), "Joe": (2, 2)}

    def test_html_has_call_buttons_and_scoreboard(self):
        today = PredictionRecord(date=D2, call=_call(ticker="USO", direction="DOWN",
                                                     stmt="Oil fades today."))
        yest = PredictionRecord(date=D1, call=_call(), resolved=True, outcome="HIT",
                                pct_change=2.3, human_calls={"Joe": "UP"})
        html = render_prediction_html(today, yest, self._stats(), ["joe@x.com"])
        assert 'data-block="prediction"' in html
        assert "Oil fades today." in html
        assert "mailto:" in html
        assert "Yesterday" in html and "HIT" in html
        assert "Bot" in html

    def test_empty_when_no_call_today(self):
        assert render_prediction_html(None, None, {}, ["joe@x.com"]) == ""
        assert render_prediction_plain(None, None, {}, ["joe@x.com"]) == ""

    def test_plain_has_play_instructions(self):
        today = PredictionRecord(date=D2, call=_call())
        out = render_prediction_plain(today, None, self._stats(), ["joe@x.com"])
        assert "THE CALL" in out and "reply-all UP or DOWN" in out
