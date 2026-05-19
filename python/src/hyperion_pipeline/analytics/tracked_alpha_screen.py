"""Screen ``HYPERLIQUID_TRACKED_USERS`` with relaxed small-account copy-paper rules."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import create_async_engine

from hyperion_pipeline.analytics.copy_paper_backtest import CopyPaperResult, run_copy_paper_backtest
from hyperion_pipeline.config.settings import get_settings
from hyperion_pipeline.ingestion.fill_normalizer import FillNormalizer
from hyperion_pipeline.ingestion.sources import HyperliquidHistoricalFillSource
from hyperion_pipeline.storage.postgres_fills import count_fills_for_wallet, fetch_trade_fills_for_wallet

FillsSourceMode = Literal["auto", "postgres", "rest"]


@dataclass(slots=True)
class ScreenRow:
    wallet: str
    fills_source: str
    fill_rows: int
    paper_legs: int
    first_fill: str | None
    last_fill: str | None
    span_days: float | None
    leg_pct_of_fills: float
    mtm_return_pct: float
    realized_return_pct: float
    ending_equity_realized: float
    ending_equity_mtm: float
    unrealized_usd: float
    realized_pnl_usd: float
    max_dd_pct: float
    total_fees: float
    total_slippage: float
    paper_turnover_usd: float
    fee_bps_of_turnover: float | None


def parse_tracked_wallets(settings: object) -> list[str]:
    raw = (getattr(settings, "hyperliquid_tracked_users", None) or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for part in raw.split(","):
        w = part.strip().lower()
        if w.startswith("0x") and len(w) >= 8:
            out.append(w)
    return out


def _span_days(first: datetime | None, last: datetime | None) -> float | None:
    if first is None or last is None:
        return None
    delta = last - first
    return max(0.0, delta.total_seconds() / 86400.0)


def _row_from_result(r: CopyPaperResult, *, fills_source: str) -> ScreenRow:
    turnover = sum(t.paper_size * t.paper_price for t in r.trades)
    unrealized = r.ending_equity_mtm - r.ending_equity
    realized_pnl = r.ending_equity - r.starting_equity
    fc = r.fill_count
    leg_pct = (100.0 * r.trade_count / fc) if fc > 0 else 0.0
    fee_bps_turn = (10_000.0 * r.total_fees / turnover) if turnover > 1e-9 else None
    span = _span_days(r.first_fill, r.last_fill)
    return ScreenRow(
        wallet=r.wallet,
        fills_source=fills_source,
        fill_rows=fc,
        paper_legs=r.trade_count,
        first_fill=str(r.first_fill) if r.first_fill else None,
        last_fill=str(r.last_fill) if r.last_fill else None,
        span_days=span,
        leg_pct_of_fills=leg_pct,
        mtm_return_pct=r.total_return_mtm_pct,
        realized_return_pct=r.total_return_pct,
        ending_equity_realized=r.ending_equity,
        ending_equity_mtm=r.ending_equity_mtm,
        unrealized_usd=unrealized,
        realized_pnl_usd=realized_pnl,
        max_dd_pct=r.max_drawdown_pct,
        total_fees=r.total_fees,
        total_slippage=r.total_slippage,
        paper_turnover_usd=turnover,
        fee_bps_of_turnover=fee_bps_turn,
    )


async def _fetch_rest_fills(wallet: str) -> list:
    settings = get_settings()
    src = HyperliquidHistoricalFillSource(settings)
    try:
        ms = int(time.time() * 1000)
        raw = await src.fetch_fill_payloads(wallet.lower(), start_time_ms=0, end_time_ms=ms)
    finally:
        await src.close()
    return FillNormalizer.from_fill_dicts(wallet.lower(), raw)


def run_screen(
    *,
    starting_equity: float = 100.0,
    min_trade_usd: float = 20.0,
    max_trade_usd: float = 40.0,
    max_equity_pct_per_fill: float = 0.45,
    copy_scale: float = 0.001,
    fee_bps: float = 4.0,
    slippage_bps: float = 6.0,
    rest_delay_sec: float = 80.0,
    wallets_override: list[str] | None = None,
    fills_source: FillsSourceMode = "auto",
) -> Path:
    """
    Run fill-follow simulation for each tracked wallet; write ranked markdown to
    ``analytics/research/copy_backtest/tracked_alpha_screen_*.md``.

    ``fills_source``:
    - **auto** — Postgres fills when count > 0, else REST (mixed windows).
    - **postgres** — only local ``fills`` (empty tape if none).
    - **rest** — always REST ``userFillsByTime`` (same API for all; use for apples-to-apples).
    """
    if fills_source not in ("auto", "postgres", "rest"):
        raise ValueError(f"fills_source must be auto|postgres|rest, got {fills_source!r}")

    settings = get_settings()
    wallets = wallets_override or parse_tracked_wallets(settings)
    if not wallets:
        raise SystemExit(
            "No wallets: set HYPERLIQUID_TRACKED_USERS in .env or pass --wallet repeatedly."
        )

    hyperion_root = Path(__file__).resolve().parents[4]
    out_dir = hyperion_root / "analytics" / "research" / "copy_backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    src_tag = fills_source.replace(":", "")
    report_path = out_dir / f"tracked_alpha_screen_{src_tag}_{stamp}.md"

    async def _run() -> list[ScreenRow]:
        engine = create_async_engine(settings.database_url)
        out_rows: list[ScreenRow] = []
        try:
            for i, w in enumerate(wallets):
                src_label = ""
                fill_input_rows = 0
                fills: list = []

                if fills_source == "rest":
                    if i > 0:
                        await asyncio.sleep(rest_delay_sec)
                    fills = await _fetch_rest_fills(w)
                    src_label = "rest (userFillsByTime)"
                elif fills_source == "postgres":
                    cnt = await count_fills_for_wallet(engine, w)
                    fills = await fetch_trade_fills_for_wallet(engine, w) if cnt > 0 else []
                    src_label = f"postgres ({cnt:,} rows)"
                else:
                    cnt = await count_fills_for_wallet(engine, w)
                    if cnt > 0:
                        fills = await fetch_trade_fills_for_wallet(engine, w)
                        src_label = f"postgres ({cnt:,} rows)"
                    else:
                        if i > 0:
                            await asyncio.sleep(rest_delay_sec)
                        fills = await _fetch_rest_fills(w)
                        src_label = "rest (userFillsByTime, no local fills)"

                r = run_copy_paper_backtest(
                    w,
                    fills,
                    starting_equity=starting_equity,
                    copy_scale=copy_scale,
                    max_equity_pct_per_fill=max_equity_pct_per_fill,
                    min_trade_usd=min_trade_usd,
                    max_trade_usd=max_trade_usd,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                )
                out_rows.append(_row_from_result(r, fills_source=src_label))
        finally:
            await engine.dispose()
        return out_rows

    screen_rows = asyncio.run(_run())

    lines: list[str] = [
        "# Tracked alpha — copy-paper screen (small account)",
        "",
        f"Generated: **{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC**",
        "",
        "## What this report is testing",
        "",
        "Each row is a **naive fill mirror**: paper size ∝ whale fill, with your min/max notional, "
        "fee and slippage assumptions. It is **not** Hyperion live signals, consensus, or exchange "
        "position accounting.",
        "",
        "### How to read the two rankings",
        "",
        "- **Realized %** — PnL from **closed** paper legs and fees, using ending **cash** equity "
        "(no mark on open exposure). Better for “did the mirroring bank money in this toy model”.",
        "- **MTM %** — Marks **open** paper positions at the **last fill price** per coin. Can look "
        "great while **realized %** is negative (paper gains are unrealized).",
        "",
        "### Fill source for this run",
        "",
        f"- **Mode:** `{fills_source}`",
    ]
    if fills_source == "auto":
        lines.append(
            "- **auto** mixes Postgres (ingest window) with REST when local tape is empty — "
            "**time spans differ**; cross-wallet ranks are weak."
        )
    elif fills_source == "rest":
        lines.append(
            "- **rest** uses the same Hyperliquid API path for every wallet — **better for comparing "
            "wallets**, still subject to rate limits and API caps on history."
        )
    else:
        lines.append("- **postgres** uses only local `fills` — wallets with no rows are empty tape.")

    lines.extend(
        [
            "",
            "## Simulation parameters",
            "",
            f"- Starting equity: **${starting_equity:,.2f}**",
            f"- `copy_scale`: **{copy_scale}**; cap **{max_equity_pct_per_fill:.0%}** of equity; "
            f"absolute max **${max_trade_usd:.0f}** per leg",
            f"- Minimum leg: **${min_trade_usd:.0f}**",
            f"- Fees: **{fee_bps}** bps/leg; slippage model: **{slippage_bps}** bps adverse",
            "",
            "## Diagnostics (all wallets, input order)",
            "",
            "| Wallet | Source | Span (d) | Whale fills | Paper legs | Leg % | Realized % | MTM % | "
            "Δ Equity real $ | Unreal $ | End $ real | End $ MTM | Max DD % | Fees | Slip | "
            "Turnover $ | Fees as % of turnover |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
            "---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for sr in screen_rows:
        span_s = f"{sr.span_days:.2f}" if sr.span_days is not None else "—"
        fee_t = f"{sr.fee_bps_of_turnover:.1f} bps" if sr.fee_bps_of_turnover is not None else "—"
        lines.append(
            f"| `{sr.wallet}` | {sr.fills_source} | {span_s} | {sr.fill_rows:,} | {sr.paper_legs:,} | "
            f"{sr.leg_pct_of_fills:.2f} | {sr.realized_return_pct:.2f} | {sr.mtm_return_pct:.2f} | "
            f"{sr.realized_pnl_usd:+.2f} | {sr.unrealized_usd:+.2f} | ${sr.ending_equity_realized:,.2f} | "
            f"${sr.ending_equity_mtm:,.2f} | {sr.max_dd_pct:.2f} | ${sr.total_fees:,.2f} | "
            f"${sr.total_slippage:,.2f} | ${sr.paper_turnover_usd:,.2f} | {fee_t} |"
        )

    by_realized = sorted(screen_rows, key=lambda x: x.realized_return_pct, reverse=True)
    by_mtm = sorted(screen_rows, key=lambda x: x.mtm_return_pct, reverse=True)

    lines.extend(
        [
            "",
            "## Ranked by realized return % (cash path — primary for “banked” toy PnL)",
            "",
            "| Rank | Wallet | Realized % | MTM % | Unreal $ | Legs | Span (d) |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for rank, sr in enumerate(by_realized, start=1):
        span_s = f"{sr.span_days:.2f}" if sr.span_days is not None else "—"
        lines.append(
            f"| {rank} | `{sr.wallet}` | {sr.realized_return_pct:.2f} | {sr.mtm_return_pct:.2f} | "
            f"{sr.unrealized_usd:+.2f} | {sr.paper_legs:,} | {span_s} |"
        )

    lines.extend(
        [
            "",
            "## Ranked by MTM % (includes open positions — easy to overread)",
            "",
            "| Rank | Wallet | MTM % | Realized % | Unreal $ | Legs |",
            "| ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for rank, sr in enumerate(by_mtm, start=1):
        lines.append(
            f"| {rank} | `{sr.wallet}` | {sr.mtm_return_pct:.2f} | {sr.realized_return_pct:.2f} | "
            f"{sr.unrealized_usd:+.2f} | {sr.paper_legs:,} |"
        )

    red = [
        s
        for s in screen_rows
        if s.paper_legs > 0
        and abs(s.unrealized_usd) > abs(s.realized_pnl_usd)
        and abs(s.unrealized_usd) > 1.0
    ]
    mtm_pos_real_neg = [s for s in screen_rows if s.mtm_return_pct > 0 and s.realized_return_pct < 0]

    lines.extend(
        [
            "",
            "## Red flags (heuristic)",
            "",
            "**Unrealized dominates** — |unrealized| > |realized PnL| and |unreal| > $1: MTM ranking "
            "can overstate “quality”.",
            "",
        ]
    )
    if not red:
        lines.append("_None passed this filter._")
    else:
        for s in sorted(red, key=lambda x: -abs(x.unrealized_usd)):
            lines.append(
                f"- `{s.wallet}` — unreal **${s.unrealized_usd:+.2f}** vs realized PnL **${s.realized_pnl_usd:+.2f}** "
                f"(MTM {s.mtm_return_pct:.1f}% vs realized {s.realized_return_pct:.1f}%)"
            )

    lines.extend(
        [
            "",
            "**MTM positive, realized negative** — open marks carry the story:",
            "",
        ]
    )
    if not mtm_pos_real_neg:
        lines.append("_None._")
    else:
        for s in mtm_pos_real_neg:
            lines.append(
                f"- `{s.wallet}` — MTM **{s.mtm_return_pct:.2f}%**, realized **{s.realized_return_pct:.2f}%**"
            )

    profitable_realized = [s for s in screen_rows if s.realized_return_pct > 0 and s.paper_legs > 0]
    lines.extend(
        [
            "",
            "## Wallets with positive realized % and at least one paper leg",
            "",
        ]
    )
    if not profitable_realized:
        lines.append("_None._")
    else:
        for s in sorted(profitable_realized, key=lambda x: x.realized_return_pct, reverse=True):
            lines.append(
                f"- `{s.wallet}` — realized **{s.realized_return_pct:.2f}%** "
                f"(MTM {s.mtm_return_pct:.2f}%, unreal ${s.unrealized_usd:+.2f})"
            )

    lines.extend(
        [
            "",
            "## Per-wallet time span",
            "",
            "| Wallet | First fill | Last fill |",
            "| --- | --- | --- |",
        ]
    )
    for sr in screen_rows:
        lines.append(f"| `{sr.wallet}` | {sr.first_fill or '—'} | {sr.last_fill or '—'} |")

    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report_path
