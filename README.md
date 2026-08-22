# Plant Based

Sistema modulare per monitorare la salute delle piante con nodi ESP32.

## Obiettivo della prima fase

Il progetto finale avrà quattro nodi ESP32 indipendenti. In questo repository
stiamo sviluppando il **primo nodo** (`plant-node-01`), che monitora fino a
quattro vasi senza irrigazione automatica:

- umidità del terreno tramite sensori capacitivi e ADS1115;
- temperatura e umidità dell'aria tramite SHT31-D;
- luminosità tramite BH1750;
- collegamento Wi-Fi;
- in seguito, invio dei dati via MQTT a un hub centrale.

L'irrigazione verrà aggiunta solo dopo aver raccolto dati sufficienti per
calibrare bene i sensori ed evitare attivazioni errate.

## Architettura prevista

```text
Sensori vaso ─> Nodo ESP32 1 ─┐
Sensori vaso ─> Nodo ESP32 2 ─┼── Wi-Fi / MQTT ──> Raspberry Pi ──> Telegram
Sensori vaso ─> Nodo ESP32 3 ─┤                         ├─ broker MQTT
Sensori vaso ─> Nodo ESP32 4 ─┘                         ├─ storico dati
                                                        └─ bot e comandi
```

Il Raspberry Pi, acceso in modo permanente, ospiterà il broker MQTT, il
servizio che conserva e interpreta i dati e il bot Telegram. Gli ESP32 non
espongono direttamente un bot: pubblicano letture e ricevono comandi tramite
MQTT. Questo evita di duplicare token Telegram, logica e sicurezza su quattro
nodi.

## Cosa fa già questa base

1. avvia la porta seriale a 115200 baud;
2. inizializza il bus I2C sui pin SDA 21 e SCL 22;
3. cerca e stampa gli indirizzi dei dispositivi I2C collegati;
4. legge temperatura e umidita' dall'SHT31-D ogni due secondi;
5. legge i quattro canali dell'ADS1115, quando collegato;
6. tenta la connessione Wi-Fi senza bloccare il programma;
7. continua a funzionare offline se le credenziali non sono ancora presenti.

## Preparazione

Il progetto usa [PlatformIO](https://platformio.org/) con framework Arduino.

1. Installa Visual Studio Code e l'estensione PlatformIO IDE, oppure la CLI di
   PlatformIO.
2. Copia `include/secrets.example.h` in `include/secrets.h`.
3. Inserisci in `secrets.h` nome e password della tua rete Wi-Fi.
4. Collega l'ESP32 via USB.
5. Compila e carica il firmware.
6. Apri il monitor seriale a 115200 baud.

### Configurazione MQTT

Per abilitare la comunicazione, aggiungi in `include/secrets.h` le quattro
impostazioni MQTT presenti in `include/secrets.example.h`: host del broker,
porta e, se necessari, username e password. Se `MQTT_BROKER_HOST` resta vuoto
o non viene definito, il nodo continua a funzionare offline.

Il nodo usa questi topic:

- `plants/plant-node-01/state`: stato `online`/`offline`, inviato retained;
- `plants/plant-node-01/measurements`: misure SHT31 e ADS1115 ogni 10 secondi;
- `plants/plant-node-01/config`: comandi JSON di calibrazione.

Esempi di messaggi da pubblicare su `config`:

```json
{"channel":0,"dry":2.700,"wet":1.250,"threshold":35}
```

Il messaggio aggiorna solo i campi presenti e li salva in NVS. Per ripristinare
il canale ai valori iniziali:

```json
{"channel":0,"reset":true}
```

Il Last Will MQTT pubblica `state=offline` se il nodo perde la connessione
senza poter chiudere la sessione. Wi-Fi, sensori e calibrazioni continuano a
funzionare indipendentemente dalla disponibilita' del broker.

### Broker MQTT locale su macOS

Per installare Mosquitto con Homebrew:

```bash
brew install mosquitto
```

Per un test locale con log visibili, avvia il broker manualmente:

```bash
mosquitto -v
```

Il broker ascolta sulla porta `1883`. Lascia questa finestra aperta e, in un
secondo terminale, ascolta i messaggi del nodo:

```bash
mosquitto_sub -h localhost -p 1883 -t 'plants/#' -v
```

Per ricevere i messaggi dall'ESP32, il broker deve accettare connessioni dalla
rete locale. Se Mosquitto stampa un avviso sulla configurazione del listener,
crea `~/mosquitto-debug.conf` con:

```text
listener 1883 0.0.0.0
allow_anonymous true
persistence false
log_type all
```

Avvialo con:

```bash
mosquitto -c ~/mosquitto-debug.conf -v
```

Trova l'indirizzo IP del Mac e riportalo in `MQTT_BROKER_HOST` dentro
`include/secrets.h`:

```bash
ipconfig getifaddr en0
```

Per esempio, se il comando restituisce `192.168.1.20`:

```cpp
#define MQTT_BROKER_HOST "192.168.1.20"
#define MQTT_BROKER_PORT 1883
```

In questo caso puoi ascoltare anche usando l'indirizzo del Mac:

```bash
mosquitto_sub -h 192.168.1.20 -p 1883 -t 'plants/#' -v
```

Per inviare una calibrazione dal Mac:

```bash
mosquitto_pub -h 192.168.1.20 -p 1883 \
      -t 'plants/plant-node-01/config' \
      -m '{"channel":0,"threshold":40}'
```

Per avviare Mosquitto come servizio in background:

```bash
brew services start mosquitto
```

Per fermarlo o controllarne lo stato:

```bash
brew services stop mosquitto
brew services list
```

`allow_anonymous true` e' adatto solo al debug nella rete locale: non esporre
questa configurazione su Internet.

Le credenziali reali non vengono salvate nel repository perché `secrets.h` è
presente in `.gitignore`.

## Primo cablaggio I2C

Per il primo test basta collegare un solo sensore, per esempio SHT31-D:

| ESP32 | SHT31-D |
|---|---|
| 3V3 | VIN/VCC |
| GND | GND |
| GPIO 21 | SDA |
| GPIO 22 | SCL |

Al riavvio lo scanner dovrebbe trovare normalmente l'indirizzo `0x44`.
Verifica comunque le etichette stampate sul tuo modulo prima di alimentarlo.

Per questo specifico modulo SHT31 collega inoltre `AD` a `GND`: seleziona
l'indirizzo `0x44`. Il pin `AL` resta scollegato.

## Cablaggio ADS1115 e primo test

L'ADS1115 e l'SHT31 condividono lo stesso bus I2C: SDA e SCL vanno quindi
collegati agli stessi due pin dell'ESP32, in parallelo.

| ESP32 | ADS1115 |
|---|---|
| 3V3 | VDD / VCC |
| GND | GND |
| GPIO 21 (`D21`) | SDA |
| GPIO 22 (`D22`) | SCL |
| GND | ADDR |

Lascia `ALRT` scollegato. Con `ADDR` a massa l'indirizzo dell'ADS1115 e'
`0x48`, diverso da quello dell'SHT31 (`0x44`), percio' i due moduli possono
stare sullo stesso bus senza conflitti.

Per un test sicuro collega prima `A0` a `GND`: nel monitor comparira' circa
`A0: 0.000 V`. Poi spostalo su `3V3`: leggerai circa `A0: 3.300 V`.
Non collegare agli ingressi A0-A3 tensioni superiori a 3,3 V quando il modulo
e' alimentato a 3,3 V. Gli altri canali, se lasciati scollegati, fluttuano e i
loro valori non sono significativi.

## Cablaggio BH1750 e primo test

Il BH1750 condivide il bus I2C con SHT31-D e ADS1115. Collega il modulo in
parallelo a SDA, SCL, 3V3 e GND:

| ESP32 | BH1750 |
|---|---|
| 3V3 | VCC |
| GND | GND |
| GPIO 21 (`D21`) | SDA |
| GPIO 22 (`D22`) | SCL |

Lascia il pin `ADDR` scollegato per usare l'indirizzo `0x23` configurato nel
firmware. Se il modulo collega `ADDR` a VCC, aggiorna `BH1750_ADDRESS` in
`include/config.h` a `0x5C`. Il monitor seriale deve mostrare il dispositivo
allo scan I2C e una riga `[BH1750] Luminosita' ... lux` ogni due secondi.
Le misure valide vengono pubblicate nel campo `light.lux` del topic
`measurements` e conservate nello storico dell'hub.

## Calibrazione umidita' terreno

Il firmware converte ogni lettura in percentuale con la curva iniziale
condivisa da `A0` a `A3`: `2,70 V = 0%` (terreno asciutto) e `1,25 V = 100%`
(terreno ben bagnato). I valori oltre gli estremi vengono limitati a `0-100%`.
La scelta e' una base provvisoria per sensori capacitivi a 3,3 V: prima di
basare avvisi o irrigazione sulle percentuali, rileva sul monitor seriale i due
valori del tuo sensore e aggiorna `SOIL_DRY_VOLTAGE` e
`SOIL_WET_VOLTAGE` in `include/config.h`. Esempio di log:

```text
[ADS1115] A0: 1.850 V, 59% A1: 2.700 V, 0% A2: 1.250 V, 100% A3: 2.100 V, 41%
```

### Calibrazione persistente per vaso

Il nodo conserva in NVS la calibrazione dei singoli canali: resta quindi valida
anche dopo riavvii o aggiornamenti del firmware. Nel monitor seriale imposta il
terminatore di riga su `Newline` e invia, ad esempio:

```text
cal show
cal A0 dry 2.700
cal A0 wet 1.250
cal A0 threshold 35
cal A0 reset
```

Misura `dry` con il sensore nel terriccio asciutto e `wet` nel terriccio ben
bagnato. `threshold` e' una percentuale tra 0 e 100, predisposta per gli avvisi
futuri. `reset` elimina dalla NVS soltanto la calibrazione del canale indicato e
riporta ai valori iniziali di `config.h`.

## Roadmap

### Fase 1 — Primo nodo: misure affidabili (adesso)

- [x] Base ESP32, seriale, Wi-Fi e scanner I2C
- [x] Lettura SHT31-D
- [x] Driver e test ADS1115
- [x] Primo sensore terreno su A0 e raccolta dei valori dry/wet
- [x] Conversione Volt → percentuale per ogni canale A0-A3 (calibrazione
      iniziale condivisa)
- [x] Calibrazione locale persistente per ogni vaso: `dry`, `wet` e soglia
      percentuale, salvati nella memoria NVS dell'ESP32
- [x] Lettura BH1750 e pubblicazione della luminosita' in lux
- [ ] Test con tutti e quattro i sensori terreno e denominazione dei vasi
- [ ] Stabilizzazione: media delle letture, gestione errori e log leggibili

### Fase 2 — Comunicazione del primo nodo

- [x] Client MQTT sul nodo `plant-node-01`
- [x] Pubblicazione periodica di misure, percentuali e stato del nodo
- [x] Ricezione di configurazioni MQTT, inclusa la calibrazione dei vasi
- [x] Il nodo continua a misurare con le ultime calibrazioni anche se Wi-Fi,
      Raspberry o MQTT non sono disponibili

### Fase 3 — Raspberry Pi: hub centrale

- [x] Installazione del broker MQTT sul Raspberry Pi
- [x] Servizio hub che riceve i dati dei nodi
- [x] Storico locale delle ultime misure e stato dei nodi in SQLite
- [x] Bot Telegram ospitato sul Raspberry Pi, con più utenti autorizzabili
- [x] Comandi Telegram → hub → MQTT → nodo, ad esempio calibrazione dry/wet,
      soglie e richiesta dello stato

Le istruzioni per installare l'hub sono in [`hub/README.md`](hub/README.md).
Il broker del Raspberry usa l'indirizzo `192.168.1.10`; aggiorna lo stesso
indirizzo in `include/secrets.h` prima di caricare il firmware.

### Fase 4 — Dati utili e identita' delle piante

- [x] Salvare lo storico completo delle misure con timestamp, invece di
      conservare soltanto l'ultimo valore
- [~] Dare un nome leggibile a ciascun nodo (ad esempio `Balcone nord` o
      `Serra`), mantenendo comunque il suo ID tecnico MQTT; la configurazione
      testuale attuale deve essere sostituita da un flusso guidato e validato
- [~] Dare un nome leggibile a ciascun vaso/canale (ad esempio `Basilico
      cucina` o `Aloe balcone`), collegandolo al nodo corretto; il wizard deve
      impedire ID inesistenti, canali duplicati e nomi ambigui
- [x] Registrare per ogni vaso specie, posizione e note opzionali
- [x] Implementare il monitoraggio della temperatura dell'aria per nodo,
      conservando minimo, massimo, media e andamento giornaliero
- [x] Implementare il sensore BH1750 e misurare la luminosita' istantanea in lux
- [x] Conservare nello storico e mostrare i riepiloghi min/max/media/ultima
      lettura della luminosita'
- [ ] Calcolare durata e variazione dell'esposizione durante la giornata
- [ ] Inserire temperatura ed esposizione nel recap Telegram giornaliero delle
      08:00, evidenziando valori mancanti, anomali o fuori dai limiti configurati
- [ ] Analizzare temperatura, umidita' dell'aria ed esposizione per descrivere
      l'ambiente circostante e individuare condizioni persistenti sfavorevoli
      alle piante
- [ ] Distinguere sensore non configurato, dato vecchio, nodo offline e dato
      anomalo: nessuno di questi casi deve causare un consiglio di irrigazione
- [ ] Aggiungere comandi o configurazione per impostare soglia, durata minima
      tra due avvisi e fascia oraria di notifica
- [~] Aggiungere comandi guidati per impostare nome del nodo e del vaso, con
      specie, posizione e note; includere tastiere inline, conferme e `/annulla`
- [x] Esporre riepiloghi delle ultime 24 ore/7 giorni, con umidita' minima,
      massima, media e ultima lettura
- [ ] Configurare durata minima tra due avvisi e fascia oraria di notifica
- [ ] Esporre riepiloghi giornalieri completi con andamento persistente
      dell'esposizione luminosa
- [ ] Registrare l'evento di irrigazione (manuale o automatico), quantita' o
      durata e note, per confrontare l'azione con la risposta del terreno

### Fase 5 — Avvisi e supervisione semiautomatica

- [ ] Creare un motore di alert per vaso: se l'umidita' resta sotto la soglia
      per piu' di 1 ora, inviare un warning Telegram indicando pianta, nodo,
      canale, valore attuale, soglia e ora d'inizio del problema
- [ ] Eseguire automaticamente il controllo degli alert tramite un job `cron`
      o scheduler persistente sul Raspberry, senza dipendere da un comando
      Telegram manuale
- [ ] Usare isteresi, cooldown e deduplicazione: un warning non va ripetuto a
      ogni misura e si deve inviare un messaggio di rientro quando il valore
      torna sopra la soglia
- [ ] Non notificare durante un'interruzione del nodo o con letture stale;
      inviare invece un avviso distinto per nodo offline o sensore guasto
- [ ] Aggiungere una coda di notifiche persistente e riprovare gli invii
      Telegram falliti senza perdere gli alert
- [ ] Consentire di marcare un alert come `visto`, `irrigato` o `rimandato`,
      mantenendo lo stato nel database
- [ ] Proporre `/irrigare NOME` come richiesta di azione con conferma esplicita,
      senza attivare pompe finche' l'utente non conferma

### Fase 6 — Telegram semplice per utenti non tecnici

- [x] Pubblicare una mappa completa dei comandi con `/help` e descrizioni in
      linguaggio naturale, senza richiedere conoscenza di nodi, topic o canali
- [x] Implementare il primo gruppo consultivo: `/start`, `/help`, `/piante`,
      `/pianta NOME`, `/stato`/`/status`, `/storico`, `/rinomina` e `/whoami`
- [ ] Implementare `/problemi`, `/impostazioni`, `/annulla` e la gestione
      guidata di comandi incompleti o piante inesistenti
- [ ] Implementare `/irrigare NOME`, `/conferma` e `/rimanda` solo quando la
      logica di irrigazione e gli alert delle fasi 5 e 8 saranno disponibili
- [x] Creare un menu principale Telegram con pulsanti inline: `Le mie piante`,
      `Stato nodi` e `Aiuto`
- [x] Permettere di scegliere una pianta da un elenco di pulsanti, senza
      richiedere nome tecnico del nodo o numero del canale
- [x] Mostrare la schermata base della pianta con ultima umidita' disponibile,
      nodo e canale
- [ ] Aggiungere nella schermata della pianta i pulsanti contestuali `Storico`,
      `Rinomina` e, quando disponibili, `Problemi` e `Irriga`
- [ ] Gestire callback Telegram con identificativi firmati e validare sempre
      che l'utente abbia accesso alla pianta selezionata
- [ ] Aggiungere navigazione `Indietro`, paginazione per molte piante e
      messaggi aggiornabili senza creare una nuova conversazione a ogni click
- [ ] Mantenere tutti i comandi testuali come alternativa, inclusi `/help`
      e `/whoami`
- [x] Rispondere con messaggi leggibili mostrando umidita', temperatura,
      umidita' dell'aria e stato del nodo quando i dati sono disponibili
- [ ] Distinguere in modo esplicito dato mancante, dato vecchio, nodo offline,
      sensore non configurato e lettura anomala
- [ ] Separare i permessi: consultazione per tutti gli utenti autorizzati,
      modifica impostazioni e irrigazione soltanto per gli amministratori
- [ ] Configurare orari silenziosi, preferenze per pianta e riepilogo quotidiano
      senza nascondere gli alert critici
- [ ] Inviare automaticamente ogni mattina alle 08:00 un recap Telegram con
      stato dei nodi, vasi sotto soglia, nodi offline, ultime letture e azioni
      consigliate
- [ ] Rendere configurabili ora e fuso orario del recap, evitando invii doppi
      dopo riavvii o cambi d'ora legale

### Fase 7 — Dal primo nodo ai quattro nodi

- [ ] Parametrizzare ID, canali e nomi dei vasi senza duplicare il firmware
- [ ] Assemblare e testare nodi `plant-node-02`, `03` e `04`
- [ ] Pagina/stato Telegram che raggruppa tutti i nodi
- [ ] Avvisi basati su soglie specifiche per vaso e pianta

### Fase 8 — Irrigazione controllata, solo dopo i dati

- [ ] Irrigazione manuale comandata dal Raspberry Pi
- [ ] Modalita' semi-automatica: proposta Telegram, conferma dell'utente,
      attivazione per durata limitata e registrazione dell'esito
- [ ] Verificare dopo l'irrigazione che l'umidita' sia salita; segnalare
      serbatoio vuoto, pompa bloccata o assenza di risposta del sensore
- [ ] Aggiungere limiti di sicurezza: durata massima, pausa minima tra cicli,
      numero massimo giornaliero, arresto manuale e blocco con dati anomali
- [ ] Solo alla fine valutare l'automazione completa, con modalita' manuale,
      simulazione e pulsante di arresto sempre disponibili
