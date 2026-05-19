"""Walk-forward diagnostics for FIFO paper replay (pandas friendly)."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import structlog

from hyperion_pipeline.analytics.paper_replay.aggregate_metrics import summarize_wallet_round_trip_replay
from hyperion_pipeline.analytics.paper_replay.slippage import SlippageModel
from hyperion_pipeline.models.pydantic_domain import TradeFill

logger = structlog.get_logger(__name__)


REQUIRED_COLUMNS = frozenset(
    {
        "coin",
        "side",
        "size",
        "leverage",
        "price",
        "event_timestamp",
        "event_key",
        "fill_dir",
        "closed_pnl_usd",
        "fee_usd",
    }
)


def dataframe_to_trade_fills(df: pd.DataFrame, wallet: str) -> list[TradeFill]:
    """
    Purpose: convert analytic fill frames back into canonical ``TradeFill`` records.

    Inputs: ``pandas.DataFrame`` with Step 4 column contract (see ``REQUIRED_COLUMNS``).
    Outputs: sorted list acceptable to FIFO constructors.

    Gotcha: ``event_timestamp`` may arrive as ``Timestamp`` — converted to Python ``datetime``.
    """

    missing = REQUIRED_COLUMNS.difference(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")

    wl = wallet.lower()
    fills: list[TradeFill] = []
    ordered = df.sort_values(["event_timestamp", "event_key"])
    for rd in ordered.to_dict("records"):
        ts = rd["event_timestamp"]
        if isinstance(ts, pd.Timestamp):
            ts = ts.to_pydatetime()

        fd_raw = rd.get("fill_dir")
        fill_dir = None if pd.isna(fd_raw) or fd_raw is None else str(fd_raw)

        cp_raw = rd.get("closed_pnl_usd")
        closed_pnl = None if pd.isna(cp_raw) or cp_raw is None else float(cp_raw)

        fee_raw = rd.get("fee_usd")
        fee_usd = None if pd.isna(fee_raw) or fee_raw is None else float(fee_raw)

        fills.append(
            TradeFill(
                wallet=wl,
                coin=str(rd["coin"]),
                side=str(rd["side"]).lower(),
                size=float(rd["size"]),
                leverage=float(rd.get("leverage") or 0.0),
                price=float(rd["price"]),
                event_timestamp=ts,
                event_key=str(rd["event_key"]),
                fill_dir=fill_dir,
                closed_pnl_usd=closed_pnl,
                fee_usd=fee_usd,
                raw={},
            )
        )
    return fills


def walk_forward_trade_metrics(
    df: pd.DataFrame,
    wallet: str,
    *,
    slippage_model: SlippageModel,
    realized_only: bool,
    train_frac: float = 0.67,
) -> dict[str, Any]:
    """
    Purpose: split calendar coverage 67%/33%, then rerun Step‑2 aggregates independently.

    Inputs: fills frame plus wallet id, replay knobs, train fraction clamped implicitly at ≥1 test day.
    Outputs: structured dict with summarized ``WalletPaperReplayRow`` objects embedded.

    Gotcha: walk windows use distinct **exchange-timestamp** calendar days, not ingestion ``created_at``.
    """

    if df.empty:
        raise ValueError("dataframe is empty")

    working = df.copy()
    ts_col = pd.to_datetime(working["event_timestamp"], utc=True)
    working["_day"] = ts_col.dt.normalize()

    days = sorted(working["_day"].dropna().unique().tolist())
    if len(days) < 4:
        raise ValueError("need at least 4 distinct calendar days for walk-forward slicing")

    split_idx = max(1, min(len(days) - 1, int(math.floor(train_frac * len(days)))))
    train_days = set(days[:split_idx])
    test_days = set(days[split_idx:])

    train_df = working[working["_day"].isin(train_days)].drop(columns=["_day"])
    test_df = working[working["_day"].isin(test_days)].drop(columns=["_day"])

    train_fills = dataframe_to_trade_fills(train_df, wallet)
    test_fills = dataframe_to_trade_fills(test_df, wallet)

    train_metrics = summarize_wallet_round_trip_replay(
        wallet=wallet.lower(),
        fills=train_fills,
        slippage_model=slippage_model,
        realized_only=realized_only,
    )
    test_metrics = summarize_wallet_round_trip_replay(
        wallet=wallet.lower(),
        fills=test_fills,
        slippage_model=slippage_model,
        realized_only=realized_only,
    )

    if test_metrics.closed_trips_n < 10:
        logger.warning(
            "walk_forward_sparse_test_bucket",
            wallet=wallet.lower(),
            closed_trips_test=test_metrics.closed_trips_n,
        )

    gap = math.nan
    if not math.isnan(train_metrics.win_rate) and not math.isnan(test_metrics.win_rate):
        gap = abs(train_metrics.win_rate - test_metrics.win_rate)

    stable_flag = (
        not math.isnan(gap)
        and gap <= 0.10
        and not math.isnan(test_metrics.win_rate)
        and test_metrics.realized_closed_pnl_usd > 0
    )

    return {
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "stable_flag": bool(stable_flag),
        "calendar_days_total": len(days),
        "train_days": len(train_days),
        "test_days": len(test_days),
    }
