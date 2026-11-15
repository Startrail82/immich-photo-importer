"""Stap 5 van een run: Immich laten scannen en de favorieten markeren."""

from __future__ import annotations

import time
from pathlib import Path, PurePosixPath
from typing import Callable

from .config import Config
from .immich import ImmichClient, ImmichError
from .runlog import RunLog
from .store import Store

STEP = "6/6 Immich"


def to_immich_path(cfg: Config, host_path: Path) -> str:
    """Zet een pad in onze container om naar het pad zoals Immich het ziet."""
    relative = host_path.relative_to(cfg.target)
    return str(PurePosixPath(cfg.immich_target_path) / PurePosixPath(relative.as_posix()))


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
    if not moved_any and not pending:
        log.info(STEP, "niets te doen")
        return

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
    except ImmichError as exc:
        stats.errors += 1
        log.error(STEP, f"Immich-fout: {exc}")


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
