from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest
from conftest import make_jpeg, needs_exiftool

from photoimporter.organizer import run_import

FAKE = Path(__file__).parent / "fake_icloudpd.py"


@pytest.fixture
def icloud(env, tmp_path, monkeypatch):
    cfg, store, dirs = env
    spec_file = tmp_path / "spec.json"
    call_log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_ICLOUD_SPEC", str(spec_file))
    monkeypatch.setenv("FAKE_ICLOUD_LOG", str(call_log))

    def configure(spec: dict, **overrides):
        spec_file.write_text(json.dumps(spec))
        values = dict(
            icloud_username="bram@example.com",
            icloud_password="geheim-wachtwoord",
            icloud_command=f"{sys.executable} {FAKE}",
            icloud_auth_wait_seconds=30,
        )
        values.update(overrides)
        return dataclasses.replace(cfg, **values)

    def calls() -> list[list[str]]:
        if not call_log.exists():
            return []
        return [json.loads(line) for line in call_log.read_text().splitlines()]

    return configure, calls, store, dirs, tmp_path


def source(tmp_path: Path, name: str, color, date: str) -> str:
    return str(make_jpeg(tmp_path / "bron" / name, color, date))


def event_messages(store, run_id) -> str:
    return "\n".join(e["message"] for e in store.list_events(run_id))


def test_library_option_is_passed_to_every_icloudpd_call(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}}, icloud_library="SharedSync-ABC")

    run_import(cfg, store)

    auth, fav, other = calls()
    for call in (auth, fav, other):
        assert call[call.index("--library") + 1] == "SharedSync-ABC"


def test_no_library_option_by_default(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}})

    run_import(cfg, store)

    assert all("--library" not in call for call in calls())


@needs_exiftool
def test_download_then_sort_favorite_and_other(icloud):
    configure, calls, store, dirs, tmp = icloud
    fav = source(tmp, "fav.jpg", (1, 1, 1), "2024:07:08 09:10:11")
    other = source(tmp, "other.jpg", (2, 2, 2), "2023:01:02 03:04:05")
    cfg = configure(
        {
            "downloads": {
                "Favorites": [{"name": "IMG_1.jpg", "src": fav}],
                "ALL": [{"name": "IMG_2.jpg", "src": other}],
            }
        }
    )

    result = run_import(cfg, store)

    assert result.status == "ok"
    assert result.stats.downloaded == 2
    assert (dirs["target"] / "2024" / "2024_07" / "2024_07_08" / "IMG_1.jpg").exists()
    assert (dirs["target"] / "2024" / "2024_07" / "2024_07_08" / "IMG_1.jpg.xmp").exists()
    assert (dirs["target"] / "2023" / "2023_01" / "2023_01_02" / "IMG_2.jpg").exists()
    assert not (dirs["target"] / "2023" / "2023_01" / "2023_01_02" / "IMG_2.jpg.xmp").exists()
    assert store.get_state("icloud_status") == "ok"


@needs_exiftool
def test_favorite_also_in_all_photos_stays_favorite(icloud):
    """Dezelfde foto zit in 'Favorites' en in 'alle foto's': een bestand, mét favorietmarkering."""
    configure, calls, store, dirs, tmp = icloud
    both = source(tmp, "both.jpg", (7, 7, 7), "2024:07:08 09:10:11")
    cfg = configure(
        {
            "downloads": {
                "Favorites": [{"name": "IMG_1.jpg", "src": both}],
                "ALL": [{"name": "IMG_1.jpg", "src": both}],
            }
        }
    )

    result = run_import(cfg, store)

    day = dirs["target"] / "2024" / "2024_07" / "2024_07_08"
    assert sorted(p.name for p in day.iterdir()) == ["IMG_1.jpg", "IMG_1.jpg.xmp"]
    assert result.stats.duplicates_removed == 1
    assert len(store.list_pending_favorites()) == 1


@needs_exiftool
def test_command_line_is_correct_and_password_never_logged(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}})

    result = run_import(cfg, store)

    auth, fav, other = calls()
    assert "--auth-only" in auth
    assert fav[fav.index("--album") + 1] == "Favorites"
    assert fav[fav.index("--directory") + 1] == str(dirs["fav"])
    assert "--album" not in other
    assert other[other.index("--directory") + 1] == str(dirs["other"])
    for call in (fav, other):
        assert "--set-exif-datetime" in call
        assert call[call.index("--folder-structure") + 1] == "none"
        assert call[call.index("--mfa-provider") + 1] == "webui"
        assert call[call.index("--cookie-directory") + 1] == str(cfg.cookie_dir)
    assert "geheim-wachtwoord" not in event_messages(store, result.run_id)


@needs_exiftool
def test_login_failure_skips_downloads_but_still_sorts_inbox(icloud):
    configure, calls, store, dirs, tmp = icloud
    make_jpeg(dirs["inbox"] / "kaart.jpg", (3, 3, 3), "2022:05:06 07:08:09")
    cfg = configure({"auth": "fail", "downloads": {}})

    result = run_import(cfg, store)

    assert result.status == "ok_met_fouten"
    assert len(calls()) == 1  # alleen de login, geen downloads
    assert (dirs["target"] / "2022" / "2022_05" / "2022_05_06" / "kaart.jpg").exists()
    assert store.get_state("icloud_status") == "fout"
    assert "Invalid email/password" in event_messages(store, result.run_id)


@needs_exiftool
def test_waiting_for_code_is_reported_and_times_out(icloud, monkeypatch):
    configure, calls, store, dirs, tmp = icloud
    monkeypatch.setattr("photoimporter.icloud.HINT_AFTER_SECONDS", 1)
    cfg = configure({"auth": "hang"}, icloud_auth_wait_seconds=3)
    seen_states: list[str] = []
    real_set = store.set_state

    def spy(key, value):
        seen_states.append(value)
        real_set(key, value)

    monkeypatch.setattr(store, "set_state", spy)

    result = run_import(cfg, store)

    assert result.status == "ok_met_fouten"
    assert "wacht_op_code" in seen_states
    assert store.get_state("icloud_status") == "fout"
    text = event_messages(store, result.run_id)
    assert "tweestapscode" in text
    assert len(calls()) == 1


@needs_exiftool
def test_favorites_failure_skips_other_download(icloud):
    configure, calls, store, dirs, tmp = icloud
    other = source(tmp, "other.jpg", (2, 2, 2), "2023:01:02 03:04:05")
    cfg = configure(
        {"fail_album": "Favorites", "downloads": {"ALL": [{"name": "IMG_2.jpg", "src": other}]}}
    )

    result = run_import(cfg, store)

    assert result.status == "ok_met_fouten"
    assert len(calls()) == 2  # login + favorieten; 'overig' is bewust overgeslagen
    assert not any((dirs["other"]).iterdir())


@needs_exiftool
def test_dry_run_and_unconfigured_do_not_call_icloudpd(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}})
    run_import(cfg.with_dry_run(True), store)
    run_import(dataclasses.replace(cfg, icloud_password=""), store)
    assert calls() == []
    assert store.get_state("icloud_status") == "uit"


@needs_exiftool
def test_second_run_downloads_nothing_new_and_is_stable(icloud):
    configure, calls, store, dirs, tmp = icloud
    fav = source(tmp, "fav.jpg", (1, 1, 1), "2024:07:08 09:10:11")
    cfg = configure({"downloads": {"Favorites": [{"name": "IMG_1.jpg", "src": fav}]}})
    run_import(cfg, store)
    second = run_import(cfg, store)  # de fake levert hetzelfde bestand opnieuw af
    # het wordt opnieuw gedownload (icloudpd ziet het niet meer in de downloadmap), maar herkend
    # als identiek duplicaat en verwijderd: er blijft precies een foto met sidecar over
    day = dirs["target"] / "2024" / "2024_07" / "2024_07_08"
    assert sorted(p.name for p in day.iterdir()) == ["IMG_1.jpg", "IMG_1.jpg.xmp"]
    assert second.stats.duplicates_removed == 1
    assert second.status == "ok"


def test_icloud_check_finds_favorites_album(icloud):
    from photoimporter.icloud import check_icloud
    from photoimporter.runlog import ConsoleLog

    configure, calls, store, dirs, tmp = icloud
    assert check_icloud(configure({}), ConsoleLog()) == 0
    # Nederlandstalig account: album heet anders, dan moet de gebruiker de naam aanpassen
    assert check_icloud(configure({"albums": ["Alle foto's", "Favorieten"]}), ConsoleLog()) == 1
    ok = configure({"albums": ["Alle foto's", "Favorieten"]}, icloud_favorites_album="Favorieten")
    assert check_icloud(ok, ConsoleLog()) == 0
