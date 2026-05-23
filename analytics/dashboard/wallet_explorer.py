"""
Hyperion Wallet Explorer Dashboard
==================================

Local Dash web UI to interactively explore a wallet's trades on coin
candlestick charts. Per-candle marker aggregation with letter codes:

    L green = Open Long  count          L red = Close Long  count
    S green = Open Short count          S red = Close Short count

Color convention: GREEN = position opened, RED = position closed.
Letter:           L = Long side,         S = Short side.

Workflow
--------
1. Pull a wallet first:
       python analytics/scripts/force_pull_wallet.py 0x...
2. Start the dashboard:
       python analytics/dashboard/wallet_explorer.py
3. Open http://127.0.0.1:8050 in your browser.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import requests
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).resolve().parent))
import twitter_pulls  # noqa: E402

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DATA_LAKE = Path(__file__).resolve().parents[1] / "data_lake" / "wallets"
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

REPO_ROOT = Path(__file__).resolve().parents[2]
FORCE_PULL_TWITTER_SCRIPT = REPO_ROOT / "analytics" / "scripts" / "force_pull_twitter.py"


# --------------------------------------------------------------------------- #
# Wallet IO
# --------------------------------------------------------------------------- #

def wallet_path(wallet: str) -> Path:
    return DATA_LAKE / f"{wallet.lower()}.parquet"


def wallet_exists(wallet: str) -> bool:
    return wallet_path(wallet).exists()


def load_wallet(wallet: str) -> pl.DataFrame:
    return pl.read_parquet(wallet_path(wallet)).sort("timestamp")


def coin_options(df: pl.DataFrame) -> list[dict[str, str]]:
    counts = (
        df.group_by("coin")
        .agg(pl.len().alias("n"))
        .sort("n", descending=True)
    )
    return [
        {"label": f"{r['coin']} ({r['n']:,} fills)", "value": r["coin"]}
        for r in counts.iter_rows(named=True)
    ]


# --------------------------------------------------------------------------- #
# Hyperliquid candle fetching (paginated)
# --------------------------------------------------------------------------- #

def fetch_candles(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """Paginate candleSnapshot. Hyperliquid returns max 5000 candles per call."""
    out: list[dict] = []
    seen: set[int] = set()
    cur = start_ms

    while cur < end_ms:
        try:
            r = requests.post(
                HL_API,
                json={
                    "type": "candleSnapshot",
                    "req": {
                        "coin": coin,
                        "interval": interval,
                        "startTime": cur,
                        "endTime": end_ms,
                    },
                },
                headers=HL_HEADERS,
                timeout=HL_TIMEOUT,
            )
        except requests.RequestException:
            break

        if r.status_code != 200:
            break

        batch = r.json() or []
        if not batch:
            break

        fresh = [c for c in batch if c["t"] not in seen]
        if not fresh:
            break

        for c in fresh:
            seen.add(c["t"])
        out.extend(fresh)

        if len(batch) < 5000:
            break

        newest = max(c["t"] for c in fresh)
        if newest <= cur:
            break
        cur = newest + 1

    out.sort(key=lambda c: c["t"])
    return out


def candles_to_df(candles: list[dict]) -> pl.DataFrame:
    if not candles:
        return pl.DataFrame(
            schema={
                "timestamp": pl.Datetime("ms"),
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
            }
        )
    df = pl.DataFrame(candles).with_columns(
        [
            pl.from_epoch(pl.col("t"), time_unit="ms").alias("timestamp"),
            pl.col("o").cast(pl.Float64).alias("open"),
            pl.col("h").cast(pl.Float64).alias("high"),
            pl.col("l").cast(pl.Float64).alias("low"),
            pl.col("c").cast(pl.Float64).alias("close"),
            pl.col("v").cast(pl.Float64).alias("volume"),
        ]
    ).select(["timestamp", "open", "high", "low", "close", "volume"])
    return df.sort("timestamp")


# --------------------------------------------------------------------------- #
# Per-candle trade aggregation
# --------------------------------------------------------------------------- #

def aggregate_trades(df: pl.DataFrame, coin: str, interval: str) -> pl.DataFrame:
    """Return one row per candle bucket with counts/size/vwap/pnl per direction."""
    trades = df.filter(pl.col("coin") == coin).sort("timestamp")
    if trades.is_empty():
        return trades

    every = POLARS_EVERY[interval]

    def metrics_for(direction: str) -> list[pl.Expr]:
        mask = pl.col("dir") == direction
        size_sum = pl.col("size").filter(mask).sum()
        return [
            mask.sum().alias(f"{_key(direction)}_n"),
            size_sum.alias(f"{_key(direction)}_size"),
            (
                (pl.col("price") * pl.col("size")).filter(mask).sum() / size_sum
            ).alias(f"{_key(direction)}_vwap"),
            pl.col("closed_pnl").filter(mask).sum().alias(f"{_key(direction)}_pnl"),
        ]

    aggs: list[pl.Expr] = []
    for direction in ("Open Long", "Close Long", "Open Short", "Close Short"):
        aggs.extend(metrics_for(direction))

    return trades.group_by_dynamic("timestamp", every=every).agg(aggs)


def _key(direction: str) -> str:
    return direction.lower().replace(" ", "_")


def join_buckets_with_candles(
    buckets: pl.DataFrame, candles_df: pl.DataFrame
) -> pl.DataFrame:
    """Attach the candle high/low for the bucket via backward as-of join."""
    if buckets.is_empty() or candles_df.is_empty():
        return buckets
    return buckets.sort("timestamp").join_asof(
        candles_df.sort("timestamp").select(["timestamp", "high", "low"]),
        on="timestamp",
        strategy="backward",
    )


# --------------------------------------------------------------------------- #
# Chart builder
# --------------------------------------------------------------------------- #

def build_figure(
    coin: str,
    interval: str,
    candles_df: pl.DataFrame,
    buckets_joined: pl.DataFrame,
    wallet: str,
    tweets_df: pl.DataFrame | None = None,
) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        row_heights=[0.8, 0.2],
        shared_xaxes=True,
        vertical_spacing=0.04,
        subplot_titles=(
            f"<b>{coin} / USD</b>  -  {interval}  -  Wallet {wallet[:10]}...{wallet[-6:]}",
            "Volumen",
        ),
    )

    if not candles_df.is_empty():
        fig.add_trace(
            go.Candlestick(
                x=candles_df["timestamp"],
                open=candles_df["open"],
                high=candles_df["high"],
                low=candles_df["low"],
                close=candles_df["close"],
                name=coin,
                increasing=dict(line=dict(color=CANDLE_UP, width=1.4), fillcolor=CANDLE_UP),
                decreasing=dict(line=dict(color=CANDLE_DOWN, width=1.4), fillcolor=CANDLE_DOWN),
                whiskerwidth=0.3,
                showlegend=False,
            ),
            row=1,
            col=1,
        )

        bar_colors = [
            CANDLE_UP if c >= o else CANDLE_DOWN
            for o, c in zip(candles_df["open"], candles_df["close"])
        ]
        fig.add_trace(
            go.Bar(
                x=candles_df["timestamp"],
                y=candles_df["volume"],
                marker_color=bar_colors,
                opacity=0.7,
                name="Volumen",
                showlegend=False,
                hovertemplate="Vol: %{y:,.2f}<extra></extra>",
            ),
            row=2,
            col=1,
        )

    if not buckets_joined.is_empty() and "high" in buckets_joined.columns:
        _add_marker_trace(
            fig, buckets_joined,
            kind="open_long",  letter="L", color=BUY_GREEN,
            anchor="high", offset_mult=1.012, name="Open Long",
        )
        _add_marker_trace(
            fig, buckets_joined,
            kind="close_long", letter="L", color=SELL_RED,
            anchor="high", offset_mult=1.028, name="Close Long",
        )
        _add_marker_trace(
            fig, buckets_joined,
            kind="open_short", letter="S", color=BUY_GREEN,
            anchor="low",  offset_mult=0.988, name="Open Short",
        )
        _add_marker_trace(
            fig, buckets_joined,
            kind="close_short", letter="S", color=SELL_RED,
            anchor="low",  offset_mult=0.972, name="Close Short",
        )

    if tweets_df is not None and not tweets_df.is_empty() and not candles_df.is_empty():
        _add_tweet_markers(fig, tweets_df, candles_df, interval)

    fig.update_layout(
        height=820,
        template="plotly_dark",
        paper_bgcolor=BG_PAPER,
        plot_bgcolor=BG_PLOT,
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0, font=dict(size=11, color=TEXT_DIM),
        ),
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
        margin=dict(l=60, r=30, t=80, b=40),
        dragmode="pan",
    )
    fig.update_yaxes(title_text="Preis (USD)", row=1, col=1, gridcolor=GRID)
    fig.update_yaxes(title_text="Volumen", row=2, col=1, gridcolor=GRID, tickformat=".3s")
    fig.update_xaxes(gridcolor=GRID, row=1, col=1)
    fig.update_xaxes(gridcolor=GRID, row=2, col=1, title_text="Zeit (UTC)")
    return fig


def _add_marker_trace(
    fig: go.Figure,
    df: pl.DataFrame,
    *,
    kind: str,
    letter: str,
    color: str,
    anchor: str,
    offset_mult: float,
    name: str,
) -> None:
    count_col = f"{kind}_n"
    size_col = f"{kind}_size"
    vwap_col = f"{kind}_vwap"
    pnl_col = f"{kind}_pnl"

    flt = df.filter(pl.col(count_col) > 0)
    if flt.is_empty():
        return

    xs = flt["timestamp"].to_list()
    base_y = flt[anchor].to_list()
    ys = [b * offset_mult if b is not None else None for b in base_y]
    counts = flt[count_col].to_list()
    sizes = flt[size_col].to_list()
    vwaps = flt[vwap_col].to_list()
    pnls = flt[pnl_col].to_list()
    texts = [f"{letter} {n}" for n in counts]

    hover = [
        f"<b>{name}</b><br>"
        f"Trades: {n}<br>"
        f"Size: {sz:,.2f}<br>"
        f"VWAP: ${(vw if vw is not None else 0):,.4f}<br>"
        f"Closed PnL: ${(pn if pn is not None else 0):,.2f}"
        for n, sz, vw, pn in zip(counts, sizes, vwaps, pnls)
    ]

    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="text",
            text=texts,
            textfont=dict(color=color, size=12, family="monospace"),
            textposition="middle center",
            name=name,
            hovertext=hover,
            hovertemplate="%{hovertext}<extra></extra>",
        ),
        row=1,
        col=1,
    )


def _add_tweet_markers(
    fig: go.Figure,
    tweets_df: pl.DataFrame,
    candles_df: pl.DataFrame,
    interval: str,
) -> None:
    """Render tweet bubbles on a dedicated row pinned to the bottom of the price subplot.

    TradingView-style news ribbon: a thin band just above the x-axis. The
    y-anchor is the global candle low; the row offset is a percent of the
    candle-price-range so the markers do not overlap candles.
    """
    if tweets_df.is_empty() or candles_df.is_empty():
        return

    low_min = float(candles_df["low"].min())
    high_max = float(candles_df["high"].max())
    span = max(high_max - low_min, 1e-9)

    t_min_naive = candles_df["timestamp"].min()
    t_max_naive = candles_df["timestamp"].max()
    t_min = _ensure_utc(t_min_naive)
    t_max = _ensure_utc(t_max_naive)

    tweets = tweets_df.with_columns(
        pl.col("created_at").cast(pl.Datetime("ms", time_zone="UTC"))
    )
    in_range, before, after = twitter_pulls.split_in_range_and_out_of_range(
        tweets, t_min, t_max
    )

    band_y = low_min - span * 0.05

    if not in_range.is_empty():
        buckets = twitter_pulls.aggregate_tweets_to_buckets(in_range, interval)
        if not buckets.is_empty():
            xs = buckets["bucket"].to_list()
            counts = buckets["count"].to_list()
            id_lists = buckets["ids"].to_list()
            ys = [band_y] * len(xs)
            texts = [f"\U0001F4AC {n}" for n in counts]
            hovers = [
                f"<b>{n} Tweet{'s' if n != 1 else ''}</b><br>"
                f"{xs[i].strftime('%Y-%m-%d %H:%M') if hasattr(xs[i], 'strftime') else xs[i]}"
                for i, n in enumerate(counts)
            ]
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="text",
                    text=texts,
                    textfont=dict(color=TWEET_CYAN, size=15),
                    textposition="middle center",
                    name="Tweets",
                    hovertext=hovers,
                    hovertemplate="%{hovertext}<extra></extra>",
                    customdata=[{"kind": "tweets", "ids": ids} for ids in id_lists],
                ),
                row=1,
                col=1,
            )

    _add_edge_bubble(
        fig, before, anchor_x=t_min, band_y=band_y,
        color=TWEET_AMBER, label_suffix="vorher",
    )
    _add_edge_bubble(
        fig, after, anchor_x=t_max, band_y=band_y,
        color=TWEET_AMBER, label_suffix="danach",
    )


def _add_edge_bubble(
    fig: go.Figure,
    tweets: pl.DataFrame,
    *,
    anchor_x,
    band_y: float,
    color: str,
    label_suffix: str,
) -> None:
    if tweets.is_empty():
        return
    n = tweets.height
    ids = tweets["id"].to_list()
    oldest = tweets["created_at"].min()
    newest = tweets["created_at"].max()
    hover = (
        f"<b>{n} Tweets {label_suffix}</b><br>"
        f"Aelteste: {oldest}<br>Neueste: {newest}"
    )
    fig.add_trace(
        go.Scatter(
            x=[anchor_x],
            y=[band_y],
            mode="text",
            text=[f"\U0001F4AC* {n}"],
            textfont=dict(color=color, size=15),
            textposition="middle center",
            name=f"Tweets {label_suffix}",
            hovertext=[hover],
            hovertemplate="%{hovertext}<extra></extra>",
            customdata=[{"kind": "tweets", "ids": ids}],
        ),
        row=1,
        col=1,
    )


def _ensure_utc(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(dt)).replace(tzinfo=timezone.utc)
    except Exception:
        return dt


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _to_utc_ms(dt) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _empty_figure(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BG_PAPER,
        plot_bgcolor=BG_PLOT,
        height=820,
        annotations=[
            dict(
                text=msg,
                xref="paper", yref="paper",
                x=0.5, y=0.5,
                showarrow=False,
                font=dict(size=14, color=TEXT_DIM),
            )
        ],
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    return fig


# --------------------------------------------------------------------------- #
# Dash app
# --------------------------------------------------------------------------- #

app = Dash(__name__, title="Hyperion Wallet Explorer", suppress_callback_exceptions=True)

app.layout = html.Div(
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
                html.Div(
                    id="wallet-status",
                    style={"fontSize": "12px", "marginLeft": "8px"},
                ),
            ],
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
                    style={"width": "260px", "color": "#000"},
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

        dcc.Loading(
            id="chart-loading",
            type="dot",
            color=BUY_GREEN,
            children=dcc.Graph(
                id="chart",
                style={"height": "830px"},
                config={"scrollZoom": True, "displaylogo": False},
                figure=_empty_figure("Bitte eine Wallet laden."),
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
    ],
)


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #

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
    base_style = {"fontSize": "12px", "marginLeft": "8px"}

    if not wallet or not wallet.strip().lower().startswith("0x"):
        return (
            [], None,
            "Bitte eine 0x... Wallet-Adresse eingeben.",
            {**base_style, "color": SELL_RED},
            None,
        )

    w = wallet.strip().lower()
    if not wallet_exists(w):
        msg = (
            f"Parquet nicht gefunden: {wallet_path(w)}. "
            f"Erst pullen: python analytics/scripts/force_pull_wallet.py {w}"
        )
        return [], None, msg, {**base_style, "color": SELL_RED}, None

    try:
        df = load_wallet(w)
    except Exception as exc:  # parquet read errors
        return (
            [], None,
            f"Fehler beim Lesen: {exc}",
            {**base_style, "color": SELL_RED},
            None,
        )

    opts = coin_options(df)
    if not opts:
        return (
            [], None,
            "Wallet enthaelt keine Trades.",
            {**base_style, "color": SELL_RED},
            None,
        )

    msg = (
        f"OK - {len(df):,} Fills, {len(opts)} Coins. "
        f"{df['timestamp'].min()} ... {df['timestamp'].max()}"
    )
    default_coin = opts[0]["value"]
    return opts, default_coin, msg, {**base_style, "color": BUY_GREEN}, w


@app.callback(
    Output("chart", "figure"),
    Input("coin-dd", "value"),
    Input("interval-dd", "value"),
    Input("tw-history-store", "data"),
    State("wallet-store", "data"),
)
def on_chart_change(
    coin: str | None,
    interval: str | None,
    _history,
    wallet: str | None,
):
    if not wallet or not coin or not interval:
        return _empty_figure("Bitte Wallet laden und Coin auswaehlen.")

    try:
        df = load_wallet(wallet)
    except Exception as exc:
        return _empty_figure(f"Fehler beim Lesen der Wallet: {exc}")

    trades_for_coin = df.filter(pl.col("coin") == coin)
    if trades_for_coin.is_empty():
        return _empty_figure(f"Keine Trades fuer {coin}.")

    t_min = _to_utc_ms(trades_for_coin["timestamp"].min())
    t_max = _to_utc_ms(trades_for_coin["timestamp"].max())
    pad_ms = 7 * 24 * 60 * 60 * 1000
    start_ms = max(0, t_min - pad_ms)
    end_ms = t_max + pad_ms

    candles = fetch_candles(coin, interval, start_ms, end_ms)
    candles_df = candles_to_df(candles)
    if candles_df.is_empty():
        return _empty_figure(
            f"Keine Kerzen von Hyperliquid fuer {coin} @ {interval} erhalten."
        )

    buckets = aggregate_trades(df, coin, interval)
    buckets_joined = join_buckets_with_candles(buckets, candles_df)

    tweets_df = twitter_pulls.load_visible_tweets()

    return build_figure(
        coin, interval, candles_df, buckets_joined, wallet, tweets_df=tweets_df,
    )


# --------------------------------------------------------------------------- #
# Twitter callbacks
# --------------------------------------------------------------------------- #

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
            return ("Bitte @handle angeben.",
                    {**base_style, "color": SELL_RED}, True, no_update)
        try:
            datetime.strptime((frm or "").strip(), "%Y-%m-%d")
            datetime.strptime((to or "").strip(), "%Y-%m-%d")
        except ValueError:
            return ("Datum muss YYYY-MM-DD sein.",
                    {**base_style, "color": SELL_RED}, True, no_update)

        clean_handle = handle.strip().lstrip("@")
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    str(FORCE_PULL_TWITTER_SCRIPT),
                    "--handle", clean_handle,
                    "--from", frm.strip(),
                    "--to", to.strip(),
                ],
                cwd=str(REPO_ROOT),
            )
        except Exception as exc:
            return (f"Konnte Scraper nicht starten: {exc}",
                    {**base_style, "color": SELL_RED}, True, no_update)

        return (f"Starte Pull fuer @{clean_handle} ...",
                {**base_style, "color": TWEET_CYAN}, False, no_update)

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
        return (f"Fehler: {err or msg}",
                {**base_style, "color": SELL_RED}, True, no_update)

    parts = [msg or "Laeuft ..."]
    if found:
        parts.append(f"{found} Tweets")
    if last_date:
        parts.append(f"bei {last_date}")
    return (" - ".join(parts),
            {**base_style, "color": TWEET_CYAN}, False, no_update)


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
            if (h.get("handle") == key["handle"]
                    and h.get("from") == key["from"]
                    and h.get("to") == key["to"]):
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
        return ({**base_style, "transform": "translateX(100%)"},
                "", "", "")

    ids = store["ids"]
    idx = max(0, min(len(ids) - 1, store.get("idx", 0)))
    tweets = twitter_pulls.tweets_for_ids(ids)
    if not tweets:
        return ({**base_style, "transform": "translateX(0)"},
                "Tweets nicht gefunden", "", html.Div("Pull-Datei eventuell geloescht."))

    idx = max(0, min(len(tweets) - 1, idx))
    t = tweets[idx]
    title = f"@{t['handle']} - {t['created_at']}"
    counter = f"{idx + 1} / {len(tweets)}"

    media_children = []
    for url in t.get("media_urls") or []:
        if url.lower().endswith(".mp4"):
            media_children.append(
                html.Video(
                    src=url, controls=True,
                    style={"width": "100%", "marginTop": "8px", "borderRadius": "4px"},
                )
            )
        else:
            media_children.append(
                html.Img(
                    src=url,
                    style={"width": "100%", "marginTop": "8px", "borderRadius": "4px"},
                )
            )

    body = html.Div(
        children=[
            html.Div(
                t.get("text") or "",
                style={"whiteSpace": "pre-wrap", "lineHeight": "1.4",
                       "fontSize": "13px", "marginBottom": "8px"},
            ),
            html.Div(media_children),
            html.A(
                "Auf X oeffnen",
                href=t.get("url") or "#",
                target="_blank",
                style={"display": "inline-block", "marginTop": "12px",
                       "color": TWEET_CYAN, "textDecoration": "none",
                       "border": f"1px solid {TWEET_CYAN}",
                       "padding": "4px 10px", "borderRadius": "4px"},
            ),
        ],
    )

    return ({**base_style, "transform": "translateX(0)"},
            title, counter, body)


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("Hyperion Wallet Explorer  ->  http://127.0.0.1:8050")
    app.run(debug=False, host="127.0.0.1", port=8050)
