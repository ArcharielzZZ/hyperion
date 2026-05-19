# Optimierte Metriken-Referenz

> Nachschlagewerk fuer bestehende und neue Ziel-Metriken im optimierten System.

---

## Legende

| Symbol | Bedeutung |
|---|---|
| hoch = gut | Score steigt mit hoeherem Wert |
| niedrig = gut | Score steigt mit niedrigerem Wert |
| bps | Basispunkte, 1 bps = 0,01 % |
| 30d | Letzte 30 Tage |
| 90d | Rohdatenfenster / Langfrist-Kontext |

---

## A - Eligibility und Datenqualitaet

| Metrik | Zielwert | Erklaerung |
|---|---|---|
| active_days_30d | >= 2 | Mehr als nur ein Zufallstag |
| **min_trades** | >= 5 | Mindestanzahl Trades für Promotion |
| **min_volume** | >= 100 $ | Mindestvolumen für Promotion (Dust-Filter) |
| **min_pnl** | > 0 | Muss in mind. einem Fenster (7d, 1m, 3m) profitabel sein |
| **pnl_efficiency** | >= 0,1 % | PnL muss mindestens 0,1 % des gehandelten Volumens erreichen |
| **last_seen** | <= 90d | Trader ist noch relevant / aktiv |
| pnl_snapshots_30d | >= 24 | Mindestens ca. ein voller Tag stuendlicher Snapshots |
| **data_completeness_30d** | >= 70 % | Verhaeltnis von empfangenen zu erwarteten Daten |
| **avg_pillar_confidence** | >= 0.50 | Durchschnittliches Vertrauen ueber alle 5 Pillars |
| **score_raw** | 0-100 | Angezeigter absoluter Rohscore, nicht durch Confidence reduziert |
| **effective_confidence** | 0-1 | Avg. Confidence * Datenqualitaetsfaktor; steuert Tier-Freigabe |

---

## B - Multi-Window Performance (Neu)

Diese Metriken werden fuer jede Wallet in verschiedenen Zeitfenstern berechnet, um die Bestaendigkeit zu messen.

| Metrik | Zeitfenster | Erklaerung |
|---|---|---|
| **PnL (7d, 1m, 3m, 6m)** | Rollierend | Summe des realisierten Gewinns/Verlusts |
| **PnL Efficiency (7d, 1m, 3m)** | Rollierend | PnL im Verhaeltnis zum Handelsvolumen (`PnL / volume`) |
| **ROI (7d, 1m, 3m, 6m)** | Rollierend | Prozentuale Rendite bezogen auf das eingesetzte Kapital |
| **Trades (7d, 1m, 3m, 6m)** | Rollierend | Anzahl der ausgefuehrten Fills |
| **PnL All-Time** | Gesamt | Gesamter Gewinn/Verlust seit Erfassung |
| **Trades All-Time** | Gesamt | Gesamte Anzahl Trades seit Erfassung |

---

## C - Consistency (Beständigkeit)

| Metrik | Richtung | Erklaerung |
|---|---|---|
| Sharpe-like Ratio | hoch = gut | Rendite pro Risikoeinheit; Ziel: >= 2.0 = 100 Pkt, gecappt bei 3.0 oben |
| Return Volatility | niedrig = gut | Schwankung der Rendite (bps); Ziel: <= 50 = 100 Pkt, >= 500 = 0 Pkt |
| **Downside Volatility** | niedrig = gut | **Fokus:** Schwankung nur im negativen Bereich |
| Hit Rate | hoch = gut | Anteil profitabler Tage; Ziel: >= 0.65 = 100 Pkt, <= 0.35 = 0 Pkt |

---

## D - Survivability (Überlebensfähigkeit)

| Metrik | Richtung | Erklaerung |
|---|---|---|
| Max Drawdown | niedrig = gut | Groesster Rueckgang vom Hoch; Ziel: 0 % = 100 Pkt, >= 30 % = 0 Pkt |
| **Recovery Factor** | hoch = gut | Rendite / Max Drawdown (gecappt bei 10.0); Ziel: >= 5.0 = 100 Pkt, < 1.0 = 0 Pkt |
| **drawdown_days_30d** | niedrig = gut | Anzahl aktiver Tage, an denen Equity < rollendes Tages-Peak |
| **drawdown_duration_ratio** | niedrig = gut | `drawdown_days_30d / active_days_30d` (0.0 = nie unter Wasser, 1.0 = immer) |
| **drawdown_duration_score** | hoch = gut | `1.0 - drawdown_duration_ratio`; bereits normiert (0–1), direkt * 100; Tag-1-Regel: erster Snapshot = Peak |
| Active Days | hoch = gut | Regelmaessige Praesenz am Markt |

---

## E - Timing (Präzision)

| Metrik | Richtung | Erklaerung |
|---|---|---|
| Favorable Entry Rate | hoch = gut | Anteil der Einstiege besser als der Durchschnitt |
| Mean Entry Edge | hoch = gut | Durchschnittlicher Preisvorteil in bps |
| **Median Entry Edge** | hoch = gut | Robuster Preisvorteil (Schutz gegen Ausreisser) |

---

## F - Leverage Discipline

| Metrik | Richtung | Erklaerung |
|---|---|---|
| Max Leverage | niedrig = gut | Hoechster Hebel; Ziel: <= 3x = 100 Pkt, >= 50x = 0 Pkt |
| Avg Leverage | niedrig = gut | Durchschnittlicher Hebel; Ziel: <= 2x = 100 Pkt, >= 25x = 0 Pkt |
| Leverage Volatility (Std) | niedrig = gut | Schwankung im Hebel; Ziel: <= 1x = 100 Pkt, >= 15x = 0 Pkt |
| Sizing CV | niedrig = gut | Schwankung der Positionsgroessen; Ziel: <= 0.3 = 100 Pkt, >= 1.5 = 0 Pkt |

> **Liquidation-Proximity Risk:** Bewusst gestrichen (Roadmap V10). API-Daten nicht zuverlaessig genug. Gewicht proportional umverteilt (39/33/17/11 %).

---

## G - Conviction

| Metrik | Richtung | Erklaerung |
|---|---|---|
| Expectancy (Mean daily return, bps) | hoch = gut | Erwartungswert pro Tag; Ziel: >= 50 bps = 100 Pkt, = 0 bps = 0 Pkt |
| Profit Factor | hoch = gut | Gewinne / Verluste; Ziel: >= 2.5 = 100 Pkt, = 1.0 = 0 Pkt (kein Edge) |
| Positive Return Days | hoch = gut | Wie oft Tage positiv sind |
| Fills Count | hoch = gut | Aktivitaet / statistische Relevanz |
| Unique Coins 30d | moderat hoch = gut | Diversifikation; Ziel: >= 5 Coins = 100 Pkt, = 1 Coin = 0 Pkt |

---

## H - Discovery (nur fuer PROMOTED Wallets)

PENDING-Wallets bekommen keinen Discovery Score. Discovery gilt ausschliesslich fuer PROMOTED Wallets und misst Datenabdeckung und Aktivitaet.

| Metrik | Richtung | Erklaerung |
|---|---|---|
| data_completeness_30d | hoch = gut | Anteil empfangener vs. erwarteter Datenpunkte |
| pnl_snapshots_30d | hoch = gut | Genug Snapshots fuer Equity-Kurve |
| scoring_runs | hoch = gut | Anzahl vorhandener Scoring-Laeufe; steuert Cold-Start-Regel |
| age_of_last_trade | niedrig = gut | Frische Aktivitaet |
| trade_count_24h | hoch = gut | Aktuell am Markt aktiv? |
| activity_consistency_7d | hoch = gut | Regelmaessig aktiv, kein einmaliger Spike |
| days_seen_30d | hoch = gut | Wie oft in den letzten 30 Tagen gesehen |
| previous_good_score | hoch = gut | Historisch guter Score vorhanden |
| score_stability | hoch = gut | Score schwankt nicht stark |

---

## I - Score-Historie und Alerts

| Metrik | Erklaerung |
|---|---|
| score_delta_1_run | Veraenderung seit letztem Scoring |
| score_delta_7d | Veraenderung ueber 7 Tage |
| score_volatility | Schwankt der Score stark? |
| max_score_drop | Groesster beobachteter Abfall |
| alert_flag | Markiert kritische Veraenderungen |

---

## J - Status-Verwaltung

Wallet-Adressen werden niemals geloescht. Stattdessen wird der Status angepasst.

| Metrik / Status | Erklaerung |
|---|---|
| `PENDING` | Noch nicht profitabel – wartet passiv auf neue Trades |
| `PROMOTED` | Profitabel bewiesen – aktiv beobachtet und gescort |
| `ARCHIVED` | War promoted, ist aktuell inaktiv – letzter Score/Tier bleibt erhalten |
| `BANNED` | Dauerhaft blockiert (z. B. HF-Bot) – keine Verarbeitung |
| no_activity_days | Tage ohne Aktivitaet (fuer `ARCHIVED` Einstufung) |
| has_score_history | Verhindert, dass eine Adresse zu schnell archiviert wird |

---

*Zurueck zur [Uebersicht](00-UEBERSICHT.md)*
