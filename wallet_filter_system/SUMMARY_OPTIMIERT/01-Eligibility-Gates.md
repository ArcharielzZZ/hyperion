# Optimierte Eligibility Gates - Die Ingestion-Pipeline & Der 3-Stufen-Filter

> Ziel: Rechenleistung sparen und Datenmüll vermeiden. Nur Trader bewerten, die echte Menschen (oder kluge Algos) sind und bereits bewiesen haben, dass sie profitabel sind.

---

## Die neue Filter-Architektur

Anstatt jede Wallet sofort durch die komplexe Behavior-Score-Mathematik zu jagen, nutzen wir einen strengen **3-Stufen-Filter**. Wer eine Stufe nicht besteht, wird aussortiert, bevor die nächste (teurere) Stufe beginnt.

---

## Stufe 0: Ingestion Routing (Der Gatekeeper)

Noch **bevor** eine neue Transaktion aus Hyperliquid endgültig gespeichert und analysiert wird, durchläuft die Wallet-Adresse einen blitzschnellen Datenbank-Check. 
**Wichtigste Regel:** Jede Wallet-Adresse existiert exakt **ein einziges Mal** in der Datenbank (`PRIMARY KEY`). Es gibt keine Duplikate, es werden nur Labels und Daten aktualisiert.

Gibt es diese Wallet bereits?

*   **NEIN (Neu):** Die Wallet wird angelegt und durchläuft sofort den kompletten neuen 3-Stufen-Filter (Stufe 1, 2, 3), um ihr erstes Label zu erhalten.
*   **JA (Bekannt):** Wir prüfen sofort den Status:
    *   🟢 **`PROMOTED`:** (Hat ROI/PnL-Checks bereits bestanden). Geht **sofort durch**. Keine erneuten Basis-Checks nötig. Die Trades werden verarbeitet und die Metriken nahtlos aktualisiert.
    *   🔴 **`BANNED`:** (z.B. `HIGH_FREQUENCY`, `ULTRA_HF_BOT` oder künftige Ban-Gründe). Wird **komplett blockiert**. Die Trades werden nicht analysiert und PnL wird nicht berechnet. Rechenleistung gespart.
    *   🟡 **`PENDING`:** (Hat aktuell das Label `NORMAL` oder `NEW_WALLET`, aber noch nicht profitabel genug für Promoted). Wird **erneut evaluiert**. Wir prüfen, ob die Wallet durch neue Trades nun Stufe 2 besteht (Promotion) oder ob sie sich verschlechtert hat und in Stufe 1 zu `BANNED` abrutscht.

> 🔄 **Dynamische Labels:** Die Einstufungen sind nicht für immer in Stein gemeißelt. Eine `PENDING` Wallet kann bei plötzlichem Spam-Verhalten zu `BANNED` werden. Die Datenbank speichert die Wallet nur ein einziges Mal, aktualisiert aber kontinuierlich ihren Status.

---

## Stufe 1: Daten-Dichte & Bot-Check (Das Labeling)

Sobald eine Wallet ins System kommt, laden wir bis zu 10.000 Fills (das API-Maximum). Wir schauen **nur auf die Zeitstempel** und die Anzahl der Trades, um High-Frequency-Bots sofort auszusortieren.

| Szenario | Bedingung | Ergebnis / Label | Aktion |
|---|---|---|---|
| **Ultra High Frequency** | 10.000 Fills erreicht, ältester Fill ist **< 30 Tage** alt. | `ULTRA_HF_BOT` | **Aussortiert (Ban-Liste)**. Keine weitere Analyse. |
| **High Frequency** | 10.000 Fills erreicht, ältester Fill ist **30 bis 90 Tage** alt. | `HIGH_FREQUENCY` | **Aussortiert (Ban-Liste)**. Keine weitere Analyse. |
| **Normale Historie** | Ältester Fill ist **> 90 Tage** alt (egal ob 500 oder 10.000 Fills). | `NORMAL` | **Bestanden.** Geht weiter zu Stufe 2. |
| **Newcomer** | < 10.000 Fills, aber Wallet existiert erst seit **< 90 Tagen**. | `NEW_WALLET` | **Bestanden.** Geht weiter zu Stufe 2. |

**Warum?** Ein Bot, der 10.000 Trades in 2 Wochen macht, erzeugt nur "Rauschen". Wir sparen 90% unserer Rechenpower, indem wir diese Wallets sofort auf die Ban-Liste setzen und ignorieren.

---

## Stufe 2: Basic Performance Check (PnL & Volumen)

Nur Wallets mit dem Label `NORMAL` oder `NEW_WALLET` erreichen diese Stufe.
Hier berechnet das System aus den geladenen Fills blitzschnell die Performance-Basisdaten.

**Die Kriterien zum Bestehen (Promotion):**
1.  **Positives PnL (Multi-Window):** Die Wallet muss in **mindestens einem** relevanten Zeitfenster profitabel sein:
    *   **3 Monate** (PnL_3m > 0) ODER
    *   **1 Monat** (PnL_1m > 0) ODER
    *   **7 Tage** (PnL_7d > 0) - falls nicht genug Historie fuer 1m/3m vorliegt oder ein aktueller "Comeback" vorliegt.
2.  **PnL-Effizienz:** Das PnL muss mindestens **0,1 % des gehandelten Volumens** betragen (`PnL > 0.001 * volume`). Dadurch reicht ein winziger Gewinn bei riesigem Kapitalumsatz nicht fuer Promotion.
3.  **Mindest-Volumen:** Das kumulierte Handelsvolumen muss **>= 100 $** sein (schliesst Dust-Wallets aus).
4.  **Mindest-Aktivitaet:** Mindestens **5 Trades** insgesamt.
5.  **Aktualitaet:** Der letzte Trade darf maximal **90 Tage** her sein.

| Ergebnis Stufe 2 | Aktion |
|---|---|
| **Verlust / Dust / Inaktiv** | Wallet bleibt `PENDING`, wird vorerst ignoriert. |
| **Profitabel, effizient & Mindest-Volumen** | **Bestanden.** Wallet wird `PROMOTED` und geht weiter zu Stufe 3. |

> **Hinweis:** Exakter ROI wird in dieser Stufe bewusst ignoriert, da eine praezise Berechnung ohne Snapshot-Daten (Equity) fehleranfaellig ist. Als guenstiger Ersatz dient die PnL-Effizienz (`PnL / volume`). Erst in Stufe 3 wird die Rendite im Detail analysiert.

---

## Stufe 3: Deep Dive Analysis (Behavior Score)

Nur die absolute Elite (profitabel, keine HF-Bots) erreicht diese Stufe.
Erst jetzt triggert das System die teure, detaillierte Analyse der **5 Behavior Pillars**:

*   **Timing:** Hat der Trader einen echten Entry-Edge?
*   **Leverage:** Ist der Hebel diszipliniert oder zockt er?
*   **Survivability:** Wie tief waren die Drawdowns während der profitablen Phase?
*   **Consistency:** Ist die Win-Rate stabil?

**Zusätzliche Qualitäts-Gates für den Deep Dive:**
Selbst in Stufe 3 gelten Mindestanforderungen, damit der finale Score statistisch relevant ist:
*   `pnl_snapshots_30d >= 24` (Genug Datenpunkte für eine saubere Equity-Kurve)
*   `data_completeness_30d >= 70 %` (Keine massiven Datenlücken)
*   **Durchschnittliche Confidence:** Anstatt eines harten Filters wird die Confidence über alle 5 Pillars gemittelt, um die Verlässlichkeit des Gesamt-Scores zu bestimmen.

---

## Zusammenfassung des Workflows

```text
1. Transaktion aus Hyperliquid kommt rein
   ↓
2. STUFE 0: Gibt es die Wallet bereits?
   ├─ JA, ist BANNED   → Ignorieren, STOPP.
   ├─ JA, ist PROMOTED → Trades verarbeiten, Metriken updaten, WEITER.
   └─ NEIN / PENDING   → Wallet laden & Lade max. 10.000 Fills, WEITER.
        ↓
3. STUFE 1: Ist es ein High-Frequency Bot?
   ├─ JA  → Label vergeben, Wallet auf BANNED setzen, STOPP.
   └─ NEIN → Label PENDING (NORMAL/NEW) vergeben, WEITER.
        ↓
4. STUFE 2: Ist die Wallet profitabel und effizient (PnL_7d/1m/3m > 0, PnL > 0.1% Volumen, Volumen >= 100$)?
   ├─ NEIN → Basis-Daten/Label speichern, bleibt PENDING, STOPP.
   └─ JA   → Wallet wird PROMOTED, WEITER.
        ↓
5. STUFE 3: Deep Dive (Behavior Score)
   └─ Berechne Timing, Leverage, Drawdown, Consistency.
```

---

*Zurueck zur [Uebersicht](00-UEBERSICHT.md)*
