"""Twitter pull and drawer Dash callbacks."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime

from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

import twitter_pulls
from wallet_layout import (
    BUY_GREEN,
    FORCE_PULL_TWITTER_SCRIPT,
    GRID,
    REPO_ROOT,
    SELL_RED,
    TWEET_CYAN,
)


def _render_history_rows(history: list[dict]) -> list:
    if not history:
        return [
            html.Div(
                "Noch keine Pulls.",
                style={"fontSize": "12px", "opacity": 0.6},
            )
        ]
    rows = []
    for h in history:
        key = {"handle": h["handle"], "from": h["from"], "to": h["to"]}
        rows.append(
            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "20px 1fr auto",
                    "alignItems": "center",
                    "gap": "6px",
                    "padding": "6px",
                    "borderBottom": f"1px solid {GRID}",
                    "fontSize": "12px",
                },
                children=[
                    dcc.Checklist(
                        id={"role": "tw-row-visible", "key": json.dumps(key, sort_keys=True)},
                        options=[{"label": "", "value": "v"}],
                        value=["v"] if h.get("visible", True) else [],
                        style={"display": "inline-block"},
                        inputStyle={"transform": "scale(1.1)"},
                    ),
                    html.Div(
                        children=[
                            html.Div(
                                f"@{h['handle']}",
                                style={"fontWeight": 600, "color": TWEET_CYAN},
                            ),
                            html.Div(
                                f"{h['from']}  ->  {h['to']}",
                                style={"opacity": 0.7, "fontSize": "11px"},
                            ),
                            html.Div(
                                f"{h.get('count', 0)} Tweets",
                                style={"opacity": 0.6, "fontSize": "11px"},
                            ),
                        ],
                    ),
                    html.Button(
                        "x",
                        id={"role": "tw-row-delete", "key": json.dumps(key, sort_keys=True)},
                        n_clicks=0,
                        style={
                            "backgroundColor": "transparent",
                            "color": SELL_RED,
                            "border": f"1px solid {SELL_RED}",
                            "borderRadius": "3px",
                            "padding": "2px 8px",
                            "cursor": "pointer",
                            "fontSize": "12px",
                        },
                    ),
                ],
            )
        )
    return rows


def register_twitter_callbacks(app) -> None:
    @app.callback(
        Output("tw-history-list", "children"),
        Input("tw-history-store", "data"),
    )
    def on_render_history(history):
        return _render_history_rows(history or [])

    @app.callback(
        Output("tw-pull-status", "children"),
        Output("tw-pull-status", "style"),
        Output("tw-status-interval", "disabled"),
        Output("tw-history-store", "data", allow_duplicate=True),
        Input("tw-pull-btn", "n_clicks"),
        Input("tw-status-interval", "n_intervals"),
        State("tw-handle", "value"),
        State("tw-from", "value"),
        State("tw-to", "value"),
        State("tw-history-store", "data"),
        prevent_initial_call=True,
    )
    def on_force_pull(_n_clicks, _n_intervals, handle, frm, to, history):
        trigger = ctx.triggered_id
        base_style = {"fontSize": "12px", "marginLeft": "8px"}

        if trigger == "tw-pull-btn":
            if not handle or not handle.strip():
                return (
                    "Bitte @handle angeben.",
                    {**base_style, "color": SELL_RED},
                    True,
                    no_update,
                )
            try:
                datetime.strptime((frm or "").strip(), "%Y-%m-%d")
                datetime.strptime((to or "").strip(), "%Y-%m-%d")
            except ValueError:
                return (
                    "Datum muss YYYY-MM-DD sein.",
                    {**base_style, "color": SELL_RED},
                    True,
                    no_update,
                )

            clean_handle = handle.strip().lstrip("@")
            try:
                subprocess.Popen(
                    [
                        sys.executable,
                        str(FORCE_PULL_TWITTER_SCRIPT),
                        "--handle",
                        clean_handle,
                        "--from",
                        frm.strip(),
                        "--to",
                        to.strip(),
                    ],
                    cwd=str(REPO_ROOT),
                )
            except Exception as exc:
                return (
                    f"Konnte Scraper nicht starten: {exc}",
                    {**base_style, "color": SELL_RED},
                    True,
                    no_update,
                )

            return (
                f"Starte Pull fuer @{clean_handle} ...",
                {**base_style, "color": TWEET_CYAN},
                False,
                no_update,
            )

        status = twitter_pulls.load_status()
        state = status.get("state", "")
        msg = status.get("message", "")
        found = status.get("found", 0)
        last_date = status.get("last_date", "")

        if state == "done":
            new_history = twitter_pulls.load_history()
            text = f"Fertig - {found} Tweets gespeichert."
            return text, {**base_style, "color": BUY_GREEN}, True, new_history

        if state == "error":
            err = status.get("error", "")
            return (
                f"Fehler: {err or msg}",
                {**base_style, "color": SELL_RED},
                True,
                no_update,
            )

        parts = [msg or "Laeuft ..."]
        if found:
            parts.append(f"{found} Tweets")
        if last_date:
            parts.append(f"bei {last_date}")
        return (
            " - ".join(parts),
            {**base_style, "color": TWEET_CYAN},
            False,
            no_update,
        )

    @app.callback(
        Output("tw-history-store", "data", allow_duplicate=True),
        Input({"role": "tw-row-visible", "key": ALL}, "value"),
        State({"role": "tw-row-visible", "key": ALL}, "id"),
        prevent_initial_call=True,
    )
    def on_visibility_change(values, ids):
        if not ids:
            return no_update
        history = twitter_pulls.load_history()
        changed = False
        for val, ident in zip(values, ids):
            key = json.loads(ident["key"])
            visible = "v" in (val or [])
            for h in history:
                if (
                    h.get("handle") == key["handle"]
                    and h.get("from") == key["from"]
                    and h.get("to") == key["to"]
                ):
                    if bool(h.get("visible", True)) != visible:
                        h["visible"] = visible
                        changed = True
        if not changed:
            return no_update
        twitter_pulls.save_history(history)
        return history

    @app.callback(
        Output("tw-confirm-delete", "displayed"),
        Output("tw-pending-delete", "data"),
        Input({"role": "tw-row-delete", "key": ALL}, "n_clicks"),
        State({"role": "tw-row-delete", "key": ALL}, "id"),
        prevent_initial_call=True,
    )
    def on_delete_clicked(n_clicks_list, ids):
        if not any(n_clicks_list or []):
            return False, no_update
        triggered = ctx.triggered_id
        if not triggered or not isinstance(triggered, dict):
            return False, no_update
        key = json.loads(triggered["key"])
        return True, key

    @app.callback(
        Output("tw-history-store", "data", allow_duplicate=True),
        Input("tw-confirm-delete", "submit_n_clicks"),
        State("tw-pending-delete", "data"),
        prevent_initial_call=True,
    )
    def on_delete_confirmed(_submit, key):
        if not key:
            return no_update
        new_history = twitter_pulls.delete_pull(key["handle"], key["from"], key["to"])
        return new_history

    @app.callback(
        Output("tw-drawer-store", "data"),
        Input("chart", "clickData"),
        Input("tw-drawer-close", "n_clicks"),
        Input("tw-drawer-prev", "n_clicks"),
        Input("tw-drawer-next", "n_clicks"),
        State("tw-drawer-store", "data"),
        prevent_initial_call=True,
    )
    def on_drawer_update(click_data, _close, _prev, _next, store):
        store = store or {"open": False, "ids": [], "idx": 0}
        trigger = ctx.triggered_id

        if trigger == "tw-drawer-close":
            return {**store, "open": False}

        if trigger == "tw-drawer-prev":
            idx = max(0, store.get("idx", 0) - 1)
            return {**store, "idx": idx}

        if trigger == "tw-drawer-next":
            ids = store.get("ids", [])
            idx = min(len(ids) - 1, store.get("idx", 0) + 1)
            return {**store, "idx": idx}

        if trigger == "chart" and click_data:
            points = click_data.get("points") or []
            if not points:
                return no_update
            cd = points[0].get("customdata")
            if isinstance(cd, dict) and cd.get("kind") == "tweets":
                ids = list(cd.get("ids") or [])
                if ids:
                    return {"open": True, "ids": ids, "idx": 0}
            return no_update

        return no_update

    @app.callback(
        Output("tw-drawer", "style"),
        Output("tw-drawer-title", "children"),
        Output("tw-drawer-counter", "children"),
        Output("tw-drawer-body", "children"),
        Input("tw-drawer-store", "data"),
    )
    def on_render_drawer(store):
        base_style = {
            "position": "fixed",
            "top": "0",
            "right": "0",
            "width": "420px",
            "height": "100vh",
            "backgroundColor": "#0a0a1e",
            "borderLeft": f"1px solid {GRID}",
            "boxShadow": "-6px 0 24px rgba(0,0,0,0.6)",
            "padding": "16px",
            "overflowY": "auto",
            "zIndex": 2000,
            "transition": "transform 0.25s ease-in-out",
        }

        store = store or {"open": False, "ids": [], "idx": 0}
        if not store.get("open") or not store.get("ids"):
            return ({**base_style, "transform": "translateX(100%)"}, "", "", "")

        ids = store["ids"]
        idx = max(0, min(len(ids) - 1, store.get("idx", 0)))
        tweets = twitter_pulls.tweets_for_ids(ids)
        if not tweets:
            return (
                {**base_style, "transform": "translateX(0)"},
                "Tweets nicht gefunden",
                "",
                html.Div("Pull-Datei eventuell geloescht."),
            )

        idx = max(0, min(len(tweets) - 1, idx))
        t = tweets[idx]
        title = f"@{t['handle']} - {t['created_at']}"
        counter = f"{idx + 1} / {len(tweets)}"

        media_children = []
        for url in t.get("media_urls") or []:
            if url.lower().endswith(".mp4"):
                media_children.append(
                    html.Video(
                        src=url,
                        controls=True,
                        style={
                            "width": "100%",
                            "marginTop": "8px",
                            "borderRadius": "4px",
                        },
                    )
                )
            else:
                media_children.append(
                    html.Img(
                        src=url,
                        style={
                            "width": "100%",
                            "marginTop": "8px",
                            "borderRadius": "4px",
                        },
                    )
                )

        body = html.Div(
            children=[
                html.Div(
                    t.get("text") or "",
                    style={
                        "whiteSpace": "pre-wrap",
                        "lineHeight": "1.4",
                        "fontSize": "13px",
                        "marginBottom": "8px",
                    },
                ),
                html.Div(media_children),
                html.A(
                    "Auf X oeffnen",
                    href=t.get("url") or "#",
                    target="_blank",
                    style={
                        "display": "inline-block",
                        "marginTop": "12px",
                        "color": TWEET_CYAN,
                        "textDecoration": "none",
                        "border": f"1px solid {TWEET_CYAN}",
                        "padding": "4px 10px",
                        "borderRadius": "4px",
                    },
                ),
            ],
        )

        return (
            {**base_style, "transform": "translateX(0)"},
            title,
            counter,
            body,
        )
