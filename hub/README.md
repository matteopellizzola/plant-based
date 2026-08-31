# Hub Raspberry Pi

Il servizio riceve i messaggi MQTT dei nodi, conserva l'ultimo stato e l'ultima
misura di ogni nodo in SQLite e inoltra i comandi Telegram al topic `config`.
Il Raspberry previsto dal progetto è `192.168.1.10`.

## Installazione

Dal Raspberry, dopo aver copiato il repository in `/home/pellipi/plant-based`:

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients python3-venv
sudo systemctl enable --now mosquitto
cd /home/pellipi/plant-based
python3 -m venv .venv
.venv/bin/pip install -r hub/requirements.txt
cp hub/config.example.env hub/.env
nano hub/.env
```

In `hub/.env` inserisci il token creato con `@BotFather` e almeno un ID numerico
Telegram. Il broker deve accettare i nodi della LAN. Per il primo collaudo
locale puoi usare una configurazione temporanea:

```bash
sudo tee /etc/mosquitto/conf.d/plant-based.conf >/dev/null <<'EOF'
listener 1883 0.0.0.0
allow_anonymous true
persistence true
persistence_location /var/lib/mosquitto/
EOF
sudo systemctl restart mosquitto
```

`allow_anonymous true` va bene solo in una LAN fidata. Prima di esporre il
Raspberry fuori dalla rete locale configura autenticazione MQTT e aggiorna
`MQTT_USERNAME` e `MQTT_PASSWORD` sia nel firmware sia in `hub/.env`.

## Bot Telegram multiutente

Crea il bot con `@BotFather` e conserva il token soltanto in `hub/.env`.
Avvia temporaneamente il servizio, invia `/whoami` al bot e copia l'ID mostrato
in `TELEGRAM_ALLOWED_USER_IDS`. Più ID si separano con una virgola:

```dotenv
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
```

L'accesso è chiuso per tutti gli ID non presenti nell'elenco. `/whoami` è
l'unica funzione disponibile senza autorizzazione e serve solo a recuperare il
proprio ID. Questa soluzione è appropriata per più persone fidate; non usare
un gruppo Telegram come unica misura di sicurezza, perché i comandi devono
restare vincolati agli utenti esplicitamente autorizzati.

Gli utenti presenti in `TELEGRAM_ALLOWED_USER_IDS` sono amministratori e dal
menu possono usare `Add User`, incollare un ID numerico e autorizzarlo. `Remove User`
permette di eliminare gli utenti aggiunti, mentre `Users List` mostra gli
amministratori dell'ambiente e gli utenti aggiunti. Gli utenti aggiunti vengono
salvati in SQLite e restano autorizzati dopo il riavvio; gli amministratori
definiti nell'ambiente non possono essere rimossi dal bot.

Comandi disponibili agli utenti autorizzati:

```text
/start
/help
/rinomina Basilico | Basilico cucina
/storico Basilico 24h
/avvisi
/cal plant-node-01 0 dry 2.700
/cal plant-node-01 0 wet 1.250
/cal plant-node-01 0 threshold 35
/node plant-node-01 Balcone nord
/plant plant-node-01 0 Basilico Ocimum cucina vaso_piccolo
/storico plant-node-01 24h
/storico plant-node-01 7g
```

I comandi in italiano sono pensati per l'uso quotidiano: `/calibra` è l'alias
di `/cal`, e `/storico` accetta il nome
della pianta quando è composto da una sola parola (`/storico Basilico 24h`),
oltre all'ID tecnico del nodo. `/start`
e `/help` mostrano solo le funzioni già disponibili; non vengono ancora
pubblicizzati irrigazione, problemi o notifiche automatiche.

Il pulsante `Le mie piante` mostra l'alberatura completa raggruppando ogni vaso
sotto il proprio nodo. Per cambiare il nome di una pianta usa il separatore `|`, che permette di
conservare gli spazi nel nome: `/rinomina Basilico | Basilico cucina`. Il nome
nuovo deve essere unico; gli altri dati della pianta restano invariati.

`/node` salva il nome leggibile del nodo senza modificare l'ID MQTT. `/plant`
salva nome, specie, posizione e note del canale; per mantenere semplici gli
argomenti, usa una parola singola per nome, specie e posizione e lascia le note
come testo finale. Le misure vengono conservate nello storico SQLite oltre alla
cache dell'ultimo messaggio. `/storico` mostra le statistiche della temperatura
dell'aria, della luminosita' in lux e, per i vasi configurati, umidita' minima,
massima, media e ultima lettura nel periodo richiesto. La luminosita' viene
mostrata solo quando il payload del BH1750 e' valido; durata e andamento
giornaliero sono ancora da implementare.

`/avvisi` mostra gli alert per umidità del terreno sotto soglia e segnala i dati
non disponibili. Un alert di terreno basso resta aperto anche se le letture
successive tornano temporaneamente sopra soglia: viene chiuso soltanto quando
l'utente registra l'annaffiatura del vaso, dal pulsante nel dettaglio della
pianta o con `/annaffia NOME`. Ogni conferma viene conservata nello storico
SQLite, con data, ora e ID Telegram di chi l'ha registrata. Gli stessi alert
vengono inviati automaticamente via Telegram agli utenti autorizzati: il
controllo avviene ogni 60 secondi e un messaggio viene inviato solo quando una
condizione entra in allarme. Un nodo è
considerato offline dopo 5 minuti senza messaggi; entrambi i valori sono
configurabili in `hub/.env` con `ALERT_CHECK_INTERVAL_SECONDS` e
`NODE_OFFLINE_AFTER_SECONDS`. Il bot non formula ancora consigli di
irrigazione; l'andamento giornaliero della luce resta una funzione delle fasi
successive.

Il recap viene inviato ogni giorno all'ora configurata da `DAILY_RECAP_TIME`
(predefinita `08:00`) nel fuso `TIMEZONE` (predefinito `Europe/Rome`). Imposta
facoltativamente `QUIET_HOURS_START` e `QUIET_HOURS_END` per silenziare gli
avvisi informativi in una fascia, anche notturna; gli alert critici restano
immediati. Gli amministratori possono modificare ora, fuso e fascia silenziosa
da Telegram con `Recap e notifiche`; la scelta viene salvata nel database e ha
precedenza sui valori iniziali di `.env`. Usa `/recap` per visualizzare il
messaggio in qualunque momento. Il recap contiene anche il pulsante “Ho
annaffiato tutte le piante”: dopo conferma, registra l'evento per ogni vaso e
chiude i relativi avvisi di umidità aperti.

## TODO bot Telegram: configurazione guidata e interfaccia a pulsanti

La configurazione guidata è disponibile dal menu, con tastiere inline e stato
temporaneo separato per utente. I comandi testuali restano disponibili per
compatibilità, ma applicano le stesse validazioni.

### Stato attuale dei pulsanti

- [x] Menu principale con `Aggiungi pianta`, `Configura nodo`, `Le mie piante`,
	`Stato nodi` e `Aiuto`.
- [x] Scelta del nodo e del canale nel wizard di aggiunta pianta.
- [x] Conferma e annullamento delle configurazioni guidate.
- [x] Pulsante di ritorno al menu in ogni schermata e gestione coerente del
	pulsante `Annulla`.
- [x] Nessuna funzione deve richiedere di digitare un ID tecnico quando può
	essere scelto da una tastiera.

### Milestone 1: integrita' dei dati

- [x] Distinguere sempre ID tecnico MQTT e nome leggibile; il nome non deve
	mai essere usato come ID del nodo.
- [x] Costruire l'elenco dei nodi dai messaggi MQTT ricevuti, mostrando ID e
	nome attuale quando disponibile.
- [x] Rifiutare `/node` se l'ID non e' tra i nodi conosciuti, spiegando come
	accendere il nodo e attendere il primo messaggio MQTT.
- [x] Rifiutare `/plant` se il nodo non e' conosciuto.
- [x] Verificare che il canale sia disponibile e non gia' assegnato a un'altra
	pianta, salvo esplicita modifica.
- [x] Validare nomi vuoti, duplicati, lunghezze massime e caratteri non
	gestibili prima di scrivere nel database.
- [x] Aggiungere test per nodo inesistente, canale occupato e nomi duplicati.

### Milestone 2: wizard per i nodi

- [x] Aggiungere dal menu il pulsante `Imposta nome nodo`.
- [x] Mostrare una tastiera con i nodi disponibili, indicando ID tecnico,
	nome attuale e stato online/offline.
- [x] Chiedere il nuovo nome in un messaggio separato, con `/annulla` sempre
	disponibile.
- [x] Mostrare un riepilogo e chiedere conferma prima di salvare.
- [x] Gestire timeout, `/annulla`, `/start` e nuovi comandi durante il wizard.

### Milestone 3: wizard per piante e canali

- [x] Aggiungere dal menu il pulsante `Aggiungi pianta`.
- [x] Far scegliere prima il nodo da una tastiera, poi il canale libero da
	`A0` a `A3`, senza chiedere all'utente l'ID a mano.
- [x] Chiedere in sequenza nome pianta, specie, posizione e note opzionali.
- [x] Consentire di saltare i campi opzionali e correggere l'ultimo campo.
- [x] Mostrare il riepilogo completo e richiedere conferma finale.
- [x] Riutilizzare lo stesso flusso per modificare o riassegnare una pianta.

### Milestone 3b: azioni sulle piante

- [x] Aggiungere un pulsante `Rinomina` nel dettaglio della pianta.
- [x] Chiedere il nuovo nome in un messaggio separato e mostrare il riepilogo
	prima del salvataggio.
- [x] Aggiungere pulsanti `Modifica` e `Sposta canale` riutilizzando il wizard,
	con scelta del nodo e dei soli canali liberi.
- [x] Mostrare nel dettaglio tutti i dati configurati: specie, posizione, note,
	soglia e ultima lettura.
- [x] Aggiungere pulsanti per lo storico `24h` e `7g` dalla pianta o dal nodo.
- [ ] Aggiungere test per rinomina, modifica, riassegnazione e conflitti.

### Milestone 4: interfaccia conversazionale coerente

- [x] Sostituire il menu iniziale con azioni operative: `Aggiungi pianta`,
	`Configura nodo`, `Le mie piante`, `Stato nodi` e `Aiuto`.
- [x] Usare callback con identificativi opachi e verificare sempre l'utente
	autorizzato prima di eseguire un'azione.
- [x] Separare handler, stato delle conversazioni e rendering delle tastiere
	in moduli testabili.
- [x] Limitare ogni transizione alle opzioni valide nello stato corrente.
- [x] Aggiungere test degli handler e del percorso completo con messaggi e
	callback simulati.

### Milestone 4b: configurazione e consultazione a pulsanti

- [x] Aggiungere `Calibra sensore` al menu principale inline.
- [x] Far scegliere nodo, canale `A0`-`A3` e parametro `dry`, `wet` o soglia da
	tastiere inline; chiedere soltanto il valore numerico.
- [x] Chiedere conferma prima dell'invio MQTT della calibrazione.
- [x] Aggiungere `Stato nodi` dettagliato, con un pulsante per ogni nodo e ID
	tecnico sempre visibile.
- [x] Aggiungere pulsanti per temperatura, umidità, luce e storico del nodo.
- [x] Aggiungere pulsanti storico `24h` e `7g` dal dettaglio della pianta.
- [x] Rendere navigabili da pulsanti tutte le piante dell'alberatura, senza
	dipendere da comandi testuali.
- [x] Aggiungere navigazione uniforme `Indietro`, `Menu` e `Annulla` a ogni
	schermata.
- [x] Gestire callback scadute, nodi scomparsi e dati non più disponibili senza
	errori visibili all'utente.
- [ ] Aggiungere test di autorizzazione per ogni callback e test end-to-end dei
	percorsi principali.

### Milestone 5: operazioni distruttive e manutenzione

- [x] Aggiungere rimozione guidata di pianta e nodo con conferma esplicita.
- [x] Separare cancellazione della configurazione, ultimo stato e storico;
	proporre una scelta chiara invece di eliminare tutto implicitamente.
- [x] Mostrare sempre l'ID tecnico coinvolto prima di un'operazione distruttiva.
- [x] Registrare nei log chi ha eseguito la modifica e su quale entita'.

### Milestone 6: funzioni future

- [x] Aggiungere alert per umidità sotto soglia e nodi offline.
- [x] Aggiungere recap giornaliero configurabile da ambiente e fascia silenziosa.
- [ ] Aggiungere andamento giornaliero di temperatura, umidità e luminosità.
- [x] Registrare manualmente annaffiature e mantenere aperto l'avviso fino alla
	conferma dell'utente.
- [ ] Aggiungere consigli di irrigazione soltanto dopo una validazione sui dati
	raccolti.

## Avvio automatico

Il servizio è già configurato per l'utente `pellipi` e il percorso
`/home/pellipi/plant-based`:

```bash
cd /home/pellipi/plant-based
sudo cp hub/plant-hub.service.example /etc/systemd/system/plant-hub.service
sudo systemctl daemon-reload
sudo systemctl enable --now plant-hub
sudo systemctl status plant-hub --no-pager
```

Dopo un riavvio del Raspberry il servizio partirà automaticamente. Per seguire
i log in tempo reale:

```bash
sudo journalctl -u plant-hub -f
```

Il servizio deve risultare `active (running)`. Se fallisce, controlla il
dettaglio con:

```bash
sudo journalctl -u plant-hub -n 100 --no-pager
```

Il database viene creato in `hub/data/plant_hub.sqlite3`. I test del contratto
e della persistenza si eseguono anche senza dipendenze installate:

```bash
python3 -m unittest discover -s hub -p 'test_*.py'
```
