# Optimierte Verbesserungs-Roadmap

> Grobe Reihenfolge fuer die Umsetzung. Noch kein Code, nur fachliche Ziel-Logik.

---

## Prioritaet 1 - Muss zuerst sauber werden

### P0 - Multi-Window Storage & Aggregation (Implementiert)

**Problem:** 2 Millionen Wallets koennen nicht live in komplexen Fenstern (7d, 1m, 3m, 6m) berechnet werden.

**Ziel:** Einführung einer `wallet_daily_stats` Tabelle und rollierender Performance-Fenster.

**Nutzen:** Ermöglicht blitzschnelle Filterung über Monate hinweg bei minimalem Ressourcenverbrauch.

**Status:** Datenbank-Schema und Aggregations-Logik implementiert.

---

### P1 - Recovery Factor absichern

**Problem:** Bei Max Drawdown = 0 kann Recovery Factor mathematisch problematisch werden.

**Ziel:** Wert cappen oder Sonderfall sauber behandeln.

**Nutzen:** Verhindert falsche Scores oder technische Fehler.

**Aufwand:** Klein

---

### P2 - Score-Historie speichern

**Problem:** Aktuell sieht man nur den aktuellen Score.

**Ziel:** Bei jedem Scoring-Run Score, Pillars, Confidence, Tier und Datenqualitaet speichern.

**Nutzen:** Man erkennt Verschlechterung, Stabilitaet und Trends.

**Aufwand:** Mittel

---

### P3 - Datenqualitaet messen

**Problem:** Lueckenhafte Daten koennen Scores verzerren.

**Ziel:** `data_completeness_score` pro Wallet berechnen.

**Nutzen:** Fairere Scores und bessere Backfill-Entscheidungen.

**Aufwand:** Mittel

---

### P4 - Promotion mit PnL-Effizienz schuetzen

**Problem:** Reines `PnL > 0` kann Wallets promoten, die bei sehr hohem Volumen nur minimal Gewinn machen.

**Ziel:** Stufe 2 nur bestehen, wenn `PnL > 0` und `PnL / volume >= 0,1 %`.

**Nutzen:** Verhindert Scheinprofitabilitaet ohne echte Rendite, solange exakte Equity/ROI-Daten noch fehlen.

**Aufwand:** Klein

---

## Prioritaet 2 - Score fairer machen

### V1 - Absolute Performance-Grenzen

**Problem:** Ein Trader kann relativ gut aussehen, obwohl er absolut schlecht performt.

**Ziel:** Negative 30d-Rendite, Profit Factor < 1 oder extreme Drawdowns deckeln den Score.

**Nutzen:** Schuetzt vor "besten der schlechten Trader".

**Aufwand:** Mittel

---

### V2 - Score und Confidence getrennt halten

**Problem:** Shrinkage Richtung 0 bestraft neue `PROMOTED` Wallets zu stark und verdeckt gute Rohleistung.

**Ziel:** `score_raw` anzeigen, `effective_confidence` separat speichern und Top-Tiers nur mit Mindest-Confidence erlauben.

**Nutzen:** Nutzer sieht z. B. "Score 82, Confidence niedrig" statt kuenstlich "Score 25". Weniger Zufallsgewinner ohne unfairen Newcomer-Abschlag.

**Aufwand:** Mittel

---

### V3 - Status und Tier trennen

**Problem:** Beobachtungsstatus (`PENDING`, `ARCHIVED`, `BANNED`) und Qualitaets-Tier (`high_quality`, `active_tracked`, `watchlist`) werden sonst vermischt.

**Ziel:** Status beschreibt Verarbeitung, Tier beschreibt Qualitaet. `ARCHIVED` ist Status, kein Tier.

**Nutzen:** Besserer Ueberblick und weniger Fehlinterpretation.

**Aufwand:** Klein bis mittel

---

## Prioritaet 3 - System intelligenter machen

### V4 - Alerts bei Score-Abfall

**Problem:** Schlechter werdende Trader fallen nicht automatisch auf.

**Ziel:** Warnung bei starkem Score-Abfall, Drawdown-Anstieg oder Datenqualitaetsverlust.

**Nutzen:** Besseres Risiko-Management.

**Aufwand:** Mittel

---

### V5 - Archivierung fuer inaktive Wallets

**Problem:** Wallets ohne Aktivitaet bleiben moeglicherweise im Pool.

**Ziel:** Wallets niemals loeschen, sondern Status auf `ARCHIVED` setzen und aus aktivem Scoring entfernen.

**Nutzen:** Weniger aktiver Datenmuell bei vollstaendiger historischer Nachvollziehbarkeit.

**Aufwand:** Mittel

---

### V6 - Coin-Diversifikation ergaenzen

**Problem:** Ein Trader kann wegen eines einzelnen Coins gut aussehen.

**Ziel:** `unique_coins_30d` oder Konzentrationsrisiko als Metrik aufnehmen.

**Nutzen:** Stabilere Bewertung.

**Aufwand:** Klein bis mittel

---

## Prioritaet 4 - Spaeter pruefen

### V7 - Zeitgewichtung

**Idee:** Juengere Trades zaehlen staerker als alte Trades.

**Nutzen:** Score reagiert schneller auf Veraenderungen.

**Risiko:** Kann zu nervoes werden.

---

### V8 - Korrelation zwischen Pillars pruefen

**Idee:** Consistency und Conviction messen teilweise aehnliche Dinge.

**Nutzen:** Gewichtungen koennen fachlich sauberer werden.

**Risiko:** Braucht echte Datenanalyse.

---

### V10 - Liquidation-Proximity Risk als Metrik ergaenzen

**Idee:** Den Abstand jeder Position zum Liquidationspreis als Score-Faktor in Pillar 4 (Leverage Discipline) aufnehmen.

**Nutzen:** Erkennt gefaehrliches Hebel-Verhalten auch bei formal niedrigem Durchschnittshebel.

**Warum jetzt nicht:** Die API liefert Liquidationsdaten aktuell nicht zuverlaessig genug. "Falls Daten verfuegbar" waere beim Coden ein garantierter Stolperstein. Erst implementieren, wenn der Datenzugang stabil geprueft ist.

**Gewicht nach Einbau:** Ca. 10 % aus den anderen vier Metriken proportional entnehmen.

---

### V9 - Discovery Cold-Start-Regel

**Idee:** `History / Stability` im Discovery Score erst nutzen, wenn mindestens 5 Scoring-Laeufe vorhanden sind.

**Nutzen:** Neue `PROMOTED` Wallets werden nicht benachteiligt, nur weil noch keine Score-Historie existiert.

**Risiko:** In den ersten Runs zaehlen Datenabdeckung und Aktivitaet staerker.

---

## Empfohlene Umsetzungsreihenfolge

| Reihenfolge | Thema | Warum zuerst? |
|---|---|---|
| 1 | Recovery Factor absichern | Kleiner Aufwand, hohes Bug-Risiko |
| 2 | PnL-Effizienz in Stufe 2 | Kleiner Aufwand, bessere Promotion-Qualitaet |
| 3 | Score-Historie | Grundlage fuer Trends, Alerts und Discovery Cold-Start |
| 4 | Datenqualitaet | Grundlage fuer faire Scores |
| 5 | Score und Confidence trennen | Fairer fuer Newcomer |
| 6 | Status/Tier-Logik | Macht Auswertung verstaendlicher |
| 7 | Absolute Caps | Verhindert schlechte Top-Scores |
| 8 | Archivierung | Entfernt inaktive Wallets aus aktivem Scoring |
| 9 | Alerts | Nutzt Score-Historie sinnvoll |
| 10 | Schwellen kalibrieren | Nach erstem echten Scoring-Run Verteilung pruefen und Zielwerte anpassen |
| 11 | Liquidation-Proximity Risk | Sobald API-Daten zuverlaessig verfuegbar sind |

---

*Zurueck zur [Uebersicht](00-UEBERSICHT.md)*
