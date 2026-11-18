# Changelog

Every commit bumps the version (minor for features, patch for fixes).

## 0.7.2

- Log tab: "Alles selecteren" button selects the whole visible log, so it is easy to copy and share.
- Files that are too recent to import (default 5 minutes unchanged) are no longer listed one by one;
  the scan now logs one line per source with the count, the age limit and a few example names.

## 0.7.1

- Settings tab: two balanced columns (max. 1300 px wide, centered) instead of a row of narrow
  cards; one column below 900 px.

## 0.7.0

- Web UI: the log and the list of earlier runs now have their own tabs (**Runs** and **Log**).
  Clicking a run in **Runs** opens its log in the **Log** tab (deep link `#log/<id>`).
- The layout now uses the full screen width and reflows to fewer columns on smaller screens
  (e.g. a 13" laptop); the log fills the window height.

## 0.6.0

- Inbox now has `favorieten` and `overig` subfolders (created automatically). Inbox files are always
  imported: no duplicate registry, a name conflict (even identical content) gives a renamed copy
  (`_1`, ...). Files in `Inbox/favorieten` get the rating sidecar and are marked as favorite in Immich.

## 0.5.4

- A file whose hash is in the registry is never placed again, even if it was moved, renamed or
  deleted on the target in the meantime. To re-add such a file, put it on the target by hand.

## 0.5.3

- Removed the "newest N photos" option (`--recent`, `PI_ICLOUD_RECENT`) and the "Full synchronisation"
  button again: every run now reads all photos in iCloud.

## 0.5.2

- `find-duplicates`: a plain name such as `IMG_5357.JPG` is no longer mistaken for a numbered copy,
  so the clean name is kept and `IMG_5357_1.JPG` is removed (not the other way round).

## 0.5.1

- `find-duplicates`: skips Synology `@eaDir`/`#recycle` folders, accepts an optional folder
  argument and prints progress.

## 0.5.0

- Duplicates: every imported file is recorded by hash in the database, so a photo that comes back
  under another name or extension, or after the NAS copy was modified (e.g. an embedded rating),
  is recognised. Fallback for older files: compare the image data hash within the day folder.
- Incremental downloads: only the newest N photos per run (`--recent`, setting / `PI_ICLOUD_RECENT`,
  default 500). First run, deleting in iCloud and the new "Full synchronisation" button read everything.
- New command `photoimporter find-duplicates`: lists duplicates on the target and deletes nothing
  unless `--apply` is given.

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
