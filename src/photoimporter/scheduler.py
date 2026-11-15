"""Planner: dagelijks op een vast uur, plus een 'nu uitvoeren' die direct start."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable

from .config import Config
from .organizer import RunAlreadyActive, RunResult, run_import
from .store import Store

log = logging.getLogger("photoimporter.planner")


def next_run_after(now: datetime, hhmm: str) -> datetime:
    """Eerstvolgende moment (strikt na 'now') waarop het opgegeven uur voorkomt."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


class Scheduler:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        runner: Callable[..., RunResult] = run_import,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.cfg = cfg
        self.store = store
        self._runner = runner
        self._now = now
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._manual = False
        self._lock = threading.Lock()
        self._running = False
        self._thread = threading.Thread(target=self._loop, name="planner", daemon=True)

    # --- publieke API (ook voor de webinterface) --------------------------
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def trigger_now(self) -> bool:
        """Vraagt een run 'nu'. False als er al een loopt."""
        with self._lock:
            if self._running:
                return False
            self._manual = True
        self._wake.set()
        return True

    @property
    def running(self) -> bool:
        return self._running

    @property
    def next_run(self) -> datetime | None:
        if not self.cfg.schedule_enabled:
            return None
        return next_run_after(self._now(), self.cfg.schedule_time)

    # --- intern --------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            target = self.next_run
            timeout = None if target is None else max(0.0, (target - self._now()).total_seconds())
            woken = self._wake.wait(timeout)
            if self._stop.is_set():
                return
            with self._lock:
                manual, self._manual = self._manual, False
                self._wake.clear()
            if not woken and target is None:
                continue
            self._run_once("handmatig" if manual else "gepland")

    def _run_once(self, trigger: str) -> None:
        with self._lock:
            self._running = True
        try:
            log.info("%s run gestart", trigger)
            result = self._runner(self.cfg, self.store, trigger)
            log.info("run %s klaar: %s", result.run_id, result.status)
        except RunAlreadyActive:
            log.warning("run overgeslagen: er loopt al een run (bv. via de opdrachtregel)")
        except Exception:
            log.exception("onverwachte fout in de planner")
        finally:
            with self._lock:
                self._running = False
