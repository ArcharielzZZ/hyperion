from pathlib import Path

from hyperion_pipeline.ingestion.fill_normalizer import FillNormalizer


def test_normalize_user_fills_snapshot():
    hyperion_root = Path(__file__).resolve().parents[2]
    fixture = hyperion_root / "services" / "ingest" / "fixtures" / "user_fills_snapshot.json"
    blob = __import__("json").loads(fixture.read_text(encoding="utf-8"))
    data = blob["data"]
    fills = FillNormalizer.from_user_fills_snapshot(data["user"], data)
    assert len(fills) == 2
    assert fills[0].coin == "BTC"
    assert fills[0].side == "buy"
    assert fills[0].event_key.startswith("fill|0x31ca8395cf837de08b24da3f660e77761dfb974b|BTC|")


def test_merge_dedupe():
    from datetime import datetime, timezone

    from hyperion_pipeline.merge.unified_pipeline import MergeEvent, UnifiedMergePipeline

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    ev = [
        MergeEvent("a", t1, t0, "h1"),
        MergeEvent("a", t0, t0, "h2"),
        MergeEvent("b", t0, t0, "h3"),
    ]
    deduped = UnifiedMergePipeline.dedupe_by_event_key(ev)
    assert [e.event_key for e in deduped] == ["a", "b"]
