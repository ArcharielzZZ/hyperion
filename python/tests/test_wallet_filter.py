"""Wallet filter stage unit tests."""

from datetime import datetime, timedelta, timezone

from hyperion_pipeline.models.pydantic_domain import TradeFill
from hyperion_pipeline.wallet_filter.config import WalletFilterConfig
from hyperion_pipeline.wallet_filter.pipeline import evaluate_wallet_filter
from hyperion_pipeline.wallet_filter.stages import stage1_hf_label, stage2_promotion
from hyperion_pipeline.wallet_filter.aggregates import multi_window_from_fills


def _fill(ts: datetime, *, closed_pnl: float = 1.0, size: float = 100.0) -> TradeFill:
    return TradeFill(
        wallet="0xabc",
        coin="BTC",
        side="buy",
        size=size,
        leverage=1.0,
        price=100.0,
        event_timestamp=ts,
        event_key=f"k-{ts.timestamp()}",
        source="test",
        closed_pnl_usd=closed_pnl,
    )


def test_ultra_hf_bot_banned() -> None:
    cfg = WalletFilterConfig()
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    fills = [_fill(start + timedelta(minutes=3 * i)) for i in range(10_000)]
    assert stage1_hf_label(fills, cfg) == "ULTRA_HF_BOT"
    result = evaluate_wallet_filter(
        "0xabc", fills, cfg=cfg, as_of=start + timedelta(days=21)
    )
    assert result.status == "BANNED"
    assert result.promoted is False


def test_stage2_promotion_positive_pnl() -> None:
    cfg = WalletFilterConfig(min_volume_usd=100.0, min_trades=5, pnl_efficiency_ratio=0.0001)
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    as_of = start + timedelta(days=35)
    fills = [_fill(start + timedelta(days=i), closed_pnl=50.0, size=10.0) for i in range(30)]
    mw = multi_window_from_fills(fills, as_of=as_of)
    ok, _ = stage2_promotion(mw, cfg, as_of=as_of)
    assert ok is True


def test_whale_override_promotes() -> None:
    cfg = WalletFilterConfig(min_volume_usd=100.0, min_trades=3)
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    as_of = start + timedelta(days=5)
    fills = [
        _fill(start + timedelta(days=i), closed_pnl=20.0, size=50.0)
        for i in range(5)
    ]
    result = evaluate_wallet_filter(
        "0xabc",
        fills,
        cfg=cfg,
        force_whale_promoted=True,
        pnl_snapshots_30d=15,
        as_of=as_of,
    )
    assert result.promoted is True
    assert result.status == "PROMOTED"
