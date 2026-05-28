"""Dash layout and UI constants for Wallet Explorer."""

from __future__ import annotations

from pathlib import Path

from dash import dcc, html

import twitter_pulls

REPO_ROOT = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

HL_API = "https://api.hyperliquid.xyz/info"
HL_HEADERS = {"Content-Type": "application/json"}
HL_TIMEOUT = 20

INTERVALS = ["15m", "30m", "1h", "4h", "1d", "1w", "1M"]
DEFAULT_INTERVAL = "4h"

POLARS_EVERY = {
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
    "1M": "1mo",
}

BUY_GREEN = "#00e676"
SELL_RED = "#ff1744"
CANDLE_UP = "#26a69a"
CANDLE_DOWN = "#ef5350"
BG_PAPER = "#070714"
BG_PLOT = "#0a0a1e"
GRID = "#14142a"
TEXT_DIM = "#c0c0e0"
TWEET_CYAN = "#4fc3f7"
TWEET_AMBER = "#ffb74d"
LEDGER_AMBER = "#ffc107"
ORDER_FILLED = "#00e676"
ORDER_CANCEL = "#78909c"
ORDER_OPEN = "#64b5f6"
ORDER_LINE_OPACITY = 0.42

ORDER_LINE_STYLES: dict[str, dict] = {
    "long_open": {"color": BUY_GREEN, "dash": "solid", "name": "Limit Long Open"},
    "long_close": {"color": SELL_RED, "dash": "solid", "name": "Limit Long Close"},
    "short_open": {"color": BUY_GREEN, "dash": "dot", "name": "Limit Short Open"},
    "short_close": {"color": SELL_RED, "dash": "dot", "name": "Limit Short Close"},
    "canceled": {"color": LEDGER_AMBER, "dash": "solid", "name": "Limit Canceled (Long)"},
    "canceled_short": {
        "color": LEDGER_AMBER,
        "dash": "dot",
        "name": "Limit Canceled (Short)",
    },
}

SPOT_ORDER_LINE_STYLES: dict[str, dict] = {
    "spot_limit_buy": {"color": BUY_GREEN, "dash": "solid", "name": "Limit Kauf"},
    "spot_limit_sell": {"color": SELL_RED, "dash": "solid", "name": "Limit Verkauf"},
    "spot_limit_close": {"color": LEDGER_AMBER, "dash": "solid", "name": "Limit Close"},
    "spot_limit_canceled": {"color": LEDGER_AMBER, "dash": "dot", "name": "Limit Cancel"},
}

PLACEMENT_MARKER_STYLES: dict[str, dict] = {
    "long_open": {"letter": "X", "color": BUY_GREEN, "anchor": "high", "offset": 1.038},
    "long_close": {"letter": "X", "color": SELL_RED, "anchor": "high", "offset": 1.052},
    "short_open": {"letter": "Y", "color": BUY_GREEN, "anchor": "low", "offset": 0.962},
    "short_close": {"letter": "Y", "color": SELL_RED, "anchor": "low", "offset": 0.948},
    "canceled": {"letter": "X", "color": LEDGER_AMBER, "anchor": "high", "offset": 1.024},
    "canceled_short": {"letter": "Y", "color": LEDGER_AMBER, "anchor": "low", "offset": 0.976},
}

SPOT_PLACEMENT_MARKER_STYLES: dict[str, dict] = {
    "spot_limit_buy": {"letter": "X", "color": BUY_GREEN, "anchor": "high", "offset": 1.038},
    "spot_limit_sell": {"letter": "X", "color": SELL_RED, "anchor": "low", "offset": 0.962},
    "spot_limit_close": {"letter": "X", "color": LEDGER_AMBER, "anchor": "high", "offset": 1.052},
    "spot_limit_canceled": {"letter": "X", "color": LEDGER_AMBER, "anchor": "high", "offset": 1.024},
}

FORCE_PULL_TWITTER_SCRIPT = REPO_ROOT / "analytics" / "scripts" / "force_pull_twitter.py"
FORCE_PULL_WALLET_SCRIPT = REPO_ROOT / "analytics" / "scripts" / "force_pull_wallet_bundle.py"

CHART_GRAPH_CONFIG = {
    "scrollZoom": True,
    "displaylogo": False,
    "doubleClick": "reset+autosize",
    "displayModeBar": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
}


def build_layout(initial_figure):
    return html.Div(
    style={
        "backgroundColor": BG_PAPER,
        "color": TEXT_DIM,
        "minHeight": "100vh",
        "fontFamily": "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
        "padding": "20px",
        "position": "relative",
    },
    children=[
        html.H2("Hyperion - Wallet Explorer", style={"margin": "0 0 4px 0"}),
        html.Div(
            "Wallet eingeben -> Coin auswaehlen -> Intervall durchschalten.",
            style={"fontSize": "13px", "opacity": 0.7, "marginBottom": "16px"},
        ),

        html.Div(
            style={
                "display": "flex", "gap": "10px", "alignItems": "center",
                "flexWrap": "wrap", "marginBottom": "12px",
            },
            children=[
                dcc.Input(
                    id="wallet-input",
                    type="text",
                    placeholder="0x... (Wallet-Adresse)",
                    debounce=False,
                    n_submit=0,
                    style={
                        "width": "440px", "padding": "8px",
                        "backgroundColor": BG_PLOT, "color": TEXT_DIM,
                        "border": f"1px solid {GRID}", "borderRadius": "4px",
                        "fontFamily": "monospace",
                    },
                ),
                html.Button(
                    "Wallet laden",
                    id="load-btn", n_clicks=0,
                    style={
                        "padding": "8px 16px",
                        "backgroundColor": "#1e1e3a", "color": TEXT_DIM,
                        "border": f"1px solid {GRID}", "borderRadius": "4px",
                        "cursor": "pointer",
                    },
                ),
                html.Button(
                    "Wallet Force Pull",
                    id="wallet-pull-btn", n_clicks=0,
                    style={
                        "padding": "8px 16px",
                        "backgroundColor": "#1a3a2a", "color": BUY_GREEN,
                        "border": f"1px solid {BUY_GREEN}", "borderRadius": "4px",
                        "cursor": "pointer", "fontWeight": 600,
                    },
                ),
                html.Div(
                    id="wallet-status",
                    style={"fontSize": "12px", "marginLeft": "8px"},
                ),
                html.Div(
                    id="wallet-pull-status",
                    style={"fontSize": "12px", "marginLeft": "8px", "opacity": 0.85},
                ),
            ],
        ),
        html.Div(
            "Wallet Pull: volle Historie (Fills, Orders, Funding, Ledger, Kerzen 4h pro Coin). Kann bei vielen Coins mehrere Minuten dauern.",
            style={"fontSize": "11px", "opacity": 0.55, "marginBottom": "12px"},
        ),

        dcc.Loading(
            id="score-loading",
            type="circle",
            color=BUY_GREEN,
            children=html.Div(
                id="score-panel",
                children="Research Score erscheint nach «Wallet laden».",
                style={
                    "fontSize": "12px",
                    "opacity": 0.55,
                    "marginBottom": "12px",
                    "minHeight": "48px",
                },
            ),
            style={"marginBottom": "12px"},
        ),

        html.Div(
            style={
                "display": "flex", "gap": "10px", "alignItems": "center",
                "flexWrap": "wrap", "marginBottom": "12px",
            },
            children=[
                html.Label("Coin:", style={"minWidth": "60px"}),
                dcc.Dropdown(
                    id="coin-dd",
                    options=[],
                    placeholder="Erst Wallet laden ...",
                    clearable=False,
                    searchable=True,
                    style={"width": "320px", "color": "#000"},
                ),
                html.Label("Intervall:", style={"minWidth": "70px", "marginLeft": "12px"}),
                dcc.Dropdown(
                    id="interval-dd",
                    options=[{"label": i, "value": i} for i in INTERVALS],
                    value=DEFAULT_INTERVAL,
                    clearable=False,
                    style={"width": "120px", "color": "#000"},
                ),
            ],
        ),
        html.Div(
            id="coin-hint",
            children="Nach Wallet laden: Coin im Dropdown waehlen — pro Coin ein eigener Chart (Kerzen 4h aus Bundle).",
            style={"fontSize": "11px", "opacity": 0.6, "marginBottom": "12px"},
        ),

        html.Div(
            style={
                "display": "flex", "gap": "10px", "alignItems": "center",
                "flexWrap": "wrap", "marginBottom": "8px",
                "padding": "10px", "border": f"1px solid {GRID}",
                "borderRadius": "6px", "backgroundColor": "#0c0c20",
            },
            children=[
                html.Span("Twitter Force Pull:", style={"fontWeight": 600, "color": TWEET_CYAN}),
                dcc.Input(
                    id="tw-handle",
                    type="text",
                    placeholder="@handle",
                    style={
                        "width": "160px", "padding": "6px",
                        "backgroundColor": BG_PLOT, "color": TEXT_DIM,
                        "border": f"1px solid {GRID}", "borderRadius": "4px",
                        "fontFamily": "monospace",
                    },
                ),
                dcc.Input(
                    id="tw-from",
                    type="text",
                    placeholder="Von YYYY-MM-DD",
                    style={
                        "width": "150px", "padding": "6px",
                        "backgroundColor": BG_PLOT, "color": TEXT_DIM,
                        "border": f"1px solid {GRID}", "borderRadius": "4px",
                        "fontFamily": "monospace",
                    },
                ),
                dcc.Input(
                    id="tw-to",
                    type="text",
                    placeholder="Bis YYYY-MM-DD",
                    style={
                        "width": "150px", "padding": "6px",
                        "backgroundColor": BG_PLOT, "color": TEXT_DIM,
                        "border": f"1px solid {GRID}", "borderRadius": "4px",
                        "fontFamily": "monospace",
                    },
                ),
                html.Button(
                    "Force Pull Twitter",
                    id="tw-pull-btn", n_clicks=0,
                    style={
                        "padding": "6px 14px",
                        "backgroundColor": "#1e3a5f", "color": TWEET_CYAN,
                        "border": f"1px solid {TWEET_CYAN}", "borderRadius": "4px",
                        "cursor": "pointer", "fontWeight": 600,
                    },
                ),
                html.Span(
                    id="tw-pull-status",
                    style={"fontSize": "12px", "marginLeft": "8px", "opacity": 0.85},
                ),
            ],
        ),
        html.Div(
            "Hinweis: Beim ersten Mal oeffnet sich Chrome - bei x.com einloggen, dann laeuft der Pull automatisch.",
            style={"fontSize": "11px", "opacity": 0.55, "marginBottom": "12px"},
        ),

        html.Div(
            "Chart: Ziehen = verschieben | Mausrad = zoomen | Linke Preis-Achse ziehen = vertikal skalieren | "
            "Zeit-Leiste unten = horizontal scrollen | Doppelklick = Reset",
            style={"fontSize": "11px", "opacity": 0.55, "marginBottom": "6px"},
        ),
        dcc.Loading(
            id="chart-loading",
            type="dot",
            color=BUY_GREEN,
            children=dcc.Graph(
                id="chart",
                style={"height": "990px"},
                config=CHART_GRAPH_CONFIG,
                figure=initial_figure,
            ),
        ),

        # History panel - floating top-right
        html.Div(
            id="tw-history-panel",
            style={
                "position": "fixed",
                "top": "20px",
                "right": "20px",
                "width": "360px",
                "maxHeight": "70vh",
                "overflowY": "auto",
                "backgroundColor": "#0c0c20",
                "border": f"1px solid {GRID}",
                "borderRadius": "6px",
                "padding": "10px",
                "zIndex": 1000,
                "boxShadow": "0 4px 16px rgba(0,0,0,0.5)",
            },
            children=[
                html.Div(
                    "Twitter Pulls",
                    style={
                        "fontWeight": 700, "color": TWEET_CYAN,
                        "marginBottom": "8px", "fontSize": "13px",
                    },
                ),
                html.Div(id="tw-history-list"),
            ],
        ),

        # Side drawer (tweet detail) - hidden by default
        html.Div(
            id="tw-drawer",
            style={
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
                "transform": "translateX(100%)",
                "transition": "transform 0.25s ease-in-out",
            },
            children=[
                html.Div(
                    style={"display": "flex", "justifyContent": "space-between",
                           "alignItems": "center", "marginBottom": "10px"},
                    children=[
                        html.Span(
                            id="tw-drawer-title",
                            style={"fontWeight": 700, "color": TWEET_CYAN, "fontSize": "14px"},
                        ),
                        html.Button(
                            "x",
                            id="tw-drawer-close",
                            n_clicks=0,
                            style={
                                "backgroundColor": "transparent",
                                "color": TEXT_DIM,
                                "border": f"1px solid {GRID}",
                                "borderRadius": "4px",
                                "padding": "2px 10px",
                                "cursor": "pointer",
                                "fontSize": "16px",
                            },
                        ),
                    ],
                ),
                html.Div(
                    id="tw-drawer-nav",
                    style={"display": "flex", "gap": "8px", "marginBottom": "10px"},
                    children=[
                        html.Button("< Vor.", id="tw-drawer-prev", n_clicks=0,
                                    style={"padding": "4px 10px",
                                           "backgroundColor": "#1e1e3a",
                                           "color": TEXT_DIM,
                                           "border": f"1px solid {GRID}",
                                           "borderRadius": "4px",
                                           "cursor": "pointer"}),
                        html.Span(id="tw-drawer-counter",
                                  style={"alignSelf": "center", "fontSize": "12px",
                                         "opacity": 0.7}),
                        html.Button("Naechster >", id="tw-drawer-next", n_clicks=0,
                                    style={"padding": "4px 10px",
                                           "backgroundColor": "#1e1e3a",
                                           "color": TEXT_DIM,
                                           "border": f"1px solid {GRID}",
                                           "borderRadius": "4px",
                                           "cursor": "pointer"}),
                    ],
                ),
                html.Div(id="tw-drawer-body"),
            ],
        ),

        dcc.ConfirmDialog(
            id="tw-confirm-delete",
            message="Pull samt Parquet-Datei wirklich loeschen?",
        ),

        dcc.Store(id="wallet-store"),
        dcc.Store(id="tw-history-store", data=twitter_pulls.load_history()),
        dcc.Store(id="tw-drawer-store", data={"open": False, "ids": [], "idx": 0}),
        dcc.Store(id="tw-pending-delete"),
        dcc.Interval(id="tw-status-interval", interval=1000, disabled=True),
        dcc.Interval(id="wallet-status-interval", interval=1000, disabled=True),
    ],
)
