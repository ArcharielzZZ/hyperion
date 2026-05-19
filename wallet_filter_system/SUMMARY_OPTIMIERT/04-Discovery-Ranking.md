# Optimiertes Discovery Ranking

> Ziel: Discovery gilt ausschliesslich fuer PROMOTED Wallets und beantwortet, wie gut ihre Datenabdeckung ist und ob sie noch aktiv beobachtet werden sollen.

---

## Aufgabe des Discovery Scores

Der Discovery Score beantwortet fuer **PROMOTED** Wallets:

```
Wie gut sind unsere eigenen Daten – und ist die Wallet noch aktiv genug, um gescort zu werden?
```

**Wichtig:** PENDING-Wallets bekommen keinen Discovery Score. Sie werden passiv durch die Ingestion-Pipeline re-evaluiert, sobald neue Trades reinkommen. Kein aktives Polling, kein extra Aufwand.

| Score | Frage |
|---|---|
| Behavior Score | Wie gut handelt der Trader? |
| Discovery Score | Wie vollstaendig sind unsere Daten – und ist der Trader noch aktiv? |

---

## Formel (nur fuer PROMOTED Wallets)

```
Discovery Score =
    50 % Data Coverage Score
  + 30 % Activity Score
  + 20 % History / Stability Score
```

### Cold-Start-Regel

Frisch promotete Wallets haben noch keine Score-Historie. Deshalb darf `History / Stability` sie nicht automatisch benachteiligen.

```
Wenn scoring_runs < 5:
    Discovery Score = 65 % Data Coverage + 35 % Activity

Wenn scoring_runs >= 5:
    Discovery Score = 50 % Data Coverage + 30 % Activity + 20 % History / Stability
```

Damit ist History ein Bonus fuer bewiesene Stabilitaet, aber kein Strafblock fuer neue `PROMOTED` Wallets.

---

## Data Coverage Score

Misst, wie vollstaendig unsere eigenen Daten fuer diese Wallet sind.

| Metrik | Gewicht | Erklaerung |
|---|---|---|
| data_completeness_30d | 40 % | Anteil empfangener vs. erwarteter Datenpunkte |
| pnl_snapshots_30d | 30 % | Genug Snapshots fuer saubere Equity-Kurve? |
| fills_30d vorhanden | 15 % | Haben wir eigene Fills? |
| latest_behavior_score vorhanden | 15 % | Ist ein Score bereits berechnet? |

---

## Activity Score

Misst, ob die Wallet noch aktiv am Markt ist.

| Metrik | Gewicht | Erklaerung |
|---|---|---|
| age_of_last_trade | 40 % | Wie lang ist der letzte Trade her? |
| trade_count_24h | 35 % | Handelt die Wallet aktuell? |
| activity_consistency_7d | 25 % | War sie regelmaessig aktiv? |

---

## History / Stability Score

| Metrik | Gewicht | Erklaerung |
|---|---|---|
| days_seen_30d | 35 % | Wie oft wurde sie gesehen? |
| previous_good_score | 35 % | War sie frueher gut? |
| score_stability | 30 % | Ist der Score stabil oder schwankend? |

---

## Discovery ist kein Qualitaets-Tier

Discovery entscheidet nicht, ob ein Trader gut handelt. Das macht der Behavior Score.
Discovery entscheidet, ob unsere Daten vollstaendig genug sind und ob die Wallet aktiv beobachtet werden soll.

| Discovery Ergebnis | Wirkung |
|---|---|
| Data Coverage hoch, Wallet aktiv | high_quality oder active_tracked (je nach Behavior Score) |
| Data Coverage niedrig, Wallet aktiv | watchlist (Daten werden weiter gesammelt) |
| Wallet inaktiv (last_seen > 30 Tage) | Status `ARCHIVED`, letzter Tier bleibt historisch erhalten |

---

*Zurueck zur [Uebersicht](00-UEBERSICHT.md)*
