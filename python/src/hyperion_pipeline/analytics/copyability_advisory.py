"""Research-only copyability sub-score persisted to ``trader_copyability_advisory``."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hyperion_pipeline.analytics.paper_replay.aggregate_metrics import (
    slipped_closed_legs,
    summarize_wallet_round_trip_replay,
)
from hyperion_pipeline.analytics.paper_replay.replay_core import SlippedLeg
from hyperion_pipeline.analytics.paper_replay.slippage import SlippageModel
from hyperion_pipeline.models.pydantic_domain import TradeFill


@dataclass(slots=True)
class CopyabilityComputation:
    """Normalized component scores aggregated into a 0–100 headline."""

    copyability_score: float
    signal_frequency_component: float
    timing_component: float
    realized_ratio_component: float
    stability_component: float


def timing_score_from_hold_hours(avg_hold_h: float) -> float:
    """
    Purpose: map average hold latency to copy-lag heuristics (0–100).

    Inputs: hours between FIFO open and paired closes.
    Outputs: peaked around the 4h–48h research band.

    Gotcha: ignore if you need exchange session boundaries — timestamps are naive UTC alignment only.
    """

    h = max(0.0, float(avg_hold_h))
    if h < 1.0:
        return 25.0 * h
    if h < 4.0:
        return 25.0 + 20.0 * (h - 1.0) / 3.0
    if h <= 48.0:
        return 92.0
    if h <= 168.0:
        span = 120.0
        return max(25.0, 92.0 * (1.0 - (h - 48.0) / span))
    return max(8.0, 25.0 * math.exp(-(h - 168.0) / 240.0))


def peer_percentile_higher_is_better(peer_values: dict[str, float], wallet: str) -> float:
    """
    Purpose: map wallet-specific scalars onto a percentile grid vs peers.

    Inputs: ascending-friendly metrics keyed by lowercase wallet ids.
    Outputs: empirical percentile bucketed 0–100.

    Gotcha: with <2 wallets every score collapses toward 55.0 intentionally.
    """

    w = wallet.lower()
    if w not in peer_values:
        return 0.0
    vals = sorted(peer_values.values())
    target = peer_values[w]
    if len(vals) < 2:
        return 55.0
    count_le = sum(1 for v in vals if v <= target)
    return 100.0 * count_le / len(vals)


def stability_from_slipped_rounds(slips: list[SlippedLeg], *, horizon_days: int = 30) -> float:
    """
    Purpose: reward steadier rolling hit rates on FIFO-closed slips.

    Inputs: chronological ``SlippedLeg`` nets with exit timestamps.
    Outputs: bounded 0–100 where ``100`` means negligible variance across windows.

    Gotcha: needs ≥4 slips per evaluated window anchor; silently widens horizons when tapes are sparse.
    """

    if not slips:
        return 25.0

    zoned: list[tuple[datetime, float]] = []
    for s in slips:
        ts = s.exit_ts if s.exit_ts.tzinfo else s.exit_ts.replace(tzinfo=UTC)
        ts = ts.astimezone(UTC)
        zoned.append((ts, s.net_pnl_usd))

    days = sorted({ts.date() for ts, _ in zoned})
    if len(days) < 4:
        return 35.0

    rolls: list[float] = []
    for anchor in days:
        window_floor = anchor - timedelta(days=horizon_days)
        nets = [pnl for ts, pnl in zoned if window_floor <= ts.date() <= anchor]
        if len(nets) >= 4:
            rolls.append(sum(1 for x in nets if x > 0.0) / len(nets))

    if len(rolls) < 2:
        prior = rolls[0] if rolls else 0.45
        return float(max(20.0, 100.0 * (1.0 - prior)))

    std = statistics.pstdev(rolls)
    return float(max(0.0, min(100.0, 100.0 * (1.0 - std))))


def compute_copyability_batch_inputs(
    *,
    promoted_wallets: list[str],
    fills_by_wallet: dict[str, list[TradeFill]],
    slippage_model: SlippageModel,
    realized_only: bool,
) -> dict[str, CopyabilityComputation]:
    """
    Purpose: unify percentile scaffolding for simultaneous advisory snapshots.

    Inputs: promoted wallets, hydrated tapes keyed by wallet, slippage/replay parity flags.
    Outputs: mapping wallet→``CopyabilityComputation``.

    Gotcha: recomputes ``summarize_wallet_round_trip_replay`` internally — keep ``realized_only`` aligned
            with whatever research tables you are publishing alongside this score.
    """

    trades_30 = {w.lower(): float(len(fills_by_wallet.get(w.lower(), []))) for w in promoted_wallets}

    avg_hold_map: dict[str, float] = {}
    rv_map: dict[str, float] = {}
    stability_map: dict[str, float] = {}

    for w in promoted_wallets:
        wl = w.lower()
        fills = fills_by_wallet.get(wl, [])
        slips = slipped_closed_legs(
            fills=fills,
            slippage_model=slippage_model,
            realized_only=realized_only,
        )

        replay_row = summarize_wallet_round_trip_replay(
            wallet=wl,
            fills=fills,
            slippage_model=slippage_model,
            realized_only=realized_only,
        )

        avg_hold = statistics.mean([s.hold_hours for s in slips]) if slips else float("nan")
        avg_hold_map[wl] = 12.0 if math.isnan(avg_hold) else float(avg_hold)

        rr = replay_row.realized_vs_mtm_ratio
        rv_map[wl] = 0.0 if math.isnan(rr) else float(rr)

        stability_map[wl] = stability_from_slipped_rounds(slips)

    out: dict[str, CopyabilityComputation] = {}
    for w in promoted_wallets:
        wl = w.lower()
        freq_component = peer_percentile_higher_is_better(trades_30, wl)
        timing_component = timing_score_from_hold_hours(avg_hold_map[wl])
        realized_component = peer_percentile_higher_is_better(rv_map, wl)
        stability_component = stability_map.get(wl, 30.0)
        stacked = statistics.mean(
            [freq_component, timing_component, realized_component, stability_component]
        )

        capped = float(max(0.0, min(100.0, stacked)))

        out[wl] = CopyabilityComputation(
            copyability_score=capped,
            signal_frequency_component=freq_component,
            timing_component=timing_component,
            realized_ratio_component=realized_component,
            stability_component=stability_component,
        )

    return out
