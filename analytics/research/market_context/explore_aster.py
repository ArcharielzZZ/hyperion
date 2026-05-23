import polars as pl
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import webbrowser, os, sys

sys.stdout.reconfigure(encoding="utf-8")

WALLET_FILE = "analytics/data_lake/wallets/0x2d99fe0f36c1aebd28a1a2c0e82e8ca13c2ea351.parquet"
COIN        = "ASTER"
OUT_HTML    = "analytics/research/market_context/aster_chart.html"

# ── Daten laden ───────────────────────────────────────────────────────────────
print(f"Lade {COIN} Trades...")
df    = pl.read_parquet(WALLET_FILE)
aster = df.filter(pl.col("coin") == COIN).sort("timestamp")
print(f"  {len(aster):,} Fills | {aster['timestamp'].min()} - {aster['timestamp'].max()}")

# ── Kerzen aus den Trades selbst bauen (1-Minuten-Bins) ──────────────────────
candles = aster.group_by_dynamic("timestamp", every="1m").agg([
    pl.col("price").sort_by(pl.col("timestamp")).first().alias("open"),
    pl.col("price").max().alias("high"),
    pl.col("price").min().alias("low"),
    pl.col("price").sort_by(pl.col("timestamp")).last().alias("close"),
    pl.col("size").sum().alias("volume"),
    pl.len().alias("n"),
])
print(f"  {len(candles)} 1-Minuten-Kerzen gebaut.")

# ── Buys / Sells klassifizieren und pro Minute aggregieren ───────────────────
BUY_DIRS  = ["Open Long",  "Close Short"]
SELL_DIRS = ["Open Short", "Close Long"]

buys  = aster.filter(pl.col("dir").is_in(BUY_DIRS))
sells = aster.filter(pl.col("dir").is_in(SELL_DIRS))

def aggregate_by_minute(df_subset: pl.DataFrame) -> pl.DataFrame:
    if df_subset.is_empty():
        return df_subset
    return df_subset.group_by_dynamic("timestamp", every="1m").agg([
        ((pl.col("price") * pl.col("size")).sum() / pl.col("size").sum()).alias("vwap"),
        pl.col("size").sum().alias("total_size"),
        pl.len().alias("trade_count"),
    ])

agg_buys  = aggregate_by_minute(buys)
agg_sells = aggregate_by_minute(sells)

all_sizes = (
    agg_buys["total_size"].to_list() if not agg_buys.is_empty() else [] +
    agg_sells["total_size"].to_list() if not agg_sells.is_empty() else []
)
mean_sz = sum(all_sizes) / len(all_sizes) if all_sizes else 1.0

def marker_size(sz, lo=12.0, hi=40.0):
    ratio = max(0.25, min(4.0, sz / mean_sz))
    return lo + (hi - lo) * (ratio - 0.25) / 3.75

print(f"  {len(agg_buys)} Buy-Minuten, {len(agg_sells)} Sell-Minuten nach Aggregation.")

# ── Chart ─────────────────────────────────────────────────────────────────────
fig = make_subplots(
    rows=2, cols=1,
    row_heights=[0.78, 0.22],
    shared_xaxes=True,
    vertical_spacing=0.04,
    subplot_titles=(
        f"<b>{COIN} / USD</b>  —  Wallet {WALLET_FILE.split('/')[-1][:12]}…",
        "Volumen (ASTER pro Minute)",
    )
)

# Candlestick
fig.add_trace(go.Candlestick(
    x=candles["timestamp"],
    open=candles["open"],
    high=candles["high"],
    low=candles["low"],
    close=candles["close"],
    name=COIN,
    increasing=dict(line=dict(color="#26a69a", width=2), fillcolor="#26a69a"),
    decreasing=dict(line=dict(color="#ef5350", width=2), fillcolor="#ef5350"),
    whiskerwidth=0.3,
), row=1, col=1)

# Grüne Dreiecke (Kaeufe)
if not agg_buys.is_empty():
    b_hover = [
        f"<b>Kaeufe</b><br>{r['trade_count']} Trades<br>"
        f"Menge: {r['total_size']:,.0f} ASTER<br>"
        f"VWAP: ${r['vwap']:.4f}"
        for r in agg_buys.iter_rows(named=True)
    ]
    fig.add_trace(go.Scattergl(
        x=agg_buys["timestamp"],
        y=agg_buys["vwap"],
        mode="markers",
        name="Kauf (Close Short)",
        marker=dict(
            symbol="triangle-up",
            color="#00e676",
            size=[marker_size(s) for s in agg_buys["total_size"]],
            line=dict(width=1, color="#004d1a"),
            opacity=0.95,
        ),
        text=b_hover,
        hovertemplate="%{text}<extra></extra>",
    ), row=1, col=1)

# Rote Dreiecke (Verkaeufe)
if not agg_sells.is_empty():
    s_hover = [
        f"<b>Verkaeufe</b><br>{r['trade_count']} Trades<br>"
        f"Menge: {r['total_size']:,.0f} ASTER<br>"
        f"VWAP: ${r['vwap']:.4f}"
        for r in agg_sells.iter_rows(named=True)
    ]
    fig.add_trace(go.Scattergl(
        x=agg_sells["timestamp"],
        y=agg_sells["vwap"],
        mode="markers",
        name="Verkauf (Open Short)",
        marker=dict(
            symbol="triangle-down",
            color="#ff1744",
            size=[marker_size(s) for s in agg_sells["total_size"]],
            line=dict(width=1, color="#4d0000"),
            opacity=0.95,
        ),
        text=s_hover,
        hovertemplate="%{text}<extra></extra>",
    ), row=1, col=1)

# Volumen-Balken
bar_colors = [
    "#26a69a" if c >= o else "#ef5350"
    for o, c in zip(candles["open"], candles["close"])
]
fig.add_trace(go.Bar(
    x=candles["timestamp"],
    y=candles["volume"],
    name="Volumen",
    marker_color=bar_colors,
    opacity=0.75,
    hovertemplate="Vol: %{y:,.0f} ASTER<extra></extra>",
), row=2, col=1)

# Annotationen fuer die zwei Sessions
for ts, label in [
    ("2025-09-24 16:11:00", "Session 1  (Sep 24)"),
    ("2025-09-25 10:00:00", "Session 2  (Sep 25)"),
]:
    fig.add_vline(
        x=ts,
        line=dict(color="#888", width=1, dash="dot"),
        row=1, col=1,
    )
    fig.add_annotation(
        x=ts, y=1.04, yref="paper",
        text=label, showarrow=False,
        font=dict(color="#aaa", size=10),
    )

# Layout
fig.update_layout(
    title=dict(
        text=(
            "<b>ASTER Trades  —  Wallet 0x2d99fe0f…351</b><br>"
            f"<sup>95.740 Fills  |  {len(agg_buys)} Kauf-Minuten  |  "
            f"{len(agg_sells)} Verkauf-Minuten  |  Preis $1.97 – $2.43</sup>"
        ),
        font=dict(size=14, color="#c0c0e0"),
        x=0,
    ),
    height=880,
    template="plotly_dark",
    paper_bgcolor="#070714",
    plot_bgcolor="#0a0a1e",
    showlegend=True,
    legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
    hovermode="x unified",
    xaxis_rangeslider_visible=False,
    margin=dict(l=60, r=30, t=100, b=50),
)

fig.update_yaxes(title_text="Preis (USD)", row=1, col=1,
                 gridcolor="#14142a", tickformat=".4f")
fig.update_yaxes(title_text="Volumen", row=2, col=1,
                 gridcolor="#14142a", tickformat=".3s")
fig.update_xaxes(gridcolor="#14142a", showspikes=True, spikecolor="#666",
                 spikethickness=1, row=1, col=1)
fig.update_xaxes(gridcolor="#14142a", title_text="Zeit (UTC)", row=2, col=1)

# ── Als HTML speichern und oeffnen ────────────────────────────────────────────
print(f"\nSpeichere Chart nach {OUT_HTML}...")
fig.write_html(OUT_HTML, include_plotlyjs="cdn")
abs_path = os.path.abspath(OUT_HTML)
print(f"Oeffne: file:///{abs_path}")
webbrowser.open(f"file:///{abs_path}")
print("Fertig!")
