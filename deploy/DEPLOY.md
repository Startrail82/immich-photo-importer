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

## 2. Stack aanmaken (vanuit Git in Dockhand)

Voeg in Dockhand de repo `https://github.com/Startrail82/immich-photo-importer` toe als Git-bron
(is de repo privé, geef dan een token of deploy key mee). Maak daarna op utility-gpu een stack
`immichphotoimporter` vanuit die repo, met compose-pad `docker-compose.yml` (in de root van de repo).

Zet de variabelen uit `deploy/.env.example` in Dockhand (het wachtwoord en de Immich-sleutel als
secret). Controleer ook dat de poorten vrij zijn:

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

Haal in Dockhand de nieuwste versie van de Git-bron op en deploy de stack opnieuw met een rebuild
van de image.

## Bewaren

Het volume `immichphotoimporter_pi_data` bevat de database en de iCloud-sessie (cookies). Behandel
het als geheim; neem het eventueel op in je back-uproutine.
