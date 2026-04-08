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

## 2026-04-08 - Invio Candele M1 Nel Payload

Decisione: l'EA invia a ogni chiamata anche le ultime 10 candele `M1`.

Motivo: coprire downtime brevi fino a circa 10 minuti senza dover reinviare una finestra molto ampia.

Impatto:

- il payload include 9 candele chiuse recenti e 1 candela corrente
- il timeframe trattato lato server e` `M1`

## 2026-04-08 - Persistenza Candele Chiuse E Stati Correnti

Decisione: le candele chiuse vengono archiviate in storico con inserimento incrementale; la candela aperta viene registrata come serie di stati intra-minuto in una tabella separata e volatile.

Motivo: mantenere lo storico definitivo delle barre chiuse e, allo stesso tempo, conservare l'evoluzione della barra corrente durante il minuto.

Impatto:

- `closed_candles` contiene una riga per candela chiusa
- `current_candle_states` contiene molti snapshot della candela aperta corrente
- quando cambia `open_time` della candela corrente, gli snapshot del minuto precedente possono essere eliminati

Da ricordare: per le candele chiuse non si aggiorna il record esistente; i duplicati vengono ignorati.

## 2026-04-08 - Comandi Operativi Server Verso MT4

Decisione: il server puo` restituire all'EA un oggetto `command` nella risposta del `POST /api/trades`.

Motivo: preparare il sistema a una logica decisionale futura senza dipendere da un canale separato.

Impatto:

- il protocollo server->EA supporta `NONE`, `OPEN`, `CLOSE`
- l'EA esegue il comando sul simbolo del chart corrente
- il nome simbolo non viene cablato lato server

## 2026-04-08 - Logica Demo Dei Comandi

Decisione: in assenza della logica di decisione reale, il server usa una logica demo random.

Motivo: tenere pronto il canale comando ed esercitare il ciclo completo richiesta-risposta-esecuzione su account demo.

Impatto:

- circa una volta ogni 30 secondi il server restituisce un comando diverso da `NONE`
- se non ci sono trade, il comando demo apre un `BUY`
- se c'e` almeno un trade, il comando demo prova a chiuderlo

## 2026-04-09 - Strategia Di Trading In Modulo Separato

Decisione: la funzione che decide i comandi di trading vive in un file dedicato, separato da server, persistenza e dashboard.

Motivo: isolare la logica decisionale e preparare il passaggio da strategia demo a strategia reale senza toccare il server HTTP.

Impatto:

- il punto di ingresso della decisione e` `trade_monitor/strategy.py`
- `server.py` usa il risultato della strategia ma non contiene piu` la logica decisionale
