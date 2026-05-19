# Optimierte Behavior Pillars - Die Bewertungssaeulen

> Ziel: Der Behavior Score soll echte Trader-Qualitaet messen, nicht nur relative Position im aktuellen Pool.

---

## Grundprinzip

Das System nutzt **kein relatives Percentile-Ranking** mehr. Der Behavior Score misst die absolute Leistung (0-100 Punkte). Jeder Trader startet bei 0 und muss sich Punkte durch Performance und Daten (Confidence) verdienen.

Optimiert wird sie durch:
- Absolute Zielwerte pro Pillar (statt Vergleich mit anderen Tradern)
- Durchschnitts-Confidence ueber alle Pillars
- Datenqualitaets-Abschlag (zieht Score Richtung 0)
- Score-Historie zur Stabilitaetspruefung

---

## Optimierte Gewichtung

| Pillar | Gewicht | Fokus |
|---|---|---|
| Consistency | 25 % | Gleichmaessige Renditen |
| Survivability | 25 % | Drawdowns und Verlustphasen |
| Conviction | 20 % | Profitabilitaet und echte Gewinnkraft |
| Leverage Discipline | 15 % | Kontrollierter Hebel |
| Timing | 15 % | Gute Einstiege |

**Hinweis zur Gewichtung:** Diese Gewichtung ist vorlaeufig. Consistency und Conviction koennen sich teilweise ueberschneiden (z. B. Sharpe-like Ratio vs. Expectancy). Bevor die Gewichte final festgelegt werden, sollte die Korrelation der Pillars mit echten Wallet-Daten gemessen werden.

---

## Pillar 1 - Consistency

Misst: Wie stabil sind die Renditen?

| Metrik | Gewicht | Optimierung |
|---|---|---|
| Sharpe-like Ratio | 35 % | Cap gegen extreme Ausreisser |
| Inverse Return Volatility | 20 % | Bleibt gleich |
| Inverse Downside Volatility | 25 % | Negative Schwankung staerker gewichten |
| Hit Rate | 20 % | Bleibt gleich |

**Absolute Zielwerte (Ausgangshypothese – nach erstem Run kalibrieren):**

| Metrik | Schwelle | Punkte | Hinweis |
|---|---|---|---|
| Sharpe-like Ratio | >= 2.0 | 100 | linear; gecappt bei 3.0 oben |
| | = 1.0 | 60 | |
| | = 0.0 | 0 | |
| Return Volatility (Std taegl. Returns, bps, invertiert) | <= 50 bps | 100 | linear bis... |
| | = 200 bps | 50 | |
| | >= 500 bps | 0 | |
| Hit Rate | >= 0.65 | 100 | linear bis... |
| | = 0.50 | 50 | |
| | <= 0.35 | 0 | |

**Optimierte Idee:** Ein Trader mit extremen Gewinntagen, aber unruhigem Verlauf, soll nicht nur wegen hoher Rendite stark wirken.

---

## Pillar 2 - Survivability (Überlebensfähigkeit)

Misst: Kann der Trader Verlustphasen ueberleben und wie geht er mit Drawdowns um?

| Metrik | Gewicht | Optimierung |
|---|---|---|
| Inverse Max Drawdown | 40 % | Bleibt wichtigste Metrik |
| Recovery Factor | 25 % | Bei Drawdown = 0 sauber cappen (max. 10.0) |
| Drawdown Duration | 20 % | **Neu:** Bestraft "HODL-to-death" Strategien |
| Active Days | 15 % | Kontinuitaet der Praesenz |

**Optimierte Idee:** Ein Trader, der einen 20% Drawdown in 2 Tagen aufholt, ist besser als einer, der 2 Monate braucht, um wieder auf Break-Even zu kommen. Die **Drawdown Duration** misst den Anteil der aktiven Handelstage, an denen die Equity unter dem bisherigen rollierenden Hochpunkt lag.

**Formel (konkret, kein Interpretationsspielraum):**

```
# Fuer jeden aktiven Tag t:
rolling_peak(t) = max(Equity an Tag 1 bis einschliesslich Tag t)
under_water(t)  = 1  wenn Equity(t) < rolling_peak(t), sonst 0

drawdown_days_30d       = Summe von under_water(t) ueber alle aktiven Tage in 30d
drawdown_duration_ratio = drawdown_days_30d / active_days_30d

# Pillar-Score (invertiert, hoch = gut):
drawdown_duration_score = 1.0 - drawdown_duration_ratio
```

**Sonderfaelle:**
- **Tag 1 (neuer Trader):** `rolling_peak(1) = Equity(1)`. Kein Trader ist am ersten Tag "unter Wasser". Korrekt per Definition, da kein historisches Hoch existiert.
- **Kein Recovery in 30d:** Kein Sonderfall noetig. Alle Tage unter dem Peak werden gezaehlt; `ratio` kann maximal 1.0 werden → `score = 0.0`.
- **active_days_30d = 0:** Keine Bewertung moeglich → Confidence dieses Pillars = 0.

**Beispiel:**

```
Tag 1: Equity 100 → Peak = 100 → unter Wasser: NEIN
Tag 2: Equity  95 → Peak = 100 → unter Wasser: JA
Tag 3: Equity 105 → Peak = 105 → unter Wasser: NEIN
Tag 4: Equity 102 → Peak = 105 → unter Wasser: JA

drawdown_days_30d       = 2
active_days_30d         = 4
drawdown_duration_ratio = 2 / 4 = 0.50
drawdown_duration_score = 1.0 - 0.50 = 0.50
```

**Absolute Zielwerte – weitere Survivability-Metriken (Ausgangshypothese – nach erstem Run kalibrieren):**

| Metrik | Schwelle | Punkte | Hinweis |
|---|---|---|---|
| Max Drawdown (invertiert) | = 0 % | 100 | linear bis... |
| | = 10 % | 70 | |
| | = 20 % | 40 | |
| | >= 30 % | 0 | greift auch absoluter Cap |
| Recovery Factor (gecappt bei 10.0) | >= 5.0 | 100 | linear bis... |
| | = 2.0 | 60 | |
| | = 1.0 | 30 | |
| | < 1.0 | 0 | |
| Drawdown Duration Score | direkt * 100 | – | bereits normiert (0.0–1.0), kein extra Mapping noetig |

---

## Pillar 3 - Timing (Präzision)

Misst: Sind die Einstiege im Vergleich zum Marktumfeld gut?

| Metrik | Gewicht | Optimierung |
|---|---|---|
| Favorable Entry Rate | 45 % | Wie oft ist der Einstieg besser als der Durchschnitt? |
| Mean Entry Edge | 35 % | Durchschnittlicher Vorteil (in bps) |
| Median Entry Edge | 20 % | **Neu:** Schutz gegen "Glückstreffer" (Ausreisser) |

**Optimierte Idee:** Median hilft zu unterscheiden, ob ein Trader wirklich System hat oder nur einen einzigen Trade perfekt getroffen hat, der den Durchschnitt nach oben zieht.

---

## Pillar 4 - Leverage Discipline

Misst: Nutzt der Trader Hebel verantwortungsvoll?

| Metrik | Gewicht | Optimierung |
|---|---|---|
| Inverse Max Leverage | 39 % | Sehr hoher Hebel bleibt schlecht |
| Inverse Avg Leverage | 33 % | Bleibt wichtig |
| Inverse Leverage Volatility | 17 % | Bleibt gleich |
| Inverse Sizing CV | 11 % | Bleibt gleich |

> **Hinweis:** Liquidation-Proximity Risk wurde bewusst gestrichen. "Falls Daten verfuegbar" ist beim Coden ein garantierter Stolperstein. Das Gewicht (10 %) wurde proportional auf die vier verbleibenden Metriken umverteilt. Siehe Roadmap V10 fuer die spaetere Ergaenzung.

**Absolute Zielwerte (Ausgangshypothese – nach erstem Run kalibrieren):**

| Metrik | Schwelle | Punkte | Hinweis |
|---|---|---|---|
| Max Leverage | <= 3x | 100 | linear bis... |
| | = 10x | 60 | |
| | = 25x | 20 | |
| | >= 50x | 0 | |
| Avg Leverage | <= 2x | 100 | linear bis... |
| | = 5x | 60 | |
| | = 15x | 20 | |
| | >= 25x | 0 | |
| Leverage Volatility (Std) | <= 1x | 100 | linear bis... |
| | = 5x | 50 | |
| | >= 15x | 0 | |
| Sizing CV | <= 0.3 | 100 | linear bis... |
| | = 0.7 | 50 | |
| | >= 1.5 | 0 | |

**Optimierte Idee:** Nicht jeder Hebel ist automatisch schlecht. Gefaehrlich ist ein Hebel, der konsistent hoch und volatil ist – also keine gezielte Risikonahme, sondern Zocken.

---

## Pillar 5 - Conviction (Überzeugung & Profitabilität)

Misst: Verdient der Trader wirklich Geld und hat er einen echten "Edge"?

| Metrik | Gewicht | Optimierung |
|---|---|---|
| Expectancy | 35 % | Erwartungswert pro Trade |
| Profit Factor | 30 % | Bruttogewinn / Bruttoverlust |
| Positive Return Days | 15 % | Wie viele Tage enden im Plus? |
| Unique Coins 30d | 10 % | **Neu:** Diversifikations-Bonus |
| Fills Count | 10 % | Statistische Relevanz |

**Absolute Zielwerte (Ausgangshypothese – nach erstem Run kalibrieren):**

| Metrik | Schwelle | Punkte | Hinweis |
|---|---|---|---|
| Expectancy (Mean daily return, bps) | >= 50 bps | 100 | linear bis... |
| | = 10 bps | 50 | |
| | = 0 bps | 0 | |
| | < 0 bps | 0 | absoluter Cap greift bereits |
| Profit Factor | >= 2.5 | 100 | linear bis... |
| | = 1.5 | 60 | |
| | = 1.0 | 0 | Break-Even = kein Edge |
| | < 1.0 | 0 | absoluter Cap greift bereits |
| Unique Coins 30d | >= 5 | 100 | |
| | = 3 | 60 | |
| | = 1 | 0 | Klumpenrisiko |

**Neu: Unique Coins 30d.** Ein Trader, der nur einen einzigen Coin (z.B. nur BTC) handelt, hat ein höheres Klumpenrisiko. Diversifikation ueber mehrere Coins zeigt ein breiteres Verstaendnis des Marktes und reduziert den Zufallsfaktor.

---

## Zusaetzlicher Qualitaetsfaktor

Jeder Pillar bekommt neben dem Score auch einen Qualitaetsfaktor:

```
pillar_final = pillar_score * data_quality_factor
```

Der Faktor liegt zwischen 0.70 und 1.00.

| Datenlage | Faktor |
|---|---|
| Vollstaendig | 1.00 |
| Kleine Luecken | 0.90 |
| Deutliche Luecken | 0.80 |
| Kritische Luecken | 0.70 oder keine Bewertung |

---

## Interpretation

| Score | Bedeutung |
|---|---|
| 85-100 | Sehr stark, aber nur mit hoher Confidence gueltig |
| 70-84 | Gut und beobachtenswert |
| 55-69 | Ueberdurchschnittlich |
| 45-54 | Neutral oder wenig Daten |
| 30-44 | Schwach |
| 0-29 | Sehr schwach |

---

## Kalibrierungs-Checkliste (nach erstem echten Scoring-Run)

Alle oben genannten Zielwerte sind **Ausgangshypothesen**. Nach dem ersten Run mit echten Wallet-Daten sollte folgendes geprueft werden:

1. **Verteilungscheck:** Wie viele `PROMOTED` Wallets landen ueber Score 70?
   - Zielkorridor: **10–20 %** der promoted Wallets.
   - Mehr als 30 %: Schwellen zu weich → anziehen.
   - Weniger als 5 %: Schwellen zu hart → lockern.

2. **Rohmetriken exportieren:** Perzentilverteilung von Sharpe, Max Drawdown, Leverage etc. ueber alle promoted Wallets ausgeben. Dann Schwellen an die echten Verteilungen anpassen.

3. **Zu kalibrieren (markiert als vorläufig):**
   - Sharpe-like Ratio: Ist 2.0 als Zielwert realistisch auf Hyperliquid? (Markt tendiert zu hoher Volatilitaet)
   - Return Volatility: 500 bps als unteres Ende – koennte zu streng sein fuer volatile Assets.
   - Expectancy: 50 bps als Spitzenwert – pruefen ob erreichbar bei realistischen Trade-Frequenzen.
   - Unique Coins: Thresholds (5 / 3 / 1) – pruefen ob die meisten Trader eher spezialisiert oder breit aufgestellt sind.
   - Pillar-Gewichte: Erst nach V8 (Korrelationsanalyse) final festlegen.

---

*Zurueck zur [Uebersicht](00-UEBERSICHT.md)*
