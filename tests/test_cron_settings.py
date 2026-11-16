from __future__ import annotations

import dataclasses
import json
import threading
import time
from datetime import datetime

import pytest

from photoimporter.config import ConfigError
from photoimporter.cron import CronError, describe, parse
from photoimporter.organizer import RunResult, RunStats
from photoimporter.scheduler import Scheduler
from photoimporter.settings import SettingsError, SettingsManager

NOW = datetime(2026, 11, 16, 23, 0)  # maandag


def nxt(expr, now=NOW):
    return parse(expr).next_after(now)


def test_daily_and_every_n_minutes():
    assert nxt("0 12 * * *") == datetime(2026, 11, 17, 12, 0)
    assert nxt("*/15 * * * *") == datetime(2026, 11, 16, 23, 15)
    assert nxt("0 12 * * *", datetime(2026, 11, 16, 11, 59, 30)) == datetime(2026, 11, 16, 12, 0)
    assert nxt("0 12 * * *", datetime(2026, 11, 16, 12, 0)) == datetime(2026, 11, 17, 12, 0)  # strikt na 'nu'


def test_weekdays_ranges_lists_and_names():
    assert nxt("0 3 * * 1-5") == datetime(2026, 11, 17, 3, 0)  # dinsdag
    assert nxt("30 8 * * mon,fri") == datetime(2026, 11, 20, 8, 30)
    assert nxt("0 0 * * 7") == nxt("0 0 * * 0") == datetime(2026, 11, 22, 0, 0)  # zondag, 0 en 7
    assert nxt("0 9 * * ma") == datetime(2026, 11, 23, 9, 0)  # Nederlandse afkorting


def test_day_of_month_month_and_year_rollover():
    assert nxt("0 12 1 * *") == datetime(2026, 12, 1, 12, 0)
    assert nxt("0 12 1 1 *") == datetime(2027, 1, 1, 12, 0)
    assert nxt("0 12 29 2 *") == datetime(2028, 2, 29, 12, 0)  # schrikkeljaar


def test_dom_and_weekday_both_restricted_means_either():
    # klassiek cron: 'de 1e van de maand OF een maandag'
    assert nxt("0 12 1 * 1", datetime(2026, 11, 16, 13, 0)) == datetime(2026, 11, 23, 12, 0)


@pytest.mark.parametrize(
    "bad", ["", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "a * * * *", "*/0 * * * *", "5-1 * * * *"]
)
def test_invalid_expressions_are_rejected(bad):
    with pytest.raises(CronError):
        parse(bad)


def test_never_occurring_expression_is_reported():
    with pytest.raises(CronError):
        nxt("0 0 31 2 *")


def test_descriptions_in_dutch():
    assert describe("0 12 * * *") == "elke dag om 12:00"
    assert describe("*/15 * * * *") == "elke 15 minuten"
    assert describe("0 3 * * 1-5") == "elke werkdag (ma-vr) om 03:00"
    assert describe("30 8 * * mon,fri") == "op maandag en vrijdag om 08:30"
    assert "cron-expressie" in describe("0 12 1 * *")


def test_config_cron_overrides_time_and_is_validated(env):
    cfg, _store, _dirs = env
    assert dataclasses.replace(cfg, schedule_time="07:05").cron_expression == "5 7 * * *"
    assert dataclasses.replace(cfg, schedule_cron="*/5 * * * *").cron_expression == "*/5 * * * *"
    with pytest.raises(ConfigError):
        dataclasses.replace(cfg, schedule_cron="nonsens").validate()
    with pytest.raises(ConfigError):
        dataclasses.replace(cfg, icloud_keep_days=-1).validate()


# --- instellingen -----------------------------------------------------------------


@pytest.fixture
def manager(env):
    cfg, store, _ = env
    return SettingsManager(cfg, store), cfg, store


def test_defaults_come_from_the_environment_config(manager):
    mgr, cfg, _ = manager
    s = mgr.current()
    assert s["schedule_cron"] == "0 12 * * *"
    assert s["libraries"] == ["PrimarySync"]
    assert s["delete_enabled"] is False and s["keep_days"] == 14
    assert mgr.effective().icloud_libraries == () and mgr.effective().icloud_keep_days is None


def test_saved_settings_override_and_survive_a_restart(manager):
    mgr, cfg, store = manager
    mgr.update(
        {
            "schedule_cron": "30  6 * * 1-5",
            "libraries": ["SharedSync-ABC"],
            "favorites_album": "Favorieten",
            "delete_enabled": True,
            "keep_days": 21,
        }
    )
    again = SettingsManager(cfg, store)  # nieuw object = herstart van de container
    eff = again.effective()
    assert eff.cron_expression == "30 6 * * 1-5"  # witruimte genormaliseerd
    assert eff.icloud_libraries == ("SharedSync-ABC",)
    assert eff.icloud_favorites_album == "Favorieten"
    assert eff.icloud_keep_days == 21


def test_delete_switched_off_keeps_the_day_count_but_deletes_nothing(manager):
    mgr, *_ = manager
    mgr.update({"delete_enabled": True, "keep_days": 30})
    mgr.update({"delete_enabled": False})
    assert mgr.current()["keep_days"] == 30
    assert mgr.effective().icloud_keep_days is None


def test_partial_update_leaves_other_values_alone(manager):
    mgr, *_ = manager
    mgr.update({"libraries": ["PrimarySync", "SharedSync-ABC"]})
    mgr.update({"keep_days": 7})
    assert mgr.current()["libraries"] == ["PrimarySync", "SharedSync-ABC"]


@pytest.mark.parametrize(
    "bad",
    [
        {"schedule_cron": "morgen"},
        {"libraries": []},
        {"libraries": ["x y"]},
        {"libraries": ["--delete-after-download"]},  # nooit een optie in het commando kunnen smokkelen
        {"libraries": "PrimarySync"},
        {"favorites_album": "  "},
        {"keep_days": 0},
        {"keep_days": "veel"},
        {"keep_days": 99999},
    ],
)
def test_invalid_settings_are_rejected_and_nothing_is_saved(manager, bad):
    mgr, *_ = manager
    before = mgr.current()
    with pytest.raises(SettingsError):
        mgr.update(bad)
    assert mgr.current() == before


def test_schedule_preview(manager):
    mgr, *_ = manager
    ok = mgr.describe_schedule(NOW, "0 12 * * *")
    assert ok["valid"] and ok["text"] == "elke dag om 12:00"
    assert ok["upcoming"] == ["2026-11-17 12:00", "2026-11-18 12:00", "2026-11-19 12:00"]
    bad = mgr.describe_schedule(NOW, "abc")
    assert not bad["valid"] and bad["error"]


def test_known_libraries_are_remembered(manager):
    mgr, *_ = manager
    mgr.remember_libraries(["PrimarySync", "SharedSync-ABC"], "2 bibliotheek/bibliotheken gevonden")
    mgr.remember_libraries(None, "bibliotheken ophalen mislukt: indexing")
    known = mgr.known_libraries()
    assert known["names"] == ["PrimarySync", "SharedSync-ABC"]  # lijst blijft bij een mislukte poging
    assert "mislukt" in known["message"]


# --- planner met cron en live instellingen ------------------------------------------


def wait_for(cond, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_scheduler_follows_changed_settings_without_restart_and_without_running(env):
    cfg, store, _ = env
    mgr = SettingsManager(cfg, store)
    calls: list = []

    def runner(c, s, trigger="handmatig"):
        calls.append((trigger, c.icloud_keep_days))
        return RunResult(1, "ok", RunStats())

    scheduler = Scheduler(cfg, store, runner=runner, settings=mgr)
    assert scheduler.next_run.strftime("%H:%M") == "12:00"
    scheduler.start()
    try:
        mgr.update({"schedule_cron": "0 5 * * *", "delete_enabled": True, "keep_days": 9})
        scheduler.reload()
        assert wait_for(lambda: scheduler.next_run.strftime("%H:%M") == "05:00")
        time.sleep(0.2)
        assert calls == []  # herplannen start geen run
        assert scheduler.trigger_now()
        assert wait_for(lambda: calls == [("handmatig", 9)])  # de run gebruikt de nieuwe instellingen
    finally:
        scheduler.stop()


def test_scheduler_with_disabled_schedule_via_settings(env):
    cfg, store, _ = env
    mgr = SettingsManager(cfg, store)
    mgr.update({"schedule_enabled": False})
    assert Scheduler(cfg, store, settings=mgr).next_run is None


def test_scheduled_cron_run_fires(env):
    cfg, store, _ = env
    calls: list[str] = []
    clock = {"now": datetime(2026, 11, 16, 8, 59, 59, 500000)}
    cfg = dataclasses.replace(cfg, schedule_cron="0 9 * * 1")
    scheduler = Scheduler(
        cfg, store, runner=lambda c, s, t="x": calls.append(t) or RunResult(1, "ok", RunStats()), now=lambda: clock["now"]
    )

    def advance():
        time.sleep(0.6)
        clock["now"] = datetime(2026, 11, 16, 9, 0, 0, 100000)

    threading.Thread(target=advance, daemon=True).start()
    scheduler.start()
    try:
        assert wait_for(lambda: calls == ["gepland"])
    finally:
        scheduler.stop()


def test_stored_settings_json_is_plain(manager):
    mgr, cfg, store = manager
    mgr.update({"keep_days": 5})
    stored = json.loads(store.get_state("settings"))
    assert stored["keep_days"] == 5 and "password" not in json.dumps(stored).lower()


# --- verbindingen -----------------------------------------------------------
def _mgr(env):
    from photoimporter.settings import SettingsManager

    cfg, store, _dirs = env
    return SettingsManager(cfg, store), store


def test_connection_settings_override_env_and_secrets_are_kept_apart(env):
    mgr, store = _mgr(env)
    mgr.update({"icloud_username": "a@b.c", "icloud_password": "pw1", "immich_url": "https://i.example", "immich_api_key": "k1"})
    eff = mgr.effective()
    assert (eff.icloud_username, eff.icloud_password, eff.immich_url, eff.immich_api_key) == ("a@b.c", "pw1", "https://i.example", "k1")
    assert "pw1" not in json.dumps(mgr.current()) and "k1" not in json.dumps(mgr.current())
    assert mgr.secret_flags() == {"icloud_password_set": True, "immich_api_key_set": True}


def test_empty_secret_keeps_stored_value(env):
    mgr, _ = _mgr(env)
    mgr.update({"icloud_password": "pw1"})
    mgr.update({"icloud_password": "", "icloud_username": "x@y.z"})
    assert mgr.effective().icloud_password == "pw1"


def test_invalid_url_is_rejected(env):
    from photoimporter.settings import SettingsError

    mgr, _ = _mgr(env)
    with pytest.raises(SettingsError):
        mgr.update({"immich_url": "immich.example"})


def test_with_overrides_uses_form_values_without_saving(env):
    mgr, _ = _mgr(env)
    mgr.update({"icloud_password": "bewaard"})
    cfg = mgr.with_overrides({"icloud_username": "form@x.y", "icloud_password": "", "immich_api_key": "nieuw"})
    assert cfg.icloud_username == "form@x.y" and cfg.icloud_password == "bewaard" and cfg.immich_api_key == "nieuw"
    assert mgr.effective().immich_api_key != "nieuw"
