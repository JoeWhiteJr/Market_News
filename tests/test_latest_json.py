"""Tests for the market-context feed published for the Robinhood-Agentic /market page.

``write_latest_json`` emits the most recent briefing records to ``docs/latest.json`` next to
scores.html, so the trading backend can GET a stable GitHub Pages URL once a day. Best-effort:
it must never raise, mirroring ``write_scores_page``.
"""

from __future__ import annotations

import json
from pathlib import Path

from market_mover.scores_page import LATEST_JSON_DAYS, write_latest_json


def _write_ledger(path: Path, dates: list[str]) -> None:
    """Write a minimal JSONL ledger, one record per date, each with a single pick."""
    lines = []
    for day in dates:
        rec = {
            "date": day,
            "schema_version": 1,
            "picks": [
                {"rank": 1, "title": f"Story on {day}", "primary_ticker": "NVDA",
                 "summary": "s", "impact_score": 7, "source_name": "Reuters", "source_url": None}
            ],
            "contrarian": {"headline": "h", "argument": "a", "source_url": None, "source_name": "x"},
        }
        lines.append(json.dumps(rec))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_writes_recent_records_newest_first(tmp_path: Path) -> None:
    ledger = tmp_path / "briefings.jsonl"
    # Deliberately out of order in the file to prove the writer sorts by date.
    _write_ledger(ledger, ["2026-08-11", "2026-08-14", "2026-08-12", "2026-08-13"])
    out = tmp_path / "docs" / "latest.json"

    assert write_latest_json(ledger, out, generated_label="2026-08-16T12:30:00Z") is True

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["generated_at"] == "2026-08-16T12:30:00Z"
    dates = [b["date"] for b in payload["briefings"]]
    assert dates == ["2026-08-14", "2026-08-13", "2026-08-12", "2026-08-11"]
    assert payload["count"] == len(dates)
    # picks survive intact: they are the headline feed the consumer reads.
    assert payload["briefings"][0]["picks"][0]["primary_ticker"] == "NVDA"


def test_caps_at_days_window(tmp_path: Path) -> None:
    ledger = tmp_path / "briefings.jsonl"
    days = [f"2026-08-{d:02d}" for d in range(1, 1 + LATEST_JSON_DAYS + 3)]
    _write_ledger(ledger, days)
    out = tmp_path / "docs" / "latest.json"

    write_latest_json(ledger, out)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["count"] == LATEST_JSON_DAYS
    assert len(payload["briefings"]) == LATEST_JSON_DAYS
    # The most recent day is kept; the oldest fall off.
    assert payload["briefings"][0]["date"] == days[-1]


def test_missing_ledger_writes_empty_feed(tmp_path: Path) -> None:
    # An absent ledger is not an error: load_briefing_records returns [], so we publish an
    # honest empty feed rather than crashing the pipeline.
    out = tmp_path / "docs" / "latest.json"
    assert write_latest_json(tmp_path / "nope.jsonl", out) is True
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["count"] == 0
    assert payload["briefings"] == []


def test_record_missing_date_does_not_crash(tmp_path: Path) -> None:
    ledger = tmp_path / "briefings.jsonl"
    ledger.write_text(
        json.dumps({"picks": []}) + "\n" + json.dumps({"date": "2026-08-14", "picks": []}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "docs" / "latest.json"
    assert write_latest_json(ledger, out) is True
    payload = json.loads(out.read_text(encoding="utf-8"))
    # Dated record sorts ahead of the undated one; both are present.
    assert payload["count"] == 2
    assert payload["briefings"][0]["date"] == "2026-08-14"


def test_never_raises_on_unwritable_path(tmp_path: Path) -> None:
    ledger = tmp_path / "briefings.jsonl"
    _write_ledger(ledger, ["2026-08-14"])
    # A directory where the file should go makes write_text fail; the writer must swallow it.
    clash = tmp_path / "latest.json"
    clash.mkdir()
    assert write_latest_json(ledger, clash) is False
