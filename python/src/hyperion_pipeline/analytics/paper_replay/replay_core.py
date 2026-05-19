"""Shared slip+fee application for one FIFO ``ClosedRoundTrip`` (used by metrics + copyability)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from hyperion_pipeline.analytics.paper_replay.round_trips import ClosedRoundTrip
from hyperion_pipeline.analytics.paper_replay.slippage import (
    SlippageModel,
    apply_slippage_for_long_round_trip,
    apply_slippage_for_short_round_trip,
)


@dataclass(slots=True)
class SlippedLeg:
    """Closed FIFO chunk after slippage widening and fee deduction."""

    net_pnl_usd: float
    hold_hours: float
    exit_ts: datetime
    entry_notional_usd: float
    ret_vs_notional: float
    loss_pct_vs_notional: float


def slip_leg_pnl(trip: ClosedRoundTrip, model: SlippageModel) -> SlippedLeg:
    """
    Purpose: convert a single ``ClosedRoundTrip`` into dollar PnL after slippage + allocated fees.

    Inputs: FIFO chunk and adverse slippage model.
    Outputs: ``SlippedLeg`` carrying net economics and compact return marker.

    Gotcha: ``ret_vs_notional`` divides by entry notional using pre-slippage mid — comparable across
            trips but not equal to exchange-reported ROI.
    """

    qty = trip.qty
    fees = trip.fee_open_usd + trip.fee_close_usd
    notional_mid = qty * trip.entry_px_mid

    if trip.is_long:
        adj_e, adj_x = apply_slippage_for_long_round_trip(
            entry_buy_px_mid=trip.entry_px_mid,
            exit_sell_px_mid=trip.exit_px_mid,
            model=model,
        )
        gross = (adj_x - adj_e) * qty
    else:
        adj_e, adj_x = apply_slippage_for_short_round_trip(
            entry_sell_px_mid=trip.entry_px_mid,
            exit_buy_px_mid=trip.exit_px_mid,
            model=model,
        )
        gross = (adj_e - adj_x) * qty

    net = gross - fees
    ret_n = net / notional_mid if abs(notional_mid) > 1e-12 else math.nan
    loss_pct = (net / notional_mid) * 100.0 if abs(notional_mid) > 1e-12 else math.nan
    return SlippedLeg(
        net_pnl_usd=float(net),
        hold_hours=float(trip.hold_hours),
        exit_ts=trip.exit_ts,
        entry_notional_usd=float(abs(notional_mid)),
        ret_vs_notional=float(ret_n),
        loss_pct_vs_notional=float(loss_pct),
    )
