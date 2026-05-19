# Hyperliquid Ingestion

The ingest service subscribes to tracked user and coin streams from Hyperliquid and persists
normalized records into PostgreSQL. The hardened design originally used typed contracts,
deduplication keys, replay fixtures, and metrics for malformed payloads and reconnect behavior.
