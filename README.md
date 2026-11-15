# Immich Photo Importer

Haalt foto's en video's van iCloud (en uit een Inbox-map), sorteert ze op datum naar de
Immich-bibliotheek op de NAS en geeft je iPhone-favorieten door als favoriet in Immich.

Status: **stap 1 (kern), stap 2 (Immich) en stap 3 (iCloud + planner) klaar**. Nog te bouwen:
webinterface, Docker-stack.

## Wat de kern doet

1. Scant drie bronmappen: `favorieten`, `overig` (iCloud) en `inbox` (zelf neergezette bestanden).
2. Bepaalt de datum per bestand: EXIF/QuickTime-datum, anders de oudste van wijzigings- en
   aanmaakdatum. Een video van een Live Photo volgt de datum van zijn foto.
3. Verplaatst naar `<doelmap>/JAAR/JAAR_MM/JAAR_MM_DD/`.
4. Identiek bestand (naam, grootte en hash) al aanwezig: het duplicaat wordt verwijderd.
   Zelfde naam maar andere inhoud: het nieuwe bestand krijgt `_1`, `_2`, ... en een melding.
5. Favorieten krijgen een sidecar `<bestand>.xmp` met `xmp:Rating="5"` en komen in de lijst
   van favorieten die nog in Immich gemarkeerd moeten worden.
6. Onbekende bestandstypes blijven staan en komen onder "aandacht nodig".

0. iCloud (optioneel, voor het sorteren): inloggen met `icloudpd --auth-only` (wacht zo nodig op
   een tweestapscode via de icloudpd-webpagina), daarna het album Favorites naar `favorieten`
   en alle foto's naar `overig`, met `--set-exif-datetime` en `--folder-structure none`. Mislukt
   de login of de favorietendownload, dan wordt `overig` niet gedownload; de inbox wordt altijd
   gesorteerd. Een favoriet zit ook in 'alle foto's': die tweede kopie wordt als identiek
   duplicaat verwijderd en de favorietmarkering blijft behouden.
7. Immich (optioneel, stap 5 van de run): start een scan van de externe bibliotheek, wacht tot
   Immich de nieuwe favorieten kent (zoeken op bestandspad) en zet ze via de API op favoriet.
   Favorieten die Immich nog niet kent blijven in de wachtrij voor de volgende run. Een foto
   die al eens als favoriet is gemarkeerd, wordt niet opnieuw gemarkeerd. Haal je hem in
   Immich zelf weer van favoriet, dan blijft dat zo.

Veiligheid: bestaat de doelmap niet (NAS niet gemount), dan stopt de run. Er loopt nooit
meer dan een run tegelijk. In de Inbox worden te recent gewijzigde bestanden overgeslagen.

## Gebruik

```
pip install -e ".[dev]"
photoimporter run --dry-run     # toont alleen wat er zou gebeuren
photoimporter run
```

Vereist `exiftool` (Debian/Ubuntu: `apt install libimage-exiftool-perl`).

## Configuratie (omgevingsvariabelen)

| Variabele | Standaard | Betekenis |
|---|---|---|
| `PI_SOURCE_FAVORITES` | `/import/iCloud/favorieten` | iCloud-favorieten |
| `PI_SOURCE_OTHER` | `/import/iCloud/overig` | rest van iCloud |
| `PI_SOURCE_INBOX` | `/import/Inbox` | zelf neergezette bestanden |
| `PI_TARGET` | `/target` | doelmap (Ongesorteerd) |
| `PI_DATA_DIR` | `/data` | SQLite-datastore en lock |
| `PI_MIN_AGE_INBOX_SECONDS` | `300` | Inbox: recent gewijzigde bestanden overslaan |
| `PI_MIN_AGE_ICLOUD_SECONDS` | `0` | idem voor de iCloud-mappen |
| `PI_LOG_RETENTION_DAYS` | `180` | logregels ouder dan dit worden opgeruimd |
| `PI_DRY_RUN` | `false` | alleen tonen, niets wijzigen |
| `PI_ICLOUD_USERNAME` | leeg | Apple ID; zonder gebruikersnaam en wachtwoord wordt iCloud overgeslagen |
| `PI_ICLOUD_PASSWORD` of `PI_ICLOUD_PASSWORD_FILE` | leeg | wachtwoord (of pad naar een secret-bestand) |
| `PI_ICLOUD_FAVORITES_ALBUM` | `Favorites` | exacte naam van het favorietenalbum (zie `icloud-check`) |
| `PI_ICLOUD_COOKIE_DIR` | `<data>/icloud-cookies` | bewaart de iCloud-sessie; moet een volume zijn |
| `PI_ICLOUD_AUTH_WAIT_SECONDS` | `1800` | hoe lang wachten op een tweestapscode |
| `PI_ICLOUD_DOWNLOAD_TIMEOUT_SECONDS` | `14400` | maximale duur per download |
| `PI_ICLOUD_WEB_PORT` | `8080` | poort waarop icloudpd de codepagina aanbiedt |
| `PI_ICLOUD_COMMAND` / `PI_ICLOUD_EXTRA_ARGS` | `icloudpd` / leeg | uitvoerbaar bestand en extra opties |
| `PI_SCHEDULE_TIME` / `PI_SCHEDULE_ENABLED` | `12:00` / `true` | dagelijkse starttijd (lokale tijd, stel `TZ` in) |
| `PI_IMMICH_URL` | leeg | bv. `https://immich.bluelight.one` |
| `PI_IMMICH_API_KEY` of `PI_IMMICH_API_KEY_FILE` | leeg | API-sleutel, of pad naar een bestand met de sleutel (Docker secret) |
| `PI_IMMICH_TARGET_PATH` | leeg | de doelmap zoals **Immich** hem ziet, bv. `/mnt/nfs/nas1/photo/Ongesorteerd` |
| `PI_IMMICH_LIBRARY_ID` | leeg | optioneel; standaard wordt de bibliotheek gezocht op importpad |
| `PI_IMMICH_WAIT_SECONDS` | `600` | hoe lang wachten tot Immich nieuwe bestanden kent |
| `PI_IMMICH_POLL_SECONDS` | `15` | pauze tussen twee pogingen |

De Immich-koppeling is alleen actief als URL, sleutel en pad alle drie zijn ingesteld.

### API-sleutel

Maak in Immich een sleutel met de rechten `asset.read`, `asset.update`, `library.read` en
`library.update` (Account Settings > API Keys). Controleer de verbinding met:

```
photoimporter immich-check
```

Dat commando test bereikbaarheid, leesrechten en of de bibliotheek gevonden wordt. De
schrijfrechten (`asset.update`, `library.update`) test het bewust niet, want dat zou iets wijzigen;
ontbreken ze, dan meldt de eerste echte run een duidelijke fout (HTTP 403) en blijven de
favorieten in de wachtrij staan.

## Tests

```
pytest
```


## Eerste keer inloggen bij iCloud

```
photoimporter icloud-check
```

Logt in, wacht op de tweestapscode (die voer je in via de webpagina van icloudpd op poort 8080
van de container), toont of het favorietenalbum gevonden is en bewaart de sessie in de
cookiemap. Apple laat de sessie na ongeveer twee maanden verlopen; de planner wacht dan tot de
code ingevoerd wordt en meldt dat in het log en in de status (`icloud_status`).

## Planner

```
photoimporter daemon
```

Draait elke dag op `PI_SCHEDULE_TIME` een volledige run. De webinterface (volgende stap) gebruikt
dezelfde planner voor de knop 'nu uitvoeren'.
