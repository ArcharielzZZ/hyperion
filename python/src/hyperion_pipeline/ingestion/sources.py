"""Pluggable historical fill sources (REST, files, tests)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from hyperion_pipeline.config.settings import Settings
from hyperion_pipeline.ingestion.rate_limit import AsyncTokenBucket

logger = structlog.get_logger(__name__)


@runtime_checkable
class HistoricalFillSource(Protocol):
    """Async source of raw user-fill dict payloads (pre-normalization)."""

    async def fetch_fill_payloads(
        self,
        wallet: str,
        *,
        start_time_ms: int | None,
        end_time_ms: int | None,
    ) -> list[dict]:
        """Return list of fill dicts (same shape as Hyperliquid ``fills[]`` / REST array)."""


def _retryable_http(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 502, 503)
    return isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout))


def _dedupe_fill_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, int, int]] = set()
    out: list[dict] = []
    for r in sorted(rows, key=lambda x: int(x["time"])):
        key = (str(r["hash"]), int(r["tid"]), int(r["oid"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


class HyperliquidHistoricalFillSource:
    """Hyperliquid ``POST /info`` — ``userFills`` and ``userFillsByTime`` (paginated when needed)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=settings.http_timeout_sec)
        self._bucket = AsyncTokenBucket(min_interval_sec=settings.hyperliquid_min_request_interval_sec)

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception(_retryable_http),
        stop=stop_after_attempt(12),
        wait=wait_exponential(multiplier=1, min=2, max=90),
        reraise=True,
    )
    async def _post(self, body: dict[str, Any]) -> Any:
        await self._bucket.acquire()
        url = self._settings.hyperliquid_info_url
        response = await self._client.post(url, json=body, headers={"Content-Type": "application/json"})
        response.raise_for_status()
        return response.json()

    async def _fetch_by_time_window(self, wallet: str, start_ms: int, end_ms: int) -> list[dict]:
        body: dict[str, Any] = {
            "type": "userFillsByTime",
            "user": wallet,
            "startTime": int(start_ms),
            "endTime": int(end_ms),
        }
        rows = await self._post(body)
        if not isinstance(rows, list):
            raise TypeError(f"userFillsByTime expected list, got {type(rows).__name__}")
        if len(rows) < 2000:
            return rows
        if end_ms - start_ms <= 1:
            logger.warning(
                "hyperliquid_fill_page_full_cannot_split",
                wallet=wallet,
                start_ms=start_ms,
                end_ms=end_ms,
                rows=len(rows),
            )
            return rows
        mid = (start_ms + end_ms) // 2
        left = await self._fetch_by_time_window(wallet, start_ms, mid)
        right = await self._fetch_by_time_window(wallet, mid + 1, end_ms)
        return _dedupe_fill_rows(left + right)

    async def fetch_fill_payloads(
        self,
        wallet: str,
        *,
        start_time_ms: int | None,
        end_time_ms: int | None,
    ) -> list[dict]:
        w = wallet.lower()
        if start_time_ms is None and end_time_ms is None:
            body: dict[str, Any] = {"type": "userFills", "user": w}
            rows = await self._post(body)
            if not isinstance(rows, list):
                raise TypeError(f"userFills expected list, got {type(rows).__name__}")
            return _dedupe_fill_rows(rows)
        start = int(start_time_ms or 0)
        end = int(end_time_ms or 0)
        if end <= start:
            raise ValueError("end_time_ms must be greater than start_time_ms when both are set")
        return await self._fetch_by_time_window(w, start, end)


class ListFillSource:
    """Test/dev source returning pre-built fill dicts."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def fetch_fill_payloads(
        self,
        wallet: str,
        *,
        start_time_ms: int | None,
        end_time_ms: int | None,
    ) -> list[dict]:
        _ = wallet, start_time_ms, end_time_ms
        return list(self._rows)
