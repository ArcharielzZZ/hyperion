# Research & copy-trade operations

This guide documents the **offline** controls that sit beside live Rust ingest and the scoring engine. Nothing here changes promotion rules inside `hyperion-trader-engine`.

## `infra/scripts/copy-trade-audit.ps1`

Purpose: generate a single Markdown bundle that pairs the round-trip gate SQL with the slippage-aware FIFO replay rankings.

When to run:

- After refreshing promoted wallets in Postgres.
- Before debating whether a wallet merits human copy-trader review.

Interpretation:

1. **Step 1** (`infra/sql/copy_trade_roundtrip_audit.sql`) counts exchange-tagged closes, approximates orphan legs, and flags `insufficient_evidence_gate` when there are fewer than 20 close rows in the configurable 30-day window. This is a *label-completeness* screen, not FIFO truth.
2. **Step 2** (`hyperion-pipeline paper-replay-promoted --realized-only --slippage-bps 7`) rebuilds FIFO segments, applies bilateral slippage (`LinearBpsSlippage`), subtracts allocated fees, ranks wallets, and emits the `PASS` column when **all** of `win_rate > 55%`, payoff `> 1.2` with both winners and losers, and positive realized paper PnL.

**Sufficient evidence**: the SQL gate uses **20 exchange close rows** inside the window because that is the smallest count where percentile-based heuristics stop bouncing on single outliers. It is **not** identical to “20 FIFO peeled trips”; reconcile both panels before drawing capital conclusions.

## `hyperion-pipeline` research commands

Install the Python package (`pip install -e python` from `hyperion/`) so the console script is available.

| Command | Purpose |
| --- | --- |
| `paper-replay-promoted` | FIFO + slippage aggregates for promoted wallets (loads OLTP `fills`). |
| `compute-copyability-advisory` | Writes `trader_copyability_advisory` rows (advisory only). |
| `walk-forward-replay --parquet PATH` | 67/33 calendar split metrics for offline Parquet extracts. |
| `backfill-wallet --min-round-trips N` | Logs FIFO `realized_only` segment counts after REST backfill. |

Environment mirrors:

- `HYPERION_PAPER_REPLAY_SLIPPAGE_BPS`
- `HYPERION_COPYABILITY_HORIZON_DAYS`
- `HYPERION_BACKFILL_MIN_ROUND_TRIPS`
- `HYPERION_WALK_FORWARD_TRAIN_FRAC`

## API surfacing

`/scores` merges `copyability_advisory` (latest snapshot per trader) beside each pillar score blob. `/traders/{wallet}` exposes the sibling field on the trader detail envelope. Consumers must treat it strictly as advisory telemetry—the trader-engine weight vector is untouched.

### New dependency alert

`pandas>=2.2.2` is now a first-class dependency for walk-forward tooling. Freeze it in tighter environments if required.
