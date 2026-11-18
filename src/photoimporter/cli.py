"""Commandoregel: photoimporter run [--dry-run]"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .config import Config, ConfigError
from .immich import ImmichClient, ImmichError
from .icloud import check_icloud
from .organizer import RunAlreadyActive, run_import
from .runlog import ConsoleLog
from .scheduler import Scheduler
from .settings import SettingsManager
from .store import Store


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def immich_check(cfg: Config) -> int:
    """Controleert stap voor stap of de Immich-koppeling klaar is voor gebruik."""
    log = logging.getLogger("photoimporter")
    missing = [
        name
        for name, value in (
            ("PI_IMMICH_URL", cfg.immich_url),
            ("PI_IMMICH_API_KEY", cfg.immich_api_key),
            ("PI_IMMICH_TARGET_PATH", cfg.immich_target_path),
        )
        if not value
    ]
    if missing:
        log.error("[immich-check] niet ingesteld: %s", ", ".join(missing))
        return 2

    client = ImmichClient(cfg.immich_url, cfg.immich_api_key)
    try:
        log.info("[immich-check 1/3] server bereikbaar? %s", cfg.immich_url)
        if not client.ping():
            log.error("[immich-check 1/3] onverwacht antwoord op /server/ping")
            return 1
        log.info("[immich-check 1/3] ok")

        log.info("[immich-check 2/3] bibliotheken lezen (recht: library.read)")
        libraries = client.list_libraries()
        log.info("[immich-check 2/3] ok, %d bibliotheek/bibliotheken gevonden", len(libraries))

        log.info("[immich-check 3/3] bibliotheek met importpad %s zoeken", cfg.immich_target_path)
        library_id = cfg.immich_library_id or client.find_library_id(cfg.immich_target_path)
        if library_id is None:
            log.error("[immich-check 3/3] geen bibliotheek met dit importpad gevonden")
            return 1
        log.info("[immich-check 3/3] ok, bibliotheek %s", library_id)

        log.info("[immich-check] zoeken op pad testen (recht: asset.read)")
        client.find_asset_id(cfg.immich_target_path + "/bestaat-niet.jpg")
        log.info("[immich-check] ok")
    except ImmichError as exc:
        log.error("[immich-check] mislukt: %s", exc)
        return 1
    log.info(
        "[immich-check] alles gelukt. Voor het markeren van favorieten en het starten van scans "
        "heeft de sleutel ook asset.update en library.update nodig; die test ik hier bewust niet."
    )
    return 0


def daemon(cfg: Config, web: bool = False) -> int:
    log = logging.getLogger("photoimporter")
    store = Store(cfg.data_dir / "photoimporter.db")
    settings = SettingsManager(cfg, store)
    scheduler = Scheduler(cfg, store, settings=settings)
    scheduler.start()
    app = None
    if web:
        from .webapp import WebApp

        app = WebApp(cfg, store, scheduler, port=cfg.web_port, settings=settings)
        app.start()
        log.info("webinterface luistert op poort %d", app.port)
    nxt = scheduler.next_run
    nxt_fav = scheduler.next_favorites_run
    log.info(
        "planner gestart; volgende iCloud-run: %s; volgende favorieten-sync: %s",
        nxt.strftime("%Y-%m-%d %H:%M") if nxt else "uitgeschakeld",
        nxt_fav.strftime("%Y-%m-%d %H:%M") if nxt_fav else "uitgeschakeld",
    )
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    log.info("planner stopt")
    if app:
        app.stop()
    scheduler.stop()
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="photoimporter")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="een import-run uitvoeren")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="alleen tonen wat er zou gebeuren, niets wijzigen",
    )
    dup = sub.add_parser("find-duplicates", help="dubbele foto's op de NAS zoeken (wist niets zonder --apply)")
    dup.add_argument("path", nargs="?", help="enkel deze map controleren (standaard: de hele doelmap)")
    dup.add_argument("--apply", action="store_true", help="de getoonde duplicaten ook echt wissen")
    sub.add_parser("immich-check", help="de verbinding met Immich en de API-sleutel controleren")
    sub.add_parser("icloud-check", help="inloggen bij iCloud (incl. tweestapscode) en het favorietenalbum zoeken")
    sub.add_parser("daemon", help="planner starten: dagelijks automatisch uitvoeren")
    sub.add_parser("serve", help="planner en webinterface starten (dit draait in de container)")
    args = parser.parse_args(argv)

    _setup_logging()
    log = logging.getLogger("photoimporter")
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        log.error("configuratiefout: %s", exc)
        return 2

    if args.command == "find-duplicates":
        from .dedupe import run_find_duplicates

        return run_find_duplicates(Path(args.path) if args.path else cfg.target, args.apply, print)
    if args.command == "immich-check":
        return immich_check(cfg)
    if args.command == "icloud-check":
        store = Store(cfg.data_dir / "photoimporter.db")
        try:
            return check_icloud(SettingsManager(cfg, store).effective(), ConsoleLog())
        finally:
            store.close()
    if args.command == "daemon":
        return daemon(cfg)
    if args.command == "serve":
        return daemon(cfg, web=True)

    store = Store(cfg.data_dir / "photoimporter.db")
    try:
        cfg = SettingsManager(cfg, store).effective()  # ook hier gelden de instellingen uit de webinterface
        if args.dry_run:
            cfg = cfg.with_dry_run(True)
        result = run_import(cfg, store, trigger="cli")
    except RunAlreadyActive:
        log.error("er loopt al een run, deze wordt niet gestart")
        return 3
    finally:
        store.close()
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
