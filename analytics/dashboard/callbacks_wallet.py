"""Wallet load, chart, and force-pull Dash callbacks."""

from __future__ import annotations

import subprocess
import sys
import time

import polars as pl
from dash import Input, Output, State, ctx, html, no_update

import twitter_pulls
import wallet_pulls
from analytics.lib import hl_fetch
from analytics.lib.spot_meta import format_coin_chart_title, format_coin_option_label
from analytics.lib.time_utils import candles_cover_bounds
from analytics.lib.wallet_scoring import score_wallet_from_fills
from wallet_chart import (
    _candle_range_note,
    _empty_figure,
    aggregate_order_placement_markers,
    aggregate_trades,
    build_figure,
    join_buckets_with_candles,
)
from wallet_layout import (
    BUY_GREEN,
    FORCE_PULL_WALLET_SCRIPT,
    REPO_ROOT,
    SELL_RED,
)
from wallet_score_panel import render_score_panel, render_score_placeholder


def pack_wallet_store(wallet: str) -> dict[str, str | int]:
    """Bump ``rev`` on every load/pull so Dash refreshes score even for same wallet."""
    return {"wallet": wallet.lower(), "rev": time.time_ns()}


def wallet_from_store(data) -> str | None:
    if not data:
        return None
    if isinstance(data, str):
        return data.lower()
    if isinstance(data, dict):
        wallet = data.get("wallet")
        return wallet.lower() if wallet else None
    return None


def wallet_exists(wallet: str) -> bool:
    return wallet_pulls.has_wallet_data(wallet)


def load_wallet(wallet: str) -> pl.DataFrame:
    return wallet_pulls.load_fills(wallet)


def coin_options(df: pl.DataFrame) -> list[dict[str, str]]:
    counts = (
        df.group_by("coin")
        .agg(pl.len().alias("n"))
        .sort("n", descending=True)
    )
    return [
        {
            "label": format_coin_option_label(r["coin"], int(r["n"])),
            "value": r["coin"],
        }
        for r in counts.iter_rows(named=True)
    ]


def _format_pulled_at(iso: str) -> str:
    """Format meta.json pulled_at for display (UTC, human-readable)."""
    if not iso:
        return ""
    try:
        normalized = iso.replace("Z", "").strip()
        if "T" in normalized:
            date_part, time_part = normalized.split("T", 1)
            time_part = time_part[:8]
            return f"{date_part} {time_part} UTC"
        return normalized[:10]
    except Exception:
        return iso[:19] if len(iso) >= 19 else iso


def _wallet_status_style(color: str) -> dict:
    return {"fontSize": "12px", "marginLeft": "8px", "color": color}


def _try_load_wallet(w: str) -> tuple[list, str, str, dict, dict] | None:
    """Load bundle into dashboard state; None when data missing or unreadable."""
    if not wallet_exists(w):
        return None
    try:
        wallet_pulls.clear_wallet_cache(w)
        df = load_wallet(w)
    except Exception:
        return None

    opts = coin_options(df)
    if not opts:
        return None

    msg = format_wallet_status_message(w, df, len(opts))
    return opts, opts[0]["value"], msg, _wallet_status_style(BUY_GREEN), pack_wallet_store(w)


def format_wallet_status_message(wallet: str, fills: pl.DataFrame, n_coins: int) -> str:
    stats = wallet_pulls.bundle_stats(wallet, fills)
    pulled_raw = stats.get("pulled_at") or ""
    pulled_display = _format_pulled_at(pulled_raw)

    if stats.get("is_bundle") and pulled_display:
        source = f"Bundle-Stand: {pulled_display}"
    elif stats.get("is_bundle"):
        source = "Bundle (ohne pulled_at in meta.json)"
    else:
        source = "Legacy Parquet (kein Bundle meta.json)"

    msg = (
        f"OK - {stats['fill_count']:,} Fills, "
        f"{stats['coin_count']} Coins | {source}"
    )

    msg += (
        f" | {n_coins} Coins im Dropdown — Chart wechselt pro Coin "
        f"(nicht nur BTC). Zeitraum: {stats['time_min']} ... {stats['time_max']}"
    )

    if stats.get("is_bundle"):
        msg += " | Nach externem CLI-Pull: «Wallet laden» klicken"

    orders_note = stats.get("orders_note") or ""
    order_count = stats.get("order_count") or 0
    if orders_note or order_count >= 1900:
        note = orders_note or "historicalOrders API liefert hoechstens ~2000 Orders"
        msg += f" | Hinweis Orders: {note} ({order_count:,} im Bundle)"

    market_failures = stats.get("market_failures") or []
    if market_failures:
        labels = []
        for entry in market_failures:
            coin = str(entry.get("coin") or "?")
            labels.append(format_coin_chart_title(coin))
        msg += (
            f" | Marktdaten unvollstaendig fuer: {', '.join(labels)}"
            f" (Chart laedt Kerzen live nach)"
        )
    return msg


def _build_score_panel(wallet: str, fills: pl.DataFrame) -> html.Div:
    stats = wallet_pulls.bundle_stats(wallet, fills)
    candles: dict[str, pl.DataFrame] = {}
    for coin in fills["coin"].unique().to_list():
        cndl = wallet_pulls.load_candles(wallet, str(coin), wallet_pulls.DEFAULT_INTERVAL)
        if cndl is not None and not cndl.is_empty():
            candles[str(coin)] = cndl

    try:
        result = score_wallet_from_fills(fills, candles_by_coin=candles or None)
    except Exception as exc:
        return render_score_placeholder(f"Berechnung fehlgeschlagen: {exc}")

    if result is None:
        return render_score_placeholder("Zu wenig Daten fuer einen Score.")
    return render_score_panel(result, stats)


def register_wallet_callbacks(app) -> None:
    @app.callback(
        Output("coin-dd", "options"),
        Output("coin-dd", "value"),
        Output("wallet-status", "children"),
        Output("wallet-status", "style"),
        Output("wallet-store", "data"),
        Input("load-btn", "n_clicks"),
        Input("wallet-input", "n_submit"),
        State("wallet-input", "value"),
        prevent_initial_call=True,
    )
    def on_load_wallet(_n_clicks: int, _n_submit: int, wallet: str | None):
        if not wallet or not wallet.strip().lower().startswith("0x"):
            return (
                [],
                None,
                "Bitte eine 0x... Wallet-Adresse eingeben.",
                _wallet_status_style(SELL_RED),
                None,
            )

        w = wallet.strip().lower()
        if not wallet_exists(w):
            msg = (
                f"Keine Wallet-Daten fuer {w}. "
                f"Bitte 'Wallet Force Pull' klicken oder: "
                f"python analytics/scripts/force_pull_wallet_bundle.py {w}"
            )
            return [], None, msg, _wallet_status_style(SELL_RED), None

        loaded = _try_load_wallet(w)
        if loaded is None:
            return (
                [],
                None,
                "Fehler beim Lesen oder Wallet enthaelt keine Trades.",
                _wallet_status_style(SELL_RED),
                None,
            )

        opts, default_coin, msg, style, store = loaded
        return opts, default_coin, msg, style, store

    @app.callback(
        Output("score-panel", "children"),
        Input("wallet-store", "data"),
        Input("load-btn", "n_clicks"),
    )
    def on_score_panel(store_data, _load_clicks: int | None):
        wallet = wallet_from_store(store_data)
        if not wallet:
            return render_score_placeholder("Bitte Wallet laden.")
        try:
            wallet_pulls.clear_wallet_cache(wallet)
            df = load_wallet(wallet)
            return _build_score_panel(wallet, df)
        except Exception as exc:
            return render_score_placeholder(f"Score fehlgeschlagen: {exc}")

    @app.callback(
        Output("coin-hint", "children"),
        Input("wallet-store", "data"),
        Input("coin-dd", "value"),
    )
    def on_coin_hint(store_data, coin: str | None):
        wallet = wallet_from_store(store_data)
        if not wallet:
            return "Schritt 1: Wallet-Adresse eingeben und «Wallet laden» klicken."
        if not coin:
            return f"Schritt 2: Coin waehlen ({wallet[:10]}... geladen)."
        coin_label = format_coin_chart_title(coin)
        hint = (
            f"Aktiver Chart: {coin_label}  |  "
            f"Coin im Dropdown wechseln fuer andere Token."
        )
        failure = wallet_pulls.market_failure_for_coin(wallet, coin)
        if failure and failure.get("error"):
            if failure.get("candles"):
                hint += (
                    "  |  Kerzen beim Pull fehlgeschlagen — Live-Nachladen beim Chart-Oeffnen."
                )
            else:
                hint += "  |  Marktdaten beim Pull unvollstaendig — Chart versucht Live-Nachladen."
        return hint

    @app.callback(
        Output("chart", "figure"),
        Input("wallet-store", "data"),
        Input("coin-dd", "value"),
        Input("interval-dd", "value"),
        Input("tw-history-store", "data"),
        prevent_initial_call=True,
    )
    def on_chart_change(
        store_data,
        coin: str | None,
        interval: str | None,
        _history,
    ):
        wallet = wallet_from_store(store_data)
        trigger = ctx.triggered_id
        print(
            f"[chart] trigger={trigger!r} wallet={wallet!r} coin={coin!r} interval={interval!r}",
            flush=True,
        )

        if not wallet:
            return _empty_figure("Bitte zuerst «Wallet laden» klicken.")
        if not coin:
            return _empty_figure("Bitte einen Coin im Dropdown waehlen.")
        if not interval:
            return _empty_figure("Bitte ein Intervall waehlen.")

        coin_label = format_coin_chart_title(coin)

        try:
            df = load_wallet(wallet)

            trades_for_coin = df.filter(pl.col("coin") == coin)
            if trades_for_coin.is_empty():
                return _empty_figure(f"Keine Trades fuer {coin_label}.")

            start_ms, end_ms = wallet_pulls.coin_time_bounds(df, coin)
            step_ms = hl_fetch.INTERVAL_MS.get(interval or "", 0)

            candles_df = wallet_pulls.load_candles(wallet, coin, interval)
            needs_fetch = (
                candles_df is None
                or candles_df.is_empty()
                or not candles_cover_bounds(
                    candles_df, start_ms, end_ms, slack_ms=step_ms
                )
            )
            if needs_fetch:
                candles = hl_fetch.fetch_candles_chunked(
                    coin, interval, start_ms, end_ms
                )
                candles_df = hl_fetch.candles_to_dataframe(candles)
                if not candles_df.is_empty():
                    wallet_pulls.save_candles(wallet, coin, interval, candles_df)
                    wallet_pulls.clear_wallet_cache(wallet)
                    reloaded = wallet_pulls.load_candles(wallet, coin, interval)
                    if reloaded is not None and not reloaded.is_empty():
                        candles_df = reloaded

            if candles_df.is_empty():
                return _empty_figure(
                    f"Keine Kerzen fuer {coin_label} @ {interval} erhalten."
                )

            funding_df = wallet_pulls.load_market_funding(wallet, coin)
            chart_end = candles_df["timestamp"].max()
            cache_stale = (
                funding_df is None
                or funding_df.is_empty()
                or funding_df["timestamp"].max() < chart_end
            )
            if cache_stale:
                try:
                    funding_df = hl_fetch.funding_history_to_dataframe(
                        hl_fetch.fetch_funding_history(coin, start_ms, end_ms)
                    )
                except Exception:
                    if funding_df is None:
                        funding_df = pl.DataFrame()

            orders_df = wallet_pulls.load_orders(wallet).filter(pl.col("coin") == coin)
            order_placement = aggregate_order_placement_markers(
                orders_df, interval, coin=coin
            )
            order_placement_joined = join_buckets_with_candles(
                order_placement, candles_df
            )
            ledger_df = wallet_pulls.load_ledger(wallet)
            candle_note = _candle_range_note(candles_df, trades_for_coin)

            user_funding = wallet_pulls.load_user_funding(wallet).filter(
                pl.col("coin") == coin
            )
            user_funding_total = None
            if not user_funding.is_empty():
                chart_start = candles_df["timestamp"].min()
                chart_end = candles_df["timestamp"].max()
                uf_in_range = user_funding.filter(
                    (pl.col("timestamp") >= chart_start)
                    & (pl.col("timestamp") <= chart_end)
                )
                if not uf_in_range.is_empty():
                    user_funding_total = float(uf_in_range["usdc"].sum())

            buckets = aggregate_trades(df, coin, interval)
            buckets_joined = join_buckets_with_candles(buckets, candles_df)

            tweets_df = twitter_pulls.load_visible_tweets()

            return build_figure(
                coin,
                interval,
                candles_df,
                buckets_joined,
                wallet,
                tweets_df=tweets_df,
                orders_df=orders_df,
                order_placement_joined=order_placement_joined,
                funding_df=funding_df,
                user_funding_total=user_funding_total,
                ledger_df=ledger_df,
                candle_note=candle_note,
                uirevision=f"{wallet}-{coin}-{interval}",
                coin_display=coin_label,
            )
        except Exception as exc:
            import traceback

            traceback.print_exc()
            return _empty_figure(f"Chart-Fehler fuer {coin_label}: {exc}")

    @app.callback(
        Output("wallet-pull-status", "children"),
        Output("wallet-pull-status", "style"),
        Output("wallet-status-interval", "disabled"),
        Output("coin-dd", "options", allow_duplicate=True),
        Output("coin-dd", "value", allow_duplicate=True),
        Output("wallet-status", "children", allow_duplicate=True),
        Output("wallet-status", "style", allow_duplicate=True),
        Output("wallet-store", "data", allow_duplicate=True),
        Input("wallet-pull-btn", "n_clicks"),
        Input("wallet-status-interval", "n_intervals"),
        State("wallet-input", "value"),
        prevent_initial_call=True,
    )
    def on_wallet_force_pull(_n_clicks, _n_intervals, wallet: str | None):
        trigger = ctx.triggered_id
        pull_style = {"fontSize": "12px", "marginLeft": "8px"}
        idle_load = (no_update, no_update, no_update, no_update, no_update)

        if trigger == "wallet-pull-btn":
            if not wallet or not wallet.strip().lower().startswith("0x"):
                return (
                    "Bitte zuerst eine 0x... Wallet-Adresse eingeben.",
                    {**pull_style, "color": SELL_RED},
                    True,
                    *idle_load,
                )
            w = wallet.strip().lower()
            try:
                subprocess.Popen(
                    [sys.executable, str(FORCE_PULL_WALLET_SCRIPT), w],
                    cwd=str(REPO_ROOT),
                )
            except Exception as exc:
                return (
                    f"Konnte Pull nicht starten: {exc}",
                    {**pull_style, "color": SELL_RED},
                    True,
                    *idle_load,
                )
            return (
                f"Starte Bundle-Pull fuer {w[:10]}...",
                {**pull_style, "color": BUY_GREEN},
                False,
                *idle_load,
            )

        if not wallet or not wallet.strip().lower().startswith("0x"):
            return no_update, no_update, True, *idle_load

        w = wallet.strip().lower()
        status = wallet_pulls.load_status(w)
        state = status.get("state", "")
        msg = status.get("message", "")

        if state == "done":
            fills = status.get("fill_count", 0)
            loaded = _try_load_wallet(w)
            if loaded is not None:
                opts, default_coin, status_msg, status_style, store = loaded
                pull_text = msg or f"Fertig - {fills:,} Fills — Wallet geladen."
                return (
                    pull_text,
                    {**pull_style, "color": BUY_GREEN},
                    True,
                    opts,
                    default_coin,
                    status_msg,
                    status_style,
                    store,
                )
            pull_text = msg or f"Fertig - {fills:,} Fills gespeichert. «Wallet laden» klicken."
            return pull_text, {**pull_style, "color": BUY_GREEN}, True, *idle_load

        if state == "error":
            err = status.get("error", "")
            return (
                f"Fehler: {err or msg}",
                {**pull_style, "color": SELL_RED},
                True,
                *idle_load,
            )

        coin_idx = status.get("coin_index", 0)
        coin_total = status.get("coin_total", 0)
        current = status.get("current_coin", "")
        parts = [msg or "Laeuft ..."]
        if coin_total and current:
            parts.append(f"Coin {coin_idx}/{coin_total}: {current}")
        return (
            " - ".join(parts),
            {**pull_style, "color": BUY_GREEN},
            False,
            *idle_load,
        )
