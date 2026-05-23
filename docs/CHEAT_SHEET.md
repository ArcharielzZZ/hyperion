# Hyperion Command Cheat Sheet

Eine schnelle Übersicht aller wichtigen Befehle für deine tägliche Arbeit mit der Hyperion-Plattform.

## 1. Infrastruktur & Setup (Docker & Datenbank)
Diese Befehle nutzt du, um die Basis-Systeme (PostgreSQL, Redis) zu starten und die Datenbankstruktur aktuell zu halten.

| Befehl | Beschreibung |
| :--- | :--- |
| `make dev-up` | Startet die lokale Infrastruktur (PostgreSQL, Redis, pgAdmin) im Hintergrund via Docker. |
| `make dev-down` | Stoppt die lokale Infrastruktur. |
| `make migrate` | Wendet alle SQL-Migrationen (aus `infra/migrations/`) auf die Datenbank an. **Wichtig nach Änderungen am Datenbankschema!** |

## 2. Rust Backend Services (Live Data & API)
Diese Befehle starten die einzelnen Microservices. Im Normalfall lässt du diese in separaten Terminals laufen.

| Befehl | Beschreibung |
| :--- | :--- |
| `make run-ingest` | Startet den Ingest-Worker. Verbindet sich mit dem Hyperliquid Websocket und speichert Live-Trades in Postgres. |
| `make run-trader` | Startet die Trader-Engine. Bewertet Trader live basierend auf ihren Aktionen. |
| `make run-signal` | Startet die Signal-Engine. Generiert Trading-Signale aus dem Verhalten der Top-Trader. |
| `make run-execution` | Startet die Execution-Engine (Paper Trading). Führt Signale virtuell aus. |
| `make run-api` | Startet den Axum API-Server (Port 8080). Stellt Endpunkte für Leaderboards und Scores bereit. |

## 3. Python Analytics & Data Lake (Research)
Deine wichtigsten Befehle für das Quantitative Research und das Management deiner Parquet-Dateien.

| Befehl | Beschreibung |
| :--- | :--- |
| `python analytics/scripts/force_pull_wallet.py 0x...` | **Force Pull:** Lädt die *komplette* historische Trade-Historie eines Wallets von Hyperliquid herunter und speichert sie als komprimierte Parquet-Datei im Data Lake. |
| `python analytics/research/market_context/explore.py` | Startet das interaktive Plotly-Dashboard (aktuell mit Dummy-Daten), um AS OF Joins zwischen Trades und Funding Rates zu demonstrieren. |
| `python analytics/dashboard/wallet_explorer.py` | **Wallet Explorer UI:** Lokale Dash-App auf `http://127.0.0.1:8050`. Wallet eingeben → Coin auswählen → Intervall (`15m`–`1M`) durchschalten. Per-Kerze L/S-Marker für Open/Close Long/Short. Voraussetzung: Wallet vorher mit `force_pull_wallet.py` pullen. Siehe [`analytics/dashboard/README.md`](../analytics/dashboard/README.md). |

## 4. Workflow-Tipps für Cursor
Da du in Cursor arbeitest, hier die besten Prompts/Herangehensweisen für den Agenten:

*   **Neue Charts erstellen:** Nutze den Cursor Chat (Strg+L) und sage: *"Erstelle ein neues Research-Skript für Wallet 0x... Zeige mir PnL und Volumen."* (Der Agent nutzt automatisch die `.cursor/rules/research-charts.mdc` Regel für perfekten Polars-Code).
*   **Datenbank anpassen:** *"Erstelle eine neue SQL-Migration für Tabelle X."* -> Danach immer `make migrate` ausführen!

---
*Zuletzt aktualisiert: Mai 2026*
