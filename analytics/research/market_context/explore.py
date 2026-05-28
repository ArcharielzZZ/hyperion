import time
import requests
import polars as pl
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

# ==========================================
# HYPERION: ZEC Wallet Trade Visualizer
# ==========================================
# DEPRECATED for daily use: use Wallet Explorer dashboard instead
# (analytics/dashboard/wallet_explorer.py) after force_pull_wallet_bundle.py.
# This script remains as a reference for one-off research plots.
#
# Fetches live data from Hyperliquid and renders an interactive chart:
#   - ZEC candlestick (4h) with buy/sell triangle markers
#   - Triangle size scales with fill size vs. mean size
#   - Right panel: top trades by size (time, direction, qty, price)
#   - Bottom panel: continuous green/red funding rate line

WALLET   = "0x2d99fe0f36c1aebd28a1a2c0e82e8ca13c2ea351"
COIN     = "ZEC"
START_MS = int(datetime(2025, 10, 30).timestamp() * 1000)
END_MS   = int(datetime(2025, 11, 29).timestamp() * 1000)

HL_API   = "https://api.hyperliquid.xyz/info"
HEADERS  = {"Content-Type": "application/json"}


# ── Data Fetchers ────────────────────────────────────────────────────────────

def fetch_fills(wallet: str, coin: str, start_ms: int, end_ms: int) -> list[dict]:
    """
    Forward-paginates userFillsByTime (oldest-first) to collect every fill
    for the given coin without hitting the 2000-row API cap.
    """
    print(f"  Fetching {coin} fills for {wallet[:10]}…")
    all_fills: list[dict] = []
    cur_start = start_ms

    while True:
        r = requests.post(HL_API, json={
            "type": "userFillsByTime",
            "user": wallet,
            "startTime": cur_start,
            "endTime": end_ms,
        }, headers=HEADERS)
        batch = r.json()
        if not batch:
            break

        for f in batch:
            if f.get("coin") == coin:
                all_fills.append(f)

        newest_ts = max(f["time"] for f in batch)
        if len(batch) < 2000 or newest_ts >= end_ms:
            break
        cur_start = newest_ts + 1
        time.sleep(0.2)

    print(f"  → {len(all_fills)} fills")
    return all_fills


def fetch_candles(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    print(f"  Fetching {coin} {interval} candles…")
    r = requests.post(HL_API, json={
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
    }, headers=HEADERS)
    candles = r.json()
    print(f"  → {len(candles)} candles")
    return candles


def fetch_funding(coin: str, start_ms: int) -> list[dict]:
    print(f"  Fetching {coin} funding history…")
    r = requests.post(HL_API, json={
        "type": "fundingHistory",
        "coin": coin,
        "startTime": start_ms,
    }, headers=HEADERS)
    funding = r.json()
    # Keep only entries within our window
    funding = [f for f in funding if f["time"] <= END_MS]
    print(f"  → {len(funding)} funding entries")
    return funding


# ── Chart Builder ────────────────────────────────────────────────────────────

def _marker_size(sz: float, mean_sz: float,
                 lo: float = 8.0, hi: float = 32.0) -> float:
    """Scale marker between lo and hi pixels relative to the mean."""
    ratio = sz / mean_sz if mean_sz > 0 else 1.0
    # clamp to [0.25×, 4×] of mean to avoid extreme outliers dominating
    ratio = max(0.25, min(4.0, ratio))
    return lo + (hi - lo) * (ratio - 0.25) / (4.0 - 0.25)


def build_chart(fills: list[dict], candles: list[dict], funding: list[dict]) -> None:
    if not fills:
        print("No fills – nothing to plot.")
        return

    sizes    = [float(f["sz"]) for f in fills]
    mean_sz  = sum(sizes) / len(sizes)

    # Classify fills ─────────────────────────────────────────────────────────
    # "Buy" events  → green  ▲  (Open Long or Close Short)
    # "Sell" events → red    ▼  (Open Short or Close Long)
    BUY_DIRS  = {"Open Long",   "Close Short"}
    SELL_DIRS = {"Open Short",  "Close Long"}

    buys  = [f for f in fills if f["dir"] in BUY_DIRS]
    sells = [f for f in fills if f["dir"] in SELL_DIRS]

    def fill_to_ts(f: dict) -> datetime:
        return datetime.fromtimestamp(f["time"] / 1000)

    # Candles ─────────────────────────────────────────────────────────────────
    c_times  = [datetime.fromtimestamp(c["t"] / 1000) for c in candles]
    c_open   = [float(c["o"]) for c in candles]
    c_high   = [float(c["h"]) for c in candles]
    c_low    = [float(c["l"]) for c in candles]
    c_close  = [float(c["c"]) for c in candles]

    # Funding ─────────────────────────────────────────────────────────────────
    f_times = [datetime.fromtimestamp(f["time"] / 1000) for f in funding]
    f_rates = [float(f["fundingRate"]) for f in funding]

    # Top-50 fills for the side table (sorted by size desc) ──────────────────
    top_fills = sorted(fills, key=lambda x: float(x["sz"]), reverse=True)[:50]

    # ── Layout ────────────────────────────────────────────────────────────────
    fig = make_subplots(
        rows=2, cols=2,
        column_widths=[0.76, 0.24],
        row_heights=[0.72, 0.28],
        shared_xaxes=False,
        vertical_spacing=0.05,
        horizontal_spacing=0.015,
        specs=[
            [{"type": "xy"},    {"type": "table", "rowspan": 2}],
            [{"type": "xy"},    None],
        ],
        subplot_titles=(
            f"ZEC/USD  ·  {WALLET[:10]}…{WALLET[-6:]}",
            "Top Trades by Size",
            "Funding Rate",
        ),
    )

    # ── Candlestick ───────────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=c_times,
        open=c_open, high=c_high, low=c_low, close=c_close,
        name="ZEC",
        increasing=dict(line=dict(color="#26a69a"), fillcolor="#26a69a"),
        decreasing=dict(line=dict(color="#ef5350"), fillcolor="#ef5350"),
        hoverinfo="x+y",
    ), row=1, col=1)

    # ── Buy markers  (▲ green) ────────────────────────────────────────────────
    if buys:
        b_x     = [fill_to_ts(f) for f in buys]
        b_y     = [float(f["px"]) for f in buys]
        b_sizes = [_marker_size(float(f["sz"]), mean_sz) for f in buys]
        b_hover = [
            f"<b>{f['dir']}</b><br>"
            f"Size: {float(f['sz']):.2f} ZEC<br>"
            f"Price: ${float(f['px']):,.2f}<br>"
            f"Time: {fill_to_ts(f).strftime('%b %d %H:%M')}"
            for f in buys
        ]
        fig.add_trace(go.Scatter(
            x=b_x, y=b_y,
            mode="markers",
            name="Buy / Close Short",
            marker=dict(
                symbol="triangle-up",
                color="#00e676",
                size=b_sizes,
                line=dict(width=1, color="#004d1a"),
                opacity=0.9,
            ),
            text=b_hover,
            hovertemplate="%{text}<extra></extra>",
        ), row=1, col=1)

    # ── Sell markers  (▼ red) ─────────────────────────────────────────────────
    if sells:
        s_x     = [fill_to_ts(f) for f in sells]
        s_y     = [float(f["px"]) for f in sells]
        s_sizes = [_marker_size(float(f["sz"]), mean_sz) for f in sells]
        s_hover = [
            f"<b>{f['dir']}</b><br>"
            f"Size: {float(f['sz']):.2f} ZEC<br>"
            f"Price: ${float(f['px']):,.2f}<br>"
            f"Time: {fill_to_ts(f).strftime('%b %d %H:%M')}"
            for f in sells
        ]
        fig.add_trace(go.Scatter(
            x=s_x, y=s_y,
            mode="markers",
            name="Sell / Open Short",
            marker=dict(
                symbol="triangle-down",
                color="#ff1744",
                size=s_sizes,
                line=dict(width=1, color="#4d0000"),
                opacity=0.9,
            ),
            text=s_hover,
            hovertemplate="%{text}<extra></extra>",
        ), row=1, col=1)

    # ── Side table ────────────────────────────────────────────────────────────
    t_time  = [fill_to_ts(f).strftime("%b %d  %H:%M") for f in top_fills]
    t_dir   = [f["dir"]              for f in top_fills]
    t_size  = [f"{float(f['sz']):,.2f}" for f in top_fills]
    t_price = [f"${float(f['px']):,.1f}" for f in top_fills]
    t_color = [
        "#00e676" if f["dir"] in BUY_DIRS else "#ff5252"
        for f in top_fills
    ]

    fig.add_trace(go.Table(
        header=dict(
            values=["<b>Time</b>", "<b>Direction</b>", "<b>Size (ZEC)</b>", "<b>Price</b>"],
            fill_color="#12122a",
            font=dict(color="#c0c0e0", size=11),
            align="left",
            height=26,
            line_color="#2a2a4a",
        ),
        cells=dict(
            values=[t_time, t_dir, t_size, t_price],
            fill_color=[["#0a0a1e"] * len(top_fills)] * 4,
            font=dict(color=[t_color, t_color, t_color, t_color], size=10),
            align="left",
            height=21,
            line_color="#1a1a3a",
        ),
    ), row=1, col=2)

    # ── Funding rate – continuous green/red line ──────────────────────────────
    if f_times and f_rates:
        # Positive area (fill to zero, green)
        pos_rates = [r if r >= 0 else 0 for r in f_rates]
        fig.add_trace(go.Scatter(
            x=f_times, y=pos_rates,
            mode="lines",
            fill="tozeroy",
            fillcolor="rgba(0, 230, 118, 0.25)",
            line=dict(color="#00e676", width=1.5),
            name="Funding +",
            hovertemplate="Rate: %{y:.6f}<extra></extra>",
        ), row=2, col=1)

        # Negative area (fill to zero, red)
        neg_rates = [r if r <= 0 else 0 for r in f_rates]
        fig.add_trace(go.Scatter(
            x=f_times, y=neg_rates,
            mode="lines",
            fill="tozeroy",
            fillcolor="rgba(255, 23, 68, 0.25)",
            line=dict(color="#ff1744", width=1.5),
            name="Funding –",
            hovertemplate="Rate: %{y:.6f}<extra></extra>",
        ), row=2, col=1)

        # Zero line as a scatter trace
        fig.add_trace(go.Scatter(
            x=[f_times[0], f_times[-1]],
            y=[0, 0],
            mode="lines",
            line=dict(color="#555577", width=1, dash="dot"),
            showlegend=False,
            hoverinfo="skip",
        ), row=2, col=1)

    # ── Global layout ─────────────────────────────────────────────────────────
    n_buys  = len(buys)
    n_sells = len(sells)
    fig.update_layout(
        title=dict(
            text=(
                f"<b>ZEC Trades  ·  {WALLET[:12]}…{WALLET[-6:]}</b>"
                f"  &nbsp;|&nbsp;  {len(fills):,} fills"
                f"  &nbsp;|&nbsp;  ▲ {n_buys:,} buys  ▼ {n_sells:,} sells"
                f"  &nbsp;|&nbsp;  mean size: {mean_sz:.2f} ZEC"
                f"  &nbsp;|&nbsp;  Oct 31 – Nov 28 2025"
            ),
            font=dict(size=13, color="#c0c0e0"),
        ),
        height=920,
        template="plotly_dark",
        paper_bgcolor="#070714",
        plot_bgcolor="#0a0a1e",
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.01,
            xanchor="left",   x=0,
            font=dict(size=11),
        ),
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
    )

    # Axes
    fig.update_yaxes(title_text="Price (USD)",    row=1, col=1,
                     gridcolor="#14142a", zerolinecolor="#14142a")
    fig.update_yaxes(title_text="Funding Rate",   row=2, col=1,
                     gridcolor="#14142a", tickformat=".5f")
    fig.update_xaxes(gridcolor="#14142a", row=1, col=1)
    fig.update_xaxes(gridcolor="#14142a", row=2, col=1, title_text="Time (UTC)")

    print("Opening chart in browser…")
    fig.show()


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"\nHYPERION - ZEC Chart - {WALLET}\n{'-'*60}")

    fills   = fetch_fills(WALLET, COIN, START_MS, END_MS)
    candles = fetch_candles(COIN, "4h", START_MS, END_MS)
    funding = fetch_funding(COIN, START_MS)

    print(f"\nBuilding chart…")
    build_chart(fills, candles, funding)
