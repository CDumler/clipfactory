# ClipFactory - Einrichtungsanleitung fuer macOS und Server

Stand: 6. August 2026

Diese Anleitung ist die gepflegte Version. Sie ist fuer drei Faelle gebaut:

1. Du richtest alles auf einem Mac ein.
2. Du machst einen lokalen Smoke-Test auf dem Mac.
3. Du laesst ClipFactory danach 24/7 auf einem Linux-Server laufen.

Wichtige Grundregel:

- Alles mit `/root/...` laeuft nur auf dem Server, nachdem du per `ssh root@<SERVER-IP>` eingeloggt bist.
- Telegram-Befehle wie `/id` und `/status` schreibst du im Chat mit deinem Bot, nicht im Terminal.
- Wenn etwas unklar ist, starte auf der Zielmaschine immer mit `bash ./doctor.sh`.

## Was dieses Projekt heute wirklich kann

- Twitch-Clips finden, transkribieren, von KI bewerten und zur Freigabe per Telegram schicken.
- Nach Freigabe automatisch rendern und hochladen.
- YouTube laeuft direkt aus dem Projekt.
- Instagram ist optional und braucht eine oeffentliche HTTPS-URL.
- TikTok ist optional; ohne TikTok-Audit landet der Upload als Entwurf in der TikTok-App.

## Bevor du startest

Du hast zwei sinnvolle Betriebsarten:

- Empfohlen: Linux-Server fuer echten 24/7-Betrieb.
- Optional: lokaler Mac fuer Test oder Dauerbetrieb, wenn Docker Desktop laeuft und der Mac nicht schlaeft.

Wenn du einfach nur dem empfohlenen Weg folgen willst: Nutze den Server fuer den Betrieb und den Mac nur fuer Browser-, SSH- und OAuth-Schritte.

## Schritt 1: Projektordner auf dem Mac vorbereiten

Auf deinem Mac:

```bash
cd <PFAD_ZU_CLIPFACTORY>
cp .env.example .env
mkdir -p data secrets/de secrets/en
```

Noch nicht `bash ./doctor.sh` starten, solange auf diesem Geraet kein Docker installiert ist oder du gar nicht lokal betreiben willst.

## Schritt 2: Laufzeit-Maschine vorbereiten

### 2A. Lokaler Mac als Laufzeit-Maschine

Wenn der Mac selbst die Container ausfuehren soll:

1. Docker Desktop fuer Mac installieren und einmal starten.
2. Im Terminal pruefen:

```bash
docker compose version
```

3. Wenn du nicht nur testest, sondern lokal dauerhaft betreiben willst: Sorge dafuer, dass der Mac nicht schlafen geht.

### 2B. Linux-Server als Laufzeit-Maschine (empfohlen)

Wenn du 24/7 willst, nimm einen kleinen Linux-Server. Stand 6. August 2026 ist bei Hetzner eine Klasse mit mindestens `4 vCPU` und `8 GB RAM` passend, z. B. aktuelle Shared-Modelle wie `CX33` oder `CAX21`, jeweils mit Ubuntu `24.04`.

Auf deinem Mac, aus dem Ordner ueber `clipfactory`:

```bash
cd <PFAD_ZUM_ORDNER_DER_CLIPFACTORY_ENTHAELT>
scp -r clipfactory root@<SERVER-IP>:/root/
ssh root@<SERVER-IP>
cd /root/clipfactory
```

Auf dem Server Docker Engine und Compose nach offizieller Docker-Doku installieren:

```bash
apt remove -y docker.io docker-compose docker-compose-v2 podman-docker containerd runc || true
apt update
apt install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker compose version
```

## Schritt 3: Twitch-Zugangsdaten

Stand heute verlangt Twitch fuer die App-Registrierung ein bestaetigtes Konto plus `2FA`.

1. Gehe zu `https://dev.twitch.tv/console`.
2. `Applications` -> `Register Your Application`.
3. Werte:
   - Name: frei waehlbar, z. B. `clipfactory`
   - OAuth Redirect URL: `http://localhost`
   - Category: beliebig passend, z. B. `Application Integration`
4. Danach bei der App auf `Manage`.
5. In `.env` eintragen:
   - `TWITCH_CLIENT_ID=...`
   - `TWITCH_CLIENT_SECRET=...`

## Schritt 4: Telegram-Bot

1. In Telegram `@BotFather` oeffnen.
2. `/newbot` ausfuehren.
3. Token in `.env` bei `TELEGRAM_BOT_TOKEN=` eintragen.
4. Dem neuen Bot einmal eine echte Nachricht schicken oder `Start` druecken.

Fuer die Chat-ID hast du zwei Wege:

- Bevorzugt vor dem ersten Start:

```text
https://api.telegram.org/bot<DEIN-TOKEN>/getUpdates
```

Wenn dort ein Eintrag mit `"chat":{"id":123456789,...}` auftaucht, trage diese Zahl als `TELEGRAM_CHAT_ID=` in `.env` ein.

- Fallback nach dem Start:
  Starte den Stack spaeter normal, schreibe dem Bot `/id`, und trage die Antwort danach in `.env` ein.

Wenn `getUpdates` nur `{"ok":true,"result":[]}` zeigt, ist das kein Fehler. Das bedeutet nur, dass Telegram fuer diesen Bot noch keine abrufbaren Updates hat oder dass die laufende App sie bereits abholt.

## Schritt 5: YouTube-Upload

Wichtige Aenderungen, Stand August 2026:

- Seit dem 1. Juni 2026 hat die YouTube Data API getrennte Standard-Quoten fuer `videos.insert` und `search.list`.
- Ein neues Projekt hat standardmaessig `100` Video-Uploads pro Tag.
- Laut aktueller `videos.insert`-Doku werden Uploads aus unbestaetigten Projekten, die nach dem 28. Juli 2020 erstellt wurden, standardmaessig auf `private` beschraenkt, bis das Projekt auditiert wurde.

Deshalb ist in `.env.example` standardmaessig gesetzt:

```dotenv
YOUTUBE_PRIVACY_STATUS_DE=private
YOUTUBE_PRIVACY_STATUS_EN=private
```

Stell erst auf `public` oder `unlisted`, wenn dein Projekt das wirklich darf.

### 5A. Google Cloud Projekt

1. `https://console.cloud.google.com/`
2. Neues Projekt erstellen.
3. `APIs & Dienste` -> `Bibliothek` -> `YouTube Data API v3` -> `Aktivieren`.
4. `APIs & Dienste` -> `OAuth-Zustimmungsbildschirm`
   - User Type: `Extern`
   - App-Name + E-Mail eintragen
   - Dich selbst als Testnutzer hinzufuegen
5. `Anmeldedaten` -> `Anmeldedaten erstellen` -> `OAuth-Client-ID`
   - Anwendungstyp: `Desktop-App`
   - JSON herunterladen
   - Datei lokal als `client_secret.json` ablegen

Ein einziges Google-Projekt reicht heute meist locker fuer DE + EN. Zwei Projekte sind nur noch fuer saubere Trennung oder spaetere Skalierung sinnvoll, nicht mehr wegen einer 6-Uploads-pro-Tag-Grenze.

### 5B. token.json lokal erzeugen

Auf deinem Mac:

```bash
cd <PFAD_ZU_CLIPFACTORY>
python3 -m pip install google-auth-oauthlib
python3 get_youtube_token.py
```

Es oeffnet sich ein Browserfenster. Mit dem Google-Konto des Zielkanals anmelden und Zugriff erlauben. Danach liegt `token.json` im Projektordner.

### 5C. Dateien in die Profilordner legen

Fuer DE:

```bash
mv token.json secrets/de/token.json
```

Fuer EN denselben Ablauf mit dem Konto des zweiten Kanals wiederholen und danach:

```bash
mv token.json secrets/en/token.json
```

`client_secret.json` brauchst du zur Laufzeit nicht im Container. Hebe die Datei lokal auf, falls du den Token spaeter neu erzeugen musst.

## Schritt 6: Instagram Reels (optional)

Instagram ist heute nur sinnvoll, wenn du einen oeffentlich erreichbaren Server mit HTTPS hast. Fuer einen reinen lokalen Mac-Test ohne oeffentliche URL ueberspringst du diesen Schritt.

### 6A. Meta/Instagram vorbereiten

1. Meta Developer App anlegen, Typ `Business`.
2. Ein Instagram Professional Account ist Pflicht: `Business` oder `Creator`.
3. Das Instagram-Konto mit einer Facebook-Seite verknuepfen.
4. Einen User- oder Page-Token mit den noetigen Rechten erzeugen:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_show_list`
   - `business_management`
5. `me/accounts` aufrufen, um die passende `page_access_token` zu bekommen.
6. `/{PAGE_ID}?fields=instagram_business_account` aufrufen, um die `IG_USER_ID` zu finden.

In `.env`:

```dotenv
IG_USER_ID_DE=
IG_ACCESS_TOKEN_DE=
IG_USER_ID_EN=
IG_ACCESS_TOKEN_EN=
PLATFORMS_DE=youtube,instagram
PLATFORMS_EN=youtube,instagram
```

Das Projekt verwendet fuer Meta bewusst die Variable `META_GRAPH_API_VERSION`, weil Meta seine Versionen regelmaessig hochzieht. Falls Meta dir einen Versionsfehler meldet, hebe diesen Wert auf die in der offiziellen Meta-Instagram-Doku bzw. im offiziellen Instagram-Postman-Workspace gezeigte stabile Version an.

### 6B. Oeffentliche HTTPS-URL

Wenn du schon eine Domain plus Reverse-Proxy hast, reicht:

```dotenv
PUBLIC_BASE_URL=https://<DEINE-DOMAIN>
```

Wenn du noch nichts hast, ist der einfachste Weg fuer einen Server:

1. Bei DuckDNS eine Subdomain anlegen.
2. DNS auf die Server-IP zeigen lassen.
3. In `.env` setzen:

```dotenv
PUBLIC_HOSTNAME=<DEINE-SUBDOMAIN>.duckdns.org
```

4. Spaeter den Reverse-Proxy mitstarten:

```bash
docker compose --profile public up -d caddy
```

Der neue `caddy`-Service im Projekt uebernimmt dann HTTPS und leitet intern an ClipFactory weiter.

## Schritt 7: TikTok (optional)

1. `https://developers.tiktok.com/`
2. App anlegen.
3. Produkt `Content Posting API` aktivieren.
4. Einen User Access Token mit Scope `video.upload` erzeugen.
5. In `.env` eintragen:

```dotenv
TIKTOK_ACCESS_TOKEN_DE=
TIKTOK_ACCESS_TOKEN_EN=
PLATFORMS_DE=youtube,tiktok
PLATFORMS_EN=youtube,tiktok
```

Wichtig: Der aktuelle Upload-Flow dieses Projekts nutzt TikToks Inbox-/Draft-Mechanik. Das bedeutet: Der Upload landet beim Nutzer in der TikTok-App und wird dort final veroeffentlicht. Das ist derzeit erwartetes Verhalten, solange deine App nicht fuer vollautomatisches Posting freigegeben ist.

## Schritt 8: OpenAI-API-Key

1. `https://platform.openai.com/api-keys`
2. API-Key erzeugen.
3. In `.env` eintragen:

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
```

Standardmaessig nutzt das Projekt fuer das Clip-Scoring `gpt-5-mini`. Das Modell bleibt absichtlich per `.env` konfigurierbar, damit du spaeter ohne Code-Aenderung wechseln kannst.

## Schritt 9: Starten und testen

Ab hier arbeitest du auf der Maschine, die Docker wirklich ausfuehrt:

- lokaler Betrieb: dein Mac
- Server-Betrieb: der Linux-Server nach `ssh`

Im Projektordner:

```bash
bash ./start.sh
docker compose logs -f
```

Wenn du zuerst nur Discovery + Telegram testen willst und den Upload spaeter nachziehst, setze vor dem Start in `.env`:

```dotenv
PLATFORMS_DE=none
PLATFORMS_EN=none
```

Dann brauchst du fuer den ersten Start noch keine `token.json`-Dateien fuer YouTube.

Wenn du Instagram plus `PUBLIC_HOSTNAME` nutzt:

```bash
docker compose --profile public up -d caddy
```

Wenn der Dienst laeuft, pruefe:

```bash
curl http://127.0.0.1:8080/health
```

### Ersten Sammellauf manuell anstossen

Auf derselben Maschine wie Docker:

```bash
curl -X POST http://127.0.0.1:8080/run/collect/de
curl -X POST http://127.0.0.1:8080/run/collect/en
```

Wenn du den Server vom Mac aus direkt ansprichst statt per SSH auf ihm zu arbeiten, ersetze `127.0.0.1` durch `<SERVER-IP>`.

Nach einigen Minuten sollten Kandidaten im Telegram-Bot auftauchen. Gib 1-2 frei und teste dann:

```bash
curl -X POST http://127.0.0.1:8080/run/upload/de
```

Telegram-Befehle:

- `/status` zeigt die Pipeline-Statistik.
- `/id` zeigt dir deine Chat-ID.

Nochmal: Diese beiden Befehle gehoeren in den Telegram-Chat, nicht ins Terminal.

## Schritt 10: Alltag

- Zu `COLLECT_TIME_DE` / `COLLECT_TIME_EN` sammelt das System neue Clips.
- Du bestaetigst gute Kandidaten in Telegram.
- Zu `UPLOAD_TIMES_DE` / `UPLOAD_TIMES_EN` wird jeweils der beste freigegebene Clip verarbeitet und hochgeladen.
- Nicht verbrauchte freigegebene Clips bleiben in der Warteschlange.

## discovery.json anpassen

Datei: `discovery.json`

Wichtige Felder:

- `extra_streamers`: immer mitpruefen
- `blocklist`: niemals clippen
- `min_live_viewers`: Einstiegsschwelle fuer Discovery
- `facecams`: optionale Facecam-Koordinaten fuer besseres 9:16-Layout

Nach Aenderungen:

```bash
docker compose restart
```

Fuer Facecam-Messung:

1. Nach einem Sammellauf eine Clip-ID nehmen.
2. Im Browser oeffnen:
   - lokal: `http://127.0.0.1:8080/frame/<CLIP_ID>`
   - server: `http://<SERVER-IP>:8080/frame/<CLIP_ID>`
3. Werte in `discovery.json` unter `facecams` eintragen.

## Wartung

Nuetzliche Befehle:

```bash
bash ./doctor.sh
docker compose ps
docker compose logs -f
docker compose restart
docker compose up -d --build
```

Rohmaterial optional loeschen:

```bash
find data/raw -type f -mtime +14 -delete
```

## Hauefige Fehler und die direkte Loesung

### `cd: /root/clipfactory: no such file or directory`

Du bist auf deinem Mac, nicht auf dem Server. `/root/...` gibt es nur nach `ssh root@<SERVER-IP>`.

### `docker: command not found`

Auf dem Mac fehlt Docker Desktop oder auf dem Server fehlt Docker Engine.

### `{"ok":true,"result":[]}` bei Telegram `getUpdates`

Kein Fehler. Schicke dem Bot zuerst eine Nachricht oder nutze spaeter `/id`.

### `/status` im Terminal gibt `no such file or directory`

`/status` ist ein Telegram-Befehl, kein Shell-Befehl.

### YouTube-Uploads bleiben privat

Das ist bei neuen, nicht auditierten Google-Projekten erwartbar. Stand August 2026 weist Google in der `videos.insert`-Doku explizit darauf hin.

### Instagram klappt lokal auf dem Mac nicht

Fuer Instagram braucht Meta eine oeffentlich erreichbare HTTPS-Video-URL. Ohne Server, Domain und HTTPS ueberspringst du Instagram am besten zuerst.

### Ich weiss nicht, wo ich anfangen soll

Auf der Zielmaschine im Projektordner:

```bash
bash ./doctor.sh
```

Wenn `doctor.sh` sauber ist, dann:

```bash
bash ./start.sh
```
