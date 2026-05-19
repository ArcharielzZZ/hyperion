"""Daily and multi-window stats from fill tapes."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from hyperion_pipeline.models.pydantic_domain import TradeFill
from hyperion_pipeline.wallet_filter.models import MultiWindowPnL


def _fill_notional(fill: TradeFill) -> float:
    return abs(float(fill.size) * float(fill.price))


def _fill_pnl(fill: TradeFill) -> float:
    if fill.closed_pnl_usd is not None:
        return float(fill.closed_pnl_usd)
    return 0.0


def build_daily_stats(fills: list[TradeFill]) -> dict[date, tuple[float, int, float]]:
    """Map UTC date -> (daily_pnl, daily_trades, daily_volume)."""

    by_day: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for fill in fills:
        d = fill.event_timestamp.astimezone(timezone.utc).date()
        row = by_day[d]
        row[0] += _fill_pnl(fill)
        row[1] += 1.0
        row[2] += _fill_notional(fill)
    return {d: (pnl, int(trades), vol) for d, (pnl, trades, vol) in by_day.items()}


def multi_window_from_daily(
    daily: dict[date, tuple[float, int, float]],
    *,
    as_of: datetime | None = None,
) -> MultiWindowPnL:
    if not daily:
        return MultiWindowPnL()

    anchor = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    d7 = anchor - timedelta(days=7)
    d30 = anchor - timedelta(days=30)
    d90 = anchor - timedelta(days=90)
    d180 = anchor - timedelta(days=180)

    out = MultiWindowPnL()
    last_ts: datetime | None = None

    for day, (pnl, trades, vol) in daily.items():
        out.total_volume += vol
        out.total_trades += trades
        if day >= d7:
            out.pnl_7d += pnl
            out.trades_7d += trades
        if day >= d30:
            out.pnl_1m += pnl
            out.trades_1m += trades
        if day >= d90:
            out.pnl_3m += pnl
            out.trades_3m += trades
        if day >= d180:
            out.pnl_6m += pnl
            out.trades_6m += trades

    return out


def multi_window_from_fills(
    fills: list[TradeFill],
    *,
    as_of: datetime | None = None,
) -> MultiWindowPnL:
    if not fills:
        return MultiWindowPnL()
    daily = build_daily_stats(fills)
    mw = multi_window_from_daily(daily, as_of=as_of)
    last = max(f.event_timestamp for f in fills)
    mw.last_trade_at = last.astimezone(timezone.utc)
    return mw
