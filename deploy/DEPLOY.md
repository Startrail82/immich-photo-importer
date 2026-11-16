# Installatie op utility-gpu (Dockhand, Traefik, Authentik)

Hostnamen in deze handleiding (pas aan als je andere kiest):

| Doel | Hostnaam | Intern adres (voor de Authentik-outpost) |
| --- | --- | --- |
| Webinterface | `immichphotoimporter.bluelight.one` | `http://immichphotoimporter:8000` |
| iCloud-codepagina | `immichphotoimporter-icloud.bluelight.one` | `http://immichphotoimporter:8080` |

De app publiceert **geen poorten** op het LAN. Ze is alleen bereikbaar via een Authentik
proxy-outpost op utility-gpu (container `authentik-outpost`, poort 9000). Traefik wijst beide
hostnamen naar `http://192.168.100.81:9000`; de outpost doet de aanmelding en stuurt door naar de
app over het gedeelde Docker-netwerk `authentik-outpost`. Wie de interne URL kent, kan er dus niet
langs Authentik heen.

De codepagina van icloudpd gebruikt paden als `/static` en `/status`; ze werkt daarom alleen op een
eigen hostnaam, niet onder een pad-prefix van de webinterface.

## 1. Mappen op de NAS (op utility-gpu)

```bash
mkdir -p /mnt/nfs/nas1/photo/Import/iCloud/favorieten /mnt/nfs/nas1/photo/Import/iCloud/overig /mnt/nfs/nas1/photo/Import/Inbox
```

Losse bestanden die nu rechtstreeks in `Import/iCloud` staan, worden niet verwerkt. Verplaats ze
naar `Import/Inbox`.

## 2. Stack aanmaken (vanuit Git in Dockhand)

Maak eerst het gedeelde netwerk aan (eenmalig, op utility-gpu):

```bash
docker network create authentik-outpost
```

Voeg in Dockhand de repo `https://github.com/Startrail82/immich-photo-importer` toe als Git-bron
(is de repo privé, geef dan een token of deploy key mee). Maak daarna op utility-gpu een stack
`immich-photo-importer` vanuit die repo, met compose-pad `docker-compose.yml` (in de root van de repo).
Let op: kies in Dockhand de omgeving **utility-gpu**.

Zet de variabelen uit `deploy/.env.example` in Dockhand (het wachtwoord en de Immich-sleutel als
secret). `ICLOUD_FAVORITES_ALBUM` moet exact de naam van je album zijn (Engelstalig: `Favorites`).
`ICLOUD_LIBRARY` mag leeg blijven: bibliotheken kies je daarna in de webinterface (Instellingen).

Maak ook de outpost-stack aan (zie punt 4).

## 3. Immich API-sleutel

Immich > Account Settings > API Keys: nieuwe sleutel met `asset.read`, `asset.update`,
`library.read` en `library.update` (twijfel je, kies alle rechten). Zet hem in `.env`.

## 4. Authentik (proxy-outpost)

1. Applications > Outposts > Create: naam `utility-gpu`, type **Proxy**, geen integratie (manuele
   deployment). Selecteer de twee applicaties van de Photo Importer en bewaar het token van de outpost.
2. Per hostnaam een Application met een **Proxy Provider** in modus **Proxy** (niet *Forward auth*),
   met als externe host `https://immichphotoimporter.bluelight.one` (intern
   `http://immichphotoimporter:8000`), resp. `https://immichphotoimporter-icloud.bluelight.one`
   (intern `http://immichphotoimporter:8080`).
3. Maak in Dockhand (omgeving utility-gpu) de stack `authentik-outpost` met de compose uit
   `deploy/outpost/docker-compose.yml` en de variabele `AUTHENTIK_TOKEN` als secret. De image-tag
   moet gelijk zijn aan die van de Authentik-server en -worker.

## 5. Traefik Manager

Twee routes (HTTPS, je Let's Encrypt-resolver) naar `http://192.168.100.81:9000`, **zonder** de
forward-auth middleware (de outpost doet de aanmelding), plus DNS-records naar je Traefik. Zet de
Host-header niet om: de outpost herkent de toepassing aan de hostnaam.

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

De codepagina bestaat alleen zolang icloudpd op een code wacht; zonder lopende login geeft de
outpost dus "Bad Gateway". Dat is normaal.

Open daarna het tabblad **Instellingen** in de webinterface en kies:

- **Planning:** een cron-expressie (standaard `0 12 * * *`, dagelijks 12:00), met uitleg en de
  eerstvolgende uitvoeringen.
- **iCloud-bibliotheken:** klik "Bibliotheken ophalen" en vink de persoonlijke en/of gedeelde
  bibliotheek aan. Meldt Apple dat de bibliotheek nog niet geïndexeerd is, probeer het later opnieuw.
- **Wissen in iCloud:** standaard uit. Aan = na het downloaden wordt alles ouder dan X dagen uit
  iCloud gewist, **ook van je iPhone** (30 dagen in "Recent verwijderd").

De instellingen worden bewaard in het datavolume en gaan voor op de variabelen in Dockhand (die
blijven de beginwaarden). Ze gelden ook voor `docker exec ... photoimporter run`.

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

Het volume `immich-photo-importer_pi_data` bevat de database en de iCloud-sessie (cookies). Behandel
het als geheim; neem het eventueel op in je back-uproutine.
