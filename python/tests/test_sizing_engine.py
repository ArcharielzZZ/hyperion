from datetime import datetime, timezone

import pytest

from hyperion_pipeline.analytics.sizing_engine import (
    CopySizingConfig,
    PortfolioRiskManager,
    SimulatorWalletProfile,
    TradeResult,
    WhaleAllocationManager,
    build_wallet_profile_from_trips,
    compute_position_size,
    estimate_stop_distance,
    estimate_variance_drain_pct,
    passes_copyability_gate,
    passes_edge_filter,
)
from hyperion_pipeline.analytics.copy_paper_simulator import WhaleRoundTrip
from hyperion_pipeline.models.pydantic_domain import TradeFill

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _trip(*, coin: str = "BTC", gross: float = 100.0) -> WhaleRoundTrip:
    o = TradeFill(
        wallet="0xtest",
        coin=coin,
        side="buy",
        size=1.0,
        price=50_000.0,
        event_timestamp=T0,
        event_key="o",
        fill_dir="Open Long",
    )
    c = TradeFill(
        wallet="0xtest",
        coin=coin,
        side="sell",
        size=1.0,
        price=51_000.0,
        event_timestamp=T1,
        event_key="c",
        fill_dir="Close Long",
        closed_pnl_usd=gross,
    )
    return WhaleRoundTrip(
        coin=coin,
        direction="long",
        open_fills=[o],
        close_fills=[c],
        open_time=T0,
        close_time=T1,
        gross_pnl_usd=gross,
    )


def test_compute_position_size_negative_expectancy():
    r = compute_position_size(
        equity=100.0,
        win_rate=0.2,
        avg_win_loss_ratio=1.0,
        copyability=0.5,
        stop_distance_pct=0.03,
    )
    assert r.skip is True
    assert r.skip_reason == "NEGATIVE_EXPECTANCY"


def test_compute_position_size_dust():
    r = compute_position_size(
        equity=10.0,
        win_rate=0.6,
        avg_win_loss_ratio=2.0,
        copyability=0.01,
        stop_distance_pct=0.03,
        min_trade_usd=5.0,
    )
    assert r.skip is True
    assert r.skip_reason == "DUST"


def test_compute_position_size_binding_stop():
    r = compute_position_size(
        equity=500.0,
        win_rate=0.55,
        avg_win_loss_ratio=1.5,
        copyability=0.5,
        stop_distance_pct=0.03,
        risk_per_trade_pct=0.01,
        max_position_pct=0.15,
        min_trade_usd=1.0,
    )
    assert r.skip is False
    assert r.position_usd >= 1.0
    assert r.binding_constraint in ("stop", "kelly", "max_cap")


def test_portfolio_risk_manager_exposure_cap():
    rm = PortfolioRiskManager(100.0, max_total_exposure_pct=0.5)
    ok, _ = rm.can_open_trade(60.0)
    assert ok is False


def test_portfolio_risk_manager_circuit_breaker():
    rm = PortfolioRiskManager(100.0, circuit_breaker_pct=0.20)
    rm.equity = 75.0
    rm._last_closed_equity = 75.0
    rm.peak_equity = 100.0
    ok, reason = rm.can_open_trade(5.0)
    assert ok is False
    assert reason == "CIRCUIT_BREAKER"


def test_portfolio_risk_manager_allow():
    rm = PortfolioRiskManager(100.0)
    ok, reason = rm.can_open_trade(10.0)
    assert ok is True
    assert reason == "OK"


def test_whale_allocation_rebalance_floor_and_sum():
    mgr = WhaleAllocationManager(1000.0, min_allocation_pct=0.02)
    mgr.wallets["0xaaa"] = mgr._ensure("0xaaa")
    mgr.wallets["0xbbb"] = mgr._ensure("0xbbb")
    mgr.wallets["0xaaa"].copyability_score = 0.5
    mgr.wallets["0xbbb"].copyability_score = 0.5
    for i in range(10):
        mgr.update_wallet("0xaaa", TradeResult(pnl_usd=10.0, pnl_pct=1.0, fees=0.1))
        mgr.update_wallet("0xbbb", TradeResult(pnl_usd=3.0, pnl_pct=0.5, fees=0.1))
    alloc = mgr.rebalance(min_copyability=0.30)
    usd = {k: v["allocation_usd"] for k, v in alloc.items()}
    assert abs(sum(usd.values()) - 1000.0) < 0.01
    assert usd["0xbbb"] >= 1000.0 * 0.02 - 0.01


def test_whale_allocation_below_copyability_floor_gets_zero():
    mgr = WhaleAllocationManager(1000.0, min_allocation_pct=0.02)
    mgr.wallets["0xlow"] = mgr._ensure("0xlow")
    mgr.wallets["0xhi"] = mgr._ensure("0xhi")
    mgr.wallets["0xlow"].copyability_score = 0.10
    mgr.wallets["0xhi"].copyability_score = 0.80
    for _ in range(8):
        mgr.update_wallet("0xhi", TradeResult(pnl_usd=5.0, pnl_pct=1.0, fees=0.1))
    alloc = mgr.rebalance(min_copyability=0.30)
    assert alloc["0xlow"]["allocation_usd"] == 0.0
    assert alloc["0xlow"]["status"] == "below_copyability_floor"
    assert alloc["0xhi"]["allocation_usd"] == pytest.approx(1000.0, rel=1e-3)


def test_passes_copyability_gate_low_copyability():
    ok, reason = passes_copyability_gate(0.10, min_copyability=0.30, n_trips_detected=100)
    assert ok is False
    assert reason == "LOW_COPYABILITY"


def test_passes_copyability_gate_insufficient_trips():
    ok, reason = passes_copyability_gate(0.50, n_trips_detected=5, min_trips_detected=20)
    assert ok is False
    assert reason == "INSUFFICIENT_TRIPS"


def test_passes_copyability_gate_ok():
    ok, reason = passes_copyability_gate(0.50, n_trips_detected=30, min_trips_detected=20)
    assert ok is True
    assert reason == "OK"


def test_passes_copyability_gate_boundary_inclusive():
    ok, reason = passes_copyability_gate(0.30, min_copyability=0.30, n_trips_detected=25)
    assert ok is True
    assert reason == "OK"


def test_estimate_stop_distance_fallbacks():
    trip = _trip()
    profile = SimulatorWalletProfile(
        win_rate=0.5,
        avg_win_loss_ratio=1.2,
        copyability_score=0.1,
        avg_win_pct=0.02,
        avg_loss_pct=0.01,
        avg_loss_pct_by_coin={"BTC": 0.04},
    )
    assert estimate_stop_distance(trip, profile, default_stop_pct=0.03) == pytest.approx(0.04)
    profile2 = SimulatorWalletProfile(0.5, 1.2, 0.1, 0.02, 0.01)
    assert estimate_stop_distance(trip, profile2, default_stop_pct=0.03) == 0.03


def test_build_wallet_profile_from_trips():
    trips = [_trip(gross=50.0), _trip(gross=-20.0), _trip(gross=30.0)]
    p = build_wallet_profile_from_trips(trips)
    assert 0.0 < p.win_rate < 1.0
    assert p.avg_win_loss_ratio > 0


def test_small_account_low_copyability_with_floor():
    """copyability_eff = max(copyability, 0.2) so low tape copyability still sizes."""

    r = compute_position_size(
        equity=100.0,
        win_rate=0.55,
        avg_win_loss_ratio=1.5,
        copyability=0.059,
        stop_distance_pct=0.03,
        min_trade_usd=1.0,
        max_position_pct=0.15,
    )
    assert r.skip is False
    assert 1.0 <= r.position_usd <= 15.0


def test_equity_ema_raw_with_few_snapshots():
    rm = PortfolioRiskManager(100.0)
    assert rm.equity_ema() == 100.0


def test_equity_ema_converges():
    rm = PortfolioRiskManager(100.0)
    for v in [100.0] + [100.0 + i for i in range(25)]:
        rm.equity = v
        rm._equity_snapshots.append(v)
        rm._last_closed_equity = v
    ema = rm.equity_ema(span=20)
    assert ema > 100.0
    assert ema < rm.equity


def test_passes_edge_filter_insufficient_edge():
    ok, reason = passes_edge_filter(0.5, 0.001, 0.001, fee_bps=4, slippage_bps=6)
    assert ok is False
    assert reason == "INSUFFICIENT_EDGE"


def test_passes_edge_filter_strong_edge():
    ok, reason = passes_edge_filter(0.6, 0.05, 0.02, fee_bps=4, slippage_bps=6)
    assert ok is True
    assert reason == "OK"


def test_passes_edge_filter_win_rate_floor():
    ok, reason = passes_edge_filter(0.4, 0.05, 0.02)
    assert ok is False
    assert reason == "WIN_RATE_TOO_LOW"


def test_record_open_does_not_change_equity():
    rm = PortfolioRiskManager(100.0)
    for _ in range(10):
        rm.record_open("BTC", 5.0)
    assert rm.equity == 100.0


def test_record_close_equity_firewall():
    rm = PortfolioRiskManager(100.0)
    rm.record_open("BTC", 10.0)
    rm.equity = 999.0
    with pytest.raises(AssertionError, match="outside of record_close"):
        rm.record_open("ETH", 5.0)
    assert rm.equity_firewall_assertions == 1


def test_ratchet_caps_growth():
    r = compute_position_size(
        equity=100_000.0,
        win_rate=0.55,
        avg_win_loss_ratio=1.5,
        copyability=0.5,
        stop_distance_pct=0.01,
        last_position_usd=1000.0,
        max_size_increase_factor=1.05,
    )
    assert r.position_usd <= 1000.0 * 1.05 + 1e-6
    assert r.binding_constraint == "ratchet"


def test_variance_drain_estimate():
    assert estimate_variance_drain_pct(10.0, 100) > 0


def test_check_missing_fill_dir_exclude():
    from hyperion_pipeline.analytics.copy_paper_simulator import check_missing_fill_dir

    q = check_missing_fill_dir("0xw", fills=[])
    assert q["recommendation"] == "exclude"
    assert q["pct_missing"] == 1.0


def test_check_missing_fill_dir_ok_and_backfill():
    from hyperion_pipeline.analytics.copy_paper_simulator import check_missing_fill_dir

    ok_fills = [
        TradeFill(
            wallet="0xw",
            coin="BTC",
            side="buy",
            size=1.0,
            price=1.0,
            event_timestamp=T0,
            event_key="a",
            fill_dir="Open Long",
        )
    ]
    assert check_missing_fill_dir("0xw", fills=ok_fills)["recommendation"] == "ok"

    mixed = [
        ok_fills[0],
        TradeFill(
            wallet="0xw",
            coin="BTC",
            side="sell",
            size=1.0,
            price=1.0,
            event_timestamp=T1,
            event_key="b",
        ),
    ]
    assert check_missing_fill_dir("0xw", fills=mixed)["recommendation"] == "backfill"


def test_classify_wallet_verdicts():
    from hyperion_pipeline.analytics.copy_paper_simulator import classify_wallet

    assert classify_wallet(False, "INSUFFICIENT_EDGE", True, "OK", 0, 0, 10) == "FILTERED_EDGE"
    assert classify_wallet(True, "OK", False, "LOW_COPYABILITY", 0, 0, 10) == "FILTERED_COPYABILITY"
    assert classify_wallet(True, "OK", True, "OK", 0, 0, 0) == "NO_DATA"
    assert classify_wallet(True, "OK", True, "OK", 1.0, 0.09, 50) == "LIVE_CANDIDATE"
    assert classify_wallet(True, "OK", True, "OK", -1.0, 0.09, 50) == "MONITOR"
    assert classify_wallet(True, "OK", True, "OK", -5.0, 0.01, 50) == "REVIEW"


def test_integration_100_trades_zero_expectancy_no_variance_drain():
    """50/50 zero-EV outcomes: EMA equity sizing should not bleed vs live-equity sizing."""

    import random

    random.seed(42)
    cfg = CopySizingConfig(ema_span=20, max_size_increase_factor=1.05)
    profile = SimulatorWalletProfile(
        win_rate=0.55,
        avg_win_loss_ratio=1.2,
        copyability_score=0.3,
        avg_win_pct=0.02,
        avg_loss_pct=0.015,
    )

    def _run(*, use_ema: bool) -> float:
        rm = PortfolioRiskManager(10_000.0)
        last_pos: float | None = None
        for _ in range(100):
            equity = rm.equity_ema(span=cfg.ema_span) if use_ema else rm.equity
            sz = compute_position_size(
                equity=equity,
                win_rate=profile.win_rate,
                avg_win_loss_ratio=profile.avg_win_loss_ratio,
                copyability=profile.copyability_score,
                stop_distance_pct=0.03,
                last_position_usd=last_pos,
                max_size_increase_factor=cfg.max_size_increase_factor,
            )
            if sz.skip:
                continue
            move = 0.01 if random.random() < 0.5 else -0.01
            pnl = sz.position_usd * move
            rm.record_open("BTC", sz.position_usd)
            rm.record_close("BTC", sz.position_usd, pnl, 0.0)
            last_pos = sz.position_usd
        return rm.equity

    random.seed(42)
    ema_equity = _run(use_ema=True)
    random.seed(42)
    live_equity = _run(use_ema=False)
    ema_ret = abs(ema_equity - 10_000.0) / 10_000.0 * 100.0
    live_ret = abs(live_equity - 10_000.0) / 10_000.0 * 100.0
    assert ema_ret < 5.0
    assert ema_ret <= live_ret
