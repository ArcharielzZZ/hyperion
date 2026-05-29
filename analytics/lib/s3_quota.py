"""Monthly S3 download quota tracking for Hyperliquid archive pulls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
USAGE_DIR = REPO_ROOT / "analytics" / "data_lake" / "s3_usage"

MONTHLY_CAP_BYTES = 100 * 1024 * 1024 * 1024  # 100 GB
WARN_BYTES = int(MONTHLY_CAP_BYTES * 0.8)


def _usage_path(month_key: str | None = None) -> Path:
    if month_key is None:
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    return USAGE_DIR / f"{month_key}.json"


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_month_usage(month_key: str | None = None) -> dict:
    """Return usage record for the month, creating an empty one if missing."""
    if month_key is None:
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    path = _usage_path(month_key)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("month", month_key)
            data.setdefault("cap_bytes", MONTHLY_CAP_BYTES)
            data.setdefault("bytes_downloaded", 0)
            data.setdefault("pulls", [])
            return data
        except Exception:
            pass
    return {
        "month": month_key,
        "cap_bytes": MONTHLY_CAP_BYTES,
        "bytes_downloaded": 0,
        "pulls": [],
    }


def bytes_remaining(month_key: str | None = None) -> int:
    usage = get_month_usage(month_key)
    return max(0, int(usage["cap_bytes"]) - int(usage["bytes_downloaded"]))


def preflight_check(estimated_bytes: int, month_key: str | None = None) -> tuple[bool, str]:
    """Return (allowed, message). Blocks when download would exceed cap."""
    usage = get_month_usage(month_key)
    downloaded = int(usage["bytes_downloaded"])
    cap = int(usage["cap_bytes"])
    if estimated_bytes <= 0:
        return True, "Kein S3-Download noetig (Cache)."
    if downloaded + estimated_bytes > cap:
        remain_gb = (cap - downloaded) / (1024**3)
        need_gb = estimated_bytes / (1024**3)
        return (
            False,
            f"S3-Limit erreicht: noch {remain_gb:.2f} GB frei, Pull braucht ~{need_gb:.2f} GB.",
        )
    return True, f"OK (~{estimated_bytes / (1024**2):.1f} MB)"


def record_download(
    byte_count: int,
    meta: dict,
    *,
    month_key: str | None = None,
) -> dict:
    """Add bytes to the monthly counter after a successful S3 object download."""
    usage = get_month_usage(month_key)
    usage["bytes_downloaded"] = int(usage["bytes_downloaded"]) + int(byte_count)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **meta,
        "bytes": int(byte_count),
    }
    usage.setdefault("pulls", []).append(entry)
    _atomic_write_json(_usage_path(usage["month"]), usage)
    return usage


def record_pull_summary(
    total_bytes: int,
    meta: dict,
    *,
    month_key: str | None = None,
) -> None:
    """Record aggregate pull metadata without double-counting per-object bytes."""
    usage = get_month_usage(month_key)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **meta,
        "bytes": int(total_bytes),
        "summary": True,
    }
    usage.setdefault("pulls", []).append(entry)
    _atomic_write_json(_usage_path(usage["month"]), usage)


def format_usage_display(month_key: str | None = None) -> tuple[str, str]:
    """Return (text, color_token) for dashboard quota line."""
    usage = get_month_usage(month_key)
    downloaded = int(usage["bytes_downloaded"])
    cap = int(usage["cap_bytes"])
    dl_gb = downloaded / (1024**3)
    cap_gb = cap / (1024**3)
    text = f"S3 diesen Monat: {dl_gb:.2f} GB / {cap_gb:.0f} GB"
    if downloaded >= WARN_BYTES:
        return text + " (Limit nahe!)", "warn"
    return text, "ok"
