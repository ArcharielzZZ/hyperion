# Hyperion Analytics – Fix-Handoff (2026-05-25)

Dieses Dokument ist fuer andere KIs/Entwickler gedacht: Was wurde in der Analytics-Review-Umsetzung geaendert, was bewusst offen blieb, und warum.

## Kontext

Kritische Review des Hyperion-Analytics-Stacks (Ingest/Pull/Dashboard). Scope der Umsetzung: **kritisch + hoch + mittel** gemaess Plan — inkl. Monolith-Split, Caching, Schema-Contract, UI-Warnungen.

---

## Umgesetzt

### Kritisch

| Datei | Aenderung | Warum |
|-------|-----------|-------|
| `analytics/lib/parquet_io.py` | **Neu:** `atomic_write_parquet()` (.tmp + replace) | Keine korrupten Parquet-Dateien bei Abbruch mitten im Schreiben |
| `analytics/scripts/force_pull_wallet_bundle.py` | Alle `write_parquet` → `atomic_write_parquet` | Bundle-Artefakte atomar |
| `analytics/scripts/force_pull_wallet.py` | Legacy `--legacy-only` ebenfalls atomar | Konsistenz |
| `analytics/lib/time_utils.py` | **Neu:** `epoch_ms_min/max`, `coin_time_bounds_from_fills`, `fills_time_bounds_ms` via `.dt.epoch("ms")` | Naive Polars-Datetimes + `.timestamp()` lieferten auf Windows/CET −1h Offset |
| `analytics/dashboard/wallet_pulls.py` | `coin_time_bounds()` nutzt `time_utils` | Dashboard-Zeitfenster korrekt |
| `analytics/lib/hl_fetch.py` | `coin_time_bounds_ms()` delegiert an `time_utils` | Pull-Zeitfenster korrekt |
| `analytics/lib/hl_fetch.py` | `orders_to_dataframe()`: dedupe nach `with_columns`, nicht unreachable | `dedupe_order_snapshots` lief vorher nie — doppelte open+terminal Rows in `orders.parquet` |

### Hoch

| Datei | Aenderung | Warum |
|-------|-----------|-------|
| `analytics/lib/hl_fetch.py` | `_post()` mit 3 Retries, Exponential Backoff (1s/2s/4s), Retry bei 429/5xx | Einzelner HTTP-Fehler beendete ganzen Pull |
| `analytics/dashboard/callbacks_wallet.py` | `format_wallet_status_message()` zeigt Orders-Hinweis bei `orders_note` oder `order_count >= 1900` | ~2000-Orders-API-Limit war nur in `meta.json`, nicht in UI |

### Mittel

| Datei | Aenderung | Warum |
|-------|-----------|-------|
| `analytics/lib/schemas.py` | **Neu:** Shared Polars-Schema-Dicts (`FILLS_SCHEMA`, `ORDERS_SCHEMA`, …) | Informeller Schema-Contract zwischen Pull und Dashboard |
| `analytics/lib/hl_fetch.py`, `wallet_pulls.py` | Schemas importiert, `pl.Utf8` → `pl.String` | Einheitlicher Contract |
| `analytics/dashboard/wallet_pulls.py` | `@lru_cache` auf `load_fills/orders/user_funding/ledger`; `clear_wallet_cache()` | Mehrfaches Parquet-Lesen pro Callback |
| `analytics/dashboard/callbacks_wallet.py` | `clear_wallet_cache()` nach erfolgreichem Force-Pull (`state=done`) | Frische Daten nach Dashboard-Pull |
| `analytics/dashboard/wallet_pulls.py` | `wallet_summary()` entfernt → `bundle_stats()` (nur Daten) | Layering: UI-Text in Callback-Schicht |
| `analytics/dashboard/wallet_chart.py` | Chart-Logik extrahiert; `classify_limit_order` mit Hyperliquid-Side-Kommentar + `logging.warning` bei unbekannten Kombinationen | Wartbarkeit + sichtbare Edge-Cases |
| `analytics/dashboard/wallet_layout.py` | Layout + Konstanten extrahiert | Monolith-Split |
| `analytics/dashboard/callbacks_wallet.py` | Wallet-Callbacks (`register_wallet_callbacks`) | Monolith-Split |
| `analytics/dashboard/callbacks_twitter.py` | Twitter-Callbacks (`register_twitter_callbacks`) | Monolith-Split |
| `analytics/dashboard/wallet_explorer.py` | Schlanker Entry (~45 Zeilen): App, Layout, Callback-Registrierung | Monolith-Split |

### Runde 3 (Follow-up)

| Datei | Aenderung | Warum |
|-------|-----------|-------|
| `analytics/tests/` | **Neu:** 13 pytest-Tests (`time_utils`, `parquet_io`, `orders`/dedupe, `classify_limit_order`, `schema_validate`) | Absicherung bei Refactors |
| `analytics/pytest.ini` | Pytest-Konfiguration | `pytest analytics/tests` aus Repo-Root |
| `analytics/lib/schema_validate.py` | **Neu:** `assert_dataframe_columns()` | Schema-Contract beim Pull enforced (Spalten) |
| `analytics/scripts/force_pull_wallet_bundle.py` | Schema-Check vor jedem Parquet-Write | Fehler beim Pull statt still im Dashboard |
| `analytics/scripts/force_pull_twitter.py` | `atomic_write_parquet` statt direktem `write_parquet` | Gleiches Write-Verhalten wie Wallet-Bundle |

**Circular Imports:** geprueft — Callbacks importieren `app` nicht; `register_*_callbacks(app)` Pattern; Smoke-Import `wallet_explorer` ok.

**Cache:** `clear_wallet_cache()` ruft `cache_clear()` auf allen gewrappten Loader-Funktionen auf (gesamter Cache, nicht pro Wallet).

---

## Nicht umgesetzt (bewusst)

| Punkt | Grund |
|-------|-------|
| **write_status Race-Condition / File-Lock** | Sehr unwahrscheinlich (typisch ein Pull pro Wallet); Lock-Infrastruktur Overkill |
| **Coin-Sortierung nach Volumen statt Fill-Count** | Nur Pull-Reihenfolge/Performance; keine Datenluecke pro Coin |
| **Background-Job fuer Live-Funding im Chart-Callback** | Research-Tool; akzeptable Latenz; grosser Dash-Umbau |
| **Data-Lake-Versionierung / Append-Modus** | Konzeptuelle Architektur-Aenderung, separates Epic |
| **Rust/PostgreSQL-Pipeline mit Dashboard verbinden** | Zwei bewusst getrennte Pipelines (Live-Ingest vs. Offline-Research) |
| **Pydantic-Modelle statt Polars-Schema-Dict** | Mehr Boilerplate ohne Mehrwert bei Single-Writer-Pipeline |

(Entfernt aus „nicht umgesetzt“: Unit-Tests und Twitter-atomares Parquet — siehe Runde 3 oben.)

---

## Verifikation (manuell)

- [x] `epoch_ms_min` auf Parquet-Roundtrip → `1704067200000` (nicht CET-offset)
- [x] `orders_to_dataframe` dedupliziert open+filled auf eine Zeile pro `oid`
- [x] `pytest analytics/tests` — 13 Tests gruen
- [ ] `force_pull_wallet_bundle.py` End-to-End gegen Live-API (optional, Netzwerk)
- [ ] Dashboard: Wallet laden → Orders-Warnung bei Bundle mit `orders_note`
- [ ] Pull abbrechen (Ctrl+C) → alte Parquet-Datei intakt, hoechstens `.tmp` liegen bleiben

---

## Modul-Struktur nach Refactor

```
analytics/
  lib/
    parquet_io.py      # atomares Schreiben
    time_utils.py      # UTC epoch ms
    schemas.py         # Polars-Schema-Contract
    hl_fetch.py        # API + DataFrame-Konverter
  dashboard/
    wallet_explorer.py # Entry (Dash app)
    wallet_layout.py   # UI-Layout + Farben
    wallet_chart.py    # Plotly-Chart-Logik
    callbacks_wallet.py
    callbacks_twitter.py
    wallet_pulls.py    # Parquet-Lesen + Cache
  scripts/
    force_pull_wallet_bundle.py
```

Start unveraendert: `python analytics/dashboard/wallet_explorer.py`

---

## Offene Follow-ups

1. Nach Code-Aenderung: Bundle **neu pullen** (alte `orders.parquet` ohne Dedupe im Pull-Pfad).
2. ~~Cache: Nach manuellem CLI-Pull ohne Dashboard-Button ggf. Dashboard neu starten oder «Wallet Force Pull» nutzen.~~ Behoben: `clear_wallet_cache()` bei «Wallet laden»; Status zeigt `Bundle-Stand` + Hinweis.
3. ~~Optional: atomares Parquet auch in `force_pull_twitter.py`.~~ Erledigt (Runde 3).
4. ~~Optional: pytest fuer `time_utils` und `dedupe_order_snapshots`.~~ Erledigt (`analytics/tests/`).
5. Schema-Validierung prueft Spalten, noch keine strict dtype-Checks.

## Naechste Session (2026-05-26): Dashboard-Strategie

**Offen:** Research-UI (Parquet) vs. Postgres-Scores aus Rust/API einbinden?

- Option A: Dashboard bleibt offline Research auf `data_lake`
- Option B: Hybrid — Charts aus Parquet + Scores/Tiers von API `:8080`
- Option C: getrennte UIs

Vor Entscheidung: laufen `ingest` + `trader-engine`? Siehe `AI-Projekt-Bibliothek/Projekt-Logs/2026-05-26-Arbeitslog.md`
