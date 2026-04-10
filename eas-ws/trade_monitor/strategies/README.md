# strategies

Una strategia per file.

## Contratto Comune

Ogni strategia riceve sempre lo stesso contesto:

- `closed_candles`
  Ultime `60` candele chiuse `M1`.
- `current_candle_states`
  Fino a `60` snapshot della candela `M1` corrente.
- `current_trade`
  Trade corrente aperto, oppure `None`.

Ogni strategia restituisce uno di questi payload:

- `{"action": "NONE"}`
- `{"action": "OPEN", "side": "BUY"|"SELL", "lot": 0.01, ...}`
- `{"action": "CLOSE", ...}`

## File Attuali

- `base.py`
  Definisce `StrategyContext`.
- `random_demo.py`
  Strategia demo attuale. Genera aperture/chiusure casuali per esercitare il flusso server -> MT4.
- `reversal_after_drop.py`
  Strategia attiva. Cerca un impulso di almeno 7 USD in 1-3 candele, una candela di stabilizzazione e un recupero live di 2 USD prima dell'ingresso. Le uscite sono demandate a `SL/TP` sull'ordine.

## Convenzioni Del Progetto

- Tutto il progetto ragiona su `M1`.
- La finestra standard della strategia e` sempre di `60` minuti.
- Le strategie leggono solo il contesto passato dal server; non accedono direttamente al DB.
