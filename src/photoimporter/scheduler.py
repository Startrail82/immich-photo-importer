"""Planner: volgens een cron-expressie (standaard dagelijks 12:00), plus een 'nu uitvoeren' dat direct start."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable

from .config import Config
from .cron import CronError
from .cron import parse as parse_cron
from .organizer import RunAlreadyActive, RunResult, run_import
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
    ):
        self.cfg = cfg
        self.store = store
        self.settings = settings
        self._runner = runner
        self._now = now
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._manual = False
        self._lock = threading.Lock()
        self._running = False
        self._bad_cron = ""  # laatst gemelde planning die nooit voorkomt
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
        try:
            return parse_cron(cfg.cron_expression).next_after(self._now())
        except CronError as exc:
            # De webinterface vraagt dit bij elke statusverversing: alleen melden als de
            # planning verandert, anders loopt het log vol.
            if self._bad_cron != cfg.cron_expression:
                self._bad_cron = cfg.cron_expression
                log.error("planning %r komt nooit voor, er wordt niets ingepland: %s", cfg.cron_expression, exc)
            return None

    # --- intern --------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                target = self.next_run
            except Exception:  # noqa: BLE001 - de planner mag nooit stilvallen
                log.exception("kon de volgende run niet bepalen; opnieuw over 60 s")
                self._wake.wait(60)  # niet wissen: een 'nu uitvoeren' mag niet verloren gaan
                continue
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
            else:
                self._run_once("gepland")

    def _run_once(self, trigger: str) -> None:
        with self._lock:
            self._running = True
        try:
            log.info("%s run gestart", trigger)
            result = self._runner(self.effective_config(), self.store, trigger)
            log.info("run %s klaar: %s", result.run_id, result.status)
        except RunAlreadyActive:
            log.warning("run overgeslagen: er loopt al een run (bv. via de opdrachtregel)")
        except Exception:
            log.exception("onverwachte fout in de planner")
        finally:
            with self._lock:
                self._running = False
