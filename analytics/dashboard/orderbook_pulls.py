"""Order book snapshot loaders for Wallet Explorer."""



from __future__ import annotations



import functools

import json

from pathlib import Path



import polars as pl



from analytics.lib.coin_paths import market_orderbook_snapshots_path

from analytics.lib.liquidity_baseline import attach_z_spread, ensure_baseline, ensure_spread_bps

from analytics.lib.schemas import ORDERBOOK_SNAPSHOT_SCHEMA



REPO_ROOT = Path(__file__).resolve().parents[2]

WALLETS_DIR = REPO_ROOT / "analytics" / "data_lake" / "wallets"

LEVELS_HOVER_SHOW = 10





def _ensure_orderbook_columns(df: pl.DataFrame) -> pl.DataFrame:

    if df.is_empty():

        return pl.DataFrame(schema=ORDERBOOK_SNAPSHOT_SCHEMA)

    for col, dtype in ORDERBOOK_SNAPSHOT_SCHEMA.items():

        if col not in df.columns:

            df = df.with_columns(pl.lit(None).cast(dtype).alias(col))

    df = df.select(list(ORDERBOOK_SNAPSHOT_SCHEMA.keys()))

    return ensure_spread_bps(df)





@functools.lru_cache(maxsize=32)

def load_orderbook_snapshots(wallet: str, coin: str) -> pl.DataFrame:

    path = market_orderbook_snapshots_path(WALLETS_DIR / wallet.lower() / "market", coin)

    if not path.exists():

        return pl.DataFrame(schema=ORDERBOOK_SNAPSHOT_SCHEMA)

    return _ensure_orderbook_columns(pl.read_parquet(path)).sort("fill_timestamp")





def load_orderbook_status(wallet: str) -> dict:

    path = WALLETS_DIR / wallet.lower() / ".orderbook_pull_status.json"

    if not path.exists():

        return {}

    try:

        return json.loads(path.read_text(encoding="utf-8"))

    except Exception:

        return {}





def clear_orderbook_cache() -> None:

    load_orderbook_snapshots.cache_clear()





def format_levels_hover_html(

    bid_levels_json: str | None,

    ask_levels_json: str | None,

    *,

    show: int = LEVELS_HOVER_SHOW,

) -> str:

    """Compact HTML for Plotly hover: top N bid/ask levels."""

    lines: list[str] = []



    def _append_side(title: str, raw: str | None) -> None:

        if not raw:

            return

        try:

            levels = json.loads(raw)

        except json.JSONDecodeError:

            return

        if not levels:

            return

        lines.append(f"<b>{title}</b>")

        for level in levels[:show]:

            px = level.get("px")

            sz = level.get("sz")

            n = level.get("n")

            if px is None or sz is None:

                continue

            n_part = f" ({n} ord)" if n else ""

            lines.append(f"  {float(px):,.4f} × {float(sz):,.4f}{n_part}")



    _append_side("Bids", bid_levels_json)

    _append_side("Asks", ask_levels_json)

    return "<br>".join(lines)





def _format_spread_line(row: dict) -> str:

    usd = row.get("ob_spread_mean")

    bps = row.get("ob_spread_bps_mean")

    z = row.get("ob_z_spread_mean")

    parts: list[str] = []

    if usd is not None:

        parts.append(f"{usd:,.4f} USD")

    if bps is not None:

        parts.append(f"{bps:,.2f} bps")

    if z is not None:

        parts.append(f"Z: {z:+.2f}σ")

    if not parts:

        return "Spread: —"

    line = "Spread: " + " | ".join(parts)

    if z is None and usd is not None:

        line += "<br><i>Z-Spread: Baseline duenn (zu wenig s3_cache-Stunden)</i>"

    return line





def format_orderbook_hover_row(row: dict) -> str:

    """Full order-book hover block for one trade bucket."""

    parts = [

        _format_spread_line(row),

        f"Kauf-Vol Top5: {row.get('ob_bid5_mean'):,.0f} | Verkauf-Vol Top5: {row.get('ob_ask5_mean'):,.0f}",

    ]

    levels = format_levels_hover_html(

        row.get("ob_bid_levels_json"),

        row.get("ob_ask_levels_json"),

    )

    if levels:

        parts.append(levels)

    return "<br>".join(parts)





def join_orderbook_to_buckets(

    buckets: pl.DataFrame,

    fills: pl.DataFrame,

    orderbook: pl.DataFrame,

    coin: str,

    interval: str,

    *,

    baseline: pl.DataFrame | None = None,

) -> pl.DataFrame:

    """Attach spread/depth, z_spread, and top levels per candle bucket for chart hover."""

    if buckets.is_empty() or orderbook.is_empty():

        return buckets



    from wallet_layout import POLARS_EVERY



    every = POLARS_EVERY.get(interval, "4h")

    fills_coin = fills.filter(pl.col("coin") == coin)

    if fills_coin.is_empty():

        return buckets



    orderbook = ensure_spread_bps(orderbook)

    if baseline is None:

        baseline = ensure_baseline()



    ob_cols = [

        "fill_hash",

        "spread",

        "spread_bps",

        "mid_px",

        "bid_depth_top5",

        "ask_depth_top5",

        "bid_levels_json",

        "ask_levels_json",

    ]

    ob_select = [c for c in ob_cols if c in orderbook.columns]



    merged = (

        fills_coin.join(

            orderbook.select(ob_select),

            left_on="hash",

            right_on="fill_hash",

            how="left",

        )

        .filter(pl.col("spread").is_not_null())

        .sort("timestamp")

    )



    if merged.is_empty():

        return buckets



    merged = attach_z_spread(merged, coin=coin, baseline=baseline)



    ob_buckets = merged.group_by_dynamic("timestamp", every=every).agg(

        [

            pl.col("spread").mean().alias("ob_spread_mean"),

            pl.col("spread_bps").mean().alias("ob_spread_bps_mean"),

            pl.col("z_spread").mean().alias("ob_z_spread_mean"),

            pl.col("bid_depth_top5").mean().alias("ob_bid5_mean"),

            pl.col("ask_depth_top5").mean().alias("ob_ask5_mean"),

            pl.col("bid_levels_json")

            .sort_by(pl.col("size"), descending=True)

            .first()

            .alias("ob_bid_levels_json"),

            pl.col("ask_levels_json")

            .sort_by(pl.col("size"), descending=True)

            .first()

            .alias("ob_ask_levels_json"),

        ]

    )

    if ob_buckets.is_empty():

        return buckets

    return buckets.sort("timestamp").join_asof(

        ob_buckets.sort("timestamp"),

        on="timestamp",

        strategy="backward",

    )


