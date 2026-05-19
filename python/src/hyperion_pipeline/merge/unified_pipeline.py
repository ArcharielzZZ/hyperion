"""Live + historical merge helpers (dedupe, ordering)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MergeEvent:
    """Canonical merge unit before persistence."""

    event_key: str
    event_time: datetime
    ingested_at: datetime
    payload_hash: str


class UnifiedMergePipeline:
    """
    Shared policy for combining REST backfill with live websocket rows.

    **Design:** ``event_key`` is the idempotency key; ``event_time`` drives analytics ordering;
    ``ingested_at`` breaks ties when exchange timestamps collide.
    """

    @staticmethod
    def sort_events(events: list[MergeEvent]) -> list[MergeEvent]:
        return sorted(events, key=lambda e: (e.event_time, e.ingested_at, e.event_key))

    @staticmethod
    def dedupe_by_event_key(events: list[MergeEvent]) -> list[MergeEvent]:
        """Keep first occurrence in ``sort_events`` order (stable)."""

        seen: set[str] = set()
        out: list[MergeEvent] = []
        for e in UnifiedMergePipeline.sort_events(events):
            if e.event_key in seen:
                continue
            seen.add(e.event_key)
            out.append(e)
        return out
