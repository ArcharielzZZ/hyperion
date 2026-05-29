"""
Hyperion Wallet Explorer Dashboard
==================================

Local Dash web UI to interactively explore a wallet's trades on coin
candlestick charts.

Workflow
--------
1. Pull a wallet (Dashboard button or CLI):
       python analytics/scripts/force_pull_wallet_bundle.py 0x...
2. Start the dashboard:
       python analytics/dashboard/wallet_explorer.py
3. Open http://127.0.0.1:8050 in your browser.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dash import Dash

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from callbacks_orderbook import register_orderbook_callbacks  # noqa: E402
from callbacks_twitter import register_twitter_callbacks  # noqa: E402
from callbacks_wallet import register_wallet_callbacks  # noqa: E402
from wallet_chart import _empty_figure  # noqa: E402
from wallet_layout import build_layout  # noqa: E402

app = Dash(__name__, title="Hyperion Wallet Explorer", suppress_callback_exceptions=True)
app.layout = build_layout(_empty_figure("Bitte eine Wallet laden."))

register_wallet_callbacks(app)
register_twitter_callbacks(app)
register_orderbook_callbacks(app)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("Hyperion Wallet Explorer  ->  http://127.0.0.1:8050")
    app.run(debug=False, host="127.0.0.1", port=8050)
