"""Chunked historical backfill with concurrency + Parquet + Postgres."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from hyperion_pipeline.config.settings import Settings
from hyperion_pipeline.ingestion.fill_normalizer import FillNormalizer
from hyperion_pipeline.ingestion.fill_storage_engine import FillStorageEngine
from hyperion_pipeline.ingestion.sources import HistoricalFillSource
from hyperion_pipeline.models.pydantic_domain import TradeFill
from hyperion_pipeline.storage.postgres_fills import update_wallet_sync_state, upsert_fills

logger = structlog.get_logger(__name__)


def _chunks(items: list[TradeFill], size: int) -> list[list[TradeFill]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


@dataclass(slots=True)
class BackfillOutcome:
    parquet_paths: list[Path]
    fills_attempted: int
    fills_inserted: int


class HistoricalBackfillService:
    """
    Downloads / normalizes fills, writes Parquet (optional), inserts into ``fills`` (optional).

    Postgres inserts use the same ``event_key`` and ``ON CONFLICT DO NOTHING`` as Rust ingest.
    """

    def __init__(
        self,
        settings: Settings,
        storage: FillStorageEngine,
        *,
        postgres_engine: AsyncEngine | None = None,
        write_parquet: bool = True,
        on_checkpoint: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._postgres = postgres_engine
        self._write_parquet = write_parquet
        self._on_checkpoint = on_checkpoint

    async def backfill_wallet(
        self,
        wallet: str,
        source: HistoricalFillSource,
        *,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> BackfillOutcome:
        wallet_l = wallet.lower()
        job_id = uuid.uuid4()
        raw = await source.fetch_fill_payloads(
            wallet_l, start_time_ms=start_time_ms, end_time_ms=end_time_ms
        )
        normalized = FillNormalizer.from_fill_dicts(wallet_l, raw)
        fills = list({f.event_key: f for f in normalized}.values())
        fills.sort(key=lambda f: f.event_timestamp)
        if not fills:
            logger.warning("backfill_no_rows", wallet=wallet_l, job_id=str(job_id))
            if self._postgres is not None:
                await update_wallet_sync_state(
                    self._postgres,
                    wallet_l,
                    status="empty",
                    cursor={"job_id": str(job_id), "fetched_raw": len(raw)},
                    last_watermark=None,
                    error=None,
                )
            return BackfillOutcome([], 0, 0)

        chunks = _chunks(fills, self._settings.backfill_chunk_fill_target)
        semaphore = asyncio.Semaphore(self._settings.backfill_concurrency)
        interval = self._settings.trader_last_seen_interval_secs

        async def _one(chunk: list[TradeFill], idx: int) -> tuple[Path | None, int, int]:
            async with semaphore:
                path: Path | None = None
                if self._write_parquet:
                    path = self._storage.write_fills_parquet(chunk, run_id=job_id)
                if self._postgres is not None:
                    att, ins = await upsert_fills(
                        self._postgres, chunk, trader_last_seen_interval_secs=interval
                    )
                else:
                    att, ins = len(chunk), 0
                payload = {
                    "job_id": str(job_id),
                    "chunk_index": idx,
                    "rows": len(chunk),
                    "postgres_inserted": ins,
                    "postgres_attempted": att,
                }
                if path is not None:
                    payload["path"] = str(path)
                logger.info("backfill_checkpoint", **payload)
                if self._on_checkpoint is not None:
                    await self._on_checkpoint(payload)
                return path, att, ins

        results = await asyncio.gather(*(_one(c, i) for i, c in enumerate(chunks)))
        written = [p for p, _, _ in results if p is not None]
        attempted_total = sum(a for _, a, _ in results)
        inserted_total = sum(i for _, _, i in results)

        last_wm = fills[-1].event_timestamp
        if self._postgres is not None:
            await update_wallet_sync_state(
                self._postgres,
                wallet_l,
                status="ok",
                cursor={
                    "job_id": str(job_id),
                    "chunks": len(chunks),
                    "fetched_raw": len(raw),
                    "unique_fills": len(fills),
                },
                last_watermark=last_wm,
                error=None,
            )

        logger.info(
            "backfill_complete",
            wallet=wallet_l,
            job_id=str(job_id),
            chunks=len(chunks),
            inserted=inserted_total,
            attempted=attempted_total,
        )
        return BackfillOutcome(written, attempted_total, inserted_total)
