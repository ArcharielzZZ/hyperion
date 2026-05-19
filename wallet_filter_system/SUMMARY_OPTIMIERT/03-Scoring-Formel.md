# Optimierte Scoring-Formel

> Ziel: Der Score soll besser gegen wenig Daten, Datenluecken, Ausreisser und schlechte absolute Performance geschuetzt sein.

---

## Schritt 1 - Rohdaten laden

Wie bisher werden Rohdaten geladen:
- Fills
- Positions
- PnL-Snapshots
- Entry-Timing Samples
- oeffentliche Aktivitaetsdaten

Optimiert:
- Rohdaten bleiben unveraendert gespeichert.
- Jede Datenquelle bekommt einen Datenqualitaetsstatus.
- Fehlende Daten werden nicht still ignoriert, sondern als Confidence-Abschlag beruecksichtigt.

---

## Schritt 2 - Datenqualitaet pruefen

Vor dem Scoring wird berechnet:

```
data_completeness_score = vorhandene_daten / erwartete_daten
```

Beispiele:

| Datenlage | Bedeutung |
|---|---|
| 90-100 % | Sehr gute Datenbasis |
| 70-89 % | Nutzbar |
| 50-69 % | Vorsicht, Score abschwaechen |
| < 50 % | Nicht fuer hohe Bewertung nutzen |

---

## Schritt 3 - Metriken berechnen

Aus den Rohdaten werden wie bisher 30d-Metriken berechnet.

Optimiert:
- zusaetzlich Median-Werte gegen Ausreisser
- zusaetzlich Drawdown-Dauer
- zusaetzlich Coin-Diversifikation
- zusaetzlich Score-Historie, wenn vorhanden

---

## Schritt 4 - Absolutes Scoring (Kein Percentile mehr)

Das System nutzt **kein relatives Ranking** mehr. Ein Score von 80 bedeutet nicht "besser als 80 % der anderen", sondern "80 von 100 moeglichen Leistungspunkten erreicht".

Jeder Pillar berechnet seinen Score absolut basierend auf festen Zielwerten.
Beispiel Timing:
- Median Entry Edge > 10 bps = 100 Punkte
- Median Entry Edge = 0 bps = 0 Punkte

**Vorteil:** Wenn der Markt crasht und alle Trader schlecht sind, bekommen alle einen schlechten Score. Der "am wenigsten schlechte" Trader wird nicht mehr kuenstlich aufgewertet.

---

## Schritt 5 - Score und Confidence getrennt halten

Jeder der 5 Pillars berechnet seine eigene Confidence basierend auf den vorhandenen Samples (z. B. Anzahl Trades fuer Timing).

```
pillar_confidence = sqrt(samples / target)
```

Fuer die gesamte Wallet wird daraus die **Durchschnitts-Confidence** gebildet:

```
avg_confidence = (C_Timing + C_Leverage + C_Survivability + C_Consistency + C_Conviction) / 5
```

Der Score wird **nicht mehr durch Confidence in Richtung 0 gezogen**. Stattdessen werden Rohleistung und Verlaesslichkeit getrennt gespeichert:

```
effective_confidence = avg_confidence * data_completeness_factor
score_raw = gewichteter absoluter Pillar-Score (0-100)
score_display = score_raw
```

**Bedeutung:** Ein neuer `PROMOTED` Trader mit starkem Rohscore wird nicht kuenstlich auf 20-30 Punkte gedrueckt, nur weil noch wenige Snapshots vorliegen. Stattdessen sieht man z. B. `Score 82, Confidence 0.35`. Die niedrige Confidence verhindert Top-Tiers, aber zerstoert nicht die sichtbare Leistung.

Tier-Vergabe nutzt deshalb:
- `score_raw` fuer die Leistungsqualitaet
- `effective_confidence` als Mindesthuerde fuer hohe Tiers
- `data_completeness_factor` als eigene Datenqualitaets-Huerde

---

## Schritt 6 - Absolute Performance-Grenze (The "Anchor")

Da das System nun absolut wertet, sind harte Deckel weniger wichtig, aber als Sicherheitsnetz bleiben sie bestehen, um fundamentale Fehler abzufangen:

| Bedingung | Effekt | Warum? |
|---|---|---|
| **30d Total Return < 0** | Max. Score: **0** | Wer Geld verliert, bekommt keine Punkte. |
| **Profit Factor < 1.0** | Max. Score: **0** | Verluste > Gewinne = kein Edge. |
| **Max Drawdown > 30%** | Max. Score: **40** | Zu hohes Risiko fuer Top-Einstufung. |

**Hinweis:** Technische Qualitaetsgrenzen (wie Data Completeness oder Min-Confidence) veraendern nicht den angezeigten Rohscore. Sie steuern, ob ein Trader in ein hohes Tier darf.

**Logik:** `score_raw = min(absoluter_pillar_score, alle_zutreffenden_performance_caps)`

---

## Schritt 7 - Optimierte Gesamtformel

```
Behavior Score = (
    0.25 * Consistency
  + 0.25 * Survivability
  + 0.20 * Conviction
  + 0.15 * Leverage Discipline
  + 0.15 * Timing
)
```

Danach erfolgt die Anwendung der **absoluten Performance-Caps**. Confidence und Datenqualitaet bleiben getrennte Felder und wirken bei der Tier-Vergabe.

---

## Schritt 8 - Score-Historie & Trend-Analyse

Jeder Scoring-Run wird in der Tabelle `trader_score_history` gespeichert. Dies ermoeglicht:

1.  **Trend-Erkennung:** Steigt oder faellt der Score?
2.  **Stabilitaets-Check:** Schwankt der Score wild (Zufall) oder ist er konstant (System)?
3.  **Verschlechterungs-Alerts:** Sofortige Warnung bei signifikanten Einbruechen.

---

## Schritt 9 - Alert bei Verschlechterung

Beispiel-Regel:

```
Wenn tracked Trader innerhalb eines Runs um > 15 Punkte faellt:
    Alert ausloesen
```

Weitere sinnvolle Alerts:
- Behavior Score faellt unter 50
- Drawdown steigt stark
- Data Completeness faellt unter 70 %
- Trader war gut, aber ist 7 Tage inaktiv

---

*Zurueck zur [Uebersicht](00-UEBERSICHT.md)*
