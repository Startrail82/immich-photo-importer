# Installation (Docker, reverse proxy, Authentik)

This guide assumes a Docker host with a stack manager that can deploy from Git (for example
Dockhand), a reverse proxy (for example Traefik) and an Authentik instance. Adapt the host names;
the examples use these:

| Purpose | Host name | Internal address (for the Authentik outpost) |
| --- | --- | --- |
| Web UI | `photoimporter.example.com` | `http://immichphotoimporter:8000` |
| iCloud code page | `photoimporter-icloud.example.com` | `http://immichphotoimporter:8080` |

The app publishes **no ports** on the LAN. It is only reachable through an Authentik proxy outpost
on the same Docker host (container `authentik-outpost`, port 9000). The reverse proxy points both
host names to `http://<docker-host-ip>:9000`; the outpost handles the login and forwards to the
app over the shared Docker network `authentik-outpost`. Anyone who knows the internal URL still
cannot get around Authentik.

icloudpd's code page uses paths such as `/static` and `/status`, so it only works on a host name
of its own, not under a path prefix of the web UI.

## 1. Folders on the storage

```bash
mkdir -p /mnt/photos/Import/iCloud/favorieten /mnt/photos/Import/iCloud/overig /mnt/photos/Import/Inbox /mnt/photos/Unsorted
```

Loose files that sit directly in `Import/iCloud` are not processed. Move them to `Import/Inbox`. The subfolders `Import/Inbox/favorieten` and `Import/Inbox/overig` are created automatically: files you drop there are always imported (never discarded as duplicates), those in `favorieten` are marked as favorites.

In Immich, create an external library whose import path is the `Unsorted` folder as Immich sees it.

## 2. Create the stack (from Git)

Create the shared network first (once, on the Docker host):

```bash
docker network create authentik-outpost
```

Add this repository as a Git source in your stack manager, then create a stack from it with compose
path `docker-compose.yml` (in the repository root). If you use Dockhand, select the correct
environment (the Docker host that will run it).

Set the variables from `deploy/.env.example`. `IMPORT_DIR`, `TARGET_DIR` and `IMMICH_TARGET_PATH`
are required (the deployment fails if they are missing, so files can never end up in the wrong
place). The iCloud and Immich values can be left empty and entered in the web UI instead
(Settings > Connections). `ICLOUD_FAVORITES_ALBUM` must be the exact album name (English accounts:
`Favorites`).

## 3. Immich API key

Immich > Account Settings > API Keys: a new key with `asset.read`, `asset.update`, `library.read`
and `library.update` (when in doubt, choose all permissions).

## 4. Authentik (proxy outpost)

1. Applications > Outposts > Create: name it, type **Proxy**, no integration (manual deployment).
   Select the two Photo Importer applications and keep the outpost token.
2. Per host name, one Application with a **Proxy Provider** in mode **Proxy** (not *Forward auth*),
   with external host `https://photoimporter.example.com` (internal `http://immichphotoimporter:8000`)
   and `https://photoimporter-icloud.example.com` (internal `http://immichphotoimporter:8080`).
3. Create a stack `authentik-outpost` with the compose file from `deploy/outpost/docker-compose.yml`
   and the variable `AUTHENTIK_TOKEN` as a secret. Replace `<docker-host-ip>` and the Authentik URL.
   The image tag must match your Authentik server and worker version.

## 5. Reverse proxy

Two HTTPS routes to `http://<docker-host-ip>:9000`, **without** a forward-auth middleware (the
outpost does the login), plus DNS records. Do not rewrite the Host header: the outpost recognises
the application by host name.

## 6. Start and verify

Start the stack (the first time it builds the image) and check:

```bash
docker logs -f immichphotoimporter
```

Open the web UI, go to **Settings > Connections** and press **Test Immich** and **Test iCloud**.
The iCloud test may wait for a two-factor code: enter it on the code page
(`https://photoimporter-icloud.example.com`). That page only exists while icloudpd is waiting for
a code, so the outpost shows "Bad Gateway" otherwise. That is normal.

The same checks exist on the command line:

```bash
docker exec -it immichphotoimporter photoimporter immich-check
docker exec -it immichphotoimporter photoimporter icloud-check
```

Then configure, in **Settings**:

- **Planning:** a cron expression (default `0 12 * * *`, daily at 12:00), with an explanation and
  the next runs.
- **iCloud libraries:** press "Fetch libraries" and tick the personal and/or shared library. If
  Apple says the library is not indexed yet, try again later.
- **Deleting in iCloud:** off by default. On = after downloading, everything older than X days is
  deleted from iCloud, **also from your iPhone** (30 days in "Recently Deleted").

Settings are stored in the data volume and take precedence over the stack variables (which remain
the initial values). They also apply to `docker exec ... photoimporter run`.

Do a dry run (nothing is changed, nothing is downloaded):

```bash
docker exec -it immichphotoimporter photoimporter run --dry-run
```

Then start the first real run with the "Run now" button. The first time it fetches your whole
iCloud library, which can take hours (maximum per download: 4 hours). If a download is cut off,
the next run continues.

## Update

Pull the latest version of the Git source in your stack manager and redeploy the stack with an
image rebuild.

## What to keep safe

The volume `immich-photo-importer_pi_data` holds the database (including the iCloud password and
Immich key if you entered them in the UI) and the iCloud session cookies. Treat it as a secret and
include it in your backups.
