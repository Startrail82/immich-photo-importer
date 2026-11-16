# Changelog

Every commit bumps the version (minor for features, patch for fixes).

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
