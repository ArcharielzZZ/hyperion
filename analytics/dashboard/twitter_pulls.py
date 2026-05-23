"""
Twitter pull helpers used by the wallet_explorer dashboard.

Reads/writes the same JSON/Parquet artefacts produced by
``analytics/scripts/force_pull_twitter.py``.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[2]
TWITTER_DIR = REPO_ROOT / "analytics" / "data_lake" / "twitter"
HISTORY_PATH = TWITTER_DIR / "history.json"
STATUS_PATH = TWITTER_DIR / ".pull_status.json"

POLARS_EVERY = {
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
    "1M": "1mo",
}


# --------------------------------------------------------------------------- #
# History I/O
# --------------------------------------------------------------------------- #

def _atomic_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        for h in data:
            h.setdefault("visible", True)
        return data
    except Exception:
        return []


def save_history(history: list[dict]) -> None:
    _atomic_write(HISTORY_PATH, history)


def load_status() -> dict:
    if not STATUS_PATH.exists():
        return {}
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_visibility(handle: str, frm: str, to: str, visible: bool) -> list[dict]:
    history = load_history()
    for h in history:
        if h.get("handle") == handle and h.get("from") == frm and h.get("to") == to:
            h["visible"] = bool(visible)
    save_history(history)
    return history


def delete_pull(handle: str, frm: str, to: str) -> list[dict]:
    """Remove the history row AND the parquet file on disk."""
    history = load_history()
    keep: list[dict] = []
    for h in history:
        if h.get("handle") == handle and h.get("from") == frm and h.get("to") == to:
            parquet_rel = h.get("parquet", "")
            if parquet_rel:
                p = (REPO_ROOT / parquet_rel).resolve()
                try:
                    if p.exists() and p.is_file():
                        p.unlink()
                except Exception:
                    pass
            media_dir = TWITTER_DIR / "media" / f"{handle.lower()}__{frm}__{to}"
            if media_dir.exists():
                try:
                    shutil.rmtree(media_dir, ignore_errors=True)
                except Exception:
                    pass
            continue
        keep.append(h)
    save_history(keep)
    return keep


# --------------------------------------------------------------------------- #
# Tweet loading + bucketing
# --------------------------------------------------------------------------- #

def load_pull_df(entry: dict) -> pl.DataFrame:
    rel = entry.get("parquet", "")
    if not rel:
        return _empty_tweets_df()
    p = (REPO_ROOT / rel).resolve()
    if not p.exists():
        return _empty_tweets_df()
    try:
        return pl.read_parquet(p).sort("created_at")
    except Exception:
        return _empty_tweets_df()


def _empty_tweets_df() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "id": pl.Utf8,
            "handle": pl.Utf8,
            "text": pl.Utf8,
            "url": pl.Utf8,
            "created_at": pl.Datetime("ms", time_zone="UTC"),
            "media_urls": pl.List(pl.Utf8),
        }
    )


def load_visible_tweets() -> pl.DataFrame:
    """Concatenate all parquet files whose history entry has visible=True."""
    frames: list[pl.DataFrame] = []
    for entry in load_history():
        if not entry.get("visible", True):
            continue
        df = load_pull_df(entry)
        if not df.is_empty():
            frames.append(df)
    if not frames:
        return _empty_tweets_df()
    out = pl.concat(frames, how="vertical_relaxed")
    out = out.unique(subset=["id"], keep="first").sort("created_at")
    return out


def aggregate_tweets_to_buckets(tweets: pl.DataFrame, interval: str) -> pl.DataFrame:
    """One row per candle bucket: count + list of tweet ids."""
    if tweets.is_empty():
        return pl.DataFrame(
            schema={
                "bucket": pl.Datetime("ms", time_zone="UTC"),
                "count": pl.UInt32,
                "ids": pl.List(pl.Utf8),
            }
        )

    every = POLARS_EVERY.get(interval, "1h")
    df = tweets.with_columns(pl.col("created_at").alias("bucket")).sort("bucket")
    return df.group_by_dynamic("bucket", every=every).agg(
        [
            pl.len().cast(pl.UInt32).alias("count"),
            pl.col("id").alias("ids"),
        ]
    )


def split_in_range_and_out_of_range(
    tweets: pl.DataFrame,
    t_min,
    t_max,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Return (in_range, before_t_min, after_t_max)."""
    if tweets.is_empty():
        return tweets, tweets, tweets

    def _as_utc(x):
        if isinstance(x, datetime):
            return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
        return x

    t_min = _as_utc(t_min)
    t_max = _as_utc(t_max)

    in_range = tweets.filter(
        (pl.col("created_at") >= t_min) & (pl.col("created_at") <= t_max)
    )
    before = tweets.filter(pl.col("created_at") < t_min)
    after = tweets.filter(pl.col("created_at") > t_max)
    return in_range, before, after


def tweets_for_ids(ids: list[str]) -> list[dict]:
    """Look up full tweet rows from every visible parquet for a list of ids."""
    if not ids:
        return []
    wanted = set(ids)
    found: dict[str, dict] = {}
    for entry in load_history():
        if not entry.get("visible", True):
            continue
        df = load_pull_df(entry)
        if df.is_empty():
            continue
        hits = df.filter(pl.col("id").is_in(list(wanted)))
        for row in hits.iter_rows(named=True):
            tid = row["id"]
            if tid not in found:
                created = row.get("created_at")
                if isinstance(created, datetime):
                    created_iso = created.astimezone(timezone.utc).isoformat()
                else:
                    created_iso = str(created) if created is not None else ""
                found[tid] = {
                    "id": tid,
                    "handle": row.get("handle", ""),
                    "text": row.get("text", ""),
                    "url": row.get("url", ""),
                    "created_at": created_iso,
                    "media_urls": list(row.get("media_urls") or []),
                }
    ordered = [found[i] for i in ids if i in found]
    ordered.sort(key=lambda r: r["created_at"])
    return ordered
