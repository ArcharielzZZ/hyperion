"""Chart building helpers for Wallet Explorer."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import plotly.graph_objects as go
import polars as pl
from plotly.subplots import make_subplots

import twitter_pulls
from analytics.lib import hl_fetch
from analytics.lib.schemas import PREPARED_LIMIT_ORDERS_SCHEMA
from analytics.lib.spot_meta import is_spot_coin
from wallet_layout import (
    BG_PAPER,
    BG_PLOT,
    BUY_GREEN,
    CANDLE_DOWN,
    CANDLE_UP,
    GRID,
    LEDGER_AMBER,
    ORDER_LINE_OPACITY,
    ORDER_LINE_STYLES,
    PLACEMENT_MARKER_STYLES,
    POLARS_EVERY,
    SELL_RED,
    SPOT_ORDER_LINE_STYLES,
    SPOT_PLACEMENT_MARKER_STYLES,
    TEXT_DIM,
    TWEET_AMBER,
    TWEET_CYAN,
)

_log = logging.getLogger(__name__)

def aggregate_trades(df: pl.DataFrame, coin: str, interval: str) -> pl.DataFrame:
    """Return one row per candle bucket with counts/size/vwap/pnl per direction."""
    trades = df.filter(pl.col("coin") == coin).sort("timestamp")
    if trades.is_empty():
        return trades

    every = POLARS_EVERY[interval]
    directions = ("Buy", "Sell") if is_spot_coin(coin) else (
        "Open Long",
        "Close Long",
        "Open Short",
        "Close Short",
    )

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
    for direction in directions:
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


def _candle_range_note(candles_df: pl.DataFrame, trades: pl.DataFrame) -> str:
    if candles_df.is_empty() or trades.is_empty():
        return ""
    c0 = candles_df["timestamp"].min()
    c1 = candles_df["timestamp"].max()
    t0 = trades["timestamp"].min()
    t1 = trades["timestamp"].max()
    if c0 <= t0 and c1 >= t1:
        return ""
    parts: list[str] = []
    if c0 > t0:
        parts.append(f"Kerzen beginnen erst ab {str(c0)[:10]}")
    if c1 < t1:
        parts.append(f"Kerzen enden bei {str(c1)[:10]} (Trades bis {str(t1)[:10]})")
    return " | " + " — ".join(parts) if parts else ""


def _classify_spot_limit_order(status: str, side: str, reduce_only: bool) -> str:
    st = (status or "").lower()
    if "cancel" in st or "reject" in st:
        return "spot_limit_canceled"
    if reduce_only:
        return "spot_limit_close"
    if side == "B":
        return "spot_limit_buy"
    if side == "A":
        return "spot_limit_sell"
    return "other"


def is_order_intent_ambiguous(coin: str, reduce_only: bool) -> bool:
    """Perp limits without reduceOnly: API does not prove long open vs short open."""
    return not is_spot_coin(coin) and not reduce_only


def classify_limit_order(
    status: str, side: str, reduce_only: bool, *, coin: str = ""
) -> str:
    """Map Hyperliquid order snapshots to chart kinds.

    Hyperliquid ``side``: ``"A"`` = Ask (sell), ``"B"`` = Bid (buy).
    """
    if is_spot_coin(coin):
        return _classify_spot_limit_order(status, side, reduce_only)

    st = (status or "").lower()
    if "cancel" in st:
        if (not reduce_only and side == "A") or (reduce_only and side == "B"):
            return "canceled_short"
        return "canceled"
    if reduce_only and side == "A":
        return "long_close"
    if reduce_only and side == "B":
        return "short_close"
    if not reduce_only and side == "B":
        return "long_open"
    if not reduce_only and side == "A":
        return "short_open"
    _log.warning(
        "Unhandled limit order classification: status=%r side=%r reduce_only=%s",
        status,
        side,
        reduce_only,
    )
    return "other"


def _empty_prepared_limit_orders() -> pl.DataFrame:
    return pl.DataFrame(schema=PREPARED_LIMIT_ORDERS_SCHEMA)


def _order_line_styles(coin: str) -> dict[str, dict]:
    return SPOT_ORDER_LINE_STYLES if is_spot_coin(coin) else ORDER_LINE_STYLES


def _placement_marker_styles(coin: str) -> dict[str, dict]:
    return SPOT_PLACEMENT_MARKER_STYLES if is_spot_coin(coin) else PLACEMENT_MARKER_STYLES


def _prepare_limit_orders(orders_df: pl.DataFrame, coin: str = "") -> pl.DataFrame:
    if orders_df.is_empty():
        return _empty_prepared_limit_orders()

    if not coin and "coin" in orders_df.columns:
        coins = orders_df["coin"].unique().to_list()
        if len(coins) == 1:
            coin = str(coins[0])

    df = orders_df.filter(
        pl.col("order_type").str.to_lowercase().str.contains("limit")
        & (pl.col("limit_px") > 0)
    )
    if df.is_empty():
        return _empty_prepared_limit_orders()

    df = hl_fetch.dedupe_order_snapshots(df)

    if "reduce_only" not in df.columns:
        df = df.with_columns(pl.lit(False).alias("reduce_only"))

    coin_l = coin

    prepared = df.with_columns(
        pl.struct(["status", "side", "reduce_only"])
        .map_elements(
            lambda s: classify_limit_order(
                s["status"], s["side"], s["reduce_only"], coin=coin_l
            ),
            return_dtype=pl.String,
        )
        .alias("order_kind")
    ).filter(pl.col("order_kind") != "other")

    return prepared.with_columns(
        pl.col("reduce_only")
        .map_elements(
            lambda ro: is_order_intent_ambiguous(coin_l, bool(ro)),
            return_dtype=pl.Boolean,
        )
        .alias("ambiguous")
    )


def _clip_time_segment(
    t_start, t_end, chart_start, chart_end
) -> tuple | None:
    if t_end < chart_start or t_start > chart_end:
        return None
    return max(t_start, chart_start), min(t_end, chart_end)


def _order_line_end(row: dict, chart_end) -> object:
    status = (row.get("status") or "").lower()
    t_end = row.get("status_timestamp")
    if t_end is None:
        return chart_end
    if status == "open":
        return chart_end
    return t_end


def _add_limit_order_lines(
    fig: go.Figure,
    orders_df: pl.DataFrame,
    chart_start,
    chart_end,
    *,
    coin: str = "",
) -> None:
    line_styles = _order_line_styles(coin)
    prepared = _prepare_limit_orders(orders_df, coin=coin)
    if prepared.is_empty():
        return

    segments: dict[str, dict[str, list]] = {
        k: {"x": [], "y": []} for k in line_styles
    }

    for row in prepared.iter_rows(named=True):
        kind = row["order_kind"]
        if kind not in segments:
            continue
        t_start = row["timestamp"]
        t_end = _order_line_end(row, chart_end)
        clipped = _clip_time_segment(t_start, t_end, chart_start, chart_end)
        if clipped is None:
            continue
        xa, xb = clipped
        px = row["limit_px"]
        segments[kind]["x"].extend([xa, xb, None])
        segments[kind]["y"].extend([px, px, None])

    for kind, style in line_styles.items():
        xs = segments[kind]["x"]
        ys = segments[kind]["y"]
        if not xs:
            continue
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                name=style["name"],
                line=dict(
                    color=style["color"],
                    width=1.5,
                    dash=style["dash"],
                ),
                opacity=ORDER_LINE_OPACITY,
                legendgroup=kind,
                showlegend=True,
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )


def aggregate_order_placement_markers(
    orders_df: pl.DataFrame, interval: str, *, coin: str = ""
) -> pl.DataFrame:
    """Per-candle counts at placement time (perp: X/Y; spot: X only)."""
    marker_styles = _placement_marker_styles(coin)
    prepared = _prepare_limit_orders(orders_df, coin=coin)
    if prepared.is_empty():
        return prepared

    every = POLARS_EVERY[interval]
    aggs: list[pl.Expr] = []
    for kind in marker_styles:
        aggs.append(pl.col("order_kind").eq(kind).sum().alias(f"{kind}_n"))
        aggs.append(
            (pl.col("order_kind").eq(kind) & pl.col("ambiguous"))
            .sum()
            .alias(f"{kind}_ambiguous_n")
        )
    return prepared.sort("timestamp").group_by_dynamic(
        "timestamp", every=every
    ).agg(aggs)


def _add_order_placement_markers(
    fig: go.Figure, buckets_joined: pl.DataFrame, *, coin: str = ""
) -> None:
    if buckets_joined.is_empty() or "high" not in buckets_joined.columns:
        return

    marker_styles = _placement_marker_styles(coin)
    line_styles = _order_line_styles(coin)

    for kind, meta in marker_styles.items():
        n_col = f"{kind}_n"
        amb_col = f"{kind}_ambiguous_n"
        if n_col not in buckets_joined.columns:
            continue
        flt = buckets_joined.filter(pl.col(n_col) > 0)
        if flt.is_empty():
            continue

        anchor = meta["anchor"]
        offset = meta["offset"]
        letter = meta["letter"]
        color = meta["color"]
        name = line_styles.get(kind, {}).get("name", kind)
        amb_note = (
            "<br><i>Richtung unsicher (kein reduceOnly) — Fill dir ist eindeutig.</i>"
        )

        xs = flt["timestamp"].to_list()
        ys = [
            (b * offset if b is not None else None)
            for b in flt[anchor].to_list()
        ]
        counts = flt[n_col].to_list()
        amb_counts = (
            flt[amb_col].to_list()
            if amb_col in flt.columns
            else [0] * len(counts)
        )
        texts = [
            f"{letter}? {n}" if amb > 0 else f"{letter} {n}"
            for n, amb in zip(counts, amb_counts)
        ]
        hover = [
            f"<b>{name}</b><br>Platziert in Kerze: {n}"
            + (amb_note if amb > 0 else "")
            for n, amb in zip(counts, amb_counts)
        ]

        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="text",
                text=texts,
                textfont=dict(color=color, size=11, family="monospace"),
                textposition="middle center",
                name=f"{name} (Kerze)",
                legendgroup=kind,
                showlegend=False,
                hovertext=hover,
                hovertemplate="%{hovertext}<extra></extra>",
            ),
            row=1,
            col=1,
        )


# --------------------------------------------------------------------------- #
# Chart builder
# --------------------------------------------------------------------------- #

def _configure_chart_interaction(
    fig: go.Figure, *, uirevision: str, datarevision: str
) -> None:
    """TradingView-style: pan, wheel zoom, Y-scale per panel, time rangeslider below."""
    fig.update_layout(
        uirevision=uirevision,
        datarevision=datarevision,
        dragmode="pan",
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
    )
    for row in (1, 2, 3):
        fig.update_xaxes(
            fixedrange=False,
            showgrid=True,
            gridcolor=GRID,
            rangeslider_visible=False,
            row=row,
            col=1,
        )
    fig.update_xaxes(
        fixedrange=False,
        gridcolor=GRID,
        title_text="Zeit (UTC)",
        rangeslider=dict(
            visible=True,
            bgcolor="#0c0c20",
            thickness=0.07,
            bordercolor=GRID,
        ),
        row=3,
        col=1,
    )
    fig.update_yaxes(
        fixedrange=False,
        autorange=True,
        gridcolor=GRID,
        title_text="Preis (USD)",
        side="left",
        row=1,
        col=1,
    )
    fig.update_yaxes(
        fixedrange=False,
        autorange=True,
        gridcolor=GRID,
        tickformat=".3s",
        title_text="Volumen",
        side="left",
        row=2,
        col=1,
    )
    fig.update_yaxes(
        fixedrange=False,
        autorange=True,
        gridcolor=GRID,
        tickformat=".5f",
        title_text="Funding Rate",
        side="left",
        row=3,
        col=1,
    )


def build_figure(
    coin: str,
    interval: str,
    candles_df: pl.DataFrame,
    buckets_joined: pl.DataFrame,
    wallet: str,
    tweets_df: pl.DataFrame | None = None,
    orders_df: pl.DataFrame | None = None,
    order_placement_joined: pl.DataFrame | None = None,
    funding_df: pl.DataFrame | None = None,
    user_funding_total: float | None = None,
    ledger_df: pl.DataFrame | None = None,
    candle_note: str = "",
    uirevision: str = "",
    coin_display: str | None = None,
) -> go.Figure:
    chart_coin = coin_display or coin
    uf_note = ""
    if user_funding_total is not None:
        flow = "netto gezahlt" if user_funding_total < 0 else "netto erhalten"
        uf_note = (
            f"  |  Wallet-Funding ({chart_coin}, sichtbarer Chart): "
            f"{user_funding_total:,.0f} USDC ({flow})"
        )

    fig = make_subplots(
        rows=3,
        cols=1,
        row_heights=[0.62, 0.15, 0.23],
        shared_xaxes=True,
        vertical_spacing=0.03,
        subplot_titles=(
            f"<b>{chart_coin} / USD</b>  -  {interval}  -  Wallet {wallet[:10]}...{wallet[-6:]}{uf_note}{candle_note}",
            "Volumen",
            "Markt Funding Rate",
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
        if is_spot_coin(coin):
            _add_marker_trace(
                fig, buckets_joined,
                kind="buy", letter="L", color=BUY_GREEN,
                anchor="high", offset_mult=1.012, name="Kauf",
            )
            _add_marker_trace(
                fig, buckets_joined,
                kind="sell", letter="L", color=SELL_RED,
                anchor="low", offset_mult=0.988, name="Verkauf",
            )
        else:
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

    if orders_df is not None and not orders_df.is_empty() and not candles_df.is_empty():
        chart_start = candles_df["timestamp"].min()
        chart_end = candles_df["timestamp"].max()
        _add_limit_order_lines(fig, orders_df, chart_start, chart_end, coin=coin)

    if (
        order_placement_joined is not None
        and not order_placement_joined.is_empty()
    ):
        _add_order_placement_markers(fig, order_placement_joined, coin=coin)

    if ledger_df is not None and not ledger_df.is_empty() and not candles_df.is_empty():
        _add_ledger_markers(fig, ledger_df, candles_df)

    if tweets_df is not None and not tweets_df.is_empty() and not candles_df.is_empty():
        _add_tweet_markers(fig, tweets_df, candles_df, interval)

    if funding_df is not None and not funding_df.is_empty():
        _add_funding_panel(fig, funding_df)

    fig.update_layout(
        height=980,
        template="plotly_dark",
        paper_bgcolor=BG_PAPER,
        plot_bgcolor=BG_PLOT,
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0, font=dict(size=11, color=TEXT_DIM),
        ),
        margin=dict(l=60, r=30, t=80, b=70),
    )
    _configure_chart_interaction(
        fig,
        uirevision=f"{wallet}-{coin}-{interval}",
        datarevision=f"{wallet}-{coin}-{interval}",
    )
    return fig


def _ledger_icon(ledger_type: str) -> str:
    t = (ledger_type or "").lower()
    if "deposit" in t:
        return "\U0001f4b5"
    if "withdraw" in t:
        return "\U0001f4b8"
    if "transfer" in t:
        return "\U00002194"
    return "\U0001f4cb"


def _add_ledger_markers(
    fig: go.Figure, ledger_df: pl.DataFrame, candles_df: pl.DataFrame
) -> None:
    low_min = float(candles_df["low"].min())
    high_max = float(candles_df["high"].max())
    span = max(high_max - low_min, 1e-9)
    band_y = high_max + span * 0.05

    xs = ledger_df["timestamp"].to_list()
    ys = [band_y] * len(xs)
    texts = [_ledger_icon(t) for t in ledger_df["ledger_type"].to_list()]
    amounts = ledger_df["usdc"].to_list()
    types = ledger_df["ledger_type"].to_list()
    hovers = [
        f"<b>{tp}</b><br>{amt:,.2f} USDC<br>{ts}"
        for tp, amt, ts in zip(types, amounts, xs)
    ]

    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="text",
            text=texts,
            textfont=dict(color=LEDGER_AMBER, size=16),
            textposition="middle center",
            name="Ledger",
            hovertext=hovers,
            hovertemplate="%{hovertext}<extra></extra>",
        ),
        row=1,
        col=1,
    )


def _add_funding_panel(fig: go.Figure, funding_df: pl.DataFrame) -> None:
    times = funding_df["timestamp"].to_list()
    rates = funding_df["funding_rate"].to_list()
    if not times:
        return

    pos_rates = [r if r >= 0 else 0 for r in rates]
    fig.add_trace(
        go.Scatter(
            x=times,
            y=pos_rates,
            mode="lines",
            fill="tozeroy",
            fillcolor="rgba(0, 230, 118, 0.25)",
            line=dict(color=BUY_GREEN, width=1.5),
            name="Funding +",
            hovertemplate="Rate: %{y:.6f}<extra></extra>",
        ),
        row=3,
        col=1,
    )

    neg_rates = [r if r <= 0 else 0 for r in rates]
    fig.add_trace(
        go.Scatter(
            x=times,
            y=neg_rates,
            mode="lines",
            fill="tozeroy",
            fillcolor="rgba(255, 23, 68, 0.25)",
            line=dict(color=SELL_RED, width=1.5),
            name="Funding -",
            hovertemplate="Rate: %{y:.6f}<extra></extra>",
        ),
        row=3,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=[times[0], times[-1]],
            y=[0, 0],
            mode="lines",
            line=dict(color="#555577", width=1, dash="dot"),
            showlegend=False,
            hoverinfo="skip",
        ),
        row=3,
        col=1,
    )


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

def _empty_figure(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BG_PAPER,
        plot_bgcolor=BG_PLOT,
        height=980,
        dragmode="pan",
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
