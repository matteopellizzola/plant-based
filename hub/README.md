# Hub Raspberry Pi

Il servizio riceve i messaggi MQTT dei nodi, conserva l'ultimo stato e l'ultima
misura di ogni nodo in SQLite e inoltra i comandi Telegram al topic `config`.
Il Raspberry previsto dal progetto è `192.168.1.10`.

## Installazione

Dal Raspberry, dopo aver copiato il repository in `/home/pi/plantBased`:

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients python3-venv
sudo systemctl enable --now mosquitto
cd /home/pi/plantBased
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
/status
/cal plant-node-01 0 dry 2.700
/cal plant-node-01 0 wet 1.250
/cal plant-node-01 0 threshold 35
```

## Avvio automatico

Adatta `User`, `WorkingDirectory` ed `ExecStart` se il percorso o l'utente sono
diversi da `pi`, poi installa il servizio:

```bash
sudo cp hub/plant-hub.service.example /etc/systemd/system/plant-hub.service
sudo systemctl daemon-reload
sudo systemctl enable --now plant-hub
sudo journalctl -u plant-hub -f
```

Il database viene creato in `hub/data/plant_hub.sqlite3`. I test del contratto
e della persistenza si eseguono anche senza dipendenze installate:

```bash
python3 -m unittest discover -s hub -p 'test_*.py'
```
