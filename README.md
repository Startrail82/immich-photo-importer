# Immich Photo Importer

Haalt foto's en video's van iCloud (en uit een Inbox-map), sorteert ze op datum naar de
Immich-bibliotheek op de NAS en geeft je iPhone-favorieten door als favoriet in Immich.

Status: **stap 1 (kern) klaar**. Nog te bouwen: iCloud-download (icloudpd), Immich-koppeling,
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

## Tests

```
pytest
```
