"""Tests for the market-context feed published for the Robinhood-Agentic /market page.

``write_latest_json`` emits ``docs/latest.json`` next to scores.html, in the shape the trading
backend's market-context route reads: top-level ``generated_at`` / ``brief_date`` / ``macro_read`` /
``headlines`` (mapped from the newest brief's picks) plus ``top_movers`` (the ranked picks). The
route serves ONE brief, so the feed is derived from the single newest briefing. Best-effort: it must
never raise, mirroring ``write_scores_page``.
"""

from __future__ import annotations

import json
from pathlib import Path

from market_mover.scores_page import write_latest_json


def _record(day: str, picks: list[dict]) -> dict:
    return {"date": day, "schema_version": 1, "picks": picks}


def _write_ledger(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_maps_newest_brief_picks_to_headlines_and_movers(tmp_path: Path) -> None:
    ledger = tmp_path / "briefings.jsonl"
    _write_ledger(ledger, [
        _record("2026-08-25", [
            {"rank": 1, "title": "TSMC lifts outlook", "summary": "read-through to compute",
             "primary_ticker": "tsm", "category": "AI hardware",
             "source_name": "Reuters", "source_url": "https://reuters.com/x"},
            {"rank": 2, "title": "Vistra signs PPA", "summary": "power leg option",
             "primary_ticker": "VST", "category": "AI power",
             "source_name": "Bloomberg", "source_url": None},
        ]),
    ])
    out = tmp_path / "docs" / "latest.json"

    assert write_latest_json(ledger, out, generated_label="2026-08-25T12:30:00Z") is True
    p = json.loads(out.read_text(encoding="utf-8"))

    assert p["schema_version"] == 2
    assert p["generated_at"] == "2026-08-25T12:30:00Z"
    assert p["brief_date"] == "2026-08-25"
    assert p["macro_read"] is None

    # headlines: mapped from picks, ticker upper-cased, url passed through, sentiment null.
    h0 = p["headlines"][0]
    assert h0 == {
        "id": "mm-2026-08-25-1",
        "title": "TSMC lifts outlook",
        "source": "Reuters",
        "url": "https://reuters.com/x",
        "published_at": "2026-08-25T12:00:00Z",
        "summary": "read-through to compute",
        "tickers": ["TSM"],
        "sentiment": None,
    }

    # top_movers: the ranked picks, justification from the pick summary, verdict null.
    m = p["top_movers"]
    assert [x["rank"] for x in m] == [1, 2]
    assert m[0]["ticker"] == "TSM" and m[0]["category"] == "AI hardware"
    assert m[0]["justification"] == "read-through to compute"
    assert m[0]["verdict"] is None
    assert m[1]["ticker"] == "VST"


def test_derives_from_the_single_newest_brief(tmp_path: Path) -> None:
    ledger = tmp_path / "briefings.jsonl"
    # Out of order in the file to prove the writer picks the newest date, not the last line.
    _write_ledger(ledger, [
        _record("2026-08-24", [{"rank": 1, "title": "old", "primary_ticker": "OLD", "summary": "s"}]),
        _record("2026-08-25", [{"rank": 1, "title": "new", "primary_ticker": "NEW", "summary": "s"}]),
        _record("2026-08-23", [{"rank": 1, "title": "older", "primary_ticker": "OLDER", "summary": "s"}]),
    ])
    out = tmp_path / "docs" / "latest.json"

    write_latest_json(ledger, out)
    p = json.loads(out.read_text(encoding="utf-8"))

    assert p["brief_date"] == "2026-08-25"
    assert [h["title"] for h in p["headlines"]] == ["new"]
    assert p["top_movers"][0]["ticker"] == "NEW"


def test_missing_ledger_writes_empty_feed(tmp_path: Path) -> None:
    # An absent ledger is not an error: publish an honest empty feed rather than crashing the pipeline.
    out = tmp_path / "docs" / "latest.json"
    assert write_latest_json(tmp_path / "nope.jsonl", out) is True
    p = json.loads(out.read_text(encoding="utf-8"))
    assert p["headlines"] == []
    assert p["top_movers"] == []
    assert p["brief_date"] is None


def test_undated_record_does_not_crash(tmp_path: Path) -> None:
    ledger = tmp_path / "briefings.jsonl"
    _write_ledger(ledger, [
        {"picks": [{"rank": 1, "title": "undated", "primary_ticker": "U", "summary": "s"}]},
        _record("2026-08-25", [{"rank": 1, "title": "dated", "primary_ticker": "D", "summary": "s"}]),
    ])
    out = tmp_path / "docs" / "latest.json"
    assert write_latest_json(ledger, out) is True
    p = json.loads(out.read_text(encoding="utf-8"))
    # The dated record is newest and is the one served.
    assert p["brief_date"] == "2026-08-25"
    assert p["headlines"][0]["title"] == "dated"


def test_never_raises_on_unwritable_path(tmp_path: Path) -> None:
    ledger = tmp_path / "briefings.jsonl"
    _write_ledger(ledger, [_record("2026-08-25", [{"rank": 1, "title": "t", "primary_ticker": "T", "summary": "s"}])])
    # A directory where the file should go makes write_text fail; the writer must swallow it.
    clash = tmp_path / "latest.json"
    clash.mkdir()
    assert write_latest_json(ledger, clash) is False
