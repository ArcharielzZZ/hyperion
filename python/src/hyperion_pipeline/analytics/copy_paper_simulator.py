"""
Paper copy-trading simulator for Hyperliquid whale tapes.

Trust hierarchy for wallet evaluation (most → least reliable):
  1. ``exchange_closed_pnl_scaled`` — Hyperliquid ``closedPnl`` on close rows × copy_scale (ground truth).
  2. ``fifo_scaled`` — FIFO Open/Close round trips on the full tape, scale net PnL (validation).
  3. ``fill_follow_legacy`` — naive per-fill mirror; bot-realism / partial-mirror stress test only.
  4. ``round_trip_copy`` — paper simulator with dust/risk filters; only complete trips where every leg passes.
"""

from __future__ import annotations

import logging
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from hyperion_pipeline.models.pydantic_domain import TradeFill

logger = logging.getLogger(__name__)

EPS = 1e-12
Direction = Literal["long", "short"]


class SkipReason(str, Enum):
    DUST = "DUST"
    RISK_CAP = "RISK_CAP"
    NO_OPEN = "NO_OPEN"
    INCOMPLETE_TRIP = "INCOMPLETE_TRIP"
    NEGATIVE_EXPECTANCY = "NEGATIVE_EXPECTANCY"
    MAX_TOTAL_EXPOSURE = "MAX_TOTAL_EXPOSURE"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    POSITION_TOO_LARGE = "POSITION_TOO_LARGE"


@dataclass(slots=True)
class WhaleRoundTrip:
    """One whale position cycle: opens (and adds) until flat again."""

    coin: str
    direction: Direction
    open_fills: list[TradeFill]
    close_fills: list[TradeFill]
    open_time: datetime
    close_time: datetime
    gross_pnl_usd: float

    @property
    def all_fills(self) -> list[TradeFill]:
        return sorted(
            self.open_fills + self.close_fills,
            key=lambda f: (f.event_timestamp, f.event_key),
        )


@dataclass(slots=True)
class SkipLogEntry:
    event_key: str
    coin: str
    reason: SkipReason
    trip_index: int | None = None


@dataclass(slots=True)
class SimulatedRoundTrip:
    """Paper economics for one successfully copied whale round trip."""

    trip: WhaleRoundTrip
    realized_pnl: float
    fees: float
    slippage: float


@dataclass(slots=True)
class RoundTripCopyMetrics:
    wallet: str
    fill_count: int
    starting_equity: float
    ending_equity: float
    ending_equity_mtm: float
    total_return_pct: float
    total_return_mtm_pct: float
    realized_pnl: float
    total_fees_paid: float
    total_slippage_cost: float
    open_notional_usd: float
    win_rate_pct: float
    avg_pnl_per_trade: float
    max_drawdown_pct: float
    coins_traded: int
    first_fill: datetime | None
    last_fill: datetime | None
    total_round_trips_detected: int
    round_trips_simulated: int
    round_trips_skipped: dict[str, int]
    skip_counts_by_reason: dict[str, int]
    copyability_score: float
    simulated_trips: list[SimulatedRoundTrip] = field(default_factory=list)
    skip_log: list[SkipLogEntry] = field(default_factory=list)
    sizing_summary: "SizingEngineSummary | None" = None
    use_dynamic_sizing: bool = False
    verdict: str = ""
    fill_dir_quality: dict[str, Any] | None = None
    passed_edge_filter: bool = True
    passed_copyability_gate: bool = True


@dataclass(slots=True)
class _CoinTripBuilder:
    signed_size: float = 0.0
    direction: Direction | None = None
    open_fills: list[TradeFill] = field(default_factory=list)
    close_fills: list[TradeFill] = field(default_factory=list)
    open_time: datetime | None = None

    def reset(self) -> None:
        self.signed_size = 0.0
        self.direction = None
        self.open_fills = []
        self.close_fills = []
        self.open_time = None


@dataclass(slots=True)
class _FifoLot:
    size: float
    entry_price: float


@dataclass(slots=True)
class PaperPositionTracker:
    """
    Per-coin signed position with FIFO closes.

    PnL is booked only on closes; opens add lots. Negative size after a close logs a data gap.
    """

    positions: dict[str, float] = field(default_factory=dict)
    lots: dict[str, deque[_FifoLot]] = field(default_factory=dict)
    avg_entry: dict[str, float] = field(default_factory=dict)

    def _ensure_coin(self, coin: str) -> None:
        if coin not in self.positions:
            self.positions[coin] = 0.0
            self.lots[coin] = deque()

    def apply_leg(
        self,
        coin: str,
        *,
        signed_delta: float,
        price: float,
        is_close: bool,
    ) -> tuple[float, bool]:
        """
        Apply a paper leg. Returns ``(realized_pnl, had_open_for_close)``.

        ``had_open_for_close`` is False when a close could not match any open lot (NO_OPEN).
        """

        self._ensure_coin(coin)
        pos = self.positions[coin]
        realized = 0.0
        had_open = True

        if is_close:
            close_qty = abs(signed_delta)
            if close_qty <= EPS:
                return 0.0, True
            if abs(pos) <= EPS:
                return 0.0, False

            dq = self.lots[coin]
            remaining = close_qty
            closing_long = pos > 0

            while remaining > EPS and dq:
                lot = dq[0]
                take = min(lot.size, remaining)
                if closing_long:
                    realized += (price - lot.entry_price) * take
                else:
                    realized += (lot.entry_price - price) * take
                lot.size -= take
                remaining -= take
                if lot.size <= EPS:
                    dq.popleft()

            if remaining > EPS:
                logger.warning(
                    "copy_paper: close without full FIFO match coin=%s remaining=%s pos=%s",
                    coin,
                    remaining,
                    pos,
                )
                had_open = False

            pos += signed_delta
            if abs(pos) <= EPS:
                pos = 0.0
                dq.clear()
                self.avg_entry.pop(coin, None)
            elif (pos > 0) != closing_long:
                # flipped — new lot at exit price
                self.lots[coin] = deque([_FifoLot(abs(pos), price)])
                self.avg_entry[coin] = price
            else:
                self._refresh_avg(coin)
        else:
            # open / add
            if abs(pos) <= EPS:
                pos = signed_delta
                self.lots[coin] = deque([_FifoLot(abs(signed_delta), price)])
            elif (pos > 0 and signed_delta > 0) or (pos < 0 and signed_delta < 0):
                pos += signed_delta
                self.lots[coin].append(_FifoLot(abs(signed_delta), price))
            else:
                # reducing without is_close flag — treat as close path
                return self.apply_leg(
                    coin, signed_delta=signed_delta, price=price, is_close=True
                )
            self._refresh_avg(coin)

        if pos < -EPS:
            logger.warning("copy_paper: negative position coin=%s pos=%s", coin, pos)
            pos = 0.0
            self.lots[coin].clear()

        self.positions[coin] = pos
        return realized, had_open

    def _refresh_avg(self, coin: str) -> None:
        dq = self.lots[coin]
        if not dq:
            self.avg_entry.pop(coin, None)
            return
        total = sum(l.size for l in dq)
        if total <= EPS:
            return
        self.avg_entry[coin] = sum(l.entry_price * l.size for l in dq) / total

    def open_notional(self, last_price: dict[str, float]) -> float:
        total = 0.0
        for coin, pos in self.positions.items():
            if abs(pos) <= EPS:
                continue
            px = last_price.get(coin, self.avg_entry.get(coin, 0.0))
            total += abs(pos) * px
        return total

    def unrealized_pnl(self, last_price: dict[str, float]) -> float:
        total = 0.0
        for coin, pos in self.positions.items():
            if abs(pos) <= EPS:
                continue
            entry = self.avg_entry.get(coin, last_price.get(coin, 0.0))
            px = last_price.get(coin, entry)
            if pos > 0:
                total += (px - entry) * pos
            else:
                total += (entry - px) * abs(pos)
        return total


def _infer_open_close(fill_dir: str | None) -> tuple[bool | None, bool | None]:
    if not fill_dir:
        return None, None
    lu = fill_dir.strip().lower()
    if lu.startswith("close"):
        return False, True
    if lu.startswith("open"):
        return True, False
    return None, None


def whale_signed_delta(fill: TradeFill) -> float | None:
    """
    Signed change to whale position from one fill.

    Uses ``fill_dir`` when present; otherwise buy=+size, sell=-size.
    """

    if fill.price <= 0 or fill.size <= 0:
        return None
    is_open, is_close = _infer_open_close(fill.fill_dir)
    qty = abs(fill.size)
    if is_open and not is_close:
        lu = (fill.fill_dir or "").lower()
        if "short" in lu or (fill.side == "sell" and "long" not in lu):
            return -qty
        return qty
    if is_close and not is_open:
        lu = (fill.fill_dir or "").lower()
        if "long" in lu:
            return -qty
        if "short" in lu:
            return qty
        return -qty if fill.side == "sell" else qty
    if fill.side == "buy":
        return qty
    return -qty


def detect_whale_round_trips(fills: list[TradeFill]) -> list[WhaleRoundTrip]:
    """
    Build round trips from FIFO-matched Open/Close slices (``fill_dir`` required).

    Each trip is one matched open lot + close slice (same contract as ``fifo_closed_round_trips``).
    Synthetic open/close ``TradeFill`` rows carry trip qty/prices for dust/risk leg checks.
  """

    from hyperion_pipeline.analytics.paper_replay.round_trips import fifo_closed_round_trips

    if not fills:
        return []

    wallet = fills[0].wallet
    trips: list[WhaleRoundTrip] = []
    for i, t in enumerate(fifo_closed_round_trips(fills, realized_only=True)):
        direction: Direction = "long" if t.is_long else "short"
        open_side = "buy" if t.is_long else "sell"
        close_side = "sell" if t.is_long else "buy"
        open_dir = "Open Long" if t.is_long else "Open Short"
        close_dir = "Close Long" if t.is_long else "Close Short"
        open_f = TradeFill(
            wallet=wallet,
            coin=t.coin,
            side=open_side,
            size=t.qty,
            price=t.entry_px_mid,
            event_timestamp=t.entry_ts,
            event_key=f"fifo-trip|{i}|{t.coin}|open",
            fill_dir=open_dir,
        )
        close_f = TradeFill(
            wallet=wallet,
            coin=t.coin,
            side=close_side,
            size=t.qty,
            price=t.exit_px_mid,
            event_timestamp=t.exit_ts,
            event_key=f"fifo-trip|{i}|{t.coin}|close",
            fill_dir=close_dir,
            closed_pnl_usd=t.hl_closed_pnl_portion_usd,
            fee_usd=t.fee_close_usd,
        )
        gross = float(t.hl_closed_pnl_portion_usd or 0.0)
        trips.append(
            WhaleRoundTrip(
                coin=t.coin,
                direction=direction,
                open_fills=[open_f],
                close_fills=[close_f],
                open_time=t.entry_ts,
                close_time=t.exit_ts,
                gross_pnl_usd=gross,
            )
        )
    return trips


def detect_whale_round_trips_position_flat(fills: list[TradeFill]) -> list[WhaleRoundTrip]:
    """
    Alternate detector: group raw fills until per-coin signed position nets to zero.

    Useful for tests; live tapes with persistent exposure prefer ``detect_whale_round_trips``.
    """

    ordered = sorted(fills, key=lambda f: (f.event_timestamp, f.event_key))
    builders: dict[str, _CoinTripBuilder] = {}
    trips: list[WhaleRoundTrip] = []

    for fill in ordered:
        delta = whale_signed_delta(fill)
        if delta is None:
            continue
        coin = fill.coin
        b = builders.setdefault(coin, _CoinTripBuilder())
        is_open, is_close = _infer_open_close(fill.fill_dir)

        if abs(b.signed_size) <= EPS and abs(delta) > EPS:
            b.direction = "long" if delta > 0 else "short"
            b.open_time = fill.event_timestamp

        if is_open and not is_close:
            b.open_fills.append(fill)
        elif is_close and not is_open:
            b.close_fills.append(fill)
        elif b.signed_size == 0 or (b.signed_size > 0 and delta > 0) or (b.signed_size < 0 and delta < 0):
            b.open_fills.append(fill)
        else:
            b.close_fills.append(fill)

        b.signed_size += delta

        if abs(b.signed_size) > EPS:
            continue

        if not b.open_fills or not b.close_fills or b.direction is None or b.open_time is None:
            b.reset()
            continue

        gross = sum(float(f.closed_pnl_usd or 0.0) for f in b.close_fills)
        close_time = max(f.event_timestamp for f in b.close_fills)
        trips.append(
            WhaleRoundTrip(
                coin=coin,
                direction=b.direction,
                open_fills=list(b.open_fills),
                close_fills=list(b.close_fills),
                open_time=b.open_time,
                close_time=close_time,
                gross_pnl_usd=gross,
            )
        )
        b.reset()

    return trips


def slip_price(side: str, price: float, slippage_bps: float, *, is_entry: bool) -> float:
    mult = slippage_bps / 10_000.0
    if side == "buy":
        return price * (1.0 + mult) if is_entry else price * (1.0 - mult)
    return price * (1.0 - mult) if is_entry else price * (1.0 + mult)


def fee_usd(notional: float, fee_bps: float) -> float:
    return abs(notional) * fee_bps / 10_000.0


def evaluate_fill_for_copy(
    fill: TradeFill,
    *,
    equity: float,
    copy_scale: float,
    max_equity_pct_per_fill: float,
    min_trade_usd: float,
    max_trade_usd: float | None,
) -> tuple[float, float, SkipReason | None]:
    """
    Size one paper leg from a whale fill.

    Returns ``(paper_size, target_notional, skip_reason)``. ``paper_size`` is 0 when skipped.
    """

    if equity < min_trade_usd:
        return 0.0, 0.0, SkipReason.RISK_CAP

    whale_notional = abs(fill.size) * fill.price
    cap_notional = equity * max_equity_pct_per_fill
    if max_trade_usd is not None:
        cap_notional = min(cap_notional, max_trade_usd)
    target_notional = min(whale_notional * copy_scale, cap_notional)
    if target_notional < min_trade_usd:
        return 0.0, target_notional, SkipReason.DUST

    paper_size = target_notional / fill.price
    return paper_size, target_notional, None


def _trip_skip_reasons(
    trip: WhaleRoundTrip,
    *,
    equity: float,
    copy_scale: float,
    max_equity_pct_per_fill: float,
    min_trade_usd: float,
    max_trade_usd: float | None,
) -> tuple[list[tuple[TradeFill, SkipReason]], list[tuple[TradeFill, float]]]:
    """
    Evaluate every leg in a trip at current equity (pre-check before simulation).

    Returns skipped fills with reasons, or accepted ``(fill, paper_size)`` legs.
    """
    skipped: list[tuple[TradeFill, SkipReason]] = []
    sized: list[tuple[TradeFill, float]] = []

    for fill in trip.all_fills:
        paper_size, _, reason = evaluate_fill_for_copy(
            fill,
            equity=equity,
            copy_scale=copy_scale,
            max_equity_pct_per_fill=max_equity_pct_per_fill,
            min_trade_usd=min_trade_usd,
            max_trade_usd=max_trade_usd,
        )
        if reason is not None:
            skipped.append((fill, reason))
        else:
            sized.append((fill, paper_size))

    if skipped:
        return skipped, []

    uniform = min(s for _, s in sized)
    return [], [(f, uniform) for f, _ in sized]


def simulate_whale_round_trip_usd(
    trip: WhaleRoundTrip,
    position_usd: float,
    *,
    fee_bps: float,
    slippage_bps: float,
) -> SimulatedRoundTrip:
    """Execute one round trip at fixed USD notional (dynamic sizing path)."""

    entry_fill = trip.open_fills[0]
    if entry_fill.price <= 0:
        return SimulatedRoundTrip(trip=trip, realized_pnl=0.0, fees=0.0, slippage=0.0)

    coin_size = position_usd / entry_fill.price
    legs: list[tuple[TradeFill, float]] = []
    for fill in trip.all_fills:
        if fill in trip.open_fills:
            legs.append((fill, coin_size))
        else:
            legs.append((fill, coin_size))
    return simulate_whale_round_trip(
        trip, legs, fee_bps=fee_bps, slippage_bps=slippage_bps
    )


def simulate_whale_round_trip(
    trip: WhaleRoundTrip,
    legs: list[tuple[TradeFill, float]],
    *,
    fee_bps: float,
    slippage_bps: float,
) -> SimulatedRoundTrip:
    """Execute one accepted round trip through the paper position tracker."""

    tracker = PaperPositionTracker()
    realized_total = 0.0
    fees_total = 0.0
    slip_total = 0.0

    for fill, paper_size in sorted(legs, key=lambda x: (x[0].event_timestamp, x[0].event_key)):
        is_open, is_close = _infer_open_close(fill.fill_dir)
        if is_close and not is_open:
            is_close_leg = True
        elif is_open and not is_close:
            is_close_leg = False
        else:
            is_close_leg = fill in trip.close_fills

        signed_delta = paper_size if fill.side == "buy" else -paper_size
        if is_close_leg:
            leg_px = slip_price(fill.side, fill.price, slippage_bps, is_entry=False)
            leg_fee = fee_usd(paper_size * leg_px, fee_bps)
        else:
            leg_px = slip_price(fill.side, fill.price, slippage_bps, is_entry=True)
            leg_fee = fee_usd(paper_size * leg_px, fee_bps)
        slip_cost = abs(leg_px - fill.price) * paper_size

        realized, had_open = tracker.apply_leg(
            trip.coin,
            signed_delta=signed_delta,
            price=leg_px,
            is_close=is_close_leg,
        )
        if is_close_leg and not had_open:
            logger.warning("copy_paper NO_OPEN on trip coin=%s key=%s", trip.coin, fill.event_key)

        realized_total += realized
        fees_total += leg_fee
        slip_total += slip_cost

    return SimulatedRoundTrip(
        trip=trip,
        realized_pnl=realized_total - fees_total,
        fees=fees_total,
        slippage=slip_total,
    )


def check_missing_fill_dir(
    wallet_address: str,
    fills: list[TradeFill] | None = None,
    db_conn: Any = None,
) -> dict[str, Any]:
    """
    Check whether fills for this wallet are missing ``fill_dir`` labels.

    When ``fills`` is omitted, ``db_conn`` may supply rows (optional integration).
  """

    _ = wallet_address
    if fills is None:
        if db_conn is None:
            return {
                "total_fills": 0,
                "fills_missing_fill_dir": 0,
                "pct_missing": 1.0,
                "recommendation": "exclude",
            }
        fills = []

    total = len(fills)
    missing = sum(1 for f in fills if not f.fill_dir)
    pct_missing = (missing / total) if total else 1.0
    if pct_missing >= 1.0:
        recommendation = "exclude"
    elif pct_missing > 0.10:
        recommendation = "backfill"
    else:
        recommendation = "ok"
    return {
        "total_fills": total,
        "fills_missing_fill_dir": missing,
        "pct_missing": pct_missing,
        "recommendation": recommendation,
    }


def classify_wallet(
    passed_edge: bool,
    edge_reason: str,
    passed_copyability: bool,
    copy_reason: str,
    dynamic_return_pct: float,
    exchange_return_pct: float,
    n_trips_detected: int,
) -> str:
    """Return leaderboard verdict: LIVE_CANDIDATE, MONITOR, REVIEW, or filter/NO_DATA codes."""

    if n_trips_detected == 0:
        return "NO_DATA"
    if not passed_edge:
        return "FILTERED_EDGE"
    if not passed_copyability:
        return "FILTERED_COPYABILITY"

    if abs(exchange_return_pct) < 1e-9:
        ratio = float("inf") if abs(dynamic_return_pct) > 1e-9 else 1.0
    else:
        ratio = abs(dynamic_return_pct / exchange_return_pct)

    if dynamic_return_pct > 0 and ratio <= 20.0:
        return "LIVE_CANDIDATE"
    if dynamic_return_pct >= -2.0:
        return "MONITOR"
    if ratio > 20.0:
        return "REVIEW"
    return "REVIEW"


def forecast_dynamic_copyability(
    trips: list[WhaleRoundTrip],
    *,
    profile: "SimulatorWalletProfile",
    cfg: "CopySizingConfig",
    starting_equity: float,
    fee_bps: float,
    slippage_bps: float,
) -> float:
    """
    Shadow run: fraction of FIFO trips that complete under dynamic sizing + risk path.

    Mirrors the main simulation loop (including circuit breaker) without mutating caller state.
    """

    from hyperion_pipeline.analytics.sizing_engine import (
        PortfolioRiskManager,
        compute_position_size,
        estimate_stop_distance,
    )

    if not trips:
        return 0.0

    risk_manager = PortfolioRiskManager(
        starting_equity,
        max_position_pct=cfg.max_position_pct,
        max_total_exposure_pct=cfg.max_total_exposure_pct,
        circuit_breaker_pct=cfg.circuit_breaker_pct,
    )
    last_position_usd: float | None = None
    simulated = 0

    for trip in trips:
        if risk_manager.is_circuit_broken():
            break
        stop_pct = estimate_stop_distance(trip, profile, default_stop_pct=cfg.default_stop_pct)
        slow_equity = risk_manager.equity_ema(span=cfg.ema_span)
        size_result = compute_position_size(
            equity=slow_equity,
            win_rate=profile.win_rate,
            avg_win_loss_ratio=profile.avg_win_loss_ratio,
            copyability=max(profile.copyability_score, 0.05),
            stop_distance_pct=stop_pct,
            risk_per_trade_pct=cfg.risk_per_trade_pct,
            max_position_pct=cfg.max_position_pct,
            min_trade_usd=cfg.min_trade_usd,
            kelly_fraction=cfg.kelly_fraction,
            last_position_usd=last_position_usd,
            max_size_increase_factor=cfg.max_size_increase_factor,
        )
        if size_result.skip:
            continue
        allowed, _ = risk_manager.can_open_trade(size_result.position_usd)
        if not allowed:
            continue
        risk_manager.record_open(trip.coin, size_result.position_usd)
        sim = simulate_whale_round_trip_usd(
            trip,
            size_result.position_usd,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        gross_pnl = sim.realized_pnl + sim.fees
        risk_manager.record_close(trip.coin, size_result.position_usd, gross_pnl, sim.fees)
        last_position_usd = size_result.position_usd
        simulated += 1

    return simulated / len(trips)


def _pre_filtered_metrics(
    *,
    wallet: str,
    ordered: list[TradeFill],
    trips: list[WhaleRoundTrip],
    starting_equity: float,
    risk_manager: "PortfolioRiskManager",
    edge_filter_result: str,
    copyability_filter_result: str,
    fill_dir_quality: dict[str, Any] | None,
    exchange_return_pct: float,
    verdict: str,
) -> RoundTripCopyMetrics:
    from hyperion_pipeline.analytics.sizing_engine import build_sizing_engine_summary

    skip_reason = (
        edge_filter_result
        if edge_filter_result != "OK"
        else copyability_filter_result
    )
    sizing_summary = build_sizing_engine_summary(
        starting_equity=starting_equity,
        risk_manager=risk_manager,
        position_sizes=[],
        binding_counts={},
        skip_dust=0,
        skip_neg=0,
        skip_exposure=0,
        skip_cb=0,
        skip_ptl=0,
        ratchet_activations=0,
        edge_filter_result=edge_filter_result,
        copyability_filter_result=copyability_filter_result,
        total_fees=0.0,
        per_whale_allocation={},
        wallet_pre_filtered=True,
    )
    passed_edge = edge_filter_result == "OK"
    passed_copy = copyability_filter_result == "OK"
    return RoundTripCopyMetrics(
        wallet=wallet.lower(),
        fill_count=len(ordered),
        starting_equity=starting_equity,
        ending_equity=starting_equity,
        ending_equity_mtm=starting_equity,
        total_return_pct=0.0,
        total_return_mtm_pct=0.0,
        realized_pnl=0.0,
        total_fees_paid=0.0,
        total_slippage_cost=0.0,
        open_notional_usd=0.0,
        win_rate_pct=0.0,
        avg_pnl_per_trade=0.0,
        max_drawdown_pct=0.0,
        coins_traded=len({t.coin for t in trips}),
        first_fill=ordered[0].event_timestamp if ordered else None,
        last_fill=ordered[-1].event_timestamp if ordered else None,
        total_round_trips_detected=len(trips),
        round_trips_simulated=0,
        round_trips_skipped={},
        skip_counts_by_reason={skip_reason: 1} if skip_reason != "OK" else {},
        copyability_score=0.0,
        simulated_trips=[],
        skip_log=[],
        sizing_summary=sizing_summary,
        use_dynamic_sizing=True,
        verdict=verdict,
        fill_dir_quality=fill_dir_quality,
        passed_edge_filter=passed_edge,
        passed_copyability_gate=passed_copy,
    )


def _sizing_config_from_settings() -> "CopySizingConfig":
    from hyperion_pipeline.analytics.sizing_engine import CopySizingConfig
    from hyperion_pipeline.config.settings import get_settings

    s = get_settings()
    return CopySizingConfig(
        risk_per_trade_pct=s.risk_per_trade_pct,
        max_position_pct=s.max_position_pct,
        max_total_exposure_pct=s.max_total_exposure_pct,
        circuit_breaker_pct=s.circuit_breaker_pct,
        min_trade_usd=s.min_trade_usd,
        default_stop_pct=s.default_stop_pct,
        kelly_fraction=s.kelly_fraction,
        sharpe_window=s.sharpe_window,
        rebalance_every_n_trades=s.rebalance_every_n_trades,
        ema_span=s.ema_span,
        edge_cost_multiplier=s.edge_cost_multiplier,
        min_win_rate=s.min_win_rate,
        max_size_increase_factor=s.max_size_increase_factor,
        min_copyability=s.min_copyability,
        min_trips_detected=s.min_trips_detected,
    )


def run_round_trip_copy_simulation(
    wallet: str,
    fills: list[TradeFill],
    *,
    starting_equity: float = 10_000.0,
    copy_scale: float = 0.0001,
    max_equity_pct_per_fill: float = 0.02,
    min_trade_usd: float = 1.0,
    max_trade_usd: float | None = None,
    fee_bps: float = 4.0,
    slippage_bps: float = 6.0,
    coin_allowlist: set[str] | None = None,
    use_dynamic_sizing: bool | None = None,
    sizing_config: "CopySizingConfig | None" = None,
    exchange_return_pct: float | None = None,
    db_conn: Any = None,
) -> RoundTripCopyMetrics:
    """
    Detect whale round trips and simulate copies.

    With ``use_dynamic_sizing=True`` (default from ``COPY_USE_DYNAMIC_SIZING``), sizes each
    trip via ``sizing_engine.compute_position_size`` instead of ``copy_scale``.
    """

    from hyperion_pipeline.analytics.sizing_engine import (
        CopySizingConfig,
        PortfolioRiskManager,
        TradeResult,
        WhaleAllocationManager,
        build_sizing_engine_summary,
        build_wallet_profile_from_trips,
        compute_position_size,
        estimate_copyability_sample,
        estimate_stop_distance,
        passes_copyability_gate,
        passes_edge_filter,
    )
    from hyperion_pipeline.config.settings import get_settings

    if use_dynamic_sizing is None:
        use_dynamic_sizing = get_settings().copy_use_dynamic_sizing
    cfg = sizing_config or _sizing_config_from_settings()
    cfg.min_trade_usd = min(min_trade_usd, cfg.min_trade_usd) if min_trade_usd else cfg.min_trade_usd

    ordered = sorted(fills, key=lambda f: f.event_timestamp)
    if coin_allowlist:
        ordered = [f for f in ordered if f.coin in coin_allowlist]

    fill_dir_quality = check_missing_fill_dir(wallet, fills=ordered, db_conn=db_conn)

    trips = detect_whale_round_trips(ordered)
    skip_log: list[SkipLogEntry] = []
    skip_by_reason: Counter[str] = Counter()
    trips_skipped: Counter[str] = Counter()
    simulated: list[SimulatedRoundTrip] = []
    trip_pnls: list[float] = []

    risk_manager = PortfolioRiskManager(
        starting_equity,
        max_position_pct=cfg.max_position_pct,
        max_total_exposure_pct=cfg.max_total_exposure_pct,
        circuit_breaker_pct=cfg.circuit_breaker_pct,
    )
    alloc = WhaleAllocationManager(
        starting_equity,
        rolling_window=cfg.sharpe_window,
        min_allocation_pct=cfg.min_allocation_pct,
    )
    alloc._ensure(wallet.lower())

    if fill_dir_quality["recommendation"] == "exclude":
        return _pre_filtered_metrics(
            wallet=wallet,
            ordered=ordered,
            trips=trips,
            starting_equity=starting_equity,
            risk_manager=risk_manager,
            edge_filter_result="OK",
            copyability_filter_result="OK",
            fill_dir_quality=fill_dir_quality,
            exchange_return_pct=exchange_return_pct or 0.0,
            verdict="NO_DATA",
        )

    profile = build_wallet_profile_from_trips(trips)
    profile.copyability_score = estimate_copyability_sample(
        trips, equity=starting_equity, profile=profile, config=cfg
    )

    edge_filter_result = "OK"
    copyability_filter_result = "OK"
    gate_copyability = profile.copyability_score

    if use_dynamic_sizing:
        _ok, edge_filter_result = passes_edge_filter(
            profile.win_rate,
            profile.avg_win_pct,
            profile.avg_loss_pct,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            edge_cost_multiplier=cfg.edge_cost_multiplier,
            min_win_rate=cfg.min_win_rate,
        )
        if not _ok:
            return _pre_filtered_metrics(
                wallet=wallet,
                ordered=ordered,
                trips=trips,
                starting_equity=starting_equity,
                risk_manager=risk_manager,
                edge_filter_result=edge_filter_result,
                copyability_filter_result="OK",
                fill_dir_quality=fill_dir_quality,
                exchange_return_pct=exchange_return_pct or 0.0,
                verdict="FILTERED_EDGE",
            )

        gate_copyability = profile.copyability_score
        if len(trips) > 1000:
            forecast_copyability = forecast_dynamic_copyability(
                trips,
                profile=profile,
                cfg=cfg,
                starting_equity=starting_equity,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
            gate_copyability = min(gate_copyability, forecast_copyability)
        _copy_ok, copyability_filter_result = passes_copyability_gate(
            gate_copyability,
            min_copyability=cfg.min_copyability,
            n_trips_detected=len(trips),
            min_trips_detected=cfg.min_trips_detected,
        )
        if not _copy_ok:
            return _pre_filtered_metrics(
                wallet=wallet,
                ordered=ordered,
                trips=trips,
                starting_equity=starting_equity,
                risk_manager=risk_manager,
                edge_filter_result="OK",
                copyability_filter_result=copyability_filter_result,
                fill_dir_quality=fill_dir_quality,
                exchange_return_pct=exchange_return_pct or 0.0,
                verdict="FILTERED_COPYABILITY",
            )

    ws = alloc.wallets[wallet.lower()]
    ws.copyability_score = gate_copyability

    binding_counts: Counter[str] = Counter()
    position_sizes: list[float] = []
    skip_dust = skip_neg = skip_exposure = skip_cb = skip_ptl = 0
    ratchet_activations = 0
    last_position_usd: float | None = None
    trades_since_rebalance = 0

    def _reason_enum(name: str) -> SkipReason:
        try:
            return SkipReason(name)
        except ValueError:
            return SkipReason.DUST

    for idx, trip in enumerate(trips):
        if use_dynamic_sizing:
            stop_pct = estimate_stop_distance(
                trip, profile, default_stop_pct=cfg.default_stop_pct
            )
            slow_equity = risk_manager.equity_ema(span=cfg.ema_span)
            size_result = compute_position_size(
                equity=slow_equity,
                win_rate=profile.win_rate,
                avg_win_loss_ratio=profile.avg_win_loss_ratio,
                copyability=max(profile.copyability_score, 0.05),
                stop_distance_pct=stop_pct,
                risk_per_trade_pct=cfg.risk_per_trade_pct,
                max_position_pct=cfg.max_position_pct,
                min_trade_usd=cfg.min_trade_usd,
                kelly_fraction=cfg.kelly_fraction,
                last_position_usd=last_position_usd,
                max_size_increase_factor=cfg.max_size_increase_factor,
            )
            if size_result.binding_constraint == "ratchet":
                ratchet_activations += 1
            if size_result.skip:
                trips_skipped["INCOMPLETE_TRIP"] += 1
                reason = size_result.skip_reason or "DUST"
                skip_by_reason[reason] += 1
                if reason == "DUST":
                    skip_dust += 1
                elif reason == "NEGATIVE_EXPECTANCY":
                    skip_neg += 1
                skip_log.append(
                    SkipLogEntry(
                        event_key=trip.open_fills[0].event_key,
                        coin=trip.coin,
                        reason=_reason_enum(reason),
                        trip_index=idx,
                    )
                )
                continue

            allowed, block_reason = risk_manager.can_open_trade(size_result.position_usd)
            if not allowed:
                trips_skipped["INCOMPLETE_TRIP"] += 1
                skip_by_reason[block_reason] += 1
                if block_reason == "MAX_TOTAL_EXPOSURE":
                    skip_exposure += 1
                elif block_reason == "CIRCUIT_BREAKER":
                    skip_cb += 1
                elif block_reason == "POSITION_TOO_LARGE":
                    skip_ptl += 1
                skip_log.append(
                    SkipLogEntry(
                        event_key=trip.open_fills[0].event_key,
                        coin=trip.coin,
                        reason=_reason_enum(block_reason),
                        trip_index=idx,
                    )
                )
                continue

            binding_counts[size_result.binding_constraint] += 1
            position_sizes.append(size_result.position_usd)
            last_position_usd = size_result.position_usd
            risk_manager.record_open(trip.coin, size_result.position_usd)

            sim = simulate_whale_round_trip_usd(
                trip,
                size_result.position_usd,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
            gross_pnl = sim.realized_pnl + sim.fees
            risk_manager.record_close(
                trip.coin, size_result.position_usd, gross_pnl, sim.fees
            )
            trade = TradeResult(
                pnl_usd=sim.realized_pnl,
                pnl_pct=(sim.realized_pnl / starting_equity) * 100.0,
                fees=sim.fees,
                coin=trip.coin,
            )
            alloc.update_wallet(wallet, trade)
            trades_since_rebalance += 1
            if trades_since_rebalance >= cfg.rebalance_every_n_trades:
                alloc.rebalance(min_copyability=cfg.min_copyability)
                trades_since_rebalance = 0
        else:
            leg_skips, accepted = _trip_skip_reasons(
                trip,
                equity=risk_manager.equity,
                copy_scale=copy_scale,
                max_equity_pct_per_fill=max_equity_pct_per_fill,
                min_trade_usd=min_trade_usd,
                max_trade_usd=max_trade_usd,
            )
            if leg_skips:
                trips_skipped[SkipReason.INCOMPLETE_TRIP.value] += 1
                for fill, reason in leg_skips:
                    skip_by_reason[reason.value] += 1
                    skip_log.append(
                        SkipLogEntry(
                            event_key=fill.event_key,
                            coin=fill.coin,
                            reason=reason,
                            trip_index=idx,
                        )
                    )
                continue
            sim = simulate_whale_round_trip(
                trip, accepted, fee_bps=fee_bps, slippage_bps=slippage_bps
            )
            gross_pnl = sim.realized_pnl + sim.fees
            notional = sum(abs(f.size) * f.price for f in trip.open_fills[:1]) or 1.0
            leg_size = notional * copy_scale
            risk_manager.record_open(trip.coin, leg_size)
            risk_manager.record_close(trip.coin, leg_size, gross_pnl, sim.fees)

        simulated.append(sim)
        trip_pnls.append(sim.realized_pnl)

    equity = risk_manager.equity
    peak_equity = risk_manager.peak_equity
    max_dd = risk_manager.drawdown() * 100.0

    detected = len(trips)
    sim_count = len(simulated)
    copyability = sim_count / detected if detected else 0.0
    wins = sum(1 for p in trip_pnls if p > 0)
    win_rate = (100.0 * wins / sim_count) if sim_count else 0.0
    avg_pnl = sum(trip_pnls) / sim_count if sim_count else 0.0
    ret_pct = ((equity - starting_equity) / starting_equity) * 100.0 if starting_equity else 0.0
    # MTM is read-only — never feed into risk_manager.equity (reporting uses final cash equity).
    equity_mtm = equity
    open_notional = 0.0

    alloc_detail = (
        alloc.rebalance(min_copyability=cfg.min_copyability) if alloc.wallets else {}
    )
    per_whale_usd = {k: float(v["allocation_usd"]) for k, v in alloc_detail.items()}

    ex_ret = exchange_return_pct if exchange_return_pct is not None else 0.0
    verdict = classify_wallet(
        passed_edge=edge_filter_result == "OK",
        edge_reason=edge_filter_result,
        passed_copyability=copyability_filter_result == "OK",
        copy_reason=copyability_filter_result,
        dynamic_return_pct=ret_pct,
        exchange_return_pct=ex_ret,
        n_trips_detected=detected,
    )

    sizing_summary = build_sizing_engine_summary(
        starting_equity=starting_equity,
        risk_manager=risk_manager,
        position_sizes=position_sizes,
        binding_counts=dict(binding_counts),
        skip_dust=skip_dust,
        skip_neg=skip_neg,
        skip_exposure=skip_exposure,
        skip_cb=skip_cb,
        skip_ptl=skip_ptl,
        ratchet_activations=ratchet_activations,
        edge_filter_result=edge_filter_result,
        copyability_filter_result=copyability_filter_result,
        total_fees=sum(s.fees for s in simulated),
        per_whale_allocation=per_whale_usd,
    )

    return RoundTripCopyMetrics(
        wallet=wallet.lower(),
        fill_count=len(ordered),
        starting_equity=starting_equity,
        ending_equity=equity,
        ending_equity_mtm=equity_mtm,
        total_return_pct=ret_pct,
        total_return_mtm_pct=ret_pct,
        realized_pnl=equity - starting_equity,
        total_fees_paid=sum(s.fees for s in simulated),
        total_slippage_cost=sum(s.slippage for s in simulated),
        open_notional_usd=open_notional,
        win_rate_pct=win_rate,
        avg_pnl_per_trade=avg_pnl,
        max_drawdown_pct=max_dd,
        coins_traded=len({t.coin for t in trips}),
        first_fill=ordered[0].event_timestamp if ordered else None,
        last_fill=ordered[-1].event_timestamp if ordered else None,
        total_round_trips_detected=detected,
        round_trips_simulated=sim_count,
        round_trips_skipped=dict(trips_skipped),
        skip_counts_by_reason=dict(skip_by_reason),
        copyability_score=copyability,
        simulated_trips=simulated,
        skip_log=skip_log,
        sizing_summary=sizing_summary,
        use_dynamic_sizing=use_dynamic_sizing,
        verdict=verdict,
        fill_dir_quality=fill_dir_quality,
        passed_edge_filter=edge_filter_result == "OK",
        passed_copyability_gate=copyability_filter_result == "OK",
    )


# re-export for reports
def format_sizing_summary_for_metrics(metrics: RoundTripCopyMetrics) -> str:
    from hyperion_pipeline.analytics.sizing_engine import format_sizing_engine_summary

    if metrics.sizing_summary is None:
        return ""
    return format_sizing_engine_summary(metrics.sizing_summary)


def format_skip_breakdown(
    *,
    total_round_trips_detected: int,
    round_trips_simulated: int,
    round_trips_skipped: dict[str, int],
    skip_counts_by_reason: dict[str, int],
    copyability_score: float,
) -> str:
    """Format skip breakdown for markdown reports."""

    lines = [
        "### Round-trip copy — skip breakdown",
        "",
        f"- **Round trips detected:** {total_round_trips_detected:,}",
        f"- **Round trips simulated:** {round_trips_simulated:,}",
        f"- **Copyability score:** {copyability_score:.4f}",
        "",
        "| Skip reason | Count |",
        "| --- | ---: |",
    ]
    for reason, label in (
        (SkipReason.INCOMPLETE_TRIP, "trips"),
        (SkipReason.DUST, "fills"),
        (SkipReason.NEGATIVE_EXPECTANCY, "fills"),
        (SkipReason.MAX_TOTAL_EXPOSURE, "fills"),
        (SkipReason.CIRCUIT_BREAKER, "fills"),
        (SkipReason.POSITION_TOO_LARGE, "fills"),
        (SkipReason.RISK_CAP, "fills"),
        (SkipReason.NO_OPEN, "fills"),
    ):
        if reason == SkipReason.INCOMPLETE_TRIP:
            cnt = round_trips_skipped.get(reason.value, 0)
        else:
            cnt = skip_counts_by_reason.get(reason.value, 0)
        if cnt:
            lines.append(f"| {reason.value} | {cnt:,} {label} |")
    lines.append("")
    return "\n".join(lines)


def metrics_to_skip_report(metrics: RoundTripCopyMetrics) -> str:
    """Format skip breakdown from full metrics object."""

    return format_skip_breakdown(
        total_round_trips_detected=metrics.total_round_trips_detected,
        round_trips_simulated=metrics.round_trips_simulated,
        round_trips_skipped=metrics.round_trips_skipped,
        skip_counts_by_reason=metrics.skip_counts_by_reason,
        copyability_score=metrics.copyability_score,
    )
