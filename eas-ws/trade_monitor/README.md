# trade_monitor

Moduli interni del server.

## Struttura

- `core.py`
  Costanti, parsing payload, normalizzazione dati, formattazione date/prezzi e logica demo dei comandi.
- `store.py`
  Schema SQLite e accesso ai dati.
- `dashboard.py`
  Rendering HTML/SVG della homepage e dei frammenti live usati da `/api/dashboard`.
- `strategies/`
  Contratto comune e strategie di trading, una per file.

## Regola Pratica

- Se cambi schema o persistenza, tocca prima `store.py`.
- Se cambi il protocollo request/response, tocca prima `core.py` e poi `server.py`.
- Se cambi la logica di apertura/chiusura trade, tocca `strategies/`.
- Se cambi layout o grafici, tocca `dashboard.py`.
- `server.py` deve restare un entrypoint sottile.
