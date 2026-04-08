# DECISIONS.md

## 2026-04-08 - Separazione Tra README E Memoria Decisionale

Decisione: usare `README.md` per la documentazione utente e `DECISIONS.md` per le scelte progettuali.

Motivo: la chat non è una memoria affidabile di lungo periodo; le decisioni devono essere recuperabili senza rileggere tutta la conversazione.

Impatto:

- il `README` resta focalizzato su avvio, uso e comportamento del progetto
- le scelte tecniche persistenti vengono annotate qui

## 2026-04-08 - Mantenere Il README

Decisione: non svuotare `README.md`.

Motivo: il file contiene informazioni operative utili su avvio del server, endpoint, payload atteso e installazione dell'EA.

Tradeoff: una parte del contenuto descrive anche comportamento interno del sistema, ma resta comunque utile come documentazione rapida del progetto.

Da ricordare: se il `README` cresce troppo, va semplificato, non azzerato.

## 2026-04-08 - Architettura Del Progetto

Decisione: mantenere l'architettura composta da EA MT4 + server Python minimale + SQLite locale.

Motivo: il progetto è locale, focalizzato e non richiede componenti aggiuntivi.

Impatto:

- niente dipendenze web esterne
- persistenza semplice da ispezionare
- dashboard generata direttamente dal server Python

## 2026-04-08 - Log Differenziale Degli Eventi

Decisione: registrare `OPEN` e `CLOSE` sempre, e registrare `UPDATE` solo quando cambiano `stop_loss` o `take_profit`.

Motivo: evitare rumore nel log dovuto a variazioni frequenti di `profit`, `bid` e `ask`, mantenendo comunque aggiornato lo stato corrente.

Impatto:

- `trade_events` resta leggibile
- `current_trades` rappresenta l'ultimo snapshot noto

## 2026-04-08 - Supporto Simbolo Lato Server

Decisione: non filtrare i trade per simbolo lato server.

Motivo: il server deve limitarsi a registrare in modo generico ciò che l'EA invia.

Tradeoff: il chiamante deve inviare un insieme coerente di trade, altrimenti il delta può riflettere uno scope incoerente.

## 2026-04-08 - Porta Del Server

Decisione: mantenere al momento la porta `80`.

Motivo: l'endpoint dell'EA resta corto e semplice, ad esempio `http://127.0.0.1/api/trades`.

Tradeoff: su sistemi Unix l'avvio può richiedere privilegi elevati.

Da ricordare: se la porta cambia, vanno aggiornati insieme `server.py`, configurazione EA e `README.md`.
