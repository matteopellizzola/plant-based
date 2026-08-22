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

Comandi disponibili agli utenti autorizzati:

```text
/start
/help
/piante
/pianta Basilico
/rinomina Basilico | Basilico cucina
/stato
/storico Basilico 24h
/status
/cal plant-node-01 0 dry 2.700
/cal plant-node-01 0 wet 1.250
/cal plant-node-01 0 threshold 35
/node plant-node-01 Balcone nord
/plant plant-node-01 0 Basilico Ocimum cucina vaso_piccolo
/storico plant-node-01 24h
/storico plant-node-01 7g
```

I comandi in italiano sono pensati per l'uso quotidiano: `/stato` è l'alias
di `/status`, `/calibra` è l'alias di `/cal`, e `/storico` accetta il nome
della pianta quando è composto da una sola parola (`/storico Basilico 24h`),
oltre all'ID tecnico del nodo. `/start`
e `/help` mostrano solo le funzioni già disponibili; non vengono ancora
pubblicizzati irrigazione, problemi o notifiche automatiche.

`/piante` mostra l'alberatura completa raggruppando ogni vaso sotto il proprio
nodo. Per cambiare il nome di una pianta usa il separatore `|`, che permette di
conservare gli spazi nel nome: `/rinomina Basilico | Basilico cucina`. Il nome
nuovo deve essere unico; gli altri dati della pianta restano invariati.

`/node` salva il nome leggibile del nodo senza modificare l'ID MQTT. `/plant`
salva nome, specie, posizione e note del canale; per mantenere semplici gli
argomenti, usa una parola singola per nome, specie e posizione e lascia le note
come testo finale. Le misure vengono conservate nello storico SQLite oltre alla
cache dell'ultimo messaggio. `/storico` mostra le statistiche della temperatura
dell'aria e, per i vasi configurati, umidita' minima, massima, media e ultima
lettura nel periodo richiesto.

Il riepilogo non invia ancora notifiche e non formula consigli di irrigazione:
alert, recap schedulato, luce BH1750 e registrazione delle irrigazioni sono
funzioni delle fasi successive o richiedono hardware non ancora implementato.

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
