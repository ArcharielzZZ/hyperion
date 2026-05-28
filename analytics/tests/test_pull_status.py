"""Tests for wallet bundle pull status writes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from analytics.scripts.force_pull_wallet_bundle import write_status  # noqa: E402


@pytest.fixture()
def status_dir(tmp_path: Path) -> Path:
    bundle_dir = tmp_path / "0xabc"
    bundle_dir.mkdir()
    return bundle_dir


def test_write_status_does_not_carry_stale_error(status_dir: Path):
    path = status_dir / ".pull_status.json"
    path.write_text(
        json.dumps(
            {
                "state": "error",
                "error": "old failure",
                "message": "Fehler beim Pull.",
            }
        ),
        encoding="utf-8",
    )

    write_status(status_dir, state="running", message="Lade Fills ...")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "running"
    assert data["error"] == ""


def test_write_status_keeps_error_on_failure(status_dir: Path):
    write_status(
        status_dir,
        state="error",
        error="ledger parse failed",
        message="Fehler beim Pull.",
    )

    data = json.loads((status_dir / ".pull_status.json").read_text(encoding="utf-8"))
    assert data["state"] == "error"
    assert data["error"] == "ledger parse failed"


def test_write_status_clears_error_on_done(status_dir: Path):
    path = status_dir / ".pull_status.json"
    path.write_text(
        json.dumps({"state": "running", "error": "stale"}),
        encoding="utf-8",
    )

    write_status(status_dir, state="done", message="Fertig", error="")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "done"
    assert data["error"] == ""
