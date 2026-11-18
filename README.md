# Immich Photo Importer

Downloads photos and videos from iCloud (and from a drop folder), sorts them by date into an
[Immich](https://immich.app) external library on your NAS, and passes your iPhone favorites on
to Immich as favorites.

It ships as a single Docker container with a small web UI (status, counters, "needs attention"
list, live log, settings, a "Run now" button). Installation: see [deploy/DEPLOY.md](deploy/DEPLOY.md).
Changes per version: see [CHANGELOG.md](CHANGELOG.md).

## What a run does

1. Scans three source folders: `favorieten` (iCloud favorites album), `overig` (the rest of
   iCloud) and `inbox` (files you drop in yourself, see below).
2. Determines each file's date: EXIF/QuickTime date, otherwise the older of the modification
   and creation time. The video part of a Live Photo follows its photo's date.
3. Moves it to `<target>/YYYY/YYYY_MM/YYYY_MM_DD/`.
4. Duplicate check. Every imported file is recorded in the database by its hash as it arrived. A
   file with a known hash is a duplicate, whatever its name, extension or folder, and even if the
   copy on the NAS was modified afterwards (for example Immich embedding a rating), moved or
   deleted: a file that was imported once is never placed again. To re-add one, put it on the
   target by hand. Without a
   record, a file in the same day folder whose image data (`exiftool` ImageDataHash) is identical
   is also a duplicate. Same name but really different content: the new file gets `_1`, `_2`, ...
   and a notice.
5. Favorites get a sidecar `<file>.xmp` with `xmp:Rating="5"` and are queued to be marked as
   favorite in Immich.
6. Unknown file types are left alone and listed under "needs attention".

iCloud (optional): logs in with `icloudpd --auth-only` (waits for a two-factor code if needed, via
icloudpd's own web page), then downloads the favorites album into `favorieten` and the photos into
`overig` (always all photos), with `--set-exif-datetime` and `--folder-structure none`. If login or the favorites
download fails, `overig` is not downloaded; the inbox is always sorted. A favorite is also part
of "all photos": that second copy is removed as an identical duplicate and the favorite flag is
kept. Several iCloud libraries (for example the personal and the shared library) can be selected
in the settings.

Optionally, iCloud assets can be deleted after they have been downloaded (see the settings; off
by default, and only items older than N days).

Immich (optional): starts a scan of the external library, waits until Immich knows the new
favorites (searching on file path) and marks them as favorite through the API. Favorites Immich
does not know yet stay queued for the next run. A photo that was marked once is not marked again;
if you un-favorite it in Immich, it stays that way.

Safety: if the target folder does not exist (NAS not mounted) the run stops. Only one run at a
time. In the inbox, recently modified files are skipped.

### Inbox: drop anything you want sorted

`Inbox/favorieten` and `Inbox/overig` (created automatically) are for files you add by hand. Whatever
is in `favorieten` is treated as a favorite (sidecar with rating 5, marked in Immich); `overig` and
loose files in the inbox are not. Inbox files are **always imported**: they bypass the duplicate
registry, and if a file with that name already exists in the day folder (even an identical one) the
new copy is renamed `_1`, `_2`, ... and listed under "needs attention". Use `iCloud/favorieten` only
for what comes from iCloud.

## Usage (without Docker)

```
pip install -e ".[dev]"
photoimporter run --dry-run     # only shows what would happen
photoimporter run
```

Requires `exiftool` (Debian/Ubuntu: `apt install libimage-exiftool-perl`) and, for iCloud,
[`icloudpd`](https://github.com/icloud-photos-downloader/icloud_photos_downloader).

## Commands

| Command | Purpose |
|---|---|
| `photoimporter run [--dry-run]` | one full run |
| `photoimporter daemon` | scheduler only (cron expression), no web UI |
| `photoimporter serve` | scheduler + web UI (the Docker default) |
| `photoimporter icloud-check` | log in to iCloud and check the favorites album |
| `photoimporter find-duplicates [folder] [--apply]` | list duplicates on the target or in one folder (image-data hash); deletes only with `--apply` |
| `photoimporter immich-check` | check the Immich connection step by step |

## Web UI

Two tabs. **Overview**: status, counters of the last run, the "needs attention" list, live log,
earlier runs and the "Run now" button. **Settings**:

- **Connections**: iCloud Apple ID, password, URL of the two-factor code page, Immich URL and API
  key. Password and key are write-only (stored, never shown again). Two test buttons check iCloud
  and Immich using the values in the form, even if not saved yet. Values saved here override the
  environment variables, which remain the fallback.
- **Planning**: a cron expression with a plain-language explanation and the next three runs.
- **iCloud libraries**: fetch the list from your account and tick the libraries to download, plus
  the name of the favorites album.
- **Edited photos**: iCloud keeps edits (color, crop) separate from the original. Choose to download
  the original (default), the edited version (or the original if nothing was edited), or both (the
  edited file gets an `-adjusted` suffix, so the photo appears twice in Immich). A photo that
  was imported before is recognised and not placed again. With "edited" the unedited original never reaches your NAS, so do
  not combine it with deleting in iCloud if you may want the original later.
- **Deleting in iCloud**: off by default. When on, assets older than N days are deleted from iCloud
  after download. This also removes them from your iPhone (30 days in "Recently Deleted").

There is deliberately no login of its own: put the UI behind a reverse proxy with authentication
(see DEPLOY.md). Requests that change something are refused if their `Origin` does not match the
host.

## Configuration (environment variables)

Most connection and schedule values can also be set in the web UI; the UI wins over these.

| Variable | Default | Meaning |
|---|---|---|
| `PI_SOURCE_FAVORITES` | `/import/iCloud/favorieten` | iCloud favorites |
| `PI_SOURCE_OTHER` | `/import/iCloud/overig` | rest of iCloud |
| `PI_SOURCE_INBOX` | `/import/Inbox` | files you drop in yourself |
| `PI_TARGET` | `/target` | target folder (the sorted tree) |
| `PI_DATA_DIR` | `/data` | SQLite store and lock |
| `PI_MIN_AGE_INBOX_SECONDS` | `300` | inbox: skip recently modified files |
| `PI_MIN_AGE_ICLOUD_SECONDS` | `0` | same for the iCloud folders |
| `PI_LOG_RETENTION_DAYS` | `180` | log lines older than this are pruned |
| `PI_DRY_RUN` | `false` | only show, change nothing |
| `PI_ICLOUD_USERNAME` | empty | Apple ID; without username and password iCloud is skipped |
| `PI_ICLOUD_PASSWORD` or `PI_ICLOUD_PASSWORD_FILE` | empty | password (or path to a secret file) |
| `PI_ICLOUD_FAVORITES_ALBUM` | `Favorites` | exact name of the favorites album (see `icloud-check`) |
| `PI_ICLOUD_LIBRARY` | empty | comma-separated libraries; empty = personal library |
| `PI_ICLOUD_VERSION` | `original` | `original`, `adjusted` (edited, or original if unedited) or `both` |
| `PI_ICLOUD_KEEP_RECENT_DAYS` | unset | set = delete from iCloud what is older than N days |
| `PI_ICLOUD_COOKIE_DIR` | `<data>/icloud-cookies` | keeps the iCloud session; must be a volume |
| `PI_ICLOUD_AUTH_WAIT_SECONDS` | `1800` | how long to wait for a two-factor code |
| `PI_ICLOUD_DOWNLOAD_TIMEOUT_SECONDS` | `14400` | maximum duration per download |
| `PI_ICLOUD_WEB_PORT` | `8080` | port where icloudpd serves the code page |
| `PI_ICLOUD_WEB_URL` | empty | public address of the code page; the "Enter code" button links here |
| `PI_ICLOUD_COMMAND` / `PI_ICLOUD_EXTRA_ARGS` | `icloudpd` / empty | executable and extra options |
| `PI_WEB_PORT` | `8000` | port of the web UI |
| `PI_SCHEDULE_CRON` | empty | cron expression (5 fields); overrides the two below |
| `PI_SCHEDULE_TIME` / `PI_SCHEDULE_ENABLED` | `12:00` / `true` | daily start time (local time, set `TZ`) |
| `PI_IMMICH_URL` | empty | e.g. `https://immich.example.com` |
| `PI_IMMICH_API_KEY` or `PI_IMMICH_API_KEY_FILE` | empty | API key, or path to a file holding it |
| `PI_IMMICH_TARGET_PATH` | empty | the target folder as **Immich** sees it, e.g. `/mnt/photos/Unsorted` |
| `PI_IMMICH_LIBRARY_ID` | empty | optional; by default the library is found by import path |
| `PI_IMMICH_WAIT_SECONDS` | `600` | how long to wait until Immich knows new files |
| `PI_IMMICH_POLL_SECONDS` | `15` | pause between two attempts |

The Immich link is only active when URL, key and target path are all set. The target path cannot
be set in the UI because it depends on how Immich mounts your storage.

### Immich API key

Create a key with the permissions `asset.read`, `asset.update`, `library.read` and
`library.update` (Account Settings > API Keys). Check the connection with the "Test Immich"
button or with `photoimporter immich-check`. The tests deliberately do not exercise the write
permissions, because that would change something; if they are missing, the first real run reports
a clear error (HTTP 403) and the favorites stay queued.

### First iCloud login

```
photoimporter icloud-check
```

Logs in, waits for the two-factor code (entered on icloudpd's web page, port 8080 in the
container), shows whether the favorites album was found and stores the session in the cookie
folder. Apple lets the session expire after roughly two months; the scheduler then waits for the
code and reports that in the log and in the status (`icloud_status`). Give the code page its own
route behind your authentication and set its address as the code-page URL, so the UI shows a
button as soon as a run is waiting for the code.

## Tests

```
pytest
```
