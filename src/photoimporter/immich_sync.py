"""Stap 5 van een run: Immich laten scannen en de favorieten markeren."""

from __future__ import annotations

import time
from pathlib import Path, PurePosixPath
from typing import Callable

from .config import Config
from .immich import ImmichClient, ImmichError
from .runlog import RunLog
from .sidecar import sidecar_path, write_rating_sidecar
from .store import Store

STEP = "6/6 Immich"


def to_immich_path(cfg: Config, host_path: Path) -> str:
    """Zet een pad in onze container om naar het pad zoals Immich het ziet."""
    relative = host_path.relative_to(cfg.target)
    return str(PurePosixPath(cfg.immich_target_path) / PurePosixPath(relative.as_posix()))


def from_immich_path(cfg: Config, immich_path: str) -> Path | None:
    """Omgekeerde van to_immich_path; None als het bestand niet onder onze doelmap valt."""
    try:
        relative = PurePosixPath(immich_path).relative_to(PurePosixPath(cfg.immich_target_path))
    except ValueError:
        return None
    return cfg.target.joinpath(*relative.parts)


def sync_immich(
    cfg: Config,
    store: Store,
    log: RunLog,
    stats,
    moved_any: bool,
    client: ImmichClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    if cfg.dry_run:
        log.info(STEP, "overgeslagen (droogloop)")
        return
    if not cfg.immich_enabled:
        log.info(STEP, "Immich-koppeling niet ingesteld, stap overgeslagen")
        return
    pending = store.list_pending_favorites()

    client = client or ImmichClient(cfg.immich_url, cfg.immich_api_key)
    try:
        if moved_any:
            library_id = cfg.immich_library_id or client.find_library_id(cfg.immich_target_path)
            if library_id is None:
                raise ImmichError(
                    f"geen Immich-bibliotheek gevonden met importpad {cfg.immich_target_path} "
                    "(of stel PI_IMMICH_LIBRARY_ID in)"
                )
            log.info(STEP, f"bibliotheekscan starten (bibliotheek {library_id})")
            client.scan_library(library_id)
        if pending:
            _mark_favorites(cfg, store, log, stats, client, pending, sleep, clock)
        _sync_rating_markers(cfg, store, log, stats, client)
    except ImmichError as exc:
        stats.errors += 1
        log.error(STEP, f"Immich-fout: {exc}")


def _sync_rating_markers(cfg, store, log, stats, client) -> None:
    """Laat een favoriet met het bestand meeverhuizen.

    Immich bewaart een favoriet enkel in zijn database; verplaats je een bestand, dan is het voor
    Immich een nieuwe foto en is de favoriet weg. Het sterrenrating staat wel in de sidecar
    (<bestand>.xmp) die mee verhuist. Daarom:
    1. elke favoriet in Immich krijgt een sidecar met rating 5 (als er nog geen sidecar is);
    2. elke foto met rating 5 die we nog niet eerder gezien hebben en geen favoriet is, wordt favoriet.
    Een foto die je zelf van favoriet haalt, blijft dat: we kijken per Immich-asset maar een keer.
    """
    written = 0
    failed = 0
    for item in client.iter_assets({"isFavorite": True}):
        host = from_immich_path(cfg, item.get("originalPath") or "")
        if host is None or not host.is_file():
            continue
        if sidecar_path(host).exists() or host.with_suffix(".xmp").exists():
            continue
        try:
            if write_rating_sidecar(host, 5):
                written += 1
        except OSError as exc:
            failed += 1
            log.warn(STEP, f"sidecar schrijven mislukt voor {host.name}: {exc}", host)
    stats.sidecars += written

    seen = store.fav_seen_ids()
    new_ids: list[str] = []
    to_mark: list[str] = []
    for item in client.iter_assets({"rating": 5}):
        asset_id = item.get("id")
        if not asset_id or asset_id in seen:
            continue
        new_ids.append(asset_id)
        if not item.get("isFavorite"):
            to_mark.append(asset_id)
            log.info(STEP, f"rating 5, nog geen favoriet: {PurePosixPath(item.get('originalPath') or asset_id).name}")
    if to_mark:
        client.set_favorite(to_mark)
        stats.favorites_set += len(to_mark)
    store.add_fav_seen(new_ids)
    log.info(
        STEP,
        f"favorieten en sidecars: {written} sidecar(s) geschreven voor favorieten in Immich"
        + (f" ({failed} mislukt)" if failed else "")
        + f", {len(to_mark)} foto(s) met rating 5 als favoriet gemarkeerd",
    )


def _mark_favorites(cfg, store, log, stats, client, pending, sleep, clock) -> None:
    waiting: dict[str, str] = {}
    for host in pending:
        try:
            waiting[host] = to_immich_path(cfg, Path(host))
        except ValueError:
            log.warn(STEP, f"pad ligt niet onder de doelmap, blijft in de wachtrij: {host}", host)

    log.info(STEP, f"{len(waiting)} favoriet(en) in de wachtrij; wachten tot Immich ze kent (max {cfg.immich_wait_seconds}s)")
    deadline = clock() + cfg.immich_wait_seconds
    while True:
        found: dict[str, str] = {}
        for host, immich_path in waiting.items():
            asset_id = client.find_asset_id(immich_path)
            if asset_id:
                found[host] = asset_id
        if found:
            client.set_favorite(list(found.values()))
            for host in found:
                store.mark_favorite_done(host)
                del waiting[host]
                log.info(STEP, f"favoriet gemarkeerd in Immich: {Path(host).name}", host)
            stats.favorites_set += len(found)
        if not waiting or clock() >= deadline:
            break
        log.info(STEP, f"{len(waiting)} foto('s) nog niet in Immich, opnieuw proberen over {cfg.immich_poll_seconds}s")
        sleep(cfg.immich_poll_seconds)

    stats.favorites_waiting = len(waiting)
    if waiting:
        log.warn(STEP, f"{len(waiting)} favoriet(en) nog niet gevonden in Immich, volgende run opnieuw")
