# Wallet Explorer Dashboard

Lokale, interaktive Web-UI: Wallet eingeben, Coin auswaehlen, Kerzen-Intervall durchschalten - bekomme den Coin-Chart mit den Wallet-Trades als kompakte Per-Kerze-Marker angezeigt.

## Marker-Konvention

Pro Kerzen-Bucket werden Trades nach Richtung aggregiert und als Textmarker gerendert:

| Marker  | Bedeutung      | Position           |
| ------- | -------------- | ------------------ |
| `L <n>` gruen | Open Long      | oberhalb der Kerze |
| `L <n>` rot   | Close Long     | oberhalb der Kerze |
| `S <n>` gruen | Open Short     | unterhalb der Kerze|
| `S <n>` rot   | Close Short    | unterhalb der Kerze|

Farben: **Gruen = eroeffnet**, **Rot = geschlossen**. Buchstabe: **L = Long**, **S = Short**.
Hover zeigt fuer jeden Marker: Trade-Anzahl, Gesamt-Size, VWAP, summierter Closed PnL.

Long und Short werden nie vermischt - vier separate Plotly-Traces, jeder einzeln ueber die Legende ein-/ausblendbar.

## Voraussetzungen

```bash
pip install "dash>=2.17" "plotly>=5.22" "requests>=2.32"
```

Polars ist bereits Teil von `hyperion-pipeline` (siehe [python/pyproject.toml](../../python/pyproject.toml)).

## Wallet pullen (volle Historie, einmalig pro Wallet)

**Im Dashboard (empfohlen):** Wallet-Adresse eingeben, **Wallet Force Pull** klicken, warten bis „Fertig“, dann **Wallet laden**.

**CLI (Alternative):**

```powershell
python analytics/scripts/force_pull_wallet_bundle.py 0x2d99fe0f36c1aebd28a1a2c0e82e8ca13c2ea351
```

Der Pull speichert ein **Bundle** unter `analytics/data_lake/wallets/<wallet>/`:

| Datei | Inhalt |
| ----- | ------ |
| `fills.parquet` | Alle Trades (volle Historie) |
| `orders.parquet` | Limit-Order-Historie (~2000 letzte) |
| `user_funding.parquet` | Funding-Zahlungen der Wallet |
| `ledger.parquet` | Ein-/Auszahlungen, Transfers |
| `market/<COIN>_candles_4h.parquet` | Kerzen pro getradetem Coin |
| `market/<COIN>_funding.parquet` | Markt-Funding-Rate pro Coin |
| `meta.json` | Metadaten (Coins, Pull-Zeitpunkt) |

Legacy: flache Datei `wallets/<wallet>.parquet` (nur Fills) wird weiter unterstuetzt.

## Dashboard starten

```powershell
python analytics/dashboard/wallet_explorer.py
```

Browser oeffnen: <http://127.0.0.1:8050>

## Bedienung

1. Wallet-Adresse (0x...) eintragen.
2. **Wallet Force Pull** (volle Historie) oder vorher per CLI pullen.
3. **Wallet laden** — Status zeigt Fills + Coins; darunter erscheint das **Research-Score-Panel**.
4. Coin waehlen (Default-Intervall `4h`).
5. Intervalle durchschalten: `15m` … `1M` (Kerzen-Cache nur fuer `4h` aus Bundle; andere Intervalle per Live-API in Zeitfenstern).

## Research Score (Bundle, Full History)

Nach **Wallet laden** berechnet das Dashboard offline einen **Research-Score** aus `fills.parquet` — ohne Postgres, ohne Live-Ingest, ohne API `:8080`.

| Anzeige | Quelle / Logik |
| ------- | -------------- |
| **Style** (Scalp, Swing, Momentum, …) | Port aus `services/trader-engine/src/scoring.rs` (`classify_style`) |
| **5 Sub-Scores + Total** | Gleiche Gewichtung wie Production (30/25/15/15/15), aber **Absolut-Scoring** pro Wallet (kein Kohorten-Ranking) |
| **Zeitraum** | Volle gepullte History (alle Fills im Bundle) |
| **PnL-Proxy** | Taegliche `closed_pnl`-Summe → Equity-Kurve |
| **Leverage-Score** | Neutral (50), da Bundle keine Leverage-Daten enthaelt |

Implementierung: [`analytics/lib/wallet_scoring.py`](../lib/wallet_scoring.py), UI: [`wallet_score_panel.py`](wallet_score_panel.py).

**Abweichung zu Live-Score:** Production nutzt Postgres (`pnl_snapshots`, `positions`) und ein 30-Tage-Fenster. Das Panel ist eine **Offline-Vorschau** zum Ausprobieren der Darstellung — kein Ersatz fuer den trader-engine-Score.

**Limit-Orders:** Horizontale Linien auf `limit_px` von Platzierung bis Fill/Cancel (offene Orders bis Chart-Ende). Kerzen-Marker nur bei Platzierung:

| Linie / Marker | Bedeutung |
| -------------- | --------- |
| Gruen durchgezogen + `X n` | Long Open |
| Rot durchgezogen + `X n` | Long Close |
| Gruen gepunktet + `Y n` gruen | Short Open |
| Rot gepunktet + `Y n` rot | Short Close |
| **`X n` gelb** (+ gelbe Linie Platz → Cancel) | Limit Long Cancel |
| **`Y n` gelb** (+ gelbe gepunktete Linie) | Limit Short Cancel |

Linien-Opacity ca. 40 %. Klassifikation: `reduce_only` + Side A/B, sonst Side B = Long Open, Side A = Short Open; Cancel-Status = gelb.

**Wichtig:** `historicalOrders` liefert pro Order oft zwei Snapshots (`open` + `filled`/`canceled`) — im Code wird pro `oid` dedupliziert (Terminal-Status gewinnt). Linie: Platzierung (`timestamp`) bis Fill/Cancel (`status_timestamp`); nur echte `open`-Orders laufen bis Chart-Ende.

**Limit Long Close ohne Limit Long Open:** `reduceOnly`-Limit-Sells (Side A) schliessen bestehende Longs — oft per **Market Open Long** (L-Marker). Das ist korrekt, kein fehlender Limit-Open.

## Chart bedienen (TradingView-aehnlich)

- **Ziehen** im Chart: verschieben (Pan)
- **Mausrad**: zoomen
- **Linke Y-Achse** (Preis / Volumen / Funding): Enden ziehen = nur diese Achse skalieren
- **Zeit-Leiste unten** (Rangeslider): horizontal scrollen / Zeitfenster waehlen
- **Doppelklick**: Zoom zuruecksetzen
- Toolbar oben rechts: Pan, Zoom-Rechteck, Autoscale, Reset

## Chart-Inhalte (pro Coin)

| Element | Quelle |
| ------- | ------ |
| Kerzen + Markt-Volumen | Bundle (`4h`) oder Live `candleSnapshot` |
| Fill-Marker (L/S) | `fills.parquet` |
| Limit-Order-Linien + X/Y-Platzierungsmarker | `orders.parquet` (`limit_px`, `timestamp`, `status_timestamp`, `reduce_only`) |
| Ledger-Icons oben am Chart | `ledger.parquet` (Deposit/Withdraw/Transfer) |
| Markt-Funding-Rate (unteres Panel) | `market/<COIN>_funding.parquet` oder Live |
| Wallet-Funding-Summe | Titelzeile: Summe `user_funding` im **sichtbaren Kerzen-Zeitraum** (negativ = gezahlt) |
| Twitter-Sprechblasen | unten am Chart (separater Pull) |
| Z-Spread @ Trade | Panel 4: Z-Score vs (coin, UTC-Stunde)-Baseline; Gruen/Grau/Rot; Hover USD+bps+Vol Top5 |

## Datenquellen

- **Wallet-Daten:** Bundle oder Legacy-Parquet unter `analytics/data_lake/wallets/`.
- **S3 Order Book:** `hyperliquid-archive` via `force_pull_orderbook.py` — nur Stunden mit Fills, 100 GB/Monat Cap.
- **Sichtbarer Bereich:** erste bis letzte Wallet-Aktivitaet auf dem Coin + 7 Tage Padding.

## Frische Daten

Wenn neue Trades hinzugekommen sind: Wallet erneut pullen und im Browser neu laden. Die App haelt keine Trade-Daten zwischen den Requests vor.

## Twitter-News auf dem Chart (Force Pull Twitter)

Zusaetzlich zu den Wallet-Trades koennen Tweets eines @handles im Chart angezeigt werden - als klickbare Sprechblasen pro Kerzen-Bucket am unteren Chart-Rand (TradingView-News-Stil).

### Voraussetzung

```bash
pip install playwright
playwright install chromium
```

### Bedienung

1. Im Dashboard oben den Block `Twitter Force Pull` ausfuellen:
   * `@handle` (z.B. `Mark_Nr1`)
   * `Von YYYY-MM-DD`
   * `Bis YYYY-MM-DD`
2. `Force Pull Twitter` klicken. Beim ersten Mal oeffnet sich Chrome -> bei `x.com` einloggen. Das Login wird in `analytics/data_lake/twitter/.x_profile/` gespeichert und bleibt erhalten.
3. Der Scraper navigiert zu `x.com/<handle>` und scrollt, bis er einen Tweet aelter als das `Von`-Datum sieht oder die Timeline endet.
4. Im Status-Feld neben dem Button siehst du Live-Fortschritt (`Sammle Tweets ... (47) - bei 2026-04-12`).
5. Nach Fertigstellung erscheinen die Sprechblasen automatisch auf dem Chart.

### CLI-Variante

```bash
python analytics/scripts/force_pull_twitter.py --handle Mark_Nr1 --from 2025-01-01 --to 2026-05-23
```

### Marker-Konvention

| Marker             | Bedeutung                                                |
| ------------------ | -------------------------------------------------------- |
| `(speech) n` cyan  | `n` Tweets im jeweiligen Kerzen-Bucket                   |
| `(speech)* n` amber| `n` Tweets ausserhalb des sichtbaren Zeitraums (Andock)  |

Klick auf eine Sprechblase oeffnet rechts einen Drawer mit Tweet-Text, Bild(ern)/Video, Datum und Link zu X. Bei mehreren Tweets pro Bucket kannst du mit `<` und `>` durchblaettern.

### History-Panel (oben rechts)

Jeder Pull erzeugt eine Zeile mit:
- Sichtbarkeits-Checkbox (Marker auf dem Chart ausblenden ohne zu loeschen)
- Handle + Zeitraum + Tweet-Anzahl
- Loesch-Button (`x`) - mit Bestaetigungsdialog, loescht den History-Eintrag UND die zugehoerige Parquet-Datei auf der Festplatte.

### Datenablage

- Pull-Parquet: `analytics/data_lake/twitter/<handle>__<from>__<to>.parquet`
- History: `analytics/data_lake/twitter/history.json`
- Live-Status (vom Scraper geschrieben, vom Dashboard gepollt): `analytics/data_lake/twitter/.pull_status.json`
- Chrome-Profil (Login-Cookies): `analytics/data_lake/twitter/.x_profile/`

Bilder/Videos werden **nicht** lokal gespeichert - der Drawer laedt sie direkt von Twitters CDN. Wenn Twitter Medien spaeter loescht, sind die URLs ungueltig (Tweet-Text bleibt erhalten).
