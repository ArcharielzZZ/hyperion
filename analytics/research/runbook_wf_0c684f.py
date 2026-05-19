"""One-off: walk-forward + PnL distribution for 0x0c684f wallet (research runbook).

Run from ``hyperion/python`` with ``src`` on PYTHONPATH::

    cd hyperion/python
    $env:PYTHONPATH = (Join-Path $pwd 'src')
    python ../analytics/research/runbook_wf_0c684f.py

Uses a local SQL fetch to avoid ``postgres_fills`` → ``ingestion`` circular imports
when executing as a standalone script.
"""

from __future__ import annotations

import asyncio
import math
import statistics
from collections import Counter
from datetime import timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from hyperion_pipeline.analytics.paper_replay.aggregate_metrics import (
    _annualized_daily_deployment_sharpe,
    _calendar_days_span_seconds,
    slipped_closed_legs,
)
from hyperion_pipeline.analytics.paper_replay.slippage import LinearBpsSlippage
from hyperion_pipeline.analytics.walk_forward_replay import walk_forward_trade_metrics
from hyperion_pipeline.config.settings import get_settings
from hyperion_pipeline.models.pydantic_domain import TradeFill

WALLET = "0x0c684f333a7e120bce61383da670bbb0157e82d0"
SLIPPAGE_BPS = 7.0

_FETCH = text(
    """
    SELECT f.coin, f.side, f.size, f.leverage, f.price, f.timestamp, f.event_key,
           f.fill_dir, f.closed_pnl_usd, f.fee_usd
    FROM fills f
    INNER JOIN traders t ON t.id = f.trader_id
    WHERE lower(t.wallet) = lower(:wallet)
      AND f.timestamp >= COALESCE(CAST(:ts_start AS TIMESTAMP WITH TIME ZONE), '-infinity'::timestamptz)
      AND f.timestamp <= COALESCE(CAST(:ts_end AS TIMESTAMP WITH TIME ZONE), 'infinity'::timestamptz)
    ORDER BY f.timestamp ASC, f.event_key ASC
    """
)


def _norm_side(raw: str) -> str:
    s = raw.strip().upper()
    if s == "B":
        return "buy"
    if s == "A":
        return "sell"
    return raw.lower()


def _utc_date(ts):
    t = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    return t.date()


async def load_fills(wallet: str) -> list[TradeFill]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    w = wallet.lower()
    out: list[TradeFill] = []
    try:
        async with engine.connect() as conn:
            result = await conn.stream(_FETCH, {"wallet": w, "ts_start": None, "ts_end": None})
            async for row in result.mappings():
                ts = row["timestamp"]
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                out.append(
                    TradeFill(
                        wallet=w,
                        coin=str(row["coin"]),
                        side=_norm_side(str(row["side"])),
                        size=float(row["size"]),
                        leverage=float(row["leverage"] or 0.0),
                        price=float(row["price"]),
                        event_timestamp=ts,
                        event_key=str(row["event_key"]),
                        source="runbook_sql",
                        fill_dir=str(row["fill_dir"]) if row.get("fill_dir") is not None else None,
                        closed_pnl_usd=float(row["closed_pnl_usd"])
                        if row.get("closed_pnl_usd") is not None
                        else None,
                        fee_usd=float(row["fee_usd"]) if row.get("fee_usd") is not None else None,
                        raw={},
                    )
                )
    finally:
        await engine.dispose()
    return out


async def main() -> None:
    slip = LinearBpsSlippage(SLIPPAGE_BPS)
    fills = await load_fills(WALLET)

    slips = slipped_closed_legs(fills=fills, slippage_model=slip, realized_only=True)
    nets = [s.net_pnl_usd for s in slips]
    rets = [s.ret_vs_notional for s in slips if not math.isnan(s.ret_vs_notional)]

    win_days = _calendar_days_span_seconds(fills) if fills else 1.0
    sharpe_code = _annualized_daily_deployment_sharpe(slips, window_days_calendar=win_days)

    buckets = {"< -500": 0, "-500 to -100": 0, "-100 to $0": 0, "$0 to $100": 0, "$100 to $500": 0, "> $500": 0}
    for x in nets:
        if x < -500:
            buckets["< -500"] += 1
        elif x < -100:
            buckets["-500 to -100"] += 1
        elif x < 0:
            buckets["-100 to $0"] += 1
        elif x < 100:
            buckets["$0 to $100"] += 1
        elif x <= 500:
            buckets["$100 to $500"] += 1
        else:
            buckets["> $500"] += 1

    mu = statistics.mean(nets) if nets else math.nan
    sd = statistics.pstdev(nets) if len(nets) > 1 else math.nan

    day_counts = Counter(_utc_date(s.exit_ts) for s in slips)
    tpd_vals = sorted(day_counts.values())
    n_days = len(tpd_vals)
    tpd_mu = statistics.mean(tpd_vals) if tpd_vals else 0.0
    tpd_sigma = statistics.pstdev(tpd_vals) if len(tpd_vals) > 1 else 0.0
    max_day = max(tpd_vals) if tpd_vals else 0

    df = pd.DataFrame(
        [
            {
                "coin": f.coin,
                "side": f.side,
                "size": f.size,
                "leverage": f.leverage,
                "price": f.price,
                "event_timestamp": f.event_timestamp,
                "event_key": f.event_key,
                "fill_dir": f.fill_dir,
                "closed_pnl_usd": f.closed_pnl_usd,
                "fee_usd": f.fee_usd,
            }
            for f in fills
        ]
    )
    out_parquet = Path(__file__).resolve().parent / "wf_0c684f_pg_fills.parquet"
    df.to_parquet(out_parquet, index=False)

    wf = walk_forward_trade_metrics(df, WALLET, slippage_model=slip, realized_only=True, train_frac=0.67)

    print("=== fills rows ===", len(fills))
    print("=== closed slipped legs ===", len(slips))
    print("=== window_days (calendar span) ===", round(win_days, 4))
    print("=== per-trade USD PnL histogram (slipped nets) ===")
    for k, v in buckets.items():
        print(f"  {k}: {v}")
    print("mean_net_usd", mu, "stdev_net_usd", sd)
    print(
        "trades_exit_per_active_day:",
        "n_calendar_days_with_exits=",
        n_days,
        "mean_trips_per_day=",
        round(tpd_mu, 3),
        "stdev_trips_per_day=",
        round(tpd_sigma, 3),
        "max_single_day_closed_trips=",
        max_day,
        "burst_ratio_max_over_mean=",
        round(max_day / tpd_mu, 3) if tpd_mu > 1e-9 else None,
    )
    mean_r = statistics.mean(rets) if len(rets) >= 1 else math.nan
    std_r = statistics.pstdev(rets) if len(rets) > 1 else math.nan
    n_trades = len(rets)
    trades_per_year = n_trades * (365.0 / max(1.0 / 24.0, win_days)) if n_trades else math.nan
    print(
        "per_trip_ret_vs_notional mean=",
        mean_r,
        "pstdev=",
        std_r,
        "n_trades_used=",
        n_trades,
        "trades_per_year_extrapolated=",
        round(trades_per_year, 2),
        "sharpe_ann_current=",
        sharpe_code,
    )
    print("parquet ->", out_parquet)
    tr = wf["train_metrics"]
    te = wf["test_metrics"]
    print("=== walk_forward ===")
    print(
        "train closed=",
        tr.closed_trips_n,
        "win_rate=",
        tr.win_rate,
        "realized_usd=",
        tr.realized_closed_pnl_usd,
        "sharpe_ann=",
        tr.sharpe_annual_trade_returns,
    )
    print(
        "test closed=",
        te.closed_trips_n,
        "win_rate=",
        te.win_rate,
        "realized_usd=",
        te.realized_closed_pnl_usd,
        "sharpe_ann=",
        te.sharpe_annual_trade_returns,
    )
    print("stable_flag", wf["stable_flag"], "train_days", wf["train_days"], "test_days", wf["test_days"])


if __name__ == "__main__":
    asyncio.run(main())
