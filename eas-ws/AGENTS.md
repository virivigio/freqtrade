# AGENTS.md

## Scopo Del Progetto

Questo repository contiene un monitor locale per trade MT4 composto da:

- un Expert Advisor MT4 che invia periodicamente lo snapshot dei trade aperti
- un server HTTP Python basato solo su standard library
- un database SQLite locale usato come transaction log e stato corrente

## Componenti Principali

- `mt4_xau_trade_reporter.mq4`: EA MT4 che invia `POST /api/trades`
- `server.py`: server HTTP, ingestione payload, persistenza SQLite, homepage HTML
- `trade_log.sqlite3`: database locale

## Flusso Operativo

1. L'EA costruisce un payload JSON con i trade aperti.
2. L'EA invia il payload a `POST /api/trades`.
3. Il server confronta lo snapshot ricevuto con `current_trades`.
4. Il server registra eventi `OPEN`, `UPDATE`, `CLOSE` in `trade_events`.
5. Il server aggiorna `current_trades`.
6. La homepage mostra stato corrente, eventi recenti ed errori API.

## Vincoli E Convenzioni

- Il progetto deve restare semplice e locale.
- Il server usa Python standard library, senza framework web.
- Il database è SQLite locale.
- Il sistema non filtra per simbolo lato server.
- Gli `UPDATE` vengono registrati solo quando cambiano `stop_loss` o `take_profit`.
- Se cambiano solo `profit`, `bid` o `ask`, viene aggiornato lo stato corrente ma non viene creato un nuovo evento.
- L'EA invia uno snapshot periodico tramite `OnTimer`.

## Regole Di Documentazione

- `README.md` serve per uso e avvio del progetto.
- `DECISIONS.md` serve per le scelte progettuali e i tradeoff.
- Quando una scelta influenza sviluppo o manutenzione futura, va annotata in `DECISIONS.md`.

## Note Per Le Sessioni Future

- Prima di proporre modifiche strutturali, rileggere `DECISIONS.md`.
- Non spostare dettagli decisionali nel `README` se non servono all'utente finale.
