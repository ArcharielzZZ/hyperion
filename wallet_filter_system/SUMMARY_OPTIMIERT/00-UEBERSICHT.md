# Hyperion Optimiert - Gesamtuebersicht

> Dieses Dokument beschreibt eine verbesserte Ziel-Version des Wallet Filter Systems. Es ist kein Code und ersetzt nicht den originalen `SUMMARY`-Ordner.

---

## Was ist der Unterschied zur originalen Summary?

Die originale `SUMMARY/` beschreibt den aktuellen Projektstand.

`SUMMARY_OPTIMIERT/` beschreibt eine grobe verbesserte Logik:
- Rohdaten bleiben sauber in der Datenbank.
- Unbrauchbare Wallets werden nicht sofort bewertet, sondern zuerst gefiltert.
- Scores bekommen mehr Schutz gegen wenig Daten, schlechte Datenqualitaet und kurzfristige Ausreisser.
- Gute Trader werden nicht nur einmal bewertet, sondern ueber Zeit beobachtet.
- Inaktive oder nutzlose Wallets koennen spaeter automatisch archiviert werden (Adressen bleiben erhalten).

---

## Optimierter Datenfluss

```
Hyperliquid (WebSocket live + REST backfill)
        |
        v
   Rohdaten-Speicherung in PostgreSQL
        |
        v
   Stufe 0: Ingestion Routing (Banned ignorieren, Promoted durchlassen)
        |
        v
   Tägliche Aggregation (Daily Summary)
        |
        v
   Multi-Window Performance (7d, 1m, 3m, 6m)
        |
        v
   Datenqualitaets-Check
        |
        v
   Pre-Filter / Cleanup-Kandidaten
        |
        v
   Eligibility Gates
        |
        v
   Verhaltensprofil 30d + Datenabdeckung 90d
        |
        v
   Optimierte Pillar-Scores
        |
        v
   Behavior Score + Confidence + Score-Historie
        |
        v
   Discovery Score + Tier-Einstufung
        |
        v
   Alerts / Beobachtung / Backfill-Prioritaet
```

---

## Schnellnavigation

| Dokument | Inhalt |
|---|---|
| [01-Eligibility-Gates.md](01-Eligibility-Gates.md) | Verbesserte Mindestbedingungen fuer Bewertung |
| [02-Behavior-Pillars.md](02-Behavior-Pillars.md) | Optimierte Bewertungssaeulen |
| [03-Scoring-Formel.md](03-Scoring-Formel.md) | Ziel-Formel mit Confidence, Datenqualitaet und History |
| [04-Discovery-Ranking.md](04-Discovery-Ranking.md) | Besseres Ranking fuer Sichtbarkeit und Datenabdeckung |
| [05-Tiers-und-Promotion.md](05-Tiers-und-Promotion.md) | Klarere Tier-Namen und sauberere Einstufung |
| [06-Metriken-Referenz.md](06-Metriken-Referenz.md) | Neue und bestehende Metriken im Ueberblick |
| [07-Verbesserungsvorschlaege.md](07-Verbesserungsvorschlaege.md) | Roadmap: Was zuerst umgesetzt werden sollte |

---

## Wichtigste Verbesserungen auf einen Blick

| Bereich | Aktuell | Optimiert |
|---|---|---|
| Rohdaten | 90 Tage gespeichert | 90 Tage Rohdaten plus Datenqualitaets-Status |
| Wallets ohne Aktivitaet | Fallen durch Gates | Werden als Cleanup-Kandidaten markiert |
| Score | Relativ zur Kohorte | Absoluter Rohscore plus separate Confidence |
| Wenig Daten | Shrinkage Richtung 50 | Score bleibt sichtbar, Confidence begrenzt Top-Tiers |
| Recovery Factor | Risiko bei Drawdown = 0 | Sauber gecappt oder separat behandelt |
| Score-Historie | Nicht vorhanden | Jeder Scoring-Run wird historisiert |
| Alerts | Nicht vorhanden | Warnung bei starkem Score-Abfall |
| Backfill | Nicht klar messbar | Vollstaendigkeit wird gemessen |

---

## Optimierte Grundidee

Das System soll nicht nur fragen:

> "Wer sieht heute gut aus?"

Sondern:

> "Wer handelt gut, hat genug Daten, bleibt stabil ueber Zeit (7d bis 6m) und wird nicht durch Datenluecken oder Zufall falsch bewertet?"

---

## Speicher-Strategie (Skalierbarkeit)

Um 2 Millionen Wallets effizient zu verwalten, nutzen wir drei Ebenen:

1.  **All-Time Gedächtnis:** Minimale Daten (PnL, Trades) für jede jemals gesehene Wallet.
2.  **Daily Summary (7d - 6m):** Tägliche Zusammenfassungen für schnelle Fenster-Berechnungen.
3.  **Rohdaten (30d):** Volle Details (Fills, Snapshots) nur für aktive/promoted Wallets.

---

*Zuletzt aktualisiert: 2026-05-15*
