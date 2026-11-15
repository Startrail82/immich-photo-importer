"""Logging per stap: naar stdout (docker logs) en naar de datastore (webinterface)."""

from __future__ import annotations

import logging
from pathlib import Path

from .store import Store

_logger = logging.getLogger("photoimporter")


class RunLog:
    def __init__(self, store: Store, run_id: int):
        self._store = store
        self.run_id = run_id

    def _emit(self, level: int, step: str, message: str, path: Path | str | None) -> None:
        _logger.log(level, "[%s] %s", step, message)
        self._store.add_event(
            self.run_id,
            logging.getLevelName(level),
            step,
            message,
            str(path) if path is not None else None,
        )

    def info(self, step: str, message: str, path: Path | str | None = None) -> None:
        self._emit(logging.INFO, step, message, path)

    def warn(self, step: str, message: str, path: Path | str | None = None) -> None:
        self._emit(logging.WARNING, step, message, path)

    def error(self, step: str, message: str, path: Path | str | None = None) -> None:
        self._emit(logging.ERROR, step, message, path)
