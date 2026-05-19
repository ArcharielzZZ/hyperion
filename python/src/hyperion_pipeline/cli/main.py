"""Typer CLI — async commands use ``asyncio.run``."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table
from sqlalchemy.ext.asyncio import create_async_engine

from hyperion_pipeline.config.settings import get_settings
from hyperion_pipeline.container import Container
from hyperion_pipeline.ingestion.historical_backfill_service import HistoricalBackfillService
from hyperion_pipeline.ingestion.sources import HyperliquidHistoricalFillSource, ListFillSource
from hyperion_pipeline.observability.logging import configure_logging
from hyperion_pipeline.ranking.config_schema import RankingConfigYaml
from hyperion_pipeline.ranking.factor_registry import FactorRegistry
from hyperion_pipeline.ranking.ranking_engine import RankingEngine
from hyperion_pipeline.storage.postgres_fills import update_wallet_sync_state

app = typer.Typer(help="Hyperion Python pipeline (backfill, analytics, ranking).")


@app.callback()
def _global_options() -> None:
    configure_logging(json_logs=False)


def _repo_example(name: str) -> Path:
    return Path(__file__).resolve().parents[3] / "example_configs" / name


@app.command("backfill-wallet")
def backfill_wallet_cmd(
    wallet: str = typer.Argument(..., help="0x-prefixed wallet"),
    fixture: Path | None = typer.Option(
        None,
        "--fixture",
        help="Optional JSON snapshot (userFills WS shape or raw REST array in key \"fills\").",
    ),
    start_ms: int | None = typer.Option(
        None,
        "--start-ms",
        help="With --end-ms: use userFillsByTime (ms). Omit both for latest userFills (up to 2000).",
    ),
    end_ms: int | None = typer.Option(
        None,
        "--end-ms",
        help="Inclusive end time in ms for userFillsByTime.",
    ),
    full_history: bool = typer.Option(
        False,
        "--full-history",
        help="Shorthand for userFillsByTime from epoch ms 0 to now; recursively splits windows that return 2000 rows (heavy for very active wallets).",
    ),
    to_postgres: bool = typer.Option(
        True,
        "--to-postgres/--no-postgres",
        help="Insert into Postgres fills (needs DATABASE_URL and migration 0009).",
    ),
    parquet: bool = typer.Option(
        True,
        "--parquet/--no-parquet",
        help="Write partitioned Parquet under PIPELINE_DATA_DIR.",
    ),
    min_round_trips: int | None = typer.Option(
        None,
        "--min-round-trips",
        envvar="HYPERION_BACKFILL_MIN_ROUND_TRIPS",
        help=(
            "When set with --to-postgres, recount FIFO realized closed trips post-run "
            "(same semantics as Step 1 gate — needs fill_dir enrichment)."
        ),
    ),
) -> None:
    """Fetch historical fills from Hyperliquid (or a fixture) and store them."""

    if full_history:
        if fixture is not None:
            typer.secho("--full-history cannot be used with --fixture.", fg="red", err=True)
            raise typer.Exit(code=1)
        if start_ms is not None or end_ms is not None:
            typer.secho("Use either --full-history or --start-ms/--end-ms, not both.", fg="red", err=True)
            raise typer.Exit(code=1)
        start_ms = 0
        end_ms = int(time.time() * 1000)
    elif (start_ms is None) ^ (end_ms is None):
        typer.secho("Use both --start-ms and --end-ms, or neither.", fg="red", err=True)
        raise typer.Exit(code=1)

    async def _run() -> None:
        settings = get_settings()
        container = Container.from_default_settings()
        storage = container.fill_storage
        engine = create_async_engine(settings.database_url, pool_pre_ping=True) if to_postgres else None
        source: ListFillSource | HyperliquidHistoricalFillSource | None = None
        try:
            if fixture is not None:
                blob = json.loads(fixture.read_text(encoding="utf-8"))
                if isinstance(blob, list):
                    fills_raw = blob
                else:
                    data = blob.get("data", blob)
                    fills_raw = data.get("fills", [])
                source = ListFillSource(fills_raw)
            else:
                source = HyperliquidHistoricalFillSource(settings)
            svc = HistoricalBackfillService(
                settings,
                storage,
                postgres_engine=engine,
                write_parquet=parquet,
            )
            try:
                outcome = await svc.backfill_wallet(
                    wallet,
                    source,
                    start_time_ms=start_ms,
                    end_time_ms=end_ms,
                )
            except Exception as exc:
                if engine is not None:
                    await update_wallet_sync_state(
                        engine,
                        wallet.lower(),
                        status="error",
                        cursor={"error_type": type(exc).__name__},
                        last_watermark=None,
                        error=str(exc)[:2000],
                    )
                raise
            typer.echo(f"parquet_files={len(outcome.parquet_paths)}")
            for p in outcome.parquet_paths:
                typer.echo(str(p))
            typer.echo(f"fills_attempted={outcome.fills_attempted} fills_inserted={outcome.fills_inserted}")

            if min_round_trips is not None and engine is not None:
                from hyperion_pipeline.analytics.paper_replay.round_trips import fifo_closed_round_trips
                from hyperion_pipeline.storage.postgres_fills import fetch_trade_fills_for_wallet

                reload_fills = await fetch_trade_fills_for_wallet(engine, wallet)
                closed_n = len(fifo_closed_round_trips(reload_fills, realized_only=True))
                gate_ok = closed_n >= min_round_trips
                typer.echo(
                    "round_trip_audit "
                    f"fifo_realized_only_closed_segments={closed_n} "
                    f"min_round_trips={min_round_trips} gate_met={gate_ok}"
                )
        finally:
            if isinstance(source, HyperliquidHistoricalFillSource):
                await source.close()
            if engine is not None:
                await engine.dispose()

    asyncio.run(_run())


@app.command("sync-live")
def sync_live_cmd() -> None:
    """Live websocket path is still Rust ``hyperion-ingest``."""

    typer.echo(
        "sync-live: run `cargo run -p hyperion-ingest` for websocket ingest. "
        "Python backfill uses REST and writes the same `fills` table when --to-postgres is on."
    )


@app.command("recompute-metrics")
def recompute_metrics_cmd() -> None:
    """Recompute ``pipeline_wallet_metrics`` — not implemented yet."""

    typer.echo(
        "recompute-metrics: implement Polars rollups from `scan_fills_lazy` then upsert pipeline_wallet_metrics."
    )


@app.command("rank-wallets")
def rank_wallets_cmd(
    config: Path = typer.Option(
        _repo_example("ranking.example.yaml"),
        "--config",
        help="YAML ranking config",
    ),
) -> None:
    """Demo ranking on synthetic factor rows."""

    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    rc = RankingConfigYaml.model_validate(raw)
    engine = RankingEngine(rc, FactorRegistry())
    keys = list(rc.weights)
    demo = {
        "0xaaa": {k: float(keys.index(k)) for k in keys},
        "0xbbb": {k: float(keys.index(k) + 1) for k in keys},
    }
    for w, s in engine.rank_wallets(demo):
        typer.echo(f"{w}\t{s:.6f}")


@app.command("copy-paper-backtest")
def copy_paper_backtest_cmd(
    wallets: list[str] = typer.Option(
        ...,
        "--wallet",
        help="Whale wallet(s) to backtest (repeat flag)",
    ),
    copy_scale: float = typer.Option(
        0.0001,
        "--copy-scale",
        help="Fraction of whale fill size to mirror",
    ),
    starting_equity: float = typer.Option(10_000.0, "--starting-equity"),
    fee_bps: float = typer.Option(4.0, "--fee-bps"),
    slippage_bps: float = typer.Option(6.0, "--slippage-bps"),
    full_history: bool = typer.Option(
        True,
        "--full-history/--recent-only",
        help="userFillsByTime from epoch vs latest userFills slice",
    ),
    use_cache: bool = typer.Option(True, "--use-cache/--no-cache"),
    from_db: bool = typer.Option(
        False,
        "--from-db",
        help="Load fills from local Postgres fills table (instead of Hyperliquid REST)",
    ),
    deep_appendix: bool = typer.Option(
        True,
        "--deep/--no-deep",
        help="Append OLTP tape appendix when --from-db",
    ),
    coins: str = typer.Option(
        "",
        "--coins",
        help="Optional comma-separated coin filter (e.g. BTC,ETH)",
    ),
) -> None:
    """Fetch historical fills and run naive fill-follow paper PnL simulation."""

    import asyncio
    import time

    from hyperion_pipeline.analytics.copy_paper_backtest import (
        format_db_deep_appendix,
        load_fills_cache,
        run_copy_paper_backtest,
        save_fills_cache,
        write_backtest_report,
    )
    from hyperion_pipeline.config.settings import get_settings
    from hyperion_pipeline.ingestion.fill_normalizer import FillNormalizer
    from hyperion_pipeline.ingestion.sources import HyperliquidHistoricalFillSource

    hyperion_root = Path(__file__).resolve().parents[4]
    cache_dir = hyperion_root / "analytics" / "research" / "copy_backtest"
    allow = {c.strip().upper() for c in coins.split(",") if c.strip()} or None

    async def _fetch_rest(wallet: str) -> list:
        w = wallet.lower()
        cache_path = cache_dir / f"{w}_fills.json"
        if not from_db and use_cache and cache_path.exists():
            typer.echo(f"cache hit {cache_path}")
            return load_fills_cache(cache_path, w)
        settings = get_settings()
        source = HyperliquidHistoricalFillSource(settings)
        try:
            if full_history:
                raw = await source.fetch_fill_payloads(w, start_time_ms=0, end_time_ms=int(time.time() * 1000))
            else:
                raw = await source.fetch_fill_payloads(w, start_time_ms=None, end_time_ms=None)
        finally:
            await source.close()
        fills = FillNormalizer.from_fill_dicts(w, raw)
        if use_cache:
            save_fills_cache(fills, cache_path)
            typer.echo(f"cached {len(fills)} fills -> {cache_path}")
        return fills

    async def _load_from_db(wallet: str) -> tuple[dict, list]:
        from sqlalchemy.ext.asyncio import create_async_engine

        from hyperion_pipeline.storage.postgres_fills import (
            fetch_trade_fills_for_wallet,
            fetch_wallet_meta,
        )

        settings = get_settings()
        engine = create_async_engine(settings.database_url)
        try:
            meta = await fetch_wallet_meta(engine, wallet)
            fills = await fetch_trade_fills_for_wallet(engine, wallet)
            return meta, fills
        finally:
            await engine.dispose()

    results = []
    metas: list[dict] = []
    fills_batch: list[list] = []

    for i, wallet in enumerate(wallets):
        if i > 0 and not from_db:
            typer.echo("pausing 45s before next wallet (rate limit)...")
            time.sleep(45)
        typer.echo(f"fetching {wallet} ...")
        if from_db:
            meta, fills = asyncio.run(_load_from_db(wallet))
            metas.append(meta)
            fills_batch.append(fills)
            typer.echo(f"  fills (Postgres)={len(fills):,}")
        else:
            fills = asyncio.run(_fetch_rest(wallet))
            metas.append({})
            fills_batch.append(fills)
            typer.echo(f"  fills={len(fills):,}")
        r = run_copy_paper_backtest(
            wallet,
            fills,
            starting_equity=starting_equity,
            copy_scale=copy_scale,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            coin_allowlist=allow,
        )
        results.append(r)
        typer.echo(
            f"  return={r.total_return_mtm_pct:.2f}% (mtm) equity=${r.ending_equity_mtm:,.2f} "
            f"fills={r.fill_count} max_dd={r.max_drawdown_pct:.2f}%"
        )

    source_note = (
        "Postgres `fills` table (local Hyperion ingest)" if from_db else "Hyperliquid REST userFills / userFillsByTime"
    )
    stamp = time.strftime("%Y%m%d_%H%M%S")
    prefix = "copy_paper_report_db_" if from_db else "copy_paper_report_"
    out = cache_dir / f"{prefix}{stamp}.md"
    write_backtest_report(
        results,
        out,
        copy_scale=copy_scale,
        starting_equity=starting_equity,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        source_note=source_note,
    )
    if from_db and deep_appendix:
        base = out.read_text(encoding="utf-8")
        for r, m, f, w in zip(results, metas, fills_batch, wallets, strict=True):
            base += format_db_deep_appendix(
                wallet=w,
                fills=f,
                result=r,
                wallet_meta=m,
                copy_scale=copy_scale,
                starting_equity=starting_equity,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
        out.write_text(base, encoding="utf-8")

    typer.echo(f"report -> {out}")


@app.command("paper-replay-promoted")
def paper_replay_promoted_cmd(
    wallet: list[str] | None = typer.Option(
        None,
        "--wallet",
        help="Run for specific address(es) instead of latest promoted discovery set (repeat flag).",
    ),
    slippage_bps: float = typer.Option(
        7.0,
        "--slippage-bps",
        envvar="HYPERION_PAPER_REPLAY_SLIPPAGE_BPS",
        help="Linear adverse slippage per leg in basis points (entry and exit both).",
    ),
    realized_only: bool = typer.Option(
        False,
        "--realized-only/--allow-partial-window-closes",
        help="Skip any close fill whose full size cannot be FIFO-matched to opens in the loaded tape.",
    ),
    markdown_out: Path | None = typer.Option(
        None,
        "--markdown-out",
        help="Optional path to append Markdown table (research ops script).",
    ),
    min_pass_pnl: float = typer.Option(
        1000.0,
        "--min-pass-pnl",
        envvar="HYPERION_PAPER_REPLAY_MIN_PASS_PNL",
        help="Minimum realized_closed_pnl_usd required for PASS gate.",
    ),
) -> None:
    """
    FIFO paper replay on promoted wallets — slippage model + closed-trip stats (research path).

    Loads ``fills`` from Postgres; requires migration 0012 enrichment for reliable Open/Close tags.
    """

    from hyperion_pipeline.analytics.paper_replay.aggregate_metrics import (
        WalletPaperReplayRow,
        markdown_table_from_wallet_rows,
        summarize_wallet_round_trip_replay,
        wallet_replay_metrics_table_rows,
    )
    from hyperion_pipeline.analytics.paper_replay.slippage import LinearBpsSlippage
    from hyperion_pipeline.config.settings import get_settings
    from hyperion_pipeline.storage.postgres_fills import (
        fetch_promoted_wallet_addresses,
        fetch_trade_fills_for_wallet,
    )

    async def _run() -> list[WalletPaperReplayRow]:
        settings = get_settings()
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        try:
            targets = (
                [w.strip().lower() for w in wallet]
                if wallet
                else await fetch_promoted_wallet_addresses(engine)
            )
            if not targets:
                return []
            slip = LinearBpsSlippage(slippage_bps)
            out_rows = []
            for w in targets:
                fills = await fetch_trade_fills_for_wallet(engine, w)
                out_rows.append(
                    summarize_wallet_round_trip_replay(
                        wallet=w,
                        fills=fills,
                        slippage_model=slip,
                        realized_only=realized_only,
                        min_pass_pnl_usd=min_pass_pnl,
                    )
                )
            return out_rows
        finally:
            await engine.dispose()

    rows = asyncio.run(_run())

    if not rows:
        typer.secho("No promoted wallets found (or empty --wallet list).", fg="yellow", err=True)
        raise typer.Exit(code=0)

    table = Table(title="Paper replay — FIFO closed trips (promoted set)")
    hdr, body = wallet_replay_metrics_table_rows(rows)

    for col in hdr:
        table.add_column(col, overflow="ellipsis")
    for b in body:
        table.add_row(*b)

    console = Console()
    console.print(table)

    if markdown_out is not None:
        md = (
            f"# paper-replay-promoted\n\n"
            f"- slippage_bps: `{slippage_bps}`\n"
            f"- realized_only: `{realized_only}`\n"
            f"- min_pass_pnl_usd: `{min_pass_pnl}`\n\n"
            + markdown_table_from_wallet_rows(rows)
        )
        markdown_out.parent.mkdir(parents=True, exist_ok=True)
        markdown_out.write_text(md, encoding="utf-8")
        typer.echo(f"Markdown -> {markdown_out}")


@app.command("compute-copyability-advisory")
def compute_copyability_advisory_cmd(
    wallet: list[str] | None = typer.Option(
        None,
        "--wallet",
        help="Run for these addresses only (repeat flag). When omitted, uses all promoted wallets.",
    ),
    horizon_days: float = typer.Option(
        30.0,
        "--horizon-days",
        envvar="HYPERION_COPYABILITY_HORIZON_DAYS",
        help="Rolling lookback anchored at NOW when selecting fills.",
    ),
    slippage_bps: float = typer.Option(
        7.0,
        "--slippage-bps",
        envvar="HYPERION_PAPER_REPLAY_SLIPPAGE_BPS",
        help="Replay slippage model (must match dashboards you reconcile against).",
    ),
    realized_only: bool = typer.Option(
        True,
        "--realized-only/--include-partial-closes",
        help="Reuse FIFO completeness posture from Step‑2 replay.",
    ),
) -> None:
    """
    Persist research-only copyability composites for API joins (migration 0013).

    Computes equal-weight blend of tape frequency / hold-shape / realized ratio percentiles /
    FIFO win-rate volatility. Never consulted by trader-engine promotion heuristics.
    """

    from hyperion_pipeline.analytics.copyability_advisory import compute_copyability_batch_inputs
    from hyperion_pipeline.analytics.paper_replay.slippage import LinearBpsSlippage
    from hyperion_pipeline.storage.postgres_copyability import insert_copyability_advisory_row
    from hyperion_pipeline.storage.postgres_fills import (
        fetch_promoted_wallet_addresses,
        fetch_trade_fills_for_wallet,
    )

    from hyperion_pipeline.models.pydantic_domain import TradeFill

    async def _persist() -> None:
        settings = get_settings()
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        try:
            wallets = (
                sorted({w.strip().lower() for w in wallet}) if wallet else await fetch_promoted_wallet_addresses(engine)
            )
            if not wallets:
                typer.secho("No wallets (--wallet empty or no promoted set).", fg="yellow")
                return
            anchor = datetime.now(timezone.utc)
            horizon = timedelta(days=horizon_days)
            fills_by_wallet: dict[str, list[TradeFill]] = {}
            for w in wallets:
                fills_by_wallet[w] = await fetch_trade_fills_for_wallet(
                    engine,
                    w,
                    ts_start=anchor - horizon,
                    ts_end=None,
                )

            comps = compute_copyability_batch_inputs(
                promoted_wallets=wallets,
                fills_by_wallet=fills_by_wallet,
                slippage_model=LinearBpsSlippage(slippage_bps),
                realized_only=realized_only,
            )

            persisted = 0
            for addr, snapshot in comps.items():
                inserted = await insert_copyability_advisory_row(
                    engine,
                    wallet=addr,
                    copyability_score=snapshot.copyability_score,
                    signal_frequency_component=snapshot.signal_frequency_component,
                    timing_component=snapshot.timing_component,
                    realized_ratio_component=snapshot.realized_ratio_component,
                    stability_component=snapshot.stability_component,
                    horizon_days=horizon_days,
                )
                if inserted:
                    persisted += 1
            typer.echo(f"wallets_considered={len(comps)} rows_inserted={persisted}")
        finally:
            await engine.dispose()

    asyncio.run(_persist())


@app.command("walk-forward-replay")
def walk_forward_replay_cmd(
    wallet: str = typer.Option(..., "--wallet", help="Lower/0x wallet matching Parquet ledger"),
    parquet_path: Path = typer.Option(
        ...,
        "--parquet",
        exists=True,
        dir_okay=False,
        help="Hive/flat Parquet with Step‑4 analytic columns.",
    ),
    slippage_bps: float = typer.Option(7.0, "--slippage-bps", envvar="HYPERION_PAPER_REPLAY_SLIPPAGE_BPS"),
    realized_only: bool = typer.Option(
        True,
        "--realized-only/--allow-partial-closes",
    ),
    train_frac: float = typer.Option(
        0.67,
        "--train-frac",
        envvar="HYPERION_WALK_FORWARD_TRAIN_FRAC",
        help="Calendar-day split for train bucket (first fraction).",
    ),
) -> None:
    """
    Manual research utility: 67/33 calendar walk-forward on a Parquet fill extract.
    """

    import pandas as pd

    from hyperion_pipeline.analytics.paper_replay.slippage import LinearBpsSlippage
    from hyperion_pipeline.analytics.walk_forward_replay import walk_forward_trade_metrics

    frame = pd.read_parquet(parquet_path)
    report = walk_forward_trade_metrics(
        frame,
        wallet,
        slippage_model=LinearBpsSlippage(slippage_bps),
        realized_only=realized_only,
        train_frac=train_frac,
    )
    train = report["train_metrics"]
    test = report["test_metrics"]
    typer.echo(
        "train "
        f"closed={train.closed_trips_n} win_rate={train.win_rate:.3f} "
        f"realized_usd={train.realized_closed_pnl_usd:.2f}"
    )
    typer.echo(
        "test "
        f"closed={test.closed_trips_n} win_rate={test.win_rate:.3f} "
        f"realized_usd={test.realized_closed_pnl_usd:.2f}"
    )
    typer.echo(
        f"stable_flag={report['stable_flag']} "
        f"train_days={report['train_days']} test_days={report['test_days']}"
    )


@app.command("screen-tracked-copy")
def screen_tracked_copy_cmd(
    wallet: list[str] | None = typer.Option(
        None,
        "--wallet",
        help="Override HYPERLIQUID_TRACKED_USERS (repeat flag for each address)",
    ),
    starting_equity: float = typer.Option(100.0, "--starting-equity"),
    min_trade_usd: float = typer.Option(20.0, "--min-trade-usd"),
    max_trade_usd: float = typer.Option(40.0, "--max-trade-usd"),
    max_equity_pct: float = typer.Option(0.45, "--max-equity-pct", help="Cap per fill as fraction of equity"),
    copy_scale: float = typer.Option(0.001, "--copy-scale"),
    fee_bps: float = typer.Option(4.0, "--fee-bps"),
    slippage_bps: float = typer.Option(6.0, "--slippage-bps"),
    rest_delay_sec: float = typer.Option(80.0, "--rest-delay-sec"),
    fills_source: str = typer.Option(
        "auto",
        "--fills-source",
        help="Fill tape: auto (PG then REST), postgres only, or rest for all (fairer cross-wallet)",
    ),
) -> None:
    """Screen all ``HYPERLIQUID_TRACKED_USERS`` with small-account copy-paper assumptions."""

    from typing import cast

    from hyperion_pipeline.analytics.tracked_alpha_screen import FillsSourceMode, run_screen

    if fills_source not in ("auto", "postgres", "rest"):
        raise typer.BadParameter("fills-source must be auto, postgres, or rest")
    ow = [w.strip().lower() for w in wallet] if wallet else None
    p = run_screen(
        starting_equity=starting_equity,
        min_trade_usd=min_trade_usd,
        max_trade_usd=max_trade_usd,
        max_equity_pct_per_fill=max_equity_pct,
        copy_scale=copy_scale,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        rest_delay_sec=rest_delay_sec,
        wallets_override=ow,
        fills_source=cast(FillsSourceMode, fills_source),
    )
    typer.echo(f"report -> {p}")


@app.command("wallet-filter-reset")
def wallet_filter_reset_cmd() -> None:
    """Demote discovery rankings and clear wallet filter state (keeps BANNED)."""

    from hyperion_pipeline.storage.wallet_filter_store import reset_wallet_filter_state

    async def _run() -> None:
        settings = get_settings()
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        try:
            traders, ranks = await reset_wallet_filter_state(engine)
            typer.echo(f"traders_reset={traders} discovery_demoted={ranks}")
        finally:
            await engine.dispose()

    asyncio.run(_run())


@app.command("wallet-filter-evaluate-all")
def wallet_filter_evaluate_all_cmd(
    sync_discovery: bool = typer.Option(
        True,
        "--sync-discovery/--no-sync-discovery",
        help="Mirror filter PROMOTED/tier into trader_discovery_rankings.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Max wallets to evaluate (default: all with fills).",
    ),
) -> None:
    """Batch wallet filter (stages 0–3) for every trader with fills in Postgres."""

    from hyperion_pipeline.wallet_filter import WalletFilterConfig, evaluate_wallet_filter
    from hyperion_pipeline.storage.postgres_fills import fetch_trade_fills_for_wallet
    from hyperion_pipeline.storage.wallet_filter_store import (
        fetch_wallet_filter_context,
        fetch_wallets_for_filter_batch,
        persist_wallet_filter_result,
    )

    cfg = WalletFilterConfig.from_env()

    async def _run() -> None:
        settings = get_settings()
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        promoted = banned = pending = 0
        try:
            rows = await fetch_wallets_for_filter_batch(engine)
            if limit is not None:
                rows = rows[:limit]
            typer.echo(f"wallets_to_evaluate={len(rows)}")
            for row in rows:
                w = str(row["wallet"])
                trader_id = row["trader_id"]
                ctx = await fetch_wallet_filter_context(engine, w)
                if ctx is None:
                    continue
                fills = await fetch_trade_fills_for_wallet(engine, w)
                existing = ctx.get("wallet_status")
                result = evaluate_wallet_filter(
                    w,
                    fills,
                    cfg=cfg,
                    existing_status=str(existing) if existing else None,
                    force_whale_promoted=bool(ctx.get("is_whale")),
                    pnl_snapshots_30d=int(ctx.get("pnl_snapshots_30d") or 0),
                    behavior_score=float(ctx["latest_behavior_score"])
                    if ctx.get("latest_behavior_score") is not None
                    else None,
                    scoring_runs=int(ctx.get("scoring_runs") or 0),
                )
                await persist_wallet_filter_result(
                    engine,
                    trader_id,
                    result,
                    fills,
                    sync_discovery=sync_discovery,
                )
                if result.status == "PROMOTED":
                    promoted += 1
                elif result.status == "BANNED":
                    banned += 1
                else:
                    pending += 1
            typer.echo(f"done promoted={promoted} banned={banned} pending_or_archived={pending}")
        finally:
            await engine.dispose()

    asyncio.run(_run())


@app.command("wallet-filter-evaluate")
def wallet_filter_evaluate_cmd(
    wallet: list[str] = typer.Option(
        ...,
        "--wallet",
        help="Wallet address(es) to run through stages 0–3 (repeat flag).",
    ),
    sync_discovery: bool = typer.Option(
        False,
        "--sync-discovery/--no-sync-discovery",
        help="Mirror PROMOTED/tier into trader_discovery_rankings (requires migration 0014).",
    ),
) -> None:
    """
  Run wallet_filter_system pipeline on Postgres fill tapes.

  Stages: HF label → PnL promotion → deep-dive gates → discovery/tier (PROMOTED only).
  """

    from hyperion_pipeline.wallet_filter import WalletFilterConfig, evaluate_wallet_filter
    from hyperion_pipeline.storage.postgres_fills import fetch_trade_fills_for_wallet
    from hyperion_pipeline.storage.wallet_filter_store import (
        fetch_wallet_filter_context,
        persist_wallet_filter_result,
    )

    cfg = WalletFilterConfig.from_env()

    async def _run() -> None:
        settings = get_settings()
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        try:
            table = Table(title="Wallet filter evaluation")
            table.add_column("wallet", overflow="ellipsis")
            table.add_column("status")
            table.add_column("label")
            table.add_column("promoted")
            table.add_column("tier")
            table.add_column("discovery")
            table.add_column("stage")
            console = Console()

            for w in wallet:
                ctx = await fetch_wallet_filter_context(engine, w)
                if ctx is None:
                    typer.secho(f"No trader row for {w} — backfill first.", fg="yellow", err=True)
                    continue
                fills = await fetch_trade_fills_for_wallet(engine, w)
                existing = ctx.get("wallet_status")
                result = evaluate_wallet_filter(
                    w,
                    fills,
                    cfg=cfg,
                    existing_status=str(existing) if existing else None,
                    force_whale_promoted=bool(ctx.get("is_whale")),
                    pnl_snapshots_30d=int(ctx.get("pnl_snapshots_30d") or 0),
                    behavior_score=float(ctx["latest_behavior_score"])
                    if ctx.get("latest_behavior_score") is not None
                    else None,
                    scoring_runs=int(ctx.get("scoring_runs") or 0),
                )
                await persist_wallet_filter_result(
                    engine,
                    ctx["trader_id"],
                    result,
                    fills,
                    sync_discovery=sync_discovery,
                )
                table.add_row(
                    result.wallet[:10] + "…",
                    result.status,
                    result.label,
                    str(result.promoted),
                    result.behavior_tier or "-",
                    f"{result.discovery_score:.1f}" if result.discovery_score is not None else "-",
                    str(result.stage_stopped),
                )
            console.print(table)
        finally:
            await engine.dispose()

    asyncio.run(_run())


@app.command("export-leaderboard")
def export_leaderboard_cmd() -> None:
    """Print example SQL for leaderboard export (Postgres)."""

    sql_path = Path(__file__).resolve().parents[3] / "example_queries" / "analytics_examples.sql"
    typer.echo(sql_path.read_text(encoding="utf-8"))


def main() -> None:
    app()
