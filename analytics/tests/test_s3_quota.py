"""Tests for S3 monthly quota tracking."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analytics.lib import s3_quota


@pytest.fixture
def usage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(s3_quota, "USAGE_DIR", tmp_path / "s3_usage")
    return s3_quota.USAGE_DIR


def test_preflight_allows_when_under_cap(usage_dir):
    allowed, msg = s3_quota.preflight_check(1024, month_key="2026-05")
    assert allowed is True
    assert "OK" in msg


def test_preflight_blocks_when_over_cap(usage_dir):
    path = usage_dir / "2026-05.json"
    usage_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "month": "2026-05",
                "cap_bytes": s3_quota.MONTHLY_CAP_BYTES,
                "bytes_downloaded": s3_quota.MONTHLY_CAP_BYTES - 100,
                "pulls": [],
            }
        ),
        encoding="utf-8",
    )
    allowed, msg = s3_quota.preflight_check(200, month_key="2026-05")
    assert allowed is False
    assert "Limit" in msg


def test_record_download_accumulates(usage_dir):
    s3_quota.record_download(500, {"key": "a"}, month_key="2026-05")
    s3_quota.record_download(300, {"key": "b"}, month_key="2026-05")
    usage = s3_quota.get_month_usage("2026-05")
    assert usage["bytes_downloaded"] == 800
    assert len(usage["pulls"]) == 2


def test_format_usage_display_warns_near_cap(usage_dir, monkeypatch):
    monkeypatch.setattr(s3_quota, "WARN_BYTES", 1000)
    s3_quota.record_download(1500, {"key": "x"}, month_key="2026-06")
    text, level = s3_quota.format_usage_display("2026-06")
    assert "100 GB" in text
    assert level == "warn"
