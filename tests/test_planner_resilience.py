"""Een planning die nooit voorkomt hoort geweigerd te worden en de planner mag nooit stilvallen."""

from __future__ import annotations

import dataclasses
import time
from datetime import datetime

import pytest

from photoimporter.config import ConfigError
from photoimporter.cron import CronError, ensure_satisfiable
from photoimporter.organizer import RunResult, RunStats
from photoimporter.scheduler import Scheduler
from photoimporter.settings import SettingsError, SettingsManager

NOW = datetime(2026, 11, 16, 10, 0)
IMPOSSIBLE = "0 0 31 2 *"  # 31 februari: geldig volgens de grammatica, komt nooit voor


def wait_for(condition, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_ensure_satisfiable_accepts_real_schedules_and_rejects_impossible_ones():
    assert ensure_satisfiable("0 12 * * *", NOW).expression == "0 12 * * *"
    assert ensure_satisfiable("0 0 29 2 *", NOW).expression == "0 0 29 2 *"  # schrikkeldag bestaat
    with pytest.raises(CronError) as err:
        ensure_satisfiable(IMPOSSIBLE, NOW)
    assert "nooit" in str(err.value)


def test_settings_refuse_an_impossible_schedule_and_keep_the_old_one(env):
    cfg, store, _ = env
    mgr = SettingsManager(cfg, store)
    with pytest.raises(SettingsError) as err:
        mgr.update({"schedule_cron": IMPOSSIBLE})
    assert "planning" in str(err.value) and "nooit" in str(err.value)
    assert mgr.current()["schedule_cron"] == "0 12 * * *"


def test_schedule_preview_reports_an_impossible_expression_instead_of_raising(env):
    cfg, store, _ = env
    mgr = SettingsManager(cfg, store)
    result = mgr.describe_schedule(NOW, IMPOSSIBLE)
    assert result["valid"] is False and result["upcoming"] == []
    assert "nooit" in result["error"]


def test_config_rejects_an_impossible_schedule(env):
    cfg, _store, _dirs = env
    with pytest.raises(ConfigError) as err:
        dataclasses.replace(cfg, schedule_cron=IMPOSSIBLE).validate()
    assert "nooit" in str(err.value)


def test_planner_keeps_running_with_an_impossible_schedule(env):
    """Vóór deze fix stierf de planner-thread hier en stopten alle automatische runs."""
    cfg, store, _ = env
    calls: list[str] = []
    cfg = dataclasses.replace(cfg, schedule_cron=IMPOSSIBLE)

    def runner(cfg, store, trigger="handmatig"):
        calls.append(trigger)
        return RunResult(len(calls), "ok", RunStats())

    scheduler = Scheduler(cfg, store, runner=runner)
    assert scheduler.next_run is None
    scheduler.start()
    try:
        time.sleep(0.3)
        assert scheduler._thread.is_alive()
        assert scheduler.trigger_now() is True
        assert wait_for(lambda: calls == ["handmatig"])
    finally:
        scheduler.stop()


def test_planner_survives_an_error_while_planning(env, monkeypatch):
    cfg, store, _ = env
    calls: list[str] = []

    def runner(cfg, store, trigger="handmatig"):
        calls.append(trigger)
        return RunResult(len(calls), "ok", RunStats())

    attempts = {"n": 0}

    def flaky(self):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("klok kapot")
        return datetime(2030, 1, 1, 12, 0)

    monkeypatch.setattr(Scheduler, "next_run", property(flaky))
    scheduler = Scheduler(cfg, store, runner=runner)
    scheduler.start()
    try:
        assert wait_for(lambda: attempts["n"] >= 1)
        time.sleep(0.2)
        assert scheduler._thread.is_alive()
        # een 'nu uitvoeren' gaat niet verloren tijdens het herstel
        assert scheduler.trigger_now() is True
        assert wait_for(lambda: calls == ["handmatig"])
    finally:
        scheduler.stop()
