"""
Account-relative position sizing for paper copy-trading.

Replaces fixed ``copy_scale`` leg sizing with Kelly + stop-distance sizing capped by
portfolio risk limits. Used only by the simulator layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

BindingConstraint = Literal["stop", "kelly", "max_cap", "dust", "ratchet"]


@dataclass(slots=True)
class CopySizingConfig:
    """Simulator sizing parameters (env-backed via ``Settings``)."""

    risk_per_trade_pct: float = 0.01
    max_position_pct: float = 0.15
    max_total_exposure_pct: float = 0.50
    circuit_breaker_pct: float = 0.20
    min_trade_usd: float = 1.0
    default_stop_pct: float = 0.03
    kelly_fraction: float = 0.25
    sharpe_window: int = 30
    rebalance_every_n_trades: int = 10
    sharpe_softmax_temperature: float = 3.0
    min_allocation_pct: float = 0.02
    ema_span: int = 20
    edge_cost_multiplier: float = 3.5
    min_win_rate: float = 0.45
    max_size_increase_factor: float = 1.05
    min_copyability: float = 0.30
    min_trips_detected: int = 20


@dataclass(slots=True)
class PositionSizeResult:
    position_usd: float
    kelly_raw: float
    kelly_adjusted: float
    stop_based_size: float
    binding_constraint: BindingConstraint
    skip: bool
    skip_reason: str | None


@dataclass(slots=True)
class TradeResult:
    """One closed paper round trip for risk / allocation tracking."""

    pnl_usd: float
    pnl_pct: float
    fees: float
    coin: str = ""


@dataclass(slots=True)
class SimulatorWalletProfile:
    """Whale tape statistics used for sizing (computed from detected round trips)."""

    win_rate: float
    avg_win_loss_ratio: float
    copyability_score: float
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_loss_pct_by_coin: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class WalletStats:
    address: str
    trade_history: list[TradeResult] = field(default_factory=list)
    rolling_window: int = 30
    copyability_score: float = 0.0


@dataclass(slots=True)
class SizingEngineSummary:
    final_equity: float
    peak_equity: float
    max_drawdown_pct: float
    circuit_breaker_triggered: bool
    trades_skipped_dust: int
    trades_skipped_neg_expectancy: int
    trades_skipped_exposure_cap: int
    trades_skipped_circuit_breaker: int
    trades_skipped_position_too_large: int
    avg_position_size_usd: float
    avg_position_pct_of_equity: float
    binding_constraint_breakdown: dict[str, int]
    per_whale_allocation_usd: dict[str, float]
    position_sizes_usd: list[float] = field(default_factory=list)
    slow_equity_used: bool = True
    edge_filter_result: str = "OK"
    copyability_filter_result: str = "OK"
    equity_firewall_assertions: int = 0
    ratchet_activations: int = 0
    estimated_fee_drag_pct: float = 0.0
    variance_drain_estimate_pct: float = 0.0
    wallet_pre_filtered: bool = False


def passes_edge_filter(
    win_rate: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    *,
    fee_bps: float = 4.0,
    slippage_bps: float = 6.0,
    edge_cost_multiplier: float = 2.0,
    min_win_rate: float = 0.45,
) -> tuple[bool, str]:
    """
    Pre-simulation gate: block wallets whose expected edge cannot cover round-trip costs.

    ``avg_win_pct`` / ``avg_loss_pct`` are fractional returns on whale notional per trip (e.g. 0.02 = 2%).
    """

    if win_rate < min_win_rate:
        return False, "WIN_RATE_TOO_LOW"

    round_trip_cost = (fee_bps + slippage_bps) / 10_000.0
    expected_edge = (win_rate * avg_win_pct) - ((1.0 - win_rate) * avg_loss_pct)
    threshold = round_trip_cost * edge_cost_multiplier
    if expected_edge < threshold:
        return False, "INSUFFICIENT_EDGE"
    return True, "OK"


def passes_copyability_gate(
    copyability: float,
    min_copyability: float = 0.30,
    n_trips_detected: int = 0,
    min_trips_detected: int = 20,
) -> tuple[bool, str]:
    """
    Pre-simulation gate: block wallets whose copied trip subset is too sparse.

    Low copyability means we mirror a random slice of the whale's activity — edge
    does not transfer to an unrepresentative subset.
    """

    if n_trips_detected < min_trips_detected:
        return False, "INSUFFICIENT_TRIPS"
    if copyability < min_copyability:
        return False, "LOW_COPYABILITY"
    return True, "OK"


def estimate_variance_drain_pct(
    avg_position_pct_of_equity: float, n_trades: int
) -> float:
    """Theoretical variance drain ≈ (position_pct² / 2) × n_trades × 100."""

    if n_trades <= 0:
        return 0.0
    frac = avg_position_pct_of_equity / 100.0
    return (frac**2 / 2.0) * n_trades * 100.0


def compute_position_size(
    *,
    equity: float,
    win_rate: float,
    avg_win_loss_ratio: float,
    copyability: float,
    stop_distance_pct: float,
    risk_per_trade_pct: float = 0.01,
    max_position_pct: float = 0.15,
    min_trade_usd: float = 1.0,
    kelly_fraction: float = 0.25,
    last_position_usd: float | None = None,
    max_size_increase_factor: float = 1.05,
) -> PositionSizeResult:
    """
    Size one round trip in USD notional using quarter-Kelly and stop-based risk.

    ``equity`` should be slow equity (EMA), not live equity, to avoid variance drain.
    """

    if avg_win_loss_ratio <= 0:
        return PositionSizeResult(
            position_usd=0.0,
            kelly_raw=0.0,
            kelly_adjusted=0.0,
            stop_based_size=0.0,
            binding_constraint="dust",
            skip=True,
            skip_reason="NEGATIVE_EXPECTANCY",
        )

    kelly_raw = (win_rate * avg_win_loss_ratio - (1.0 - win_rate)) / avg_win_loss_ratio
    if kelly_raw <= 0:
        return PositionSizeResult(
            position_usd=0.0,
            kelly_raw=kelly_raw,
            kelly_adjusted=0.0,
            stop_based_size=0.0,
            binding_constraint="dust",
            skip=True,
            skip_reason="NEGATIVE_EXPECTANCY",
        )

    kelly_adjusted = kelly_raw * kelly_fraction
    copyability_eff = max(copyability, 0.2)
    confidence_weight = copyability_eff * (1.0 if win_rate >= 0.5 else 0.6)
    kelly_size = equity * kelly_adjusted * confidence_weight

    risk_usd = equity * risk_per_trade_pct
    if stop_distance_pct > 0:
        stop_based_size = risk_usd / stop_distance_pct
    else:
        stop_based_size = kelly_size

    max_cap = equity * max_position_pct
    candidates: list[tuple[float, BindingConstraint]] = [
        (stop_based_size, "stop"),
        (kelly_size, "kelly"),
        (max_cap, "max_cap"),
    ]
    position_usd, binding = min(candidates, key=lambda x: x[0])

    if last_position_usd is not None and last_position_usd > 0:
        ratcheted = min(position_usd, last_position_usd * max_size_increase_factor)
        if ratcheted < position_usd - 1e-9:
            binding = "ratchet"
        position_usd = ratcheted

    if position_usd < min_trade_usd:
        return PositionSizeResult(
            position_usd=position_usd,
            kelly_raw=kelly_raw,
            kelly_adjusted=kelly_adjusted,
            stop_based_size=stop_based_size,
            binding_constraint="dust",
            skip=True,
            skip_reason="DUST",
        )

    return PositionSizeResult(
        position_usd=position_usd,
        kelly_raw=kelly_raw,
        kelly_adjusted=kelly_adjusted,
        stop_based_size=stop_based_size,
        binding_constraint=binding,
        skip=False,
        skip_reason=None,
    )


class PortfolioRiskManager:
    """In-memory portfolio guardrails for one simulation run."""

    def __init__(
        self,
        starting_equity: float,
        *,
        max_position_pct: float = 0.15,
        max_total_exposure_pct: float = 0.50,
        circuit_breaker_pct: float = 0.20,
    ) -> None:
        self.equity = starting_equity
        self.peak_equity = starting_equity
        self._last_closed_equity = starting_equity
        self._equity_snapshots: list[float] = [starting_equity]
        self.max_position_pct = max_position_pct
        self.max_total_exposure_pct = max_total_exposure_pct
        self.circuit_breaker_pct = circuit_breaker_pct
        self.open_positions: dict[str, float] = {}
        self.trade_history: list[TradeResult] = []
        self.circuit_breaker_triggered = False
        self.equity_firewall_assertions = 0

    def equity_ema(self, span: int = 20) -> float:
        """
        EMA of equity over closed trades. Returns raw equity if fewer than 3 snapshots.

        Uses alpha = 2 / (span + 1).
        """

        if len(self._equity_snapshots) < 3:
            return self.equity
        alpha = 2.0 / (span + 1)
        ema = self._equity_snapshots[0]
        for x in self._equity_snapshots[1:]:
            ema = alpha * x + (1.0 - alpha) * ema
        return ema

    def total_open_exposure(self) -> float:
        return sum(self.open_positions.values())

    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)

    def is_circuit_broken(self) -> bool:
        if self.drawdown() > self.circuit_breaker_pct:
            self.circuit_breaker_triggered = True
        return self.circuit_breaker_triggered

    def can_open_trade(self, size_usd: float) -> tuple[bool, str]:
        if self.is_circuit_broken():
            return False, "CIRCUIT_BREAKER"
        if self.equity > 0 and size_usd / self.equity > self.max_position_pct:
            return False, "POSITION_TOO_LARGE"
        if self.total_open_exposure() + size_usd > self.equity * self.max_total_exposure_pct:
            return False, "MAX_TOTAL_EXPOSURE"
        return True, "OK"

    def record_open(self, coin: str, size_usd: float) -> None:
        """Update exposure only — must not change ``self.equity`` (sizing firewall)."""

        if self.equity != self._last_closed_equity:
            self.equity_firewall_assertions += 1
            raise AssertionError(
                "Equity was modified outside of record_close(). "
                "Check for MTM or open-leg PnL leaking into sizing."
            )
        self.open_positions[coin] = self.open_positions.get(coin, 0.0) + size_usd

    def record_close(
        self,
        coin: str,
        size_usd: float,
        realized_pnl: float,
        fees: float,
    ) -> None:
        """Only place live equity changes. ``realized_pnl`` is gross PnL before fees."""

        prev = self.open_positions.get(coin, 0.0)
        remaining = max(0.0, prev - size_usd)
        if remaining <= 1e-9:
            self.open_positions.pop(coin, None)
        else:
            self.open_positions[coin] = remaining

        self.equity += realized_pnl - fees
        self._equity_snapshots.append(self.equity)
        self._last_closed_equity = self.equity
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        eq_before = max(self.equity - realized_pnl + fees, 1e-9)
        pnl_pct = ((realized_pnl - fees) / eq_before) * 100.0
        self.trade_history.append(
            TradeResult(pnl_usd=realized_pnl - fees, pnl_pct=pnl_pct, fees=fees, coin=coin)
        )


class WhaleAllocationManager:
    """Softmax capital split across whales (self-evolution layer)."""

    def __init__(
        self,
        total_budget: float,
        *,
        rolling_window: int = 30,
        temperature: float = 3.0,
        min_allocation_pct: float = 0.02,
    ) -> None:
        self.total_budget = total_budget
        self.rolling_window = rolling_window
        self.temperature = temperature
        self.min_allocation_pct = min_allocation_pct
        self.wallets: dict[str, WalletStats] = {}
        self._allocations: dict[str, float] = {}

    def _ensure(self, address: str) -> WalletStats:
        key = address.lower()
        if key not in self.wallets:
            self.wallets[key] = WalletStats(address=key, rolling_window=self.rolling_window)
        return self.wallets[key]

    def update_wallet(self, address: str, trade: TradeResult) -> None:
        ws = self._ensure(address)
        ws.trade_history.append(trade)
        if len(ws.trade_history) > ws.rolling_window:
            ws.trade_history = ws.trade_history[-ws.rolling_window :]

    def compute_sharpe(self, address: str) -> float:
        ws = self._ensure(address)
        trades = ws.trade_history
        if len(trades) < 5:
            return 0.0
        returns = [t.pnl_pct for t in trades]
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std = math.sqrt(var)
        sharpe = mean_r / (std + 1e-9)
        return max(-2.0, min(2.0, sharpe))

    def rebalance(
        self,
        *,
        min_copyability: float = 0.30,
    ) -> dict[str, dict[str, Any]]:
        """Softmax budget split; Sharpe weighted by copyability. Below-floor wallets get 0%."""

        if not self.wallets:
            return {}

        addresses = list(self.wallets.keys())
        sharpes = {a: self.compute_sharpe(a) for a in addresses}
        weighted: dict[str, float] = {}
        status_by_addr: dict[str, str] = {}

        for addr in addresses:
            ws = self.wallets[addr]
            sharpe = sharpes[addr]
            copyability = ws.copyability_score
            if copyability < min_copyability:
                weighted[addr] = 0.0
                status_by_addr[addr] = "below_copyability_floor"
            else:
                weighted[addr] = max(sharpe, 0.0) * copyability
                status_by_addr[addr] = "active"

        active = [a for a in addresses if weighted[a] > 0.0]
        result: dict[str, dict[str, Any]] = {}

        if not active:
            for addr in addresses:
                result[addr] = {
                    "allocation_usd": 0.0,
                    "sharpe": sharpes[addr],
                    "copyability": self.wallets[addr].copyability_score,
                    "weighted_score": weighted[addr],
                    "status": status_by_addr[addr],
                }
            self._allocations = {a: 0.0 for a in addresses}
            return result

        scaled = [weighted[a] * self.temperature for a in active]
        max_s = max(scaled)
        exp_w = [math.exp(s - max_s) for s in scaled]
        total_exp = sum(exp_w)
        raw_active = {a: w / total_exp for a, w in zip(active, exp_w, strict=True)}

        floor = self.min_allocation_pct
        n_active = len(active)
        min_total = floor * n_active
        usd_by_addr: dict[str, float] = {a: 0.0 for a in addresses}

        if min_total >= 1.0:
            per = self.total_budget / n_active
            for a in active:
                usd_by_addr[a] = per
        else:
            remainder = 1.0 - min_total
            for a in active:
                usd_by_addr[a] = self.total_budget * (floor + remainder * raw_active[a])

        for addr in addresses:
            result[addr] = {
                "allocation_usd": usd_by_addr[addr],
                "sharpe": sharpes[addr],
                "copyability": self.wallets[addr].copyability_score,
                "weighted_score": weighted[addr],
                "status": status_by_addr[addr],
            }

        self._allocations = {a: result[a]["allocation_usd"] for a in addresses}
        return result

    def get_wallet_budget(self, address: str, *, min_copyability: float = 0.30) -> float:
        key = address.lower()
        if key not in self._allocations and self.wallets:
            self.rebalance(min_copyability=min_copyability)
        return self._allocations.get(key, 0.0)


def estimate_stop_distance(
    trip: object,
    wallet_profile: SimulatorWalletProfile,
    *,
    default_stop_pct: float = 0.03,
) -> float:
    """Stop distance as fraction of entry price."""

    stop_px = getattr(trip, "stop_price", None)
    entry_px = None
    open_fills = getattr(trip, "open_fills", None)
    if open_fills:
        entry_px = open_fills[0].price
    if stop_px is not None and entry_px and entry_px > 0:
        return abs(float(entry_px) - float(stop_px)) / float(entry_px)

    coin = getattr(trip, "coin", "")
    if coin and coin in wallet_profile.avg_loss_pct_by_coin:
        return wallet_profile.avg_loss_pct_by_coin[coin]

    return default_stop_pct


def _trip_return_pct(trip: object) -> float | None:
    open_fills = getattr(trip, "open_fills", [])
    if not open_fills:
        return None
    o = open_fills[0]
    notional = abs(o.size) * o.price
    if notional <= 0:
        return None
    pnl = float(getattr(trip, "gross_pnl_usd", 0.0) or 0.0)
    return pnl / notional


def build_wallet_profile_from_trips(trips: list[object]) -> SimulatorWalletProfile:
    """Derive whale win rate, payoff ratio, and per-coin loss % from tape round trips."""

    pnls: list[float] = []
    ret_pcts: list[float] = []
    by_coin_loss: dict[str, list[float]] = {}

    for t in trips:
        pnl = float(getattr(t, "gross_pnl_usd", 0.0) or 0.0)
        pnls.append(pnl)
        rp = _trip_return_pct(t)
        if rp is not None:
            ret_pcts.append(rp)
        coin = getattr(t, "coin", "")
        open_fills = getattr(t, "open_fills", [])
        if pnl < 0 and open_fills and open_fills[0].price > 0:
            loss_pct = abs(pnl) / (abs(open_fills[0].size) * open_fills[0].price + 1e-12)
            by_coin_loss.setdefault(coin, []).append(min(loss_pct, 0.5))

    if not pnls:
        return SimulatorWalletProfile(
            win_rate=0.5,
            avg_win_loss_ratio=1.0,
            copyability_score=0.1,
            avg_win_pct=0.0,
            avg_loss_pct=0.01,
        )

    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls)
    avg_win = sum(wins) / len(wins) if wins else 1.0
    avg_loss = sum(losses) / len(losses) if losses else 1.0
    avg_rr = avg_win / avg_loss if avg_loss > 0 else 1.0

    win_rets = [r for r in ret_pcts if r > 0]
    loss_rets = [abs(r) for r in ret_pcts if r < 0]
    avg_win_pct = sum(win_rets) / len(win_rets) if win_rets else 0.01
    avg_loss_pct = sum(loss_rets) / len(loss_rets) if loss_rets else 0.01

    avg_loss_pct_by_coin = {c: sum(v) / len(v) for c, v in by_coin_loss.items() if v}

    return SimulatorWalletProfile(
        win_rate=win_rate,
        avg_win_loss_ratio=max(avg_rr, 0.01),
        copyability_score=0.1,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        avg_loss_pct_by_coin=avg_loss_pct_by_coin,
    )


def estimate_copyability_sample(
    trips: list[object],
    *,
    equity: float,
    profile: SimulatorWalletProfile,
    config: CopySizingConfig,
    sample_size: int = 400,
) -> float:
    """Fraction of trips that pass sizing pre-check at ``equity`` (for Kelly confidence)."""

    if not trips:
        return 0.0
    sample = trips[:sample_size] if len(trips) > sample_size else trips
    ok = 0
    for trip in sample:
        stop = estimate_stop_distance(trip, profile, default_stop_pct=config.default_stop_pct)
        r = compute_position_size(
            equity=equity,
            win_rate=profile.win_rate,
            avg_win_loss_ratio=profile.avg_win_loss_ratio,
            copyability=max(profile.copyability_score, 0.05),
            stop_distance_pct=stop,
            risk_per_trade_pct=config.risk_per_trade_pct,
            max_position_pct=config.max_position_pct,
            min_trade_usd=config.min_trade_usd,
            kelly_fraction=config.kelly_fraction,
            max_size_increase_factor=config.max_size_increase_factor,
        )
        if not r.skip:
            ok += 1
    return ok / len(sample)


def build_sizing_engine_summary(
    *,
    starting_equity: float,
    risk_manager: PortfolioRiskManager,
    position_sizes: list[float],
    binding_counts: dict[str, int],
    skip_dust: int,
    skip_neg: int,
    skip_exposure: int,
    skip_cb: int,
    skip_ptl: int,
    ratchet_activations: int,
    edge_filter_result: str,
    total_fees: float,
    per_whale_allocation: dict[str, float],
    copyability_filter_result: str = "OK",
    wallet_pre_filtered: bool = False,
) -> SizingEngineSummary:
    """Assemble report metrics (MTM is read-only — never fed into ``risk_manager.equity``)."""

    avg_pos = sum(position_sizes) / len(position_sizes) if position_sizes else 0.0
    avg_pos_pct = (
        100.0 * sum(p / max(starting_equity, 1e-9) for p in position_sizes) / len(position_sizes)
        if position_sizes
        else 0.0
    )
    n_trades = len(position_sizes)
    return SizingEngineSummary(
        final_equity=risk_manager.equity,
        peak_equity=risk_manager.peak_equity,
        max_drawdown_pct=risk_manager.drawdown() * 100.0,
        circuit_breaker_triggered=risk_manager.circuit_breaker_triggered,
        trades_skipped_dust=skip_dust,
        trades_skipped_neg_expectancy=skip_neg,
        trades_skipped_exposure_cap=skip_exposure,
        trades_skipped_circuit_breaker=skip_cb,
        trades_skipped_position_too_large=skip_ptl,
        avg_position_size_usd=avg_pos,
        avg_position_pct_of_equity=avg_pos_pct,
        binding_constraint_breakdown=dict(binding_counts),
        per_whale_allocation_usd=per_whale_allocation,
        position_sizes_usd=list(position_sizes),
        slow_equity_used=True,
        edge_filter_result=edge_filter_result,
        copyability_filter_result=copyability_filter_result,
        equity_firewall_assertions=risk_manager.equity_firewall_assertions,
        ratchet_activations=ratchet_activations,
        estimated_fee_drag_pct=(total_fees / starting_equity) * 100.0 if starting_equity else 0.0,
        variance_drain_estimate_pct=estimate_variance_drain_pct(avg_pos_pct, n_trades),
        wallet_pre_filtered=wallet_pre_filtered,
    )


def format_sizing_engine_summary(summary: SizingEngineSummary) -> str:
    """Markdown section for sizing engine metrics."""

    lines = [
        "### Sizing engine summary",
        "",
        f"- **Final equity:** ${summary.final_equity:,.2f}",
        f"- **Peak equity:** ${summary.peak_equity:,.2f}",
        f"- **Max drawdown:** {summary.max_drawdown_pct:.2f}%",
        f"- **Circuit breaker triggered:** {summary.circuit_breaker_triggered}",
        f"- **Slow equity (EMA) used for sizing:** {summary.slow_equity_used}",
        f"- **Edge filter:** {summary.edge_filter_result}",
        f"- **Copyability gate:** {summary.copyability_filter_result}",
        f"- **Equity firewall assertions:** {summary.equity_firewall_assertions}",
        f"- **Ratchet activations:** {summary.ratchet_activations}",
        f"- **Estimated fee drag:** {summary.estimated_fee_drag_pct:.3f}% of starting equity",
        f"- **Variance drain estimate:** {summary.variance_drain_estimate_pct:.3f}%",
        f"- **Avg position size:** ${summary.avg_position_size_usd:,.2f} "
        f"({summary.avg_position_pct_of_equity:.2f}% of starting equity)",
        "",
    ]
    if summary.wallet_pre_filtered:
        lines.extend(["### Wallets filtered pre-simulation", ""])
        if summary.edge_filter_result != "OK":
            lines.append(f"- Edge filter: **{summary.edge_filter_result}**")
        if summary.copyability_filter_result != "OK":
            lines.append(f"- Copyability gate: **{summary.copyability_filter_result}**")
        lines.append("")
    lines.extend(
        [
            "| Skip reason | Count |",
            "| --- | ---: |",
            f"| DUST | {summary.trades_skipped_dust} |",
            f"| NEGATIVE_EXPECTANCY | {summary.trades_skipped_neg_expectancy} |",
            f"| MAX_TOTAL_EXPOSURE | {summary.trades_skipped_exposure_cap} |",
            f"| CIRCUIT_BREAKER | {summary.trades_skipped_circuit_breaker} |",
            f"| POSITION_TOO_LARGE | {summary.trades_skipped_position_too_large} |",
            "",
            "**Binding constraint breakdown**",
            "",
            "| Constraint | Count |",
            "| --- | ---: |",
        ]
    )
    for k, v in sorted(summary.binding_constraint_breakdown.items()):
        lines.append(f"| {k} | {v} |")
    if summary.position_sizes_usd:
        ps = summary.position_sizes_usd
        lines.append(
            f"- **Position size range:** ${min(ps):,.2f} – ${max(ps):,.2f} ({len(ps):,} trades)"
        )
    if summary.per_whale_allocation_usd:
        lines.extend(["", "**Per-whale allocation (USD)**", ""])
        for w, usd in summary.per_whale_allocation_usd.items():
            lines.append(f"- `{w}`: ${usd:,.2f}")
    lines.append("")
    return "\n".join(lines)
