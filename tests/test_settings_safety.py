"""Instellingen die iets kunnen wissen of juist stilzwijgend aanzetten, streng controleren."""

from __future__ import annotations

import dataclasses
import json

import pytest

from photoimporter.config import ConfigError
from photoimporter.settings import SettingsError, SettingsManager


def test_keep_days_zero_is_refused_because_it_would_wipe_icloud(env):
    """`--keep-icloud-recent-days 0` wist alles; de UI stond 1..3650 toe, de omgeving niet."""
    cfg, _store, _dirs = env
    for bad in (0, -1):
        with pytest.raises(ConfigError) as err:
            dataclasses.replace(cfg, icloud_keep_days=bad).validate()
        assert "minstens 1" in str(err.value)
    dataclasses.replace(cfg, icloud_keep_days=1).validate()
    dataclasses.replace(cfg, icloud_keep_days=None).validate()


def test_a_stored_zero_never_reaches_the_icloud_command(env):
    cfg, store, _dirs = env
    store.set_state("settings", json.dumps({"delete_enabled": True, "keep_days": 0}))
    assert SettingsManager(cfg, store).effective().icloud_keep_days == 14
    store.set_state("settings", json.dumps({"delete_enabled": True, "keep_days": "kapot"}))
    assert SettingsManager(cfg, store).effective().icloud_keep_days == 14
    store.set_state("settings", json.dumps({"delete_enabled": True, "keep_days": 30}))
    assert SettingsManager(cfg, store).effective().icloud_keep_days == 30


def test_switches_are_parsed_instead_of_truthy(env):
    """`bool("false")` is True: een tekst die als 'uit' bedoeld was, zette de planning aan."""
    cfg, store, _dirs = env
    mgr = SettingsManager(cfg, store)
    mgr.update({"schedule_enabled": "false"})
    assert mgr.current()["schedule_enabled"] is False
    assert mgr.effective().schedule_enabled is False
    mgr.update({"schedule_enabled": "TRUE"})
    assert mgr.current()["schedule_enabled"] is True
    mgr.update({"delete_enabled": "nee"})
    assert mgr.current()["delete_enabled"] is False
    mgr.update({"delete_enabled": 1})
    assert mgr.current()["delete_enabled"] is True

    for value in ("misschien", "", None, 2, [], {}):
        with pytest.raises(SettingsError) as err:
            mgr.update({"schedule_enabled": value})
        assert "true of false" in str(err.value)
    assert mgr.current()["schedule_enabled"] is True  # niets bewaard


def test_a_url_without_a_scheme_is_refused_at_startup(env):
    cfg, _store, _dirs = env
    with pytest.raises(ConfigError) as err:
        dataclasses.replace(cfg, immich_url="immich.local:2283").validate()
    assert "http://" in str(err.value)
    with pytest.raises(ConfigError):
        dataclasses.replace(cfg, icloud_web_url="immich.local/code").validate()
    dataclasses.replace(cfg, immich_url="https://immich.example", icloud_web_url="http://code.example").validate()
