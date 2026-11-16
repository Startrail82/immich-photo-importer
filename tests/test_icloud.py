from __future__ import annotations

import dataclasses
import json
import sys
import threading
from pathlib import Path

import pytest
from conftest import make_jpeg, needs_exiftool

from photoimporter.icloud import ICLOUD_LOCK, _base_command, list_libraries
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
            icloud_username="user@example.com",
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


def test_shared_library_is_passed_to_icloudpd_but_primary_is_the_default(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}}, icloud_libraries=("SharedSync-ABC",))

    run_import(cfg, store)

    auth, fav, other = calls()
    assert "--library" not in auth  # inloggen is niet bibliotheekspecifiek
    for call in (fav, other):
        assert call[call.index("--library") + 1] == "SharedSync-ABC"

    configure({"downloads": {}}, icloud_libraries=("PrimarySync",))
    assert "--library" not in _base_command(
        dataclasses.replace(cfg, icloud_libraries=("PrimarySync",)), "PrimarySync"
    )


def test_no_library_option_by_default(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}})

    run_import(cfg, store)

    assert all("--library" not in call for call in calls())


def test_several_libraries_are_downloaded_one_after_the_other(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}}, icloud_libraries=("PrimarySync", "SharedSync-ABC"))

    run_import(cfg, store)

    auth, fav1, other1, fav2, other2 = calls()
    assert "--auth-only" in auth
    assert "--library" not in fav1 and "--library" not in other1
    assert fav2[fav2.index("--library") + 1] == "SharedSync-ABC"
    assert other2[other2.index("--library") + 1] == "SharedSync-ABC"
    assert fav2[fav2.index("--album") + 1] == "Favorites"


@needs_exiftool
def test_photos_of_a_shared_library_are_sorted_and_favorites_marked(icloud):
    configure, calls, store, dirs, tmp = icloud
    fav = source(tmp, "g1.jpg", (200, 0, 0), "2025:03:04 10:00:00")
    other = source(tmp, "g2.jpg", (0, 200, 0), "2025:03:05 10:00:00")
    cfg = configure(
        {
            "downloads": {
                "SharedSync-ABC|Favorites": [{"name": "IMG_G1.jpg", "src": fav}],
                "SharedSync-ABC|ALL": [
                    {"name": "IMG_G1.jpg", "src": fav},
                    {"name": "IMG_G2.jpg", "src": other},
                ],
            }
        },
        icloud_libraries=("SharedSync-ABC",),
    )

    result = run_import(cfg, store)

    assert result.status == "ok"
    assert (dirs["target"] / "2025/2025_03/2025_03_04/IMG_G1.jpg.xmp").exists()
    assert not (dirs["target"] / "2025/2025_03/2025_03_05/IMG_G2.jpg.xmp").exists()


def test_keep_days_only_applies_to_the_all_photos_download(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}}, icloud_keep_days=14)

    result = run_import(cfg, store)

    auth, fav, other = calls()
    assert "--keep-icloud-recent-days" not in fav
    assert other[other.index("--keep-icloud-recent-days") + 1] == "14"
    assert "staat AAN" in event_messages(store, result.run_id)


def test_nothing_is_deleted_in_icloud_by_default(icloud):
    configure, calls, store, dirs, tmp = icloud
    run_import(configure({"downloads": {}}), store)

    assert all("--keep-icloud-recent-days" not in c and "--delete-after-download" not in c for c in calls())


def test_no_deletion_when_the_favorites_download_fails(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}, "fail_album": "Favorites"}, icloud_keep_days=14)

    run_import(cfg, store)

    assert len(calls()) == 2  # inloggen + favorieten; 'overig' (met wissen) is niet gestart


def test_dry_run_never_starts_icloudpd(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}}, icloud_keep_days=14).with_dry_run(True)

    run_import(cfg, store)

    assert calls() == []


class _Log:
    def info(self, *a, **k): ...
    warn = error = info


def test_list_libraries_returns_names(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"libraries": ["PrimarySync", "SharedSync-ABC-123"]})

    names, message = list_libraries(cfg, store, _Log())

    assert names == ["PrimarySync", "SharedSync-ABC-123"]
    assert "2" in message


def test_list_libraries_reports_apple_error(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"libraries_fail": "has not finished indexing yet"})

    names, message = list_libraries(cfg, store, _Log())

    assert names is None
    assert "indexing" in message


def test_list_libraries_resets_the_waiting_for_code_state(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"libraries": ["PrimarySync"]})
    store.set_state("icloud_status", "wacht_op_code")

    list_libraries(cfg, store, _Log())

    assert store.get_state("icloud_status") == "ok"


def test_run_waits_for_a_busy_icloud_action_and_says_so(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}})
    ICLOUD_LOCK.acquire()
    done = threading.Event()

    def go():
        run_import(cfg, store)
        done.set()

    t = threading.Thread(target=go, daemon=True)
    t.start()
    assert not done.wait(1.0)  # wacht op de lock
    ICLOUD_LOCK.release()
    assert done.wait(15)
    runs = store.list_runs(1)
    assert "ik wacht tot die klaar is" in event_messages(store, runs[0]["id"])


def test_list_libraries_refuses_when_icloud_is_busy(icloud):
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({})
    with ICLOUD_LOCK:
        names, message = list_libraries(cfg, store, _Log())
    assert names is None and "bezig" in message


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


# --- probe_icloud (knop 'Test iCloud') ------------------------------------
def test_probe_icloud_ok_finds_album_in_each_library(icloud):
    from photoimporter.icloud import probe_icloud

    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"libraries": ["PrimarySync", "SharedSync-X"]}, icloud_libraries=("SharedSync-X",))

    result = probe_icloud(cfg, store, _Log())

    assert result["ok"] is True
    names = [s["name"] for s in result["steps"]]
    assert "Favorietenalbum in SharedSync-X" in names


def test_probe_icloud_reports_missing_album_with_alternatives(icloud):
    from photoimporter.icloud import probe_icloud

    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"albums": ["Favorieten"]}, icloud_favorites_album="Favorites")

    result = probe_icloud(cfg, store, _Log())

    assert result["ok"] is False
    last = result["steps"][-1]
    assert not last["ok"] and "Favorieten" in last["message"]


def test_probe_icloud_requires_credentials(icloud):
    from photoimporter.icloud import probe_icloud

    configure, calls, store, dirs, tmp = icloud
    cfg = configure({}, icloud_password="")

    result = probe_icloud(cfg, store, _Log())

    assert result["ok"] is False and "wachtwoord" in result["steps"][0]["message"]
