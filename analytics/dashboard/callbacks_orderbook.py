"""S3 order book pull Dash callbacks."""

from __future__ import annotations

import subprocess
import sys

from dash import Input, Output, State, ctx, no_update

import orderbook_pulls
from analytics.lib.s3_quota import format_usage_display
from wallet_layout import BUY_GREEN, FORCE_PULL_ORDERBOOK_SCRIPT, REPO_ROOT, SELL_RED


def register_orderbook_callbacks(app) -> None:
    @app.callback(
        Output("s3-quota-line", "children"),
        Output("s3-quota-line", "style"),
        Input("wallet-store", "data"),
        Input("orderbook-status-interval", "n_intervals"),
    )
    def on_s3_quota_refresh(_store, _n):
        text, level = format_usage_display()
        color = "#ffb74d" if level == "warn" else "#90caf9"
        return text, {"fontSize": "11px", "color": color, "opacity": 0.9}

    @app.callback(
        Output("orderbook-pull-status", "children"),
        Output("orderbook-pull-status", "style"),
        Output("orderbook-status-interval", "disabled"),
        Output("wallet-store", "data", allow_duplicate=True),
        Input("orderbook-pull-btn", "n_clicks"),
        Input("orderbook-status-interval", "n_intervals"),
        State("wallet-input", "value"),
        State("coin-dd", "value"),
        State("wallet-store", "data"),
        prevent_initial_call=True,
    )
    def on_orderbook_pull(_n_clicks, _n_intervals, wallet, coin, store_data):
        from callbacks_wallet import pack_wallet_store, wallet_from_store

        pull_style = {"fontSize": "12px", "marginLeft": "8px"}
        trigger = ctx.triggered_id

        if trigger == "orderbook-pull-btn":
            if not wallet or not wallet.strip().lower().startswith("0x"):
                return (
                    "Bitte zuerst eine 0x... Wallet-Adresse eingeben.",
                    {**pull_style, "color": SELL_RED},
                    True,
                    no_update,
                )
            if not coin:
                return (
                    "Bitte zuerst Wallet laden und einen Coin waehlen.",
                    {**pull_style, "color": SELL_RED},
                    True,
                    no_update,
                )
            w = wallet.strip().lower()
            try:
                subprocess.Popen(
                    [
                        sys.executable,
                        str(FORCE_PULL_ORDERBOOK_SCRIPT),
                        w,
                        coin,
                    ],
                    cwd=str(REPO_ROOT),
                )
            except Exception as exc:
                return (
                    f"Konnte Order-Book-Pull nicht starten: {exc}",
                    {**pull_style, "color": SELL_RED},
                    True,
                    no_update,
                )
            return (
                f"Starte S3 Order-Book-Pull fuer {coin} ...",
                {**pull_style, "color": BUY_GREEN},
                False,
                no_update,
            )

        w = (wallet or wallet_from_store(store_data) or "").strip().lower()
        if not w.startswith("0x"):
            return no_update, no_update, True, no_update

        status = orderbook_pulls.load_orderbook_status(w)
        state = status.get("state", "")
        msg = status.get("message", "")

        if state == "done":
            orderbook_pulls.clear_orderbook_cache()
            from analytics.lib.liquidity_baseline import ensure_baseline

            ensure_baseline()
            store = store_data if isinstance(store_data, dict) else None
            if store and store.get("wallet") == w:
                new_store = pack_wallet_store(w)
            elif store:
                new_store = {**store, "ob_rev": status.get("updated_at", "")}
            else:
                new_store = pack_wallet_store(w)
            return (
                msg or "Order-Book-Pull fertig.",
                {**pull_style, "color": BUY_GREEN},
                True,
                new_store,
            )

        if state == "error":
            return (
                f"Fehler: {status.get('error') or msg}",
                {**pull_style, "color": SELL_RED},
                True,
                no_update,
            )

        if state == "running":
            hour_idx = status.get("hour_index", 0)
            hour_total = status.get("hours_total", 0)
            est = status.get("estimated_bytes")
            parts = [msg or "Laeuft ..."]
            if hour_total:
                parts.append(f"Stunde {hour_idx}/{hour_total}")
            if est:
                parts.append(f"~{est / (1024**2):.1f} MB geschaetzt")
            return (
                " — ".join(parts),
                {**pull_style, "color": BUY_GREEN},
                False,
                no_update,
            )

        return no_update, no_update, True, no_update
