# Optimierte Tiers und Promotion

> Ziel: Die Tier-Namen sollen klar ausdruecken, was mit einer Wallet passiert.

---

## Problem der aktuellen Tier-Logik

Aktuell kann ein nicht-promoted Trader mit hohem Score als `priority` besser wirken als ein promoted Trader mit niedrigerem Score als `tracked`.

Das ist verwirrend, weil:
- `tracked` nach hoechster Stufe klingt
- `priority` ebenfalls sehr wichtig klingt
- promoted eigentlich Datenabdeckung beschreibt, nicht automatisch Qualitaet

---

## Status und Tier sauber trennen

Ein **Status** beschreibt, was das System mit einer Wallet macht.
Ein **Tier** beschreibt, wie gut ein aktiv beobachteter Trader ist.

Diese Trennung ist wichtig: `ARCHIVED` ist kein Qualitaetsurteil, sondern ein Beobachtungsstatus.

---

## Optimierte Tier-Namen

| Neuer Tier | Bedeutung |
|---|---|
| **high_quality** | Guter Behavior Score und genug Confidence |
| **active_tracked** | Gute Daten, aktiv beobachtet |
| **watchlist** | Interessant, aber noch nicht stark genug |

---

## Optimierter Entscheidungsbaum

```
Welchen Status hat die Wallet?
|
|-- PENDING  -> kein Tier, kein Scoring, Pipeline reagiert passiv auf neue Trades
|-- BANNED   -> kein Tier, keine Verarbeitung, Adresse bleibt erhalten
|-- ARCHIVED -> kein aktives Scoring, letzter Score/Tier bleibt historisch erhalten
|
|-- PROMOTED:
    |
    |-- Behavior Score >= 75 und Confidence >= 0.70 -> high_quality
    |-- Behavior Score >= 40 und Confidence >= 0.50 -> active_tracked
    |-- Aktiv aber Score zu niedrig -> watchlist
```

---

## Promotion: Vom "Unbekannten" zum "Beobachteten"

Promotion ist kein Qualitaetssiegel, sondern ein **Datenstatus**.

*   **Status PENDING:** Wallet hat Stufe 2 noch nicht bestanden. Kein aktives Polling, kein Scoring. Das System wartet passiv auf neue Trades aus der Ingestion-Pipeline.
*   **Status PROMOTED:** Wallet hat Profitabilitaet bewiesen. Wir sammeln aktiv eigene Daten (WebSocket-Streams, detaillierte Snapshots) und berechnen den Behavior Score.
*   **Status ARCHIVED:** Wallet war `PROMOTED`, ist aber aktuell inaktiv. Sie wird nicht aktiv gescort, ihr letzter Score und Tier bleiben historisch erhalten.
*   **Status BANNED:** Wallet ist dauerhaft blockiert (z. B. HF-Bot). Keine Verarbeitung, aber die Adresse bleibt in der Datenbank.

**Promotion erfolgt ausschliesslich durch die Ingestion-Pipeline:**
1.  Neue Transaktion kommt rein.
2.  Stufe 0 erkennt Wallet als PENDING.
3.  Stufe 2 prueft erneut: profitabel + PnL-Effizienz >= 0,1% Volumen + Volumen >= 100$ + >= 5 Trades?
4.  Wenn ja → `PROMOTED`.

---

## Tier-Bedingungen (nur fuer PROMOTED Wallets)

| Tier | Bedingung (UND-verknuepft) | Fokus |
|---|---|---|
| **high_quality** | score_raw >= 75, Avg. Confidence >= 0.70, Data Comp. >= 85% | Die Elite |
| **active_tracked** | score_raw >= 40, Avg. Confidence >= 0.50 | Solide & Aktuell |
| **watchlist** | score_raw vorhanden, aber Score < 40 oder Confidence noch zu niedrig | Beobachtung |

---

## Was passiert mit jedem Tier?

| Tier | Aktion |
|---|---|
| high_quality | Stark beobachten, Score-Historie und Alerts aktiv |
| active_tracked | Normal beobachten und regelmaessig scoren |
| watchlist | Weiter beobachten, seltener scoren |

---

## Status-Aktionen

| Status | Aktion |
|---|---|
| PENDING | Passiv - wartet auf neue Trades in der Pipeline |
| PROMOTED | Aktiv Daten sammeln und scoren |
| ARCHIVED | Historie behalten, nicht aktiv scoren |
| BANNED | Gesperrt - keine Verarbeitung, Adresse bleibt erhalten |

---

*Zurueck zur [Uebersicht](00-UEBERSICHT.md)*
