"""
Offline paper copy-trading backtests for Hyperliquid whale wallets.

Trust hierarchy for wallet evaluation (most → least reliable):
  1. ``exchange_closed_pnl_scaled`` — HL ``closedPnl`` on close rows × copy_scale.
  2. ``fifo_scaled`` — FIFO Open/Close on full tape, scale net PnL.
  3. ``round_trip_copy`` — complete trips only (dust/risk filters on every leg).
  4. ``fill_follow_legacy`` — per-fill mirror; bot-realism stress test only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from collections import Counter, defaultdict

from hyperion_pipeline.models.pydantic_domain import TradeFill


@dataclass(slots=True)
class CoinPosition:
    signed_size: float = 0.0
    avg_entry: float = 0.0

    @property
    def is_flat(self) -> bool:
        return abs(self.signed_size) < 1e-12


@dataclass(slots=True)
class PaperTradeRecord:
    coin: str
    side: str
    whale_size: float
    whale_price: float
    paper_size: float
    paper_price: float
    realized_pnl: float
    fees: float
    slippage_cost: float
    equity_after: float
    timestamp: datetime
    event_key: str


@dataclass(slots=True)
class CopyPaperResult:
    wallet: str
    fill_count: int
    trade_count: int
    starting_equity: float
    ending_equity: float
    ending_equity_mtm: float
    total_return_pct: float
    total_return_mtm_pct: float
    open_notional_usd: float
    win_rate_pct: float
    total_fees: float
    total_slippage: float
    max_drawdown_pct: float
    coins_traded: int
    first_fill: datetime | None
    last_fill: datetime | None
    trades: list[PaperTradeRecord] = field(default_factory=list)
    total_round_trips_detected: int = 0
    round_trips_simulated: int = 0
    round_trips_skipped: dict[str, int] = field(default_factory=dict)
    skip_counts_by_reason: dict[str, int] = field(default_factory=dict)
    copyability_score: float = 0.0
    avg_pnl_per_trade: float = 0.0
    realized_pnl: float = 0.0
    sizing_summary: object | None = None
    use_dynamic_sizing: bool = False
    verdict: str = ""
    fill_dir_quality: dict[str, Any] | None = None
    exchange_return_pct: float = 0.0


@dataclass(slots=True)
class CopyPaperBenchmarks:
    """
    Side-by-side PnL views for the same wallet tape.

    ``round_trip`` — primary paper simulator (complete trips only).
    ``fifo_slippage`` / ``exchange_closed`` — trust hierarchy validation (see module docstring).
    ``fill_follow_legacy`` — naive per-fill mirror for bot-realism only.
    """

    round_trip: CopyPaperResult
    fill_follow_legacy: CopyPaperResult
    fifo_slippage_return_pct: float
    fifo_slippage_ending_equity: float
    fifo_closed_trips: int
    exchange_closed_return_pct: float
    exchange_closed_ending_equity: float
    fills_with_dir_pct: float
    mirror_coverage_pct: float

    @property
    def fill_follow(self) -> CopyPaperResult:
        """Alias: primary copy sim (round-trip). Kept for callers expecting ``fill_follow``."""

        return self.round_trip


def exchange_closed_pnl_scaled(fills: list[TradeFill], copy_scale: float) -> float:
    """Sum HL ``closedPnl`` on Close-* rows, scaled — linear copy-size assumption."""

    total = 0.0
    for f in fills:
        if not f.fill_dir or not f.fill_dir.lower().startswith("close"):
            continue
        if f.closed_pnl_usd is not None:
            total += float(f.closed_pnl_usd) * copy_scale
    return total


def run_fifo_scaled_copy_paper(
    fills: list[TradeFill],
    *,
    starting_equity: float,
    copy_scale: float,
    slippage_bps: float,
) -> tuple[float, int]:
    """
    FIFO round trips on full whale tape, then scale net PnL per trip by ``copy_scale``.

    Fees in each trip are whale-sized; scaling net PnL approximates paper economics better
    than mirroring arbitrary subsets of fills.
    """

    from hyperion_pipeline.analytics.paper_replay.replay_core import slip_leg_pnl
    from hyperion_pipeline.analytics.paper_replay.round_trips import fifo_closed_round_trips
    from hyperion_pipeline.analytics.paper_replay.slippage import LinearBpsSlippage

    trips = fifo_closed_round_trips(fills, realized_only=True)
    slip = LinearBpsSlippage(slippage_bps)
    net = sum(slip_leg_pnl(t, slip).net_pnl_usd * copy_scale for t in trips)
    ending = starting_equity + net
    ret_pct = ((ending - starting_equity) / starting_equity) * 100.0 if starting_equity else 0.0
    return ret_pct, len(trips)


def _metrics_to_copy_result(metrics) -> CopyPaperResult:
    from hyperion_pipeline.analytics.copy_paper_simulator import RoundTripCopyMetrics
    return CopyPaperResult(
        wallet=metrics.wallet,
        fill_count=metrics.fill_count,
        trade_count=metrics.round_trips_simulated,
        starting_equity=metrics.starting_equity,
        ending_equity=metrics.ending_equity,
        ending_equity_mtm=metrics.ending_equity_mtm,
        total_return_pct=metrics.total_return_pct,
        total_return_mtm_pct=metrics.total_return_mtm_pct,
        open_notional_usd=metrics.open_notional_usd,
        win_rate_pct=metrics.win_rate_pct,
        total_fees=metrics.total_fees_paid,
        total_slippage=metrics.total_slippage_cost,
        max_drawdown_pct=metrics.max_drawdown_pct,
        coins_traded=metrics.coins_traded,
        first_fill=metrics.first_fill,
        last_fill=metrics.last_fill,
        total_round_trips_detected=metrics.total_round_trips_detected,
        round_trips_simulated=metrics.round_trips_simulated,
        round_trips_skipped=metrics.round_trips_skipped,
        skip_counts_by_reason=metrics.skip_counts_by_reason,
        copyability_score=metrics.copyability_score,
        avg_pnl_per_trade=metrics.avg_pnl_per_trade,
        realized_pnl=metrics.realized_pnl,
        sizing_summary=metrics.sizing_summary,
        use_dynamic_sizing=metrics.use_dynamic_sizing,
        verdict=metrics.verdict,
        fill_dir_quality=metrics.fill_dir_quality,
        exchange_return_pct=getattr(metrics, "exchange_return_pct", 0.0),
    )


def run_copy_paper_benchmarks(
    wallet: str,
    fills: list[TradeFill],
    *,
    starting_equity: float = 10_000.0,
    copy_scale: float = 0.0001,
    fee_bps: float = 4.0,
    slippage_bps: float = 6.0,
    coin_allowlist: set[str] | None = None,
    use_dynamic_sizing: bool | None = None,
) -> CopyPaperBenchmarks:
    """Run round-trip copy sim plus FIFO / exchange benchmarks on one tape."""

    rt = run_copy_paper_backtest(
        wallet,
        fills,
        starting_equity=starting_equity,
        copy_scale=copy_scale,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        coin_allowlist=coin_allowlist,
        use_dynamic_sizing=use_dynamic_sizing,
    )
    legacy = run_fill_follow_legacy(
        wallet,
        fills,
        starting_equity=starting_equity,
        copy_scale=copy_scale,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        coin_allowlist=coin_allowlist,
    )
    fifo_ret, fifo_n = run_fifo_scaled_copy_paper(
        fills,
        starting_equity=starting_equity,
        copy_scale=copy_scale,
        slippage_bps=slippage_bps,
    )
    ex_net = exchange_closed_pnl_scaled(fills, copy_scale)
    ex_end = starting_equity + ex_net
    ex_ret = ((ex_end - starting_equity) / starting_equity) * 100.0 if starting_equity else 0.0
    n = len(fills) or 1
    with_dir = sum(1 for f in fills if f.fill_dir)
    return CopyPaperBenchmarks(
        round_trip=rt,
        fill_follow_legacy=legacy,
        fifo_slippage_return_pct=fifo_ret,
        fifo_slippage_ending_equity=starting_equity * (1.0 + fifo_ret / 100.0),
        fifo_closed_trips=fifo_n,
        exchange_closed_return_pct=ex_ret,
        exchange_closed_ending_equity=ex_end,
        fills_with_dir_pct=100.0 * with_dir / n,
        mirror_coverage_pct=100.0 * legacy.trade_count / n if fills else 0.0,
    )


def _slip_price(side: str, price: float, slippage_bps: float, is_entry: bool) -> float:
    """Worsen fill price for paper leg (buy pays more, sell receives less)."""
    mult = slippage_bps / 10_000.0
    if side == "buy":
        return price * (1.0 + mult) if is_entry else price * (1.0 - mult)
    return price * (1.0 - mult) if is_entry else price * (1.0 + mult)


def _fee(notional: float, fee_bps: float) -> float:
    return abs(notional) * fee_bps / 10_000.0


def run_copy_paper_backtest(
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
) -> CopyPaperResult:
    """
    Round-trip copy simulation: only complete whale trips where every leg passes filters.

    See ``run_fill_follow_legacy`` for naive per-fill mirroring (not for wallet ranking).
    """
    from hyperion_pipeline.analytics.copy_paper_simulator import run_round_trip_copy_simulation

    ex_net = exchange_closed_pnl_scaled(fills, copy_scale)
    ex_ret = ((ex_net / starting_equity) * 100.0) if starting_equity else 0.0
    metrics = run_round_trip_copy_simulation(
        wallet,
        fills,
        starting_equity=starting_equity,
        copy_scale=copy_scale,
        max_equity_pct_per_fill=max_equity_pct_per_fill,
        min_trade_usd=min_trade_usd,
        max_trade_usd=max_trade_usd,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        coin_allowlist=coin_allowlist,
        use_dynamic_sizing=use_dynamic_sizing,
        exchange_return_pct=ex_ret,
    )
    result = _metrics_to_copy_result(metrics)
    result.exchange_return_pct = ex_ret
    return result


def run_fill_follow_legacy(
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
) -> CopyPaperResult:
    """
    Legacy per-fill mirror — can desync when most fills are skipped (dust / risk cap).

    Do not use for wallet quality decisions; see trust hierarchy in module docstring.
    """
    ordered = sorted(fills, key=lambda f: f.event_timestamp)
    if coin_allowlist:
        ordered = [f for f in ordered if f.coin in coin_allowlist]

    positions: dict[str, CoinPosition] = {}
    last_price: dict[str, float] = {}
    equity = starting_equity
    peak_equity = equity
    max_dd = 0.0
    trades: list[PaperTradeRecord] = []
    closed_pnls: list[float] = []

    for fill in ordered:
        last_price[fill.coin] = fill.price
        if fill.price <= 0 or fill.size <= 0:
            continue

        if equity < min_trade_usd:
            continue

        whale_notional = abs(fill.size) * fill.price
        cap_notional = equity * max_equity_pct_per_fill
        if max_trade_usd is not None:
            cap_notional = min(cap_notional, max_trade_usd)
        target_notional = min(whale_notional * copy_scale, cap_notional)
        if target_notional < min_trade_usd:
            continue

        paper_size = target_notional / fill.price
        delta = paper_size if fill.side == "buy" else -paper_size
        pos = positions.setdefault(fill.coin, CoinPosition())

        entry_price = _slip_price(fill.side, fill.price, slippage_bps, is_entry=True)
        fee_paid = _fee(paper_size * entry_price, fee_bps)
        realized = 0.0
        slip_cost = abs(entry_price - fill.price) * paper_size

        if pos.is_flat:
            pos.signed_size = delta
            pos.avg_entry = entry_price
        elif (pos.signed_size > 0 and delta > 0) or (pos.signed_size < 0 and delta < 0):
            # add to position — VWAP entry
            new_size = pos.signed_size + delta
            pos.avg_entry = (
                (pos.avg_entry * abs(pos.signed_size) + entry_price * abs(delta)) / abs(new_size)
            )
            pos.signed_size = new_size
        else:
            # reduce / flip
            close_size = min(abs(delta), abs(pos.signed_size))
            exit_price = _slip_price(fill.side, fill.price, slippage_bps, is_entry=False)
            slip_cost += abs(exit_price - fill.price) * close_size
            if pos.signed_size > 0:
                realized = (exit_price - pos.avg_entry) * close_size
            else:
                realized = (pos.avg_entry - exit_price) * close_size
            fee_paid += _fee(close_size * exit_price, fee_bps)
            pos.signed_size += delta
            if abs(pos.signed_size) < 1e-12:
                pos.signed_size = 0.0
                pos.avg_entry = 0.0
            elif abs(pos.signed_size) > close_size:
                # flipped — new leg at exit price
                pos.avg_entry = exit_price

        equity += realized - fee_paid
        if abs(realized) > 1e-12:
            closed_pnls.append(realized)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_dd = max(max_dd, ((peak_equity - equity) / peak_equity) * 100.0)

        trades.append(
            PaperTradeRecord(
                coin=fill.coin,
                side=fill.side,
                whale_size=fill.size,
                whale_price=fill.price,
                paper_size=paper_size,
                paper_price=entry_price,
                realized_pnl=realized,
                fees=fee_paid,
                slippage_cost=slip_cost,
                equity_after=equity,
                timestamp=fill.event_timestamp,
                event_key=fill.event_key,
            )
        )

    unrealized = 0.0
    open_notional = 0.0
    for coin, pos in positions.items():
        if pos.is_flat:
            continue
        px = last_price.get(coin, pos.avg_entry)
        open_notional += abs(pos.signed_size) * px
        if pos.signed_size > 0:
            unrealized += (px - pos.avg_entry) * pos.signed_size
        else:
            unrealized += (pos.avg_entry - px) * abs(pos.signed_size)

    equity_mtm = equity + unrealized
    wins = sum(1 for p in closed_pnls if p > 0)
    win_rate = (100.0 * wins / len(closed_pnls)) if closed_pnls else 0.0
    ret_pct = ((equity - starting_equity) / starting_equity) * 100.0 if starting_equity else 0.0
    ret_mtm_pct = ((equity_mtm - starting_equity) / starting_equity) * 100.0 if starting_equity else 0.0

    return CopyPaperResult(
        wallet=wallet.lower(),
        fill_count=len(ordered),
        trade_count=len(trades),
        starting_equity=starting_equity,
        ending_equity=equity,
        ending_equity_mtm=equity_mtm,
        total_return_pct=ret_pct,
        total_return_mtm_pct=ret_mtm_pct,
        open_notional_usd=open_notional,
        win_rate_pct=win_rate,
        total_fees=sum(t.fees for t in trades),
        total_slippage=sum(t.slippage_cost for t in trades),
        max_drawdown_pct=max_dd,
        coins_traded=len(positions),
        first_fill=ordered[0].event_timestamp if ordered else None,
        last_fill=ordered[-1].event_timestamp if ordered else None,
        trades=trades,
    )


def write_backtest_report(
    results: list[CopyPaperResult],
    path: Path,
    *,
    copy_scale: float,
    starting_equity: float,
    fee_bps: float,
    slippage_bps: float,
    source_note: str,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Copy-paper backtest (round-trip copy)",
        "",
        f"Generated: **{now}**",
        "",
        "## Assumptions",
        "",
        f"- Starting equity: **${starting_equity:,.0f}** per wallet",
        f"- Copy scale: **{copy_scale}** (paper size = whale size × scale, capped at 2% equity per fill)",
        f"- Fees: **{fee_bps} bps** per leg; slippage: **{slippage_bps} bps** adverse",
        f"- Fills source: {source_note}",
        "",
        "## Summary",
        "",
        "| Wallet | Verdict | Fills | Trips sim | Copyability | Return % | Win rate % | "
        "End equity | Max DD % | Fees |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        verdict = r.verdict or "—"
        lines.append(
            f"| `{r.wallet}` | {verdict} | {r.fill_count} | {r.round_trips_simulated} | "
            f"{r.copyability_score:.3f} | {r.total_return_pct:.2f} | {r.win_rate_pct:.1f} | "
            f"${r.ending_equity:,.2f} | {r.max_drawdown_pct:.2f} | ${r.total_fees:,.2f} |"
        )

    dq_lines = ["", "## Data quality warnings", ""]
    any_dq = False
    for r in results:
        if not r.fill_dir_quality:
            continue
        q = r.fill_dir_quality
        any_dq = True
        dq_lines.append(
            f"- `{r.wallet}`: {q['fills_missing_fill_dir']:,} / {q['total_fills']:,} fills "
            f"missing fill_dir ({q['pct_missing'] * 100:.1f}%) — **{q['recommendation']}**"
        )
    if any_dq:
        lines.extend(dq_lines)

    lines.extend(
        [
            "",
            "## Methodology note",
            "",
            "Trust hierarchy (wallet evaluation):",
            "",
            "1. **Exchange closedPnl (scaled)** — ground truth from HL close rows.",
            "2. **FIFO + slippage (scaled)** — validation on full tape.",
            "3. **Round-trip copy** — paper sim; complete trips only (see skip breakdown in appendix).",
            "4. **Fill-follow legacy** — per-fill mirror; bot stress test only.",
            "",
        ]
    )

    lines.extend(["", "## Interpretation", ""])
    for r in results:
        lines.append(f"### `{r.wallet}`")
        lines.append("")
        if r.fill_count == 0:
            lines.append("No fills in window — backtest not meaningful.")
        else:
            span = ""
            if r.first_fill and r.last_fill:
                span = f" ({r.first_fill.date()} → {r.last_fill.date()})"
            lines.append(
                f"- History span{span}: **{r.coins_traded}** coins, **{r.fill_count}** fills; "
                f"**{r.round_trips_simulated}** / **{r.total_round_trips_detected}** round trips copied "
                f"(copyability **{r.copyability_score:.3f}**)."
            )
            lines.append(
                f"- Simulated return **{r.total_return_pct:.2f}%**; "
                f"**{r.win_rate_pct:.1f}%** win rate on simulated trips; "
                f"avg PnL/trip **${r.avg_pnl_per_trade:,.4f}**."
            )
            if r.open_notional_usd > 0:
                lines.append(f"- Open paper exposure at last fill: **${r.open_notional_usd:,.0f}**.")
        lines.append("")

    lines.extend(
        [
            "## Caveats",
            "",
            "- Round-trip copy skips incomplete trips when any leg fails dust/risk filters.",
            "- Does not model funding, liquidations, or partial user-channel gaps.",
            "- Past copy-paper results do not guarantee live copy profitability.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def format_benchmark_comparison(bench: CopyPaperBenchmarks, *, copy_scale: float) -> str:
    """Markdown table comparing trust-hierarchy methods on the same tape."""

    from hyperion_pipeline.analytics.copy_paper_simulator import format_skip_breakdown
    from hyperion_pipeline.analytics.sizing_engine import format_sizing_engine_summary

    rt = bench.round_trip
    leg = bench.fill_follow_legacy
    skip_section = format_skip_breakdown(
        total_round_trips_detected=rt.total_round_trips_detected,
        round_trips_simulated=rt.round_trips_simulated,
        round_trips_skipped=rt.round_trips_skipped,
        skip_counts_by_reason=rt.skip_counts_by_reason,
        copyability_score=rt.copyability_score,
    )
    dyn = " · dynamic sizing" if rt.use_dynamic_sizing else ""
    parts = [
        "",
        "## PnL methodology comparison (same tape)",
        "",
        "| Method | Return % | End equity | Notes |",
        "| --- | ---: | ---: | --- |",
        f"| Exchange closedPnl × scale | {bench.exchange_closed_return_pct:.2f} | "
        f"${bench.exchange_closed_ending_equity:,.2f} | ground truth |",
        f"| FIFO + slippage × scale | {bench.fifo_slippage_return_pct:.2f} | "
        f"${bench.fifo_slippage_ending_equity:,.2f} | {bench.fifo_closed_trips:,} closed trips |",
        f"| Round-trip copy | {rt.total_return_pct:.2f} | ${rt.ending_equity:,.2f} | "
        f"copyability {rt.copyability_score:.3f}{dyn} |",
        f"| Fill-follow legacy | {leg.total_return_mtm_pct:.2f} | ${leg.ending_equity_mtm:,.2f} | "
        f"{bench.mirror_coverage_pct:.1f}% fills mirrored |",
        "",
        f"Rows with `fill_dir`: **{bench.fills_with_dir_pct:.1f}%** of fills.",
        "",
        skip_section,
    ]
    if rt.sizing_summary is not None:
        parts.append(format_sizing_engine_summary(rt.sizing_summary))
    return "\n".join(parts)


def format_db_deep_appendix(
    *,
    wallet: str,
    fills: list[TradeFill],
    result: CopyPaperResult,
    wallet_meta: dict[str, Any],
    copy_scale: float,
    starting_equity: float,
    fee_bps: float,
    slippage_bps: float,
) -> str:
    """Markdown appendix for Postgres-sourced copy-paper runs (wallet stats + tape breakdown)."""

    wlower = wallet.lower()
    whale_notional = sum(abs(f.size) * f.price for f in fills)
    whale_per_fill = whale_notional / len(fills) if fills else 0.0
    fill_coin_counts = Counter(f.coin for f in fills)
    top_fill_coins = fill_coin_counts.most_common(20)

    coin_legs = Counter(t.coin for t in result.trades)
    coin_realized: dict[str, float] = defaultdict(float)
    coin_fees: dict[str, float] = defaultdict(float)
    for t in result.trades:
        coin_realized[t.coin] += t.realized_pnl
        coin_fees[t.coin] += t.fees

    top_pnl = sorted(coin_realized.items(), key=lambda x: abs(x[1]), reverse=True)[:15]

    hr = wallet_meta.get("hit_rate_pct")
    vlm = wallet_meta.get("all_time_vlm_usd")
    roi = wallet_meta.get("all_time_roi")
    dsc = wallet_meta.get("discovery_score")
    rt = wallet_meta.get("rank_tier")
    prom = wallet_meta.get("promoted")

    meta_lines: list[str] = [
        "## Local DB — wallet context",
        "",
        "### `hyperliquid_whale_registry` / discovery (best-effort)",
        "",
    ]
    if hr is not None:
        meta_lines.append(f"- **Hit rate (public portfolio estimate):** {float(hr):.2f}%")
    else:
        meta_lines.append("- **Hit rate:** *(not in registry row)*")
    if vlm is not None:
        meta_lines.append(f"- **All-time tape volume (leaderboard):** ${vlm:,.0f}")
    if roi is not None:
        meta_lines.append(f"- **All-time ROI (leaderboard):** {float(roi) * 100:.2f}%")
    if dsc is not None:
        meta_lines.append(f"- **Latest discovery_score:** {float(dsc):.2f}")
    if rt is not None:
        meta_lines.append(f"- **rank_tier:** `{rt}`")
    if prom is not None:
        meta_lines.append(f"- **promoted:** {prom}")

    mirror_pct = (100.0 * result.trade_count / result.fill_count) if result.fill_count else 0.0
    lines = [
        "",
        "## In-depth appendix (Postgres tape)",
        "",
        f"- **Wallet:** `{wlower}`",
        f"- **OLTP fill rows used:** **{len(fills):,}**",
        "",
        "Whale-side tape (for scale intuition, not paper PnL):",
        "",
        f"- **Σ |size|×price (whale notional traded in window):** ${whale_notional:,.0f}",
        f"- **Mean notional per whale fill:** ${whale_per_fill:,.0f}",
        f"- **Time range:** {result.first_fill} → {result.last_fill}" if result.first_fill else "- **Time range:** *(empty)*",
        "",
        "### Copy simulation parameters (this run)",
        "",
        f"- `starting_equity` = ${starting_equity:,.0f}",
        f"- `copy_scale` = {copy_scale}",
        f"- Per-fill cap = 2% of running equity (see `run_copy_paper_backtest`)",
        f"- Fees {fee_bps} bps / leg, slippage {slippage_bps} bps adverse",
        "",
        "### Activity vs skips",
        "",
        f"- **Whale fills in DB:** {result.fill_count:,}",
        f"- **Paper legs executed (met $1 min notional after scale+cap):** {result.trade_count:,} (**{mirror_pct:.4f}%** of whale fills)",
        f"- **Realized return:** {result.total_return_pct:.4f}% — **MTM return:** {result.total_return_mtm_pct:.4f}%",
        f"- **Total fees (paper):** ${result.total_fees:,.2f} — **slippage paid (model):** ${result.total_slippage:,.2f}",
        "",
        "### Top whale-fill coins (by fill count in DB)",
        "",
        "| Coin | Whale fills | Share % |",
        "| --- | ---: | ---: |",
    ]
    for coin, cnt in top_fill_coins:
        pct = 100.0 * cnt / len(fills) if fills else 0.0
        lines.append(f"| {coin} | {cnt:,} | {pct:.2f} |")
    lines.extend(
        [
            "",
            "### Paper legs by coin (simulator)",
            "",
            "| Coin | Paper legs | Σ realized (paper) | Σ fees |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for coin, _ in top_fill_coins[:15]:
        if coin not in coin_legs and coin not in coin_realized:
            continue
        lines.append(
            f"| {coin} | {coin_legs.get(coin, 0):,} | ${coin_realized[coin]:,.4f} | ${coin_fees[coin]:,.4f} |"
        )
    lines.extend(
        [
            "",
            "### Largest |realized| contributors (paper, by coin)",
            "",
            "| Coin | Σ realized (paper) |",
            "| --- | ---: |",
        ]
    )
    for coin, pnl in top_pnl:
        lines.append(f"| {coin} | ${pnl:,.4f} |")
    lines.extend(meta_lines)
    lines.extend(
        [
            "",
            "## How to read this",
            "",
            "- **Whale notional** is the counterparty’s tape size×price sum — much larger than paper because `copy_scale` and the per-fill equity cap shrink the mirrored order.",
            "- **Low % of fills mirrored** usually means most scaled notionals fell below the **$1** minimum or were dust after caps.",
            "",
        ]
    )
    return "\n".join(lines)


def save_fills_cache(fills: list[TradeFill], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "coin": f.coin,
            "side": f.side,
            "sz": f.size,
            "px": f.price,
            "time": int(f.event_timestamp.timestamp() * 1000),
            "hash": f.raw.get("hash", ""),
            "tid": f.raw.get("tid", 0),
            "oid": f.raw.get("oid", 0),
            "dir": f.raw.get("dir"),
        }
        for f in fills
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_fills_cache(path: Path, wallet: str) -> list[TradeFill]:
    from hyperion_pipeline.ingestion.fill_normalizer import FillNormalizer

    raw = json.loads(path.read_text(encoding="utf-8"))
    return FillNormalizer.from_fill_dicts(wallet, raw)
