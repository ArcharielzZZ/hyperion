"""Write normalized fills to Parquet (partitioned); Postgres upsert is explicit next step."""

from __future__ import annotations

import uuid
from pathlib import Path

import polars as pl
import structlog

from hyperion_pipeline.models.pydantic_domain import TradeFill
from hyperion_pipeline.storage.partition_path import fill_partition_dir

logger = structlog.get_logger(__name__)


class FillStorageEngine:
    """
    Parquet-first storage with Snappy compression and append-friendly chunk files.

    Use ``storage.postgres_fills.upsert_fills`` for the same ``fills`` rows in Postgres.
    """

    def __init__(self, data_root: Path) -> None:
        self._root = data_root

    def write_fills_parquet(self, fills: list[TradeFill], *, run_id: uuid.UUID | None = None) -> Path:
        """Write a single chunk file; returns path written."""

        if not fills:
            raise ValueError("fills must be non-empty")
        run = run_id or uuid.uuid4()
        ts0 = fills[0].event_timestamp
        part_dir = fill_partition_dir(self._root, ts0)
        part_dir.mkdir(parents=True, exist_ok=True)
        path = part_dir / f"fills_{run.hex}_{ts0.strftime('%H%M%S')}.parquet"
        df = pl.DataFrame(
            {
                "wallet": [f.wallet for f in fills],
                "coin": [f.coin for f in fills],
                "side": [f.side for f in fills],
                "size": [f.size for f in fills],
                "leverage": [f.leverage for f in fills],
                "price": [f.price for f in fills],
                "event_timestamp": [f.event_timestamp for f in fills],
                "event_key": [f.event_key for f in fills],
                "source": [f.source for f in fills],
            }
        )
        df.write_parquet(path, compression="snappy", statistics=True)
        logger.info("wrote_parquet_chunk", path=str(path), rows=len(fills))
        return path
