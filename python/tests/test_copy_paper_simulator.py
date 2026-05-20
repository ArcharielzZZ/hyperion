from datetime import datetime, timezone

from hyperion_pipeline.analytics.copy_paper_simulator import (
    PaperPositionTracker,
    SkipReason,
    detect_whale_round_trips,
    detect_whale_round_trips_position_flat,
    evaluate_fill_for_copy,
    run_round_trip_copy_simulation,
    simulate_whale_round_trip,
    whale_signed_delta,
)
from hyperion_pipeline.analytics.sizing_engine import CopySizingConfig
from hyperion_pipeline.models.pydantic_domain import TradeFill

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone.utc)


def _fill(
    *,
    coin: str = "BTC",
    side: str = "buy",
    size: float = 1.0,
    price: float = 50_000.0,
    fill_dir: str,
    ts: datetime,
    closed_pnl: float | None = None,
    key: str = "",
) -> TradeFill:
    return TradeFill(
        wallet="0xtest",
        coin=coin,
        side=side,
        size=size,
        price=price,
        event_timestamp=ts,
        event_key=key or f"fill|0xtest|{coin}|{ts.timestamp()}",
        fill_dir=fill_dir,
        closed_pnl_usd=closed_pnl,
    )


def test_whale_signed_delta_open_close_long():
    o = _fill(side="buy", fill_dir="Open Long", ts=T0)
    c = _fill(side="sell", fill_dir="Close Long", ts=T1)
    assert whale_signed_delta(o) == 1.0
    assert whale_signed_delta(c) == -1.0


def test_detect_whale_round_trips_single_long_cycle():
    fills = [
        _fill(side="buy", fill_dir="Open Long", ts=T0, key="a"),
        _fill(side="sell", fill_dir="Close Long", ts=T1, key="b", closed_pnl=100.0),
    ]
    trips = detect_whale_round_trips_position_flat(fills)
    assert len(trips) == 1
    assert trips[0].direction == "long"
    assert len(trips[0].open_fills) == 1
    assert len(trips[0].close_fills) == 1
    assert trips[0].gross_pnl_usd == 100.0


def test_evaluate_fill_for_copy_dust_vs_ok():
    big = _fill(size=10.0, price=10_000.0, fill_dir="Open Long", ts=T0)
    _, _, dust = evaluate_fill_for_copy(
        big,
        equity=10_000.0,
        copy_scale=0.000001,
        max_equity_pct_per_fill=0.02,
        min_trade_usd=1.0,
        max_trade_usd=None,
    )
    assert dust == SkipReason.DUST

    _, _, ok = evaluate_fill_for_copy(
        big,
        equity=10_000.0,
        copy_scale=0.01,
        max_equity_pct_per_fill=0.02,
        min_trade_usd=1.0,
        max_trade_usd=None,
    )
    assert ok is None


def test_paper_position_tracker_fifo_close():
    tracker = PaperPositionTracker()
    realized, had_open = tracker.apply_leg(
        "BTC", signed_delta=0.01, price=50_000.0, is_close=False
    )
    assert had_open is True
    assert realized == 0.0
    realized, had_open = tracker.apply_leg(
        "BTC", signed_delta=-0.01, price=51_000.0, is_close=True
    )
    assert had_open is True
    assert realized > 0


def test_simulate_whale_round_trip_profitable_long():
    trip = detect_whale_round_trips_position_flat(
        [
            _fill(side="buy", fill_dir="Open Long", ts=T0, key="a"),
            _fill(
                side="sell",
                price=55_000.0,
                fill_dir="Close Long",
                ts=T1,
                key="b",
                closed_pnl=500.0,
            ),
        ]
    )[0]
    legs = [(f, 0.001) for f in trip.all_fills]
    sim = simulate_whale_round_trip(trip, legs, fee_bps=4.0, slippage_bps=6.0)
    assert sim.realized_pnl > 0


def test_run_round_trip_copy_skips_dust_trip():
    """At tiny copy_scale both legs are dust → trip skipped as INCOMPLETE_TRIP."""

    fills = [
        _fill(side="buy", fill_dir="Open Long", ts=T0, key="a"),
        _fill(side="sell", fill_dir="Close Long", ts=T1, key="b", closed_pnl=50.0),
    ]
    m = run_round_trip_copy_simulation(
        "0xtest",
        fills,
        starting_equity=10_000.0,
        copy_scale=0.000001,
        min_trade_usd=1.0,
        use_dynamic_sizing=False,
    )
    assert m.total_round_trips_detected == 1
    assert m.round_trips_simulated == 0
    assert m.round_trips_skipped.get(SkipReason.INCOMPLETE_TRIP.value, 0) == 1


def test_run_round_trip_copy_simulates_complete_trip():
    fills = [
        _fill(side="buy", size=2.0, fill_dir="Open Long", ts=T0, key="a"),
        _fill(
            side="sell",
            size=2.0,
            fill_dir="Close Long",
            ts=T1,
            key="b",
            closed_pnl=200.0,
        ),
    ]
    m = run_round_trip_copy_simulation(
        "0xtest",
        fills,
        starting_equity=10_000.0,
        copy_scale=0.05,
        min_trade_usd=1.0,
        use_dynamic_sizing=False,
    )
    assert m.round_trips_simulated == 1
    assert m.copyability_score == 1.0
    assert m.total_return_pct != 0.0


def test_low_copyability_pre_filtered(monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr(
        "hyperion_pipeline.analytics.sizing_engine.estimate_copyability_sample",
        lambda *args, **kwargs: 0.10,
    )
    fills = []
    for i in range(25):
        t0 = T0 + timedelta(hours=i * 2)
        t1 = t0 + timedelta(hours=1)
        fills.append(_fill(side="buy", size=2.0, fill_dir="Open Long", ts=t0, key=f"o{i}"))
        fills.append(
            _fill(
                side="sell",
                size=2.0,
                fill_dir="Close Long",
                ts=t1,
                key=f"c{i}",
                closed_pnl=500.0,
            )
        )
    m = run_round_trip_copy_simulation(
        "0xtest",
        fills,
        starting_equity=10_000.0,
        use_dynamic_sizing=True,
    )
    assert m.round_trips_simulated == 0
    assert m.verdict == "FILTERED_COPYABILITY"
    assert m.sizing_summary.copyability_filter_result == "LOW_COPYABILITY"


def test_edge_multiplier_35_filters_marginal_edge():
    fills = [
        _fill(side="buy", size=1.0, fill_dir="Open Long", ts=T0, key="a"),
        _fill(
            side="sell",
            size=1.0,
            fill_dir="Close Long",
            ts=T1,
            key="b",
            closed_pnl=5.0,
        ),
    ]
    cfg = CopySizingConfig(edge_cost_multiplier=3.5, min_trips_detected=1, min_copyability=0.0)
    m = run_round_trip_copy_simulation(
        "0xtest",
        fills,
        starting_equity=10_000.0,
        use_dynamic_sizing=True,
        sizing_config=cfg,
    )
    assert m.verdict == "FILTERED_EDGE"


def test_strong_whale_live_candidate_verdict(monkeypatch):
    from datetime import timedelta

    from hyperion_pipeline.analytics.copy_paper_simulator import classify_wallet

    monkeypatch.setattr(
        "hyperion_pipeline.analytics.sizing_engine.estimate_copyability_sample",
        lambda *args, **kwargs: 0.85,
    )
    fills = []
    base = T0
    for i in range(25):
        t0 = base + timedelta(hours=i * 2)
        t1 = t0 + timedelta(hours=1)
        fills.append(
            _fill(
                side="buy",
                size=2.0,
                fill_dir="Open Long",
                ts=t0,
                key=f"o{i}",
            )
        )
        fills.append(
            _fill(
                side="sell",
                size=2.0,
                fill_dir="Close Long",
                ts=t1,
                key=f"c{i}",
                closed_pnl=800.0,
            )
        )
    m = run_round_trip_copy_simulation(
        "0xtest",
        fills,
        starting_equity=10_000.0,
        use_dynamic_sizing=True,
        sizing_config=CopySizingConfig(min_trips_detected=1),
        exchange_return_pct=0.09,
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    assert m.passed_edge_filter and m.passed_copyability_gate
    assert m.sizing_summary.equity_firewall_assertions == 0
    assert classify_wallet(True, "OK", True, "OK", 1.02, 0.09, m.total_round_trips_detected) == "LIVE_CANDIDATE"
    assert m.verdict in ("LIVE_CANDIDATE", "MONITOR")
