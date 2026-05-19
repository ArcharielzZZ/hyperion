"""FIFO-linked closed round-trip extraction from enriched ``TradeFill`` rows."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from hyperion_pipeline.models.pydantic_domain import TradeFill

EPS = 1e-12


def _infer_open_close(fill_dir: str | None) -> tuple[bool | None, bool | None]:
    """
    Return ``(is_open, is_close)`` from Hyperliquid ``dir``.

    Gotcha: missing ``fill_dir`` → ``(None, None)``; with ``realized_only=True`` callers must skip fills.
    """

    if not fill_dir:
        return None, None
    lu = fill_dir.strip().lower()
    if lu.startswith("close"):
        return False, True
    if lu.startswith("open"):
        return True, False
    return None, None


Lot = tuple[float, float, datetime, float]


@dataclass(slots=True)
class ClosedRoundTrip:
    """One FIFO matched chunk: a slice of a close fill against the oldest compatible open lot."""

    coin: str
    qty: float
    is_long: bool
    entry_ts: datetime
    exit_ts: datetime
    entry_px_mid: float
    exit_px_mid: float
    hold_hours: float
    fee_open_usd: float
    fee_close_usd: float
    hl_closed_pnl_portion_usd: float | None


def unrealized_mtm_usd_remaining_lots(
    long_lots: dict[str, deque[Lot]],
    short_lots: dict[str, deque[Lot]],
    last_mid_by_coin: dict[str, float],
) -> float:
    """
    Purpose: mark-to-market of unmatched open lots after replay, for MTM denominators.

    Inputs: FIFO lot deques keyed by coin, mark prices (mid) keyed by coin.
    Outputs: sum of unrealized PnL in USD assuming mid exits at ``last_mid_by_coin``.

    Gotcha: if a coin lacks a ``last_mid_by_coin`` entry (no fills seen), falls back to each
    lot's entry price — unrealized contribution becomes 0 for that slice.
    """

    total = 0.0
    for coin, dq in long_lots.items():
        px_m = last_mid_by_coin.get(coin)
        for lot_q, lot_px, _ts, _fee in dq:
            mark = float(px_m if px_m is not None else lot_px)
            total += (mark - lot_px) * lot_q
    for coin, dq in short_lots.items():
        px_m = last_mid_by_coin.get(coin)
        for lot_q, lot_px, _ts, _fee in dq:
            mark = float(px_m if px_m is not None else lot_px)
            total += (lot_px - mark) * lot_q
    return total


def fifo_state_after_round_trips(
    fills: list[TradeFill],
    *,
    realized_only: bool,
) -> tuple[list[ClosedRoundTrip], dict[str, deque[Lot]], dict[str, deque[Lot]], dict[str, float]]:
    """
    Purpose: produce closed trips plus terminal FIFO ladders and last mids (for unrealized MTM).

    Gotcha: same labeling requirements as ``fifo_closed_round_trips``; state is simulation-only,
    not what the exchange margin engine reports if labels or sizes disagree.
    """

    ordered = sorted(fills, key=lambda x: (x.event_timestamp, x.event_key))
    long_lots: dict[str, deque[Lot]] = {}
    short_lots: dict[str, deque[Lot]] = {}
    out: list[ClosedRoundTrip] = []
    last_mid_by_coin: dict[str, float] = {}

    for f in ordered:
        last_mid_by_coin[f.coin] = f.price
        is_open, is_close = _infer_open_close(f.fill_dir)
        if realized_only and (is_open is None or is_close is None):
            continue

        coin = f.coin
        long_lots.setdefault(coin, deque())
        short_lots.setdefault(coin, deque())
        ts = f.event_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        if is_open and not is_close:
            qty = abs(f.size)
            lu = (f.fill_dir or "").lower()
            fee_o = float(f.fee_usd or 0.0)
            if "long" in lu or ("long" not in lu and "short" not in lu and f.side == "buy"):
                long_lots[coin].append((qty, f.price, ts, fee_o))
            elif "short" in lu or f.side == "sell":
                short_lots[coin].append((qty, f.price, ts, fee_o))

        elif is_close and not is_open:
            qty_close = abs(f.size)
            if qty_close <= EPS:
                continue
            lu_c = (f.fill_dir or "").lower()
            if "long" in lu_c:
                is_long_close = True
            elif "short" in lu_c:
                is_long_close = False
            else:
                is_long_close = f.side == "sell"

            dq = long_lots[coin] if is_long_close else short_lots[coin]
            fee_close_total = float(f.fee_usd or 0.0)
            hl_close_total = f.closed_pnl_usd

            qty_rem = qty_close
            batch: list[ClosedRoundTrip] = []
            while qty_rem > EPS and dq:
                lot_q, lot_px, lot_ts, lot_fee = dq[0]
                take = min(lot_q, qty_rem)
                fee_open_alloc = lot_fee * (take / lot_q) if lot_q > EPS else 0.0
                fee_close_alloc = fee_close_total * (take / qty_close)
                hl_portion: float | None
                if hl_close_total is not None:
                    hl_portion = float(hl_close_total) * (take / qty_close)
                else:
                    hl_portion = None

                hold_h = max(0.0, (ts - lot_ts).total_seconds() / 3600.0)

                batch.append(
                    ClosedRoundTrip(
                        coin=coin,
                        qty=take,
                        is_long=is_long_close,
                        entry_ts=lot_ts,
                        exit_ts=ts,
                        entry_px_mid=lot_px,
                        exit_px_mid=f.price,
                        hold_hours=hold_h,
                        fee_open_usd=fee_open_alloc,
                        fee_close_usd=fee_close_alloc,
                        hl_closed_pnl_portion_usd=hl_portion,
                    )
                )

                if lot_q - take <= EPS:
                    dq.popleft()
                else:
                    dq[0] = (lot_q - take, lot_px, lot_ts, lot_fee - fee_open_alloc)

                qty_rem -= take

            if qty_rem > EPS and realized_only:
                continue

            out.extend(batch)

    return out, long_lots, short_lots, last_mid_by_coin


def fifo_closed_round_trips(
    fills: list[TradeFill],
    *,
    realized_only: bool,
) -> list[ClosedRoundTrip]:
    """
    Purpose: match exchange-labeled closes to opens per coin via FIFO assignment.

    Inputs: ``TradeFill`` list for a single wallet (all coins; list is sorted internally).
    Outputs: one ``ClosedRoundTrip`` per matched (open-lot-slice, close-slice) pair.

    Gotcha: depends on ``fill_dir`` Open/Close labels and Long/Short hints; with
    ``realized_only=True`` a close that cannot fully deplete ``qty_close`` against open lots
    is omitted in full (including any partial peels from that close).
    """

    trips, _, _, _ = fifo_state_after_round_trips(fills, realized_only=realized_only)
    return trips
