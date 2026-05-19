"""Persist advisory copyability rows mirroring migration ``0013_copyability_advisory``."""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger(__name__)

_INSERT_ADVISORY = text(
    """
    INSERT INTO trader_copyability_advisory (
        trader_id,
        wallet,
        copyability_score,
        signal_frequency_component,
        timing_component,
        realized_ratio_component,
        stability_component,
        horizon_days,
        computed_at
    )
    SELECT
        t.id,
        :wallet,
        :score,
        :signal_frequency_component,
        :timing_component,
        :realized_ratio_component,
        :stability_component,
        :horizon_days,
        NOW()
    FROM traders t
    WHERE lower(t.wallet) = lower(:wallet)
    """
)


async def insert_copyability_advisory_row(
    engine: AsyncEngine,
    *,
    wallet: str,
    copyability_score: float,
    signal_frequency_component: float,
    timing_component: float,
    realized_ratio_component: float,
    stability_component: float,
    horizon_days: float,
) -> bool:
    """
    Purpose: append a freshly computed advisory row for dashboard joins.

    Inputs: wallet key plus five component floats (already on 0–100 scale).
    Outputs: ``True`` if the trader existed and Postgres accepted the INSERT.

    Gotcha: always inserts history rows — callers must ORDER BY computed_at DESC in read paths.
    """

    w = wallet.lower()
    async with engine.begin() as conn:
        res = await conn.execute(
            _INSERT_ADVISORY,
            {
                "wallet": w,
                "score": float(copyability_score),
                "signal_frequency_component": float(signal_frequency_component),
                "timing_component": float(timing_component),
                "realized_ratio_component": float(realized_ratio_component),
                "stability_component": float(stability_component),
                "horizon_days": float(horizon_days),
            },
        )
    inserted = int(res.rowcount or 0) > 0
    logger.info(
        "copyability_advisory_persisted",
        wallet=w,
        wrote=inserted,
        score=copyability_score,
    )
    return inserted
