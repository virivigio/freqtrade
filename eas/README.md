# MT4 Trade Monitor

Server HTTP scritto in Python standard library. Riceve una lista di trade aperti da un Expert Advisor MT4, salva su SQLite solo le differenze tra uno snapshot e il successivo e mostra tutto nella homepage.

## Cosa fa

- accetta `POST /api/trades` con un payload JSON contenente `trades`
- accetta nello stesso payload anche `candles` con le ultime 10 candele `M1`
- non impone alcun simbolo lato codice
- registra su SQLite gli eventi `OPEN` e `CLOSE`, e gli `UPDATE` solo quando cambiano `SL` o `TP`
- mantiene una proiezione dello stato corrente per mostrare i trade aperti nella homepage
- salva in modo incrementale le ultime 9 candele chiuse `M1`
- salva tutti gli stati intra-minuto della candela `M1` corrente in una tabella separata
- l'EA esegue l'invio una volta al secondo tramite `OnTimer`

## Avvio del server

```bash
python3 server.py
```

Homepage: `http://127.0.0.1/`

Endpoint EA: `http://127.0.0.1/api/trades`

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
  ],
  "candles": [
    {
      "symbol": "XAUUSD.s",
      "timeframe": "M1",
      "open_time": 1744146360,
      "open": 2320.10,
      "high": 2321.50,
      "low": 2319.90,
      "close": 2321.20,
      "volume": 87,
      "is_closed": true
    },
    {
      "symbol": "XAUUSD.s",
      "timeframe": "M1",
      "open_time": 1744146420,
      "open": 2321.20,
      "high": 2321.70,
      "low": 2321.00,
      "close": 2321.40,
      "volume": 12,
      "is_closed": false
    }
  ]
}
```

Se un ticket sparisce da un invio successivo, il server registra un evento `CLOSE`. Se cambiano solo `profit`, `bid` o `ask`, lo stato corrente viene aggiornato ma non viene creato un nuovo evento nel transaction log.

Per le candele, l'EA invia sempre le ultime 10 barre `M1`: le 9 chiuse piu` recenti e la candela corrente. Il server salva le chiuse con inserimento incrementale e registra tutti gli snapshot intra-minuto della candela aperta corrente.

## Installazione EA in MT4

1. Copia [`mt4_xau_trade_reporter.mq4`](/Users/virgilio/Documents/Code/freqtrade/eas/mt4_xau_trade_reporter.mq4) nella cartella `MQL4/Experts`.
2. Compila l'EA da MetaEditor.
3. In MT4 vai su `Tools -> Options -> Expert Advisors`.
4. Aggiungi `http://127.0.0.1` agli URL permessi per `WebRequest`.
5. Attacca l'EA a un chart qualsiasi.

## Note implementative

- Il log differenziale è nella tabella `trade_events`.
- Lo stato corrente è nella tabella `current_trades`.
- Lo storico delle candele chiuse è nella tabella `closed_candles`.
- Gli stati intra-minuto della candela corrente sono nella tabella `current_candle_states`.
- L'ultima chiamata ricevuta è salvata in `api_calls`.
- Gli errori API sono salvati in `api_errors`.
- Il codice non filtra per simbolo: assume che l'EA invii un insieme coerente di trade.
