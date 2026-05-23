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

import sys
from datetime import timezone
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import requests
from dash import Dash, Input, Output, State, dcc, html
from plotly.subplots import make_subplots

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

app = Dash(__name__, title="Hyperion Wallet Explorer")

app.layout = html.Div(
    style={
        "backgroundColor": BG_PAPER,
        "color": TEXT_DIM,
        "minHeight": "100vh",
        "fontFamily": "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
        "padding": "20px",
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

        dcc.Store(id="wallet-store"),
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
    State("wallet-store", "data"),
)
def on_chart_change(coin: str | None, interval: str | None, wallet: str | None):
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

    return build_figure(coin, interval, candles_df, buckets_joined, wallet)


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
