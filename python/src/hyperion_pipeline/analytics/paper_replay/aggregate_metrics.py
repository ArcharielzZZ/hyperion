"""Paper wallet metrics aggregated from FIFO round trips plus slippage model."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone

from hyperion_pipeline.analytics.paper_replay.replay_core import SlippedLeg, slip_leg_pnl
from hyperion_pipeline.analytics.paper_replay.round_trips import fifo_state_after_round_trips, unrealized_mtm_usd_remaining_lots
from hyperion_pipeline.analytics.paper_replay.slippage import SlippageModel
from hyperion_pipeline.models.pydantic_domain import TradeFill
@dataclass(slots=True)
class WalletPaperReplayRow:
    """One wallet row suitable for Markdown / Rich ranking tables."""

    wallet: str
    closed_trips_n: int
    win_rate: float
    payoff_ratio: float
    max_loss_pct_notional: float
    sharpe_annual_trade_returns: float
    realized_vs_mtm_ratio: float
    realized_closed_pnl_usd: float
    total_mtm_pnl_proxy_usd: float
    hl_attributed_minus_paper_net_usd: float
    pass_gate: bool
    unrealized_residual_usd: float


def summarize_wallet_round_trip_replay(
    *,
    wallet: str,
    fills: list[TradeFill],
    slippage_model: SlippageModel,
    realized_only: bool,
    min_pass_pnl_usd: float = 1000.0,
) -> WalletPaperReplayRow:
    """
    Purpose: summarize slippage- and fee-adjusted FIFO closed trips for one wallet tape.

    Inputs: lowercase wallet id, chronological ``TradeFill`` list, adverse slippage model,
            ``realized_only`` parity with FIFO builder. ``min_pass_pnl_usd`` joins payoff/PnL checks
            for PASS (see ``pass_gate``).
    Outputs: ``WalletPaperReplayRow`` with diagnostic aggregates for research tables.

    Gotcha: ``total_mtm_pnl_proxy_usd`` sums closed-trip paper PnL plus terminal MTM from
            remaining mids — it ignores funding, liquidation, transfers, and any fills without
            Open/Close tags when ``realized_only=True``.
    """

    wallet_l = wallet.lower()
    trips, long_lots, short_lots, last_mid = fifo_state_after_round_trips(
        fills, realized_only=realized_only
    )
    unreal = unrealized_mtm_usd_remaining_lots(long_lots, short_lots, last_mid)

    slipped = [slip_leg_pnl(t, slippage_model) for t in trips]
    nets = [s.net_pnl_usd for s in slipped]

    hl_sum = sum(
        t.hl_closed_pnl_portion_usd for t in trips if t.hl_closed_pnl_portion_usd is not None
    )
    exchange_minus_paper = hl_sum - sum(nets)

    wins = [n for n in nets if n > 0.0]
    losses = [n for n in nets if n < 0.0]
    closed_n = len(nets)

    win_rate = (len(wins) / closed_n) if closed_n > 0 else math.nan

    payoff = math.nan
    if wins and losses:
        payoff = statistics.mean(wins) / abs(statistics.mean(losses))

    max_loss_pct = math.nan
    if slipped:
        max_loss_pct = min(s.loss_pct_vs_notional for s in slipped)

    window_days = _calendar_days_span_seconds(fills)
    sharpe = _annualized_daily_deployment_sharpe(slipped, window_days_calendar=window_days)

    realized_sum = sum(nets)
    total_mtm_proxy = realized_sum + unreal

    rv_ratio = realized_sum / total_mtm_proxy if abs(total_mtm_proxy) > 1e-9 else math.nan

    payoff_gate_ok = wins and losses and not math.isnan(payoff) and payoff > 1.2
    pnl_gate_ok = realized_sum > 0.0
    pnl_floor_gate_ok = realized_sum > float(min_pass_pnl_usd)

    # PASS (research): payoff > 1.2, winners & losers, realized PnL > 0, and above USD floor.
    pass_gate = bool(payoff_gate_ok and pnl_gate_ok and pnl_floor_gate_ok)

    return WalletPaperReplayRow(
        wallet=wallet_l,
        closed_trips_n=closed_n,
        win_rate=float(win_rate) if not math.isnan(win_rate) else math.nan,
        payoff_ratio=float(payoff) if not math.isnan(payoff) else math.nan,
        max_loss_pct_notional=float(max_loss_pct) if not math.isnan(max_loss_pct) else math.nan,
        sharpe_annual_trade_returns=float(sharpe),
        realized_vs_mtm_ratio=float(rv_ratio),
        realized_closed_pnl_usd=float(realized_sum),
        total_mtm_pnl_proxy_usd=float(total_mtm_proxy),
        hl_attributed_minus_paper_net_usd=float(exchange_minus_paper),
        pass_gate=bool(pass_gate),
        unrealized_residual_usd=float(unreal),
    )


def slipped_closed_legs(
    *,
    fills: list[TradeFill],
    slippage_model: SlippageModel,
    realized_only: bool,
) -> list[SlippedLeg]:
    """
    Purpose: emit every slipped FIFO close for richer analytics downstream.

    Inputs: wallet tape plus slippage model + ``realized_only`` parity.
    Outputs: ``SlippedLeg`` list ordered by chronological closes.

    Gotcha: identical coverage to ``summarize_wallet_round_trip_replay`` — if that summary is NaN-heavy,
            this list will be tiny until exchange ``dir`` tags backfill completes.
    """

    trips, _, _, _ = fifo_state_after_round_trips(fills, realized_only=realized_only)
    return [slip_leg_pnl(t, slippage_model) for t in trips]


def wallet_replay_metrics_table_rows(
    rows: list[WalletPaperReplayRow],
) -> tuple[list[str], list[list[str]]]:
    """
    Purpose: shape ranked summary data for Markdown (tab-separated header + body).

    Inputs: unordered ``WalletPaperReplayRow`` rows.
    Outputs: header list and body strings ordered by descending ``realized_closed_pnl_usd``.

    Gotcha: ``payoff_ratio`` renders as blank when undefined (no losers yet).
    """

    header = [
        "wallet",
        "closed_trips",
        "win_rate",
        "payoff",
        "max_loss_pct_notional",
        "sharpe_ann_trade",
        "realized_mtm_ratio",
        "realized_closed_pnl_usd",
        "total_mtm_proxy_usd",
        "PASS",
        "unreal_usd_residual",
        "hl_attrib_minus_paper_net_usd",
    ]
    ranked = sorted(rows, key=lambda r: r.realized_closed_pnl_usd, reverse=True)
    body: list[list[str]] = []
    for r in ranked:
        body.append(
            [
                r.wallet,
                str(r.closed_trips_n),
                _fmt_pct(r.win_rate),
                "" if math.isnan(r.payoff_ratio) else f"{r.payoff_ratio:.3f}",
                f"{r.max_loss_pct_notional:.2f}" if not math.isnan(r.max_loss_pct_notional) else "",
                f"{r.sharpe_annual_trade_returns:.3f}" if not math.isnan(r.sharpe_annual_trade_returns) else "",
                "" if math.isnan(r.realized_vs_mtm_ratio) else f"{r.realized_vs_mtm_ratio:.4f}",
                f"{r.realized_closed_pnl_usd:.2f}",
                f"{r.total_mtm_pnl_proxy_usd:.2f}",
                str(r.pass_gate),
                f"{r.unrealized_residual_usd:.2f}",
                f"{r.hl_attributed_minus_paper_net_usd:.2f}",
            ]
        )
    return header, body


def markdown_table_from_wallet_rows(rows: list[WalletPaperReplayRow]) -> str:
    """Compose a Markdown table string for Appendix attachment."""

    h, body = wallet_replay_metrics_table_rows(rows)
    lines = [
        "| " + " | ".join(h) + " |",
        "| " + " | ".join("---" for _ in h) + " |",
    ]
    for b in body:
        lines.append("| " + " | ".join(b) + " |")
    return "\n".join(lines) + "\n"


def _fmt_pct(rate: float) -> str:
    if math.isnan(rate):
        return ""
    return f"{100.0 * rate:.2f}%"


def _exit_utc_date(ts: datetime) -> date:
    tt = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    return tt.date()


def _annualized_daily_deployment_sharpe(
    slipped: list[SlippedLeg],
    *,
    window_days_calendar: float,
) -> float:
    """
    Bucket closed trips by exit calendar day.
    Daily return = sum(net_pnl) / sum(entry_notional) for trips exiting that day.
    Annualized Sharpe uses mean/std of those daily returns scaled by observation-year factor
    (burst trading per trip no longer inflates sqrt(N) trade counts).
    """

    if len(slipped) < 2:
        return math.nan

    daily_pnl: dict[date, float] = defaultdict(float)
    daily_not: dict[date, float] = defaultdict(float)
    for s in slipped:
        d = _exit_utc_date(s.exit_ts)
        daily_pnl[d] += s.net_pnl_usd
        daily_not[d] += s.entry_notional_usd

    daily_rets: list[float] = []
    for d in sorted(daily_not.keys()):
        dn = daily_not[d]
        if dn <= 1e-12:
            continue
        daily_rets.append(daily_pnl[d] / dn)

    if len(daily_rets) < 2:
        return math.nan

    mu = statistics.mean(daily_rets)
    sig = statistics.pstdev(daily_rets)
    if sig < 1e-15:
        return math.nan

    window_days_calendar = max(1.0 / 24.0, float(window_days_calendar))
    n_obs = len(daily_rets)
    obs_per_year = n_obs * (365.0 / window_days_calendar)
    return (mu / sig) * math.sqrt(obs_per_year)


def _calendar_days_span_seconds(fills: list[TradeFill]) -> float:
    """Return span length in fractional days covering first-to-last timestamps."""

    if not fills:
        return 1.0
    stamps = sorted(f.event_timestamp for f in fills)

    start = stamps[0]
    end = stamps[-1]

    start = (
        start.replace(tzinfo=timezone.utc)
        if start.tzinfo is None
        else start.astimezone(timezone.utc)
    )
    end = (
        end.replace(tzinfo=timezone.utc)
        if end.tzinfo is None
        else end.astimezone(timezone.utc)
    )
    secs = max(86400.0, (end - start).total_seconds())
    return secs / 86400.0
