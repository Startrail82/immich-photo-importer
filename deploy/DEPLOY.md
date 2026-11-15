# Installatie op utility-gpu (Dockhand, Traefik, Authentik)

Hostnamen in deze handleiding (pas aan als je andere kiest):

| Doel | Hostnaam | Backend (Traefik, op utility) |
| --- | --- | --- |
| Webinterface | `immichphotoimporter.bluelight.one` | `http://192.168.100.81:8420` |
| iCloud-codepagina | `immichphotoimporter-icloud.bluelight.one` | `http://192.168.100.81:8421` |

De codepagina van icloudpd gebruikt paden als `/static` en `/status`; ze werkt daarom alleen op een
eigen hostnaam, niet onder een pad-prefix van de webinterface.

## 1. Mappen op de NAS (op utility-gpu)

```bash
mkdir -p /mnt/nfs/nas1/photo/Import/iCloud/favorieten /mnt/nfs/nas1/photo/Import/iCloud/overig /mnt/nfs/nas1/photo/Import/Inbox
```

Losse bestanden die nu rechtstreeks in `Import/iCloud` staan, worden niet verwerkt. Verplaats ze
naar `Import/Inbox`.

## 2. Stack aanmaken

Maak in Dockhand een stack `immichphotoimporter` op utility-gpu met de inhoud van
`deploy/docker-compose.yml`. Haal daarna de code en het `.env`-bestand op de VM:

```bash
cd /opt/stacks/immichphotoimporter && git clone https://github.com/Startrail82/immich-photo-importer.git app && cp app/deploy/.env.example .env && chmod 600 .env
```

Is de repo privé, log dan eerst in met `gh auth login` of gebruik een deploy key. Vul daarna
`.env` in (Apple-ID, wachtwoord, Immich-sleutel). Controleer ook dat de poorten vrij zijn:

```bash
ss -ltn | grep -E ':(8420|8421)\b' || echo "poorten vrij"
```

## 3. Immich API-sleutel

Immich > Account Settings > API Keys: nieuwe sleutel met `asset.read`, `asset.update`,
`library.read` en `library.update` (twijfel je, kies alle rechten). Zet hem in `.env`.

## 4. Authentik

Per hostnaam een Application met een **Proxy Provider** in modus *Forward auth (single
application)* en als externe host `https://immichphotoimporter.bluelight.one`, resp.
`https://immichphotoimporter-icloud.bluelight.one`. Koppel beide providers aan je bestaande
outpost. Gebruik je al een provider in modus *domain level* voor `bluelight.one`, dan hoef je
niets nieuws aan te maken.

## 5. Traefik Manager

Twee routes (HTTPS, je Let's Encrypt-resolver, dezelfde forward-auth middleware als je andere
diensten) volgens de tabel bovenaan, plus DNS-records naar je Traefik. Geen websockets nodig.

## 6. Starten en controleren

Start de stack in Dockhand (de eerste keer bouwt hij de image) en controleer:

```bash
docker logs -f immichphotoimporter
```

Controleer de Immich-koppeling:

```bash
docker exec -it immichphotoimporter photoimporter immich-check
```

Log in bij iCloud. Voer de tweestapscode in op `https://immichphotoimporter-icloud.bluelight.one`
en kijk of het favorietenalbum gevonden wordt:

```bash
docker exec -it immichphotoimporter photoimporter icloud-check
```

Doe een droge run (er wordt niets gewijzigd, ook niets gedownload):

```bash
docker exec -it immichphotoimporter photoimporter run --dry-run
```

Start daarna de eerste echte run met de knop "Nu uitvoeren" in de webinterface. De eerste keer
haalt hij je hele iCloud-bibliotheek binnen en dat kan uren duren (maximum per download: 4 uur).
Wordt de download afgebroken, dan gaat de volgende run verder.

## Update

```bash
cd /opt/stacks/immichphotoimporter/app && git pull
```

Bouw de stack daarna opnieuw in Dockhand (Rebuild / Redeploy).

## Bewaren

Het volume `immichphotoimporter_pi_data` bevat de database en de iCloud-sessie (cookies). Behandel
het als geheim; neem het eventueel op in je back-uproutine.
