"""Hyperliquid S3 L2 order book fetch, cache, and fill-aligned snapshots."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import polars as pl

from analytics.lib.coin_paths import market_orderbook_snapshots_path, sanitize_coin_for_filename
from analytics.lib.liquidity_baseline import ensure_spread_bps
from analytics.lib.parquet_io import atomic_write_parquet
from analytics.lib.s3_quota import MONTHLY_CAP_BYTES, preflight_check, record_download
from analytics.lib.schemas import ORDERBOOK_SNAPSHOT_SCHEMA
from analytics.lib.spot_meta import is_spot_coin, resolve_coin_display

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_LAKE = REPO_ROOT / "analytics" / "data_lake"
S3_CACHE_DIR = DATA_LAKE / "s3_cache"
WALLETS_DIR = DATA_LAKE / "wallets"

S3_BUCKET = "hyperliquid-archive"
S3_PREFIX = "market_data"
MAX_HOURS_PER_PULL = 500
ORDERBOOK_LEVELS_STORE = 20

HOUR_METRICS_SCHEMA: dict[str, pl.DataType] = {
    "snapshot_timestamp": pl.Datetime("ms"),
    "best_bid_px": pl.Float64,
    "best_bid_sz": pl.Float64,
    "best_ask_px": pl.Float64,
    "best_ask_sz": pl.Float64,
    "spread": pl.Float64,
    "spread_bps": pl.Float64,
    "mid_px": pl.Float64,
    "bid_depth_top5": pl.Float64,
    "ask_depth_top5": pl.Float64,
    "bid_levels_json": pl.String,
    "ask_levels_json": pl.String,
}


@dataclass(frozen=True)
class HourKey:
    date_str: str  # YYYYMMDD UTC
    hour: int  # 0-23

    def s3_key(self, coin: str) -> str:
        return f"{S3_PREFIX}/{self.date_str}/{self.hour}/l2Book/{coin}.lz4"

    def cache_dir(self, coin: str) -> Path:
        return S3_CACHE_DIR / sanitize_coin_for_filename(coin) / self.date_str

    def done_marker(self, coin: str) -> Path:
        return self.cache_dir(coin) / f"{self.hour}.done"

    def cache_parquet(self, coin: str) -> Path:
        return self.cache_dir(coin) / f"{self.hour}_metrics.parquet"


def hour_keys_from_timestamps_ms(timestamps_ms: list[int]) -> list[HourKey]:
    """Unique UTC hour buckets covering the given millisecond timestamps."""
    seen: set[tuple[str, int]] = set()
    keys: list[HourKey] = []
    for ts in timestamps_ms:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        pair = (dt.strftime("%Y%m%d"), dt.hour)
        if pair in seen:
            continue
        seen.add(pair)
        keys.append(HourKey(date_str=pair[0], hour=pair[1]))
    keys.sort(key=lambda k: (k.date_str, k.hour))
    return keys


def hour_keys_from_fills(fills: pl.DataFrame, coin: str) -> list[HourKey]:
    subset = fills.filter(pl.col("coin") == coin)
    if subset.is_empty():
        return []
    ms = subset["timestamp"].dt.epoch("ms").to_list()
    return hour_keys_from_timestamps_ms(ms)


def _parse_snapshot_payload(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            try:
                raw = ast.literal_eval(raw)
            except (SyntaxError, ValueError):
                return None
    if not isinstance(raw, dict):
        return None
    if raw.get("channel") != "l2Book":
        data = raw.get("data")
        if isinstance(data, dict) and "levels" in data:
            pass
        else:
            return None
    data = raw.get("data") or raw
    if not isinstance(data, dict):
        return None
    levels = data.get("levels")
    if not isinstance(levels, list) or len(levels) < 2:
        return None
    return data


def _levels_to_json(levels: list[dict], limit: int) -> str:
    compact = [
        {
            "px": float(level["px"]),
            "sz": float(level["sz"]),
            "n": int(level.get("n") or 0),
        }
        for level in levels[:limit]
    ]
    return json.dumps(compact, separators=(",", ":"))


def metrics_from_levels(data: dict[str, Any]) -> dict[str, Any] | None:
    levels = data.get("levels") or []
    bids = levels[0] if len(levels) > 0 else []
    asks = levels[1] if len(levels) > 1 else []
    if not bids or not asks:
        return None

    def _px_sz(level: dict) -> tuple[float, float]:
        return float(level["px"]), float(level["sz"])

    best_bid_px, best_bid_sz = _px_sz(bids[0])
    best_ask_px, best_ask_sz = _px_sz(asks[0])
    spread = best_ask_px - best_bid_px
    mid_px = (best_bid_px + best_ask_px) / 2.0
    spread_bps = (spread / mid_px * 10_000.0) if mid_px > 0 else None
    bid_depth_top5 = sum(float(l["sz"]) for l in bids[:5])
    ask_depth_top5 = sum(float(l["sz"]) for l in asks[:5])
    ts_ms = int(data.get("time") or 0)
    if ts_ms <= 0:
        return None
    return {
        "snapshot_timestamp": ts_ms,
        "best_bid_px": best_bid_px,
        "best_bid_sz": best_bid_sz,
        "best_ask_px": best_ask_px,
        "best_ask_sz": best_ask_sz,
        "spread": spread,
        "spread_bps": spread_bps,
        "mid_px": mid_px,
        "bid_depth_top5": bid_depth_top5,
        "ask_depth_top5": ask_depth_top5,
        "bid_levels_json": _levels_to_json(bids, ORDERBOOK_LEVELS_STORE),
        "ask_levels_json": _levels_to_json(asks, ORDERBOOK_LEVELS_STORE),
    }


def iter_l2_records(decompressed: bytes) -> Iterator[dict[str, Any]]:
    """Yield parsed l2Book records from decompressed archive bytes."""
    text = decompressed.decode("utf-8", errors="replace").strip()
    if not text:
        return

    if text.startswith("["):
        try:
            arr = json.loads(text)
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, dict) and "raw" in item:
                        parsed = _parse_snapshot_payload(item["raw"])
                        if parsed:
                            yield parsed
                    else:
                        parsed = _parse_snapshot_payload(item)
                        if parsed:
                            yield parsed
                return
        except json.JSONDecodeError:
            pass

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and "raw" in item:
            parsed = _parse_snapshot_payload(item["raw"])
        else:
            parsed = _parse_snapshot_payload(item)
        if parsed:
            yield parsed


def parse_lz4_metrics(path: Path) -> pl.DataFrame:
    import lz4.frame

    raw = path.read_bytes()
    decompressed = lz4.frame.decompress(raw)
    rows: list[dict[str, Any]] = []
    for data in iter_l2_records(decompressed):
        metrics = metrics_from_levels(data)
        if metrics:
            rows.append(metrics)
    if not rows:
        return pl.DataFrame(schema=HOUR_METRICS_SCHEMA)
    return normalize_metrics_timestamps(
        pl.DataFrame(rows).with_columns(
            pl.from_epoch("snapshot_timestamp", time_unit="ms").alias("snapshot_timestamp")
        )
    ).sort("snapshot_timestamp")


def normalize_metrics_timestamps(df: pl.DataFrame) -> pl.DataFrame:
    """Align snapshot timestamps with fill parquet: naive Datetime(ms)."""
    if df.is_empty():
        return df
    for col in ("bid_levels_json", "ask_levels_json"):
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).cast(pl.String).alias(col))
    if "snapshot_timestamp" not in df.columns:
        return df
    dtype = df.schema["snapshot_timestamp"]
    if isinstance(dtype, pl.Datetime) and dtype.time_zone:
        return df.with_columns(
            pl.col("snapshot_timestamp")
            .dt.replace_time_zone(None)
            .cast(pl.Datetime("ms"))
        )
    return df.with_columns(pl.col("snapshot_timestamp").cast(pl.Datetime("ms")))


def _get_s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 fehlt. Installiere mit: pip install boto3"
        ) from exc
    return boto3.client("s3")


def assert_s3_orderbook_coin_supported(coin: str) -> None:
    """Raise with a clear message when S3 l2Book cannot serve this coin symbol."""
    if is_spot_coin(coin):
        display = resolve_coin_display(coin)
        raise RuntimeError(
            f"S3 Order Book nicht verfuegbar fuer Spot-Coin {coin} ({display}). "
            "Das Archiv enthaelt nur Perpetual-Markets (z.B. BTC, ETH, ZRO). "
            "Bitte einen Perp-Coin im Dropdown waehlen."
        )
    if ":" in coin:
        raise RuntimeError(
            f"S3 Order Book nicht verfuegbar fuer HIP-3-Coin {coin!r}. "
            "Im Archiv sind nur Standard-Perps (z.B. BTC, ZEC, ZRO) enthalten."
        )


def head_object_size(s3_client, key: str) -> int | None:
    try:
        resp = s3_client.head_object(
            Bucket=S3_BUCKET,
            Key=key,
            RequestPayer="requester",
        )
        return int(resp.get("ContentLength") or 0)
    except Exception as exc:
        code = ""
        if getattr(exc, "response", None):
            code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise RuntimeError(f"S3-Fehler bei {key}: {code or exc}") from exc


@dataclass
class HourDownloadPlan:
    """Which UTC hours can be loaded from S3 vs skipped."""

    available: list[tuple[HourKey, int]]  # (hour, size_bytes)
    missing: list[HourKey]
    cached: list[HourKey]

    @property
    def fetchable(self) -> list[HourKey]:
        return [h for h, _ in self.available] + self.cached

    def estimated_bytes(self) -> int:
        return sum(size for _, size in self.available)


def plan_hour_downloads(
    s3_client,
    coin: str,
    hours: list[HourKey],
) -> HourDownloadPlan:
    available: list[tuple[HourKey, int]] = []
    missing: list[HourKey] = []
    cached: list[HourKey] = []
    for hour in hours:
        if hour.done_marker(coin).exists() and hour.cache_parquet(coin).exists():
            cached.append(hour)
            continue
        size = head_object_size(s3_client, hour.s3_key(coin))
        if size is None:
            missing.append(hour)
        else:
            available.append((hour, size))
    return HourDownloadPlan(available=available, missing=missing, cached=cached)


def estimate_download_bytes(s3_client, keys: list[str]) -> int:
    """Sum ContentLength for keys that exist; ignore missing (legacy helper)."""
    total = 0
    for key in keys:
        size = head_object_size(s3_client, key)
        if size is not None:
            total += size
    return total


def format_missing_hours(missing: list[HourKey], limit: int = 3) -> str:
    if not missing:
        return ""
    parts = [f"{h.date_str} {h.hour}:00" for h in missing[:limit]]
    suffix = f" (+{len(missing) - limit} weitere)" if len(missing) > limit else ""
    return ", ".join(parts) + suffix


def download_s3_object(s3_client, key: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    s3_client.download_file(
        S3_BUCKET,
        key,
        str(dest),
        ExtraArgs={"RequestPayer": "requester"},
    )
    size = dest.stat().st_size
    record_download(
        size,
        {"bucket": S3_BUCKET, "key": key},
    )
    return size


def load_or_fetch_hour_metrics(
    s3_client,
    coin: str,
    hour: HourKey,
    *,
    tmp_dir: Path,
) -> tuple[pl.DataFrame, int]:
    """Return hour metrics parquet and bytes downloaded (0 if cached)."""
    if hour.done_marker(coin).exists() and hour.cache_parquet(coin).exists():
        return normalize_metrics_timestamps(pl.read_parquet(hour.cache_parquet(coin))), 0

    key = hour.s3_key(coin)
    if head_object_size(s3_client, key) is None:
        return pl.DataFrame(schema=HOUR_METRICS_SCHEMA), 0

    lz4_path = tmp_dir / f"{hour.date_str}_{hour.hour}.lz4"
    downloaded = download_s3_object(s3_client, key, lz4_path)
    try:
        metrics = parse_lz4_metrics(lz4_path)
    finally:
        if lz4_path.exists():
            lz4_path.unlink()

    if metrics.is_empty():
        raise RuntimeError(f"Keine L2-Daten in {key}")

    hour.cache_dir(coin).mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(metrics, hour.cache_parquet(coin), compression="zstd")
    hour.done_marker(coin).write_text("ok\n", encoding="utf-8")
    return metrics, downloaded


def snapshots_for_fills(
    fills: pl.DataFrame,
    coin: str,
    hour_metrics_by_key: dict[tuple[str, int], pl.DataFrame],
) -> pl.DataFrame:
    subset = fills.filter(pl.col("coin") == coin).sort("timestamp")
    if subset.is_empty():
        return pl.DataFrame(schema=ORDERBOOK_SNAPSHOT_SCHEMA)

    parts: list[pl.DataFrame] = []
    for hour_key, metrics in hour_metrics_by_key.items():
        date_str, hour = hour_key
        if metrics.is_empty():
            continue
        metrics = normalize_metrics_timestamps(metrics)
        in_hour = subset.filter(
            (pl.col("timestamp").dt.strftime("%Y%m%d") == date_str)
            & (pl.col("timestamp").dt.hour() == hour)
        )
        if in_hour.is_empty():
            continue
        fills_for_join = in_hour.select(
            [
                pl.col("timestamp").alias("fill_timestamp"),
                pl.col("hash").alias("fill_hash"),
                pl.lit(coin).alias("coin"),
            ]
        ).with_columns(pl.col("fill_timestamp").cast(pl.Datetime("ms")))
        joined = fills_for_join.sort("fill_timestamp").join_asof(
            metrics.sort("snapshot_timestamp"),
            left_on="fill_timestamp",
            right_on="snapshot_timestamp",
            strategy="backward",
        )
        parts.append(joined)

    if not parts:
        return pl.DataFrame(schema=ORDERBOOK_SNAPSHOT_SCHEMA)

    result = pl.concat(parts, how="diagonal_relaxed")
    result = ensure_spread_bps(result)
    return (
        result.filter(pl.col("snapshot_timestamp").is_not_null())
        .sort("fill_timestamp")
        .unique(subset=["fill_hash"], keep="last")
        .select(list(ORDERBOOK_SNAPSHOT_SCHEMA.keys()))
    )


def orderbook_snapshots_path(wallet: str, coin: str) -> Path:
    return market_orderbook_snapshots_path(WALLETS_DIR / wallet.lower() / "market", coin)


def merge_snapshots(existing: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame:
    if existing.is_empty():
        return new
    if new.is_empty():
        return existing
    combined = pl.concat([existing, new], how="diagonal_relaxed")
    return combined.sort("fill_timestamp").unique(subset=["fill_hash"], keep="last")


def pull_orderbook_for_wallet_coin(
    wallet: str,
    coin: str,
    *,
    status_callback=None,
) -> pl.DataFrame:
    """Download minimal S3 L2 data and attach one snapshot per fill."""
    wallet_l = wallet.lower()
    fills_path = WALLETS_DIR / wallet_l / "fills.parquet"
    if not fills_path.exists():
        raise RuntimeError("Wallet-Bundle fehlt. Bitte zuerst Wallet Force Pull.")

    fills = pl.read_parquet(fills_path)
    hours = hour_keys_from_fills(fills, coin)
    if not hours:
        raise RuntimeError(f"Keine Fills fuer {coin}.")

    if len(hours) > MAX_HOURS_PER_PULL:
        raise RuntimeError(
            f"Zu viele Stunden ({len(hours)} > {MAX_HOURS_PER_PULL}). "
            "Wallet-Zeitraum zu gross fuer einen Order-Book-Pull."
        )

    assert_s3_orderbook_coin_supported(coin)

    s3_client = _get_s3_client()
    plan = plan_hour_downloads(s3_client, coin, hours)

    if not plan.fetchable:
        raise RuntimeError(
            f"Keine S3 Order-Book-Daten fuer {coin} in den {len(hours)} Trade-Stunden. "
            f"Fehlende Stunden (Beispiele): {format_missing_hours(plan.missing)}. "
            "Das Archiv ist lueckenhaft und oft mehrere Wochen hinter dem aktuellen Datum."
        )

    estimated = plan.estimated_bytes()
    allowed, msg = preflight_check(estimated)
    if not allowed:
        raise RuntimeError(msg)

    skip_note = ""
    if plan.missing:
        skip_note = (
            f" | {len(plan.missing)} Stunden ohne S3-Daten uebersprungen "
            f"({format_missing_hours(plan.missing)})"
        )

    if status_callback:
        status_callback(
            state="running",
            message=(
                f"Preflight OK ({msg}). {len(plan.available)} Stunden von S3, "
                f"{len(plan.cached)} aus Cache.{skip_note}"
            ),
            hours_total=len(plan.fetchable),
            hours_from_s3=len(plan.available),
            hours_skipped=len(plan.missing),
            estimated_bytes=estimated,
        )

    tmp_dir = WALLETS_DIR / wallet_l / ".orderbook_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    hour_metrics: dict[tuple[str, int], pl.DataFrame] = {}
    bytes_downloaded = 0

    for idx, hour in enumerate(plan.fetchable, start=1):
        if status_callback:
            status_callback(
                state="running",
                message=f"Stunde {idx}/{len(plan.fetchable)}: {hour.date_str} {hour.hour}:00 UTC{skip_note}",
                hour_index=idx,
                hours_total=len(plan.fetchable),
                hours_skipped=len(plan.missing),
            )
        metrics, dl = load_or_fetch_hour_metrics(
            s3_client, coin, hour, tmp_dir=tmp_dir
        )
        if metrics.is_empty():
            continue
        bytes_downloaded += dl
        hour_metrics[(hour.date_str, hour.hour)] = metrics

        from analytics.lib.s3_quota import get_month_usage

        if int(get_month_usage()["bytes_downloaded"]) > MONTHLY_CAP_BYTES:
            raise RuntimeError("S3-Monatslimit ueberschritten waehrend des Pulls.")

    snapshots = snapshots_for_fills(fills, coin, hour_metrics)
    if snapshots.is_empty() and plan.missing:
        raise RuntimeError(
            f"Keine Snapshots erzeugt — alle {len(plan.missing)} Trade-Stunden fehlen im S3-Archiv."
        )

    out_path = orderbook_snapshots_path(wallet_l, coin)
    existing = pl.DataFrame(schema=ORDERBOOK_SNAPSHOT_SCHEMA)
    if out_path.exists():
        existing = pl.read_parquet(out_path)
    merged = merge_snapshots(existing, snapshots)
    atomic_write_parquet(merged, out_path, compression="zstd")

    if tmp_dir.exists():
        for p in tmp_dir.iterdir():
            p.unlink(missing_ok=True)
        tmp_dir.rmdir()

    if status_callback:
        done_msg = (
            f"Fertig: {len(merged)} Snapshots, {bytes_downloaded / (1024**2):.1f} MB von S3."
        )
        if plan.missing:
            done_msg += (
                f" {len(plan.missing)} Stunden ohne Archiv-Daten uebersprungen."
            )
        status_callback(
            state="done",
            message=done_msg,
            snapshot_count=len(merged),
            bytes_downloaded=bytes_downloaded,
            hours_skipped=len(plan.missing),
        )
    return merged

