SHELL := powershell.exe
.SHELLFLAGS := -NoProfile -Command

.PHONY: dev-up dev-down migrate check fmt lint run-api run-ingest run-trader run-signal run-execution week1-discovery-snapshot week1-scanner-snapshot week1-nightly-export

dev-up:
	powershell -ExecutionPolicy Bypass -File infra/scripts/dev-up.ps1

dev-down:
	powershell -ExecutionPolicy Bypass -File infra/scripts/dev-down.ps1

migrate:
	powershell -ExecutionPolicy Bypass -File infra/scripts/migrate.ps1

check:
	cargo check

fmt:
	cargo fmt --all

lint:
	cargo clippy --workspace --all-targets -- -D warnings

run-api:
	cargo run -p hyperion-api

run-ingest:
	cargo run -p hyperion-ingest

run-trader:
	cargo run -p hyperion-trader-engine

run-signal:
	cargo run -p hyperion-signal-engine

run-execution:
	cargo run -p hyperion-execution-engine

week1-discovery-snapshot:
	powershell -ExecutionPolicy Bypass -File infra/scripts/discovery-universe-daily.ps1

week1-scanner-snapshot:
	powershell -ExecutionPolicy Bypass -File infra/scripts/scanner-coverage-snapshot.ps1

week1-nightly-export:
	powershell -ExecutionPolicy Bypass -File infra/scripts/week1-nightly-export.ps1

discover-whales:
	python infra/scripts/discover-hyperliquid-whales.py --leaderboard-cache analytics/research/hyperliquid_leaderboard_cache.json --elite --demote-existing-whales --min-all-time-vlm 25000000 --min-hit-rate 55 --enrich-top-n 400 --max-promote 150 --apply-db

restart-scoop:
	powershell -ExecutionPolicy Bypass -File infra/scripts/restart-wallet-filter-scoop.ps1

restart-live:
	powershell -ExecutionPolicy Bypass -File infra/scripts/restart-live-scoop.ps1

copy-paper-backtest:
	cd python && hyperion-pipeline copy-paper-backtest --wallet 0x023a3d058020fb76cca98f01b3c48c8938a22355 --wallet 0x3895d155b005191686476a39580d43258a518e46

screen-tracked-copy:
	cd python && hyperion-pipeline screen-tracked-copy
