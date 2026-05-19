"""Slippage-aware FIFO paper replay primitives (research only)."""

from hyperion_pipeline.analytics.paper_replay.aggregate_metrics import (
    WalletPaperReplayRow,
    markdown_table_from_wallet_rows,
    summarize_wallet_round_trip_replay,
)

__all__ = [
    "WalletPaperReplayRow",
    "markdown_table_from_wallet_rows",
    "summarize_wallet_round_trip_replay",
]
