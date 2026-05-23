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

## Wallet pullen (einmalig pro Wallet)

```powershell
python analytics/scripts/force_pull_wallet.py 0x2d99fe0f36c1aebd28a1a2c0e82e8ca13c2ea351
```

Das Skript schreibt die komplette Trade-Historie nach
`analytics/data_lake/wallets/<wallet>.parquet`. Die App liest ausschliesslich von dort.

## Dashboard starten

```powershell
python analytics/dashboard/wallet_explorer.py
```

Browser oeffnen: <http://127.0.0.1:8050>

## Bedienung

1. Wallet-Adresse (0x...) in das Textfeld eintragen, `Wallet laden` klicken oder Enter druecken.
2. Status zeigt "OK - N Fills, M Coins" - Coin-Dropdown ist jetzt befuellt.
3. Coin auswaehlen. Default-Intervall ist `4h`.
4. Intervalle durchschalten: `15m`, `30m`, `1h`, `4h`, `1d`, `1w`, `1M`. Marker werden automatisch neu pro Kerze aggregiert.

## Datenquellen

- **Wallet-Trades:** lokal aus Parquet (`analytics/data_lake/wallets/`).
- **Kerzen:** live vom Hyperliquid REST-Endpunkt `candleSnapshot` (paginiert, max 5000 Kerzen pro Call).
- **Sichtbarer Bereich:** automatisch erste bis letzte Wallet-Aktivitaet auf dem Coin + 7 Tage Padding.

## Frische Daten

Wenn neue Trades hinzugekommen sind: Wallet erneut pullen und im Browser neu laden. Die App haelt keine Trade-Daten zwischen den Requests vor.
