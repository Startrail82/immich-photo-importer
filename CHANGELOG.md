# Changelog

Every commit bumps the version (minor for features, patch for fixes).

## 0.4.5

- `PI_ICLOUD_KEEP_RECENT_DAYS` below 1 is refused at startup, and a stored `keep_days` of 0 falls
  back to 14 days. 0 means "delete everything from iCloud" and the settings tab already accepted
  only 1..3650, so the environment variable was the one way to wipe a library.
- A switch sent as text (`"false"`) is parsed as false instead of being true because a non-empty
  string is truthy; the API answers 422 with the field name for anything it cannot read.
- `PI_IMMICH_URL` and `PI_ICLOUD_WEB_URL` must start with `http://` or `https://`, so a typo shows
  up at startup instead of after the first run has moved files.

## 0.4.4

- The web API rejects a bad `limit` or `after` (for example `?limit=abc`) with a 400 and a message
  instead of closing the connection without an answer, and `?limit=-1` no longer returns every run
  ever recorded.

## 0.4.3

- A schedule that is valid but never occurs (`0 0 31 2 *`) is now refused when you save it, with
  an explanation, instead of being stored. Before this the planner thread crashed on it and no
  automatic run ever happened again until the container was restarted.
- The planner thread can no longer die from an unexpected error while planning: it logs and tries
  again, and a "Run now" during that recovery is not lost.

## 0.4.2

- A run no longer stops when exiftool is missing or fails on a batch: the files are still sorted,
  with the file date as the fallback, and the run reports `ok_met_fouten`. Before this a container
  without exiftool sorted nothing at all, even though the file date was already the documented
  fallback.

## 0.4.1

- Docs/UI: corrected how edited versions interact with files that are already on the NAS.

## 0.4.0

- Settings: choose which version of edited photos is downloaded: original (default), edited
  (`--size adjusted`, falls back to the original when nothing was edited) or both (the edited
  file gets an `-adjusted` suffix). Environment fallback: `PI_ICLOUD_VERSION`.

## 0.3.0

- Settings tab: connections (iCloud Apple ID, password, code-page URL; Immich URL and API key).
  Passwords and keys are write-only. Values saved in the UI override the environment variables.
- "Test iCloud" and "Test Immich" buttons that check the connection using the form values.
- Documentation is now in English; hard-coded personal hosts and paths were replaced by
  placeholders. Compose paths are now variables (`IMPORT_DIR`, `TARGET_DIR`, `IMMICH_TARGET_PATH`).
- MIT license added. The data database file is now created with mode 0600.

## 0.2.0 (not tagged)

- Settings tab: cron schedule, iCloud library selection, optional deletion in iCloud after N days.
- Fixes: runs wait for a running iCloud action; MFA banner also while fetching libraries.

## 0.1.0

- Core importer, Immich favorites, iCloud download, scheduler, web UI, Docker stack and
  Authentik outpost deployment.
