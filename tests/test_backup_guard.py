"""Regression tests for the GitHub backup guard (MM-T022).

On 2026-11-24 the Monday backup started at 01:05 UTC, looked up Tuesday's runs, found
none, and sent a second briefing. These run the real script against a fake ``gh``.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "ci" / "backup-guard.sh"
WINDOW = ROOT / "scripts" / "ci" / "briefing-window.sh"
MONDAY_DISPATCH = "2026-11-23T13:00:02Z"

# Mimics `gh run list --created <day | start..end>` and records `gh issue create`.
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["issue", "create"]:
    with open(os.environ["FAKE_ISSUES"], "a") as fh:
        fh.write(args[args.index("--title") + 1] + "\n")
    sys.exit(0)
if os.environ.get("FAKE_GH_FAIL"):
    sys.exit(1)
created = args[args.index("--created") + 1]
lo, hi = created.split("..") if ".." in created else (created + "T00:00:00Z", created + "T23:59:59Z")
runs = json.loads(os.environ["FAKE_RUNS"])
print(json.dumps([r for r in runs if lo <= r["createdAt"] <= hi]))
'''


def _run(createdAt: str, conclusion: str = "success", status: str = "completed",
         title: str = "Daily Market Briefing") -> dict:
    return {"createdAt": createdAt, "conclusion": conclusion, "status": status, "displayTitle": title}


def _epoch(iso: str) -> str:
    return str(int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()))


def _guard(tmp_path: Path, now: str, runs: list[dict], event: str = "schedule",
           dry_run: str = "", gh_fails: bool = False) -> tuple[int, str | None, list[str]]:
    """Run the guard; return (exit code, skip output, issues opened)."""
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    output, issues = tmp_path / "output", tmp_path / "issues"
    env = {
        **os.environ, "PATH": f"{gh.parent}:{os.environ['PATH']}", "GITHUB_OUTPUT": str(output),
        "GITHUB_EVENT_NAME": event, "DRY_RUN": dry_run, "GUARD_NOW_EPOCH": _epoch(now),
        "FAKE_RUNS": json.dumps(runs), "FAKE_ISSUES": str(issues),
    }
    if gh_fails:
        env["FAKE_GH_FAIL"] = "1"
    proc = subprocess.run(["bash", str(GUARD)], env=env, capture_output=True, text=True)
    out = output.read_text() if output.exists() else ""
    skip = out.strip().split("=")[-1] if out else None
    return proc.returncode, skip, issues.read_text().splitlines() if issues.exists() else []


def test_backup_delayed_past_utc_midnight_does_not_resend(tmp_path: Path) -> None:
    """The actual 2026-11-24 incident."""
    assert _guard(tmp_path, "2026-11-24T01:05:03", [_run(MONDAY_DISPATCH)]) == (0, "true", [])


def test_backup_skips_same_day_when_dispatch_succeeded(tmp_path: Path) -> None:
    assert _guard(tmp_path, "2026-11-23T20:30:00", [_run(MONDAY_DISPATCH)]) == (0, "true", [])


def test_backup_skips_while_dispatch_still_running(tmp_path: Path) -> None:
    runs = [_run(MONDAY_DISPATCH, conclusion="", status="in_progress")]
    assert _guard(tmp_path, "2026-11-23T13:30:00", runs) == (0, "true", [])


@pytest.mark.parametrize("runs", [
    [],
    [_run(MONDAY_DISPATCH, conclusion="failure")],
    [_run(MONDAY_DISPATCH, title="Dry run")],
    [_run("2026-11-20T13:00:00Z")],
])
def test_backup_sends_when_no_good_run_that_day(tmp_path: Path, runs: list[dict]) -> None:
    assert _guard(tmp_path, "2026-11-23T20:30:00", runs) == (0, "false", [])


@pytest.mark.parametrize("now", ["2026-11-23T23:30:00", "2026-11-24T01:05:03"])
def test_too_late_and_missed_fails_loudly_without_sending(tmp_path: Path, now: str) -> None:
    code, skip, issues = _guard(tmp_path, now, [])
    assert code != 0
    assert skip == "true"
    assert issues == ["[daily-briefing MISSED] 2026-11-23"]


def test_friday_backup_landing_on_saturday_does_not_resend(tmp_path: Path) -> None:
    assert _guard(tmp_path, "2026-11-21T02:00:00", [_run("2026-11-20T13:00:00Z")]) == (0, "true", [])


def test_summer_dispatch_at_noon_utc_is_found(tmp_path: Path) -> None:
    assert _guard(tmp_path, "2026-07-14T00:40:00", [_run("2026-07-13T12:00:02Z")]) == (0, "true", [])


def test_lookup_failure_in_hours_still_sends(tmp_path: Path) -> None:
    assert _guard(tmp_path, "2026-11-23T14:00:00", [], gh_fails=True) == (0, "false", [])


def test_manual_dispatch_always_sends(tmp_path: Path) -> None:
    result = _guard(tmp_path, "2026-11-24T01:05:00", [_run(MONDAY_DISPATCH)], event="workflow_dispatch")
    assert result == (0, "false", [])


def test_dry_run_skips(tmp_path: Path) -> None:
    result = _guard(tmp_path, "2026-11-23T13:00:00", [], event="workflow_dispatch", dry_run="true")
    assert result == (0, "true", [])


@pytest.mark.parametrize(("now", "day", "created"), [
    ("2026-11-24T01:56:19", "2026-11-23", "2026-11-23T11:00:00Z..2026-11-24T10:59:59Z"),
    ("2026-11-24T10:59:59", "2026-11-23", "2026-11-23T11:00:00Z..2026-11-24T10:59:59Z"),
    ("2026-11-24T11:00:00", "2026-11-24", "2026-11-24T11:00:00Z..2026-11-25T10:59:59Z"),
    ("2026-12-31T15:00:00", "2026-12-31", "2026-12-31T11:00:00Z..2027-01-01T10:59:59Z"),
])
def test_briefing_window(now: str, day: str, created: str) -> None:
    proc = subprocess.run(
        ["bash", "-c", f"source '{WINDOW}' && echo \"$BRIEFING_DAY $BRIEFING_CREATED\""],
        env={**os.environ, "GUARD_NOW_EPOCH": _epoch(now)}, capture_output=True, text=True, check=True,
    )
    assert proc.stdout.split() == [day, created]


def test_workflows_use_the_shared_scripts() -> None:
    workflows = ROOT / ".github" / "workflows"
    assert "scripts/ci/backup-guard.sh" in (workflows / "daily-briefing.yml").read_text()
    assert "scripts/ci/briefing-window.sh" in (workflows / "briefing-watchdog.yml").read_text()
