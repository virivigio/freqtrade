# MT4 Trade Monitor

Server HTTP scritto in Python standard library. Riceve una lista di trade aperti da un Expert Advisor MT4, salva su SQLite solo le differenze tra uno snapshot e il successivo e mostra tutto nella homepage.

## Cosa fa

- accetta `POST /api/trades` con un payload JSON contenente `trades`
- non impone alcun simbolo lato codice
- registra su SQLite gli eventi `OPEN` e `CLOSE`, e gli `UPDATE` solo quando cambiano `SL` o `TP`
- mantiene una proiezione dello stato corrente per mostrare i trade aperti nella homepage
- l'EA esegue l'invio una volta al secondo tramite `OnTimer`

## Avvio del server

```bash
python3 server.py
```

Homepage: `http://127.0.0.1/`

Endpoint EA: `http://127.0.0.1/api/trades`

Il server ascolta su `0.0.0.0:80`, quindi puo` essere raggiunto sia da `127.0.0.1`/`localhost` sia dall'IP LAN del Mac.

SQLite locale: `trade_log.sqlite3`

## Payload atteso

```json
{
  "trades": [
    {
      "ticket": 123456,
      "symbol": "XAUUSD.s",
      "open_price": 2320.10,
      "stop_loss": 2310.00,
      "take_profit": 2340.00,
      "profit": 14.35,
      "bid": 2321.50,
      "ask": 2321.70
    }
  ]
}
```

Se un ticket sparisce da un invio successivo, il server registra un evento `CLOSE`. Se cambiano solo `profit`, `bid` o `ask`, lo stato corrente viene aggiornato ma non viene creato un nuovo evento nel transaction log.

## Installazione EA in MT4

1. Copia [`mt4_xau_trade_reporter.mq4`](/Users/virgilio/Documents/Code/freqtrade/eas/mt4_xau_trade_reporter.mq4) nella cartella `MQL4/Experts`.
2. Compila l'EA da MetaEditor.
3. In MT4 vai su `Tools -> Options -> Expert Advisors`.
4. Aggiungi `http://127.0.0.1` agli URL permessi per `WebRequest`.
5. Attacca l'EA a un chart qualsiasi.

Se MT4 su macOS gira dentro Wine o un wrapper simile e `127.0.0.1` non funziona, prova anche con `http://localhost` oppure con l'IP LAN del Mac, per esempio `http://192.168.x.x`.

## Note implementative

- Il log differenziale è nella tabella `trade_events`.
- Lo stato corrente è nella tabella `current_trades`.
- L'ultima chiamata ricevuta è salvata in `api_calls`.
- Gli errori API sono salvati in `api_errors`.
- Il codice non filtra per simbolo: assume che l'EA invii un insieme coerente di trade.
