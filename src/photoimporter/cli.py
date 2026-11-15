"""Commandoregel: photoimporter run [--dry-run]"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import Config, ConfigError
from .organizer import RunAlreadyActive, run_import
from .store import Store


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


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
    args = parser.parse_args(argv)

    _setup_logging()
    log = logging.getLogger("photoimporter")
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        log.error("configuratiefout: %s", exc)
        return 2
    if args.dry_run:
        cfg = cfg.with_dry_run(True)

    store = Store(cfg.data_dir / "photoimporter.db")
    try:
        result = run_import(cfg, store, trigger="cli")
    except RunAlreadyActive:
        log.error("er loopt al een run, deze wordt niet gestart")
        return 3
    finally:
        store.close()
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
