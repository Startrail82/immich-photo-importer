"""Planner: volgens een cron-expressie (standaard dagelijks 12:00), plus een 'nu uitvoeren' dat direct start."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable

from .config import Config
from .cron import parse as parse_cron
from .organizer import RunAlreadyActive, RunResult, run_favorites_sync, run_import
from .settings import SettingsManager
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
        settings: SettingsManager | None = None,
        favorites_runner: Callable[..., RunResult] = run_favorites_sync,
    ):
        self.cfg = cfg
        self.store = store
        self.settings = settings
        self._runner = runner
        self._favorites_runner = favorites_runner
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

    def reload(self) -> None:
        """Instellingen zijn gewijzigd: opnieuw plannen zonder een run te starten."""
        self._wake.set()

    def effective_config(self) -> Config:
        return self.settings.effective() if self.settings else self.cfg

    @property
    def running(self) -> bool:
        return self._running

    @property
    def next_run(self) -> datetime | None:
        cfg = self.effective_config()
        if not cfg.schedule_enabled:
            return None
        return parse_cron(cfg.cron_expression).next_after(self._now())

    @property
    def next_favorites_run(self) -> datetime | None:
        cfg = self.effective_config()
        if not cfg.favorites_schedule_enabled:
            return None
        return parse_cron(cfg.favorites_cron).next_after(self._now())

    def _next_target(self) -> tuple[datetime | None, str]:
        """Het eerstvolgende moment en welk soort run daar hoort: 'volledig' of 'favorieten'.
        Vallen ze samen, dan is de volledige run genoeg (die doet de Immich-stap ook)."""
        full, favorites = self.next_run, self.next_favorites_run
        if full is None and favorites is None:
            return None, "volledig"
        if favorites is None or (full is not None and full <= favorites):
            return full, "volledig"
        return favorites, "favorieten"

    # --- intern --------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            target, kind = self._next_target()
            timeout = None if target is None else max(0.0, (target - self._now()).total_seconds())
            woken = self._wake.wait(timeout)
            if self._stop.is_set():
                return
            with self._lock:
                manual, self._manual = self._manual, False
                self._wake.clear()
            if manual:
                self._run_once("handmatig")
            elif woken or target is None:
                continue  # instellingen gewijzigd (of niets ingepland): opnieuw plannen
            elif kind == "favorieten":
                self._run_once("gepland (favorieten)", self._favorites_runner)
            else:
                self._run_once("gepland")

    def _run_once(self, trigger: str, runner: Callable[..., RunResult] | None = None) -> None:
        with self._lock:
            self._running = True
        try:
            log.info("%s run gestart", trigger)
            result = (runner or self._runner)(self.effective_config(), self.store, trigger)
            log.info("run %s klaar: %s", result.run_id, result.status)
        except RunAlreadyActive:
            log.warning("run overgeslagen: er loopt al een run (bv. via de opdrachtregel)")
        except Exception:
            log.exception("onverwachte fout in de planner")
        finally:
            with self._lock:
                self._running = False
