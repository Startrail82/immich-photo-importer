from __future__ import annotations

import dataclasses
import threading
import time
from datetime import datetime

import pytest

from photoimporter.cli import main
from photoimporter.config import Config, ConfigError
from photoimporter.organizer import RunResult, RunStats
from photoimporter.scheduler import Scheduler, next_run_after


def test_next_run_today_or_tomorrow():
    assert next_run_after(datetime(2026, 11, 15, 9, 0), "12:00") == datetime(2026, 11, 15, 12, 0)
    assert next_run_after(datetime(2026, 11, 15, 12, 0), "12:00") == datetime(2026, 11, 16, 12, 0)
    assert next_run_after(datetime(2026, 11, 15, 23, 59, 30), "12:00") == datetime(2026, 11, 16, 12, 0)
    assert next_run_after(datetime(2026, 12, 31, 13, 0), "00:05") == datetime(2027, 1, 1, 0, 5)


def test_schedule_time_is_validated(env):
    cfg, _store, _dirs = env
    for bad in ("12", "25:00", "12:61", "abc"):
        with pytest.raises(ConfigError):
            dataclasses.replace(cfg, schedule_time=bad).validate()
    dataclasses.replace(cfg, schedule_time="07:05").validate()


def fake_runner(calls, delay=0.0, release=None):
    def run(cfg, store, trigger="handmatig"):
        calls.append(trigger)
        if release is not None:
            release.wait(5)
        time.sleep(delay)
        return RunResult(len(calls), "ok", RunStats())

    return run


def wait_for(condition, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_trigger_now_runs_immediately_and_is_labelled_manual(env):
    cfg, store, _ = env
    calls: list[str] = []
    scheduler = Scheduler(cfg, store, runner=fake_runner(calls))
    scheduler.start()
    try:
        assert scheduler.trigger_now() is True
        assert wait_for(lambda: calls == ["handmatig"])
    finally:
        scheduler.stop()


def test_scheduled_run_happens_at_the_planned_time(env):
    cfg, store, _ = env
    calls: list[str] = []
    clock = {"now": datetime(2026, 11, 15, 11, 59, 59, 500000)}
    cfg = dataclasses.replace(cfg, schedule_time="12:00")
    scheduler = Scheduler(cfg, store, runner=fake_runner(calls), now=lambda: clock["now"])
    # de planner wacht op 'volgende run - nu' = 0,5 s; daarna loopt de klok door
    started = threading.Event()

    def advance():
        started.wait()
        time.sleep(0.6)
        clock["now"] = datetime(2026, 11, 15, 12, 0, 0, 100000)

    threading.Thread(target=advance, daemon=True).start()
    scheduler.start()
    started.set()
    try:
        assert wait_for(lambda: calls == ["gepland"], seconds=5)
    finally:
        scheduler.stop()


def test_second_trigger_while_running_is_refused(env):
    cfg, store, _ = env
    calls: list[str] = []
    release = threading.Event()
    scheduler = Scheduler(cfg, store, runner=fake_runner(calls, release=release))
    scheduler.start()
    try:
        assert scheduler.trigger_now() is True
        assert wait_for(lambda: scheduler.running)
        assert scheduler.trigger_now() is False
        release.set()
        assert wait_for(lambda: not scheduler.running)
        assert calls == ["handmatig"]
    finally:
        release.set()
        scheduler.stop()


def test_disabled_schedule_still_allows_manual_runs(env):
    cfg, store, _ = env
    calls: list[str] = []
    cfg = dataclasses.replace(cfg, schedule_enabled=False)
    scheduler = Scheduler(cfg, store, runner=fake_runner(calls))
    assert scheduler.next_run is None
    scheduler.start()
    try:
        assert scheduler.trigger_now()
        assert wait_for(lambda: calls == ["handmatig"])
    finally:
        scheduler.stop()


def test_runner_crash_does_not_kill_planner(env):
    cfg, store, _ = env
    calls: list[str] = []

    def crashing(cfg, store, trigger="handmatig"):
        calls.append(trigger)
        if len(calls) == 1:
            raise RuntimeError("boem")
        return RunResult(1, "ok", RunStats())

    scheduler = Scheduler(cfg, store, runner=crashing)
    scheduler.start()
    try:
        scheduler.trigger_now()
        assert wait_for(lambda: len(calls) == 1 and not scheduler.running)
        assert scheduler.trigger_now()
        assert wait_for(lambda: len(calls) == 2)
    finally:
        scheduler.stop()


def test_cli_icloud_check_without_credentials(env, monkeypatch, tmp_path):
    for key in ("PI_ICLOUD_USERNAME", "PI_ICLOUD_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PI_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("PI_TARGET", str(tmp_path / "t"))
    assert main(["icloud-check"]) == 2
