# 🚀 Hyperion Start-Anleitung (für Nicht-Programmierer)

Willkommen bei **Hyperion**! Diese Anleitung ist speziell für dich geschrieben, wenn du kein Programmierer bist. Hier erfährst du auf einfachste Weise, was dieses Programm alles kann und welche Befehle du kopieren und in dein Terminal einfügen musst, um die verschiedenen Funktionen zu starten.

Keine Sorge, du musst keinen Code schreiben! Du musst nur Befehle kopieren, einfügen und mit der Eingabetaste (Enter) bestätigen.

---

## 📌 Inhaltsverzeichnis
1. [Was kann dieses Programm überhaupt?](#1-was-kann-dieses-programm-überhaupt)
2. [Vorbereitung (Vor dem ersten Start)](#2-vorbereitung-vor-dem-ersten-start)
3. [Die Steuerzentrale (Infrastruktur starten)](#3-die-steuerzentrale-infrastruktur-starten)
4. [Live-Dienste starten (Die Hintergrund-Arbeiter)](#4-live-dienste-starten-die-hintergrund-arbeiter)
5. [Benutzeroberflächen & Analysen (Das Spannende!)](#5-benutzeroberflächen--analysen-das-spannende)
6. [Häufige Fragen & Probleme (FAQ)](#6-häufige-fragen--probleme-faq)

---

## 1. Was kann dieses Programm überhaupt?

Hyperion ist eine Plattform zur Analyse des Verhaltens von erfolgreichen Krypto-Händlern (Tradern) auf der Börse "Hyperliquid". 
Das Programm kann:
1. **Live-Daten mitlesen**: Es lauscht der Börse Hyperliquid und speichert jeden Trade von ausgewählten Händlern live in deiner Datenbank.
2. **Trader bewerten**: Es berechnet live, wie gut und profitabel die Trader sind (Scoring).
3. **Signale erzeugen**: Wenn Top-Trader eine Position eröffnen oder schließen, generiert das System automatische Kaufs-/Verkaufs-Signale.
4. **Handel simulieren**: Es simuliert virtuell (ohne echtes Geld), wie profitabel diese Signale gewesen wären (Paper Trading).
5. **Visuelle Dashboards anzeigen**: Es zeigt dir Charts mit Kerzencharts und den genauen Zeitpunkten, wann welcher Trader gekauft oder verkauft hat.

---

## 2. Vorbereitung (Vor dem ersten Start)

Bevor du Befehle eingibst, stelle sicher, dass:
* **Docker Desktop** auf deinem Computer geöffnet ist und im Hintergrund läuft. (Das wird für die Datenbank benötigt).
* Du in VSCode oder Cursor ein **Terminal** geöffnet hast. 
  * *Tipp:* Du öffnest das Terminal über das Menü oben unter `Terminal -> New Terminal` oder mit der Tastenkombination `Strg + ` ` ` (bzw. `Ctrl + Shift + ` ` `).

---

## 3. Die Steuerzentrale (Infrastruktur starten)

Bevor das Programm laufen kann, musst du die Datenbanken starten.

### Schritt A: Die Datenbanken einschalten
Kopiere diesen Befehl, füge ihn im Terminal ein und drücke Enter:
```bash
make dev-up
```
* **Was passiert hier?** Dieser Befehl startet im Hintergrund die Datenbanken (PostgreSQL und Redis) sowie ein Verwaltungstool (pgAdmin).

### Schritt B: Die Tabellenstruktur vorbereiten (nur nötig, wenn sich das Programm geändert hat)
Kopiere diesen Befehl und führe ihn aus:
```bash
make migrate
```
* **Was passiert hier?** Das bereitet die Datenbank darauf vor, die Trading-Daten sauber geordnet in Tabellen zu speichern.

---

## 4. Live-Dienste starten (Die Hintergrund-Arbeiter)

Diese Dienste laufen in der Regel dauerhaft im Hintergrund und sammeln Daten. Da sie live Daten empfangen, blockieren sie das jeweilige Terminalfenster. 
* *Tipp:* Du kannst in VSCode/Cursor über das `+`-Symbol oben rechts im Terminal-Bereich **neue Terminal-Reiter** öffnen, um mehrere Dienste gleichzeitig laufen zu lassen.

### 1. Der Daten-Sammler (Ingest)
Sammelt live alle Trades von Hyperliquid.
```bash
make run-ingest
```

### 2. Der Trader-Bewerter (Trader Engine)
Berechnet live die Scores der Trader basierend auf ihren Trades.
```bash
make run-trader
```

### 3. Der Signal-Geber (Signal Engine)
Erzeugt Signale, wenn Top-Trader aktiv werden.
```bash
make run-signal
```

### 4. Der Simulator (Execution Engine)
Simuliert virtuell die Trades, um die Strategie zu testen.
```bash
make run-execution
```

### 5. Die Datenschnittstelle (API)
Macht die Daten für Dashboards und Abfragen im Netzwerk verfügbar.
```bash
make run-api
```

---

## 5. Benutzeroberflächen & Analysen (Das Spannende!)

Hier siehst du die echten Ergebnisse und kannst das System bedienen!

### 📊 Die interaktive Chart-Oberfläche (Wallet Explorer)
Dies ist deine visuelle Benutzeroberfläche. Du kannst eine Wallet-Adresse eingeben, einen Coin auswählen und siehst sofort alle Trades dieses Händlers direkt auf dem Kerzenchart!

1. **Daten für eine Wallet herunterladen**:
   Bevor du ein Wallet im Chart anschauen kannst, musst du dessen historische Trades von der Börse herunterladen. Ersetze `0x...` im folgenden Befehl mit einer echten Wallet-Adresse (z.B. `0x3895d155b005191686476a39580d43258a518e46`) und führe ihn aus:
   ```bash
   python analytics/scripts/force_pull_wallet.py 0x3895d155b005191686476a39580d43258a518e46
   ```
2. **Das Dashboard starten**:
   Führe diesen Befehl aus:
   ```bash
   python analytics/dashboard/wallet_explorer.py
   ```
3. **Im Browser öffnen**:
   Sobald im Terminal steht, dass der Server läuft, öffne deinen Webbrowser (Chrome, Firefox, etc.) und gehe auf folgende Adresse:
   👉 **[http://127.0.0.1:8050](http://127.0.0.1:8050)**

Hier kannst du jetzt oben das Wallet eingeben, den Coin wählen und durch die Zeitintervalle (z.B. 15 Minuten, 1 Stunde, 1 Tag) klicken!

---

### 🐦 Twitter-News auf den Chart legen (Force Pull Twitter)

Du kannst die Tweets eines bestimmten X/Twitter-Accounts in einem von dir gewählten Zeitraum direkt als klickbare Sprechblasen am unteren Rand des Charts anzeigen lassen.

**Einmalig vorbereiten (nur beim ersten Mal):**
```bash
pip install playwright
playwright install chromium
```

**Im Dashboard nutzen:**
1. Dashboard wie oben starten und im Browser oeffnen.
2. Im neuen Feld `Twitter Force Pull`:
   * `@handle` (ohne `@`, z.B. `Mark_Nr1`)
   * `Von YYYY-MM-DD`
   * `Bis YYYY-MM-DD`
   * Auf `Force Pull Twitter` klicken.
3. Beim ersten Mal oeffnet sich Chrome - bei `x.com` einloggen. Der Login wird im persistenten Profil unter `analytics/data_lake/twitter/.x_profile/` gespeichert.
4. Danach scrollt der Scraper automatisch ueber die Profilseite, bis das `Von`-Datum erreicht ist.
5. Im Chart erscheint pro Kerze unten am Rand eine Sprechblase mit Anzahl - Klick darauf zeigt rechts den Tweet inkl. Bild/Video.
6. Tweets ausserhalb des Wallet-Trade-Zeitraums werden links/rechts an den Chart-Rand angedockt (`*` neben der Sprechblase).

**Alternativ direkt im Terminal:**
```bash
python analytics/scripts/force_pull_twitter.py --handle Mark_Nr1 --from 2025-01-01 --to 2026-05-23
```

**Oben rechts im Dashboard** siehst du eine kleine Pull-History: alle abgeschlossenen Pulls mit Sichtbarkeits-Checkbox und Loesch-Button (loescht Parquet-Datei mit).

---

### 🐋 Automatisch Top-Trader (Whales) finden
Du weißt nicht, welche Wallets du analysieren sollst? Lass das Programm die profitabelsten Trader von der Hyperliquid-Bestenliste suchen und in deine Datenbank eintragen!

Kopiere diesen Befehl und führe ihn aus:
```bash
make discover-whales
```
* **Was passiert hier?** Das Programm durchsucht die Hyperliquid-Bestenliste nach Tradern mit mindestens 25 Mio. $ Handelsvolumen und einer Gewinnrate (Hit-Rate) von über 55% und fügt die besten 150 Trader automatisch deiner Tracking-Datenbank hinzu!

---

### 📈 Markt-Kontext analysieren (Alternative Chart-Ansicht)
Möchtest du sehen, wie die Trades eines Wallets im Verhältnis zur Funding Rate (Gebühren für Positionen) und den Marktbewegungen stehen?

Führe diesen Befehl aus:
```bash
python analytics/research/market_context/explore.py
```
Das Skript lädt Daten und öffnet automatisch einen interaktiven Plotly-Chart in deinem Browser!

---

## 6. Häufige Fragen & Probleme (FAQ)

### ❓ "Befehl nicht gefunden" (z.B. bei `make dev-up`)
* **Lösung:** Stelle sicher, dass du dich im richtigen Hauptordner (`hyperion.git`) befindest. Falls du in Windows arbeitest, nutzt das Programm PowerShell. Tippe einfach genau den Befehl ein.

### ❓ "Docker not running" oder Verbindungsfehler
* **Lösung:** Stelle sicher, dass **Docker Desktop** auf deinem Computer geöffnet ist und unten links ein grünes Symbol ("Engine running") anzeigt. Wenn es nicht läuft, können die Datenbanken nicht gestartet werden.

### ❓ Wie stoppe ich die Programme wieder?
* Um einen laufenden Dienst im Terminal zu beenden (z.B. den Sammler oder das Dashboard), klicke in das Terminalfenster und drücke:
  **`Strg + C`** (bzw. `Ctrl + C`).
* Um die Datenbanken im Hintergrund komplett auszuschalten und Ressourcen freizugeben, führe diesen Befehl aus:
  ```bash
  make dev-down
  ```

### ❓ Muss ich Programmierer sein, um das zu verstehen?
* **Nein!** Nutze einfach den **Wallet Explorer** (Punkt 5). Lade dir mit `force_pull_wallet.py` ein interessantes Wallet herunter, starte das Dashboard und analysiere die Trades ganz entspannt im Browser.
