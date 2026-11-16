from __future__ import annotations

import dataclasses

import pytest
from conftest import make_jpeg, needs_exiftool
from fake_immich import FakeImmich, FakeImmichState

from photoimporter.cli import immich_check
from photoimporter.immich import ImmichClient, ImmichError
from photoimporter.immich_sync import to_immich_path
from photoimporter.organizer import run_import

IMMICH_ROOT = "/mnt/nfs/nas1/photo/Ongesorteerd"


@pytest.fixture
def fake():
    state = FakeImmichState(import_paths=[IMMICH_ROOT])
    with FakeImmich(state) as server:
        yield server


def immich_cfg(cfg, fake, **overrides):
    values = dict(
        immich_url=fake.url,
        immich_api_key="geheim",
        immich_target_path=IMMICH_ROOT,
        immich_wait_seconds=0,
        immich_poll_seconds=0,
    )
    values.update(overrides)
    return dataclasses.replace(cfg, **values)


def immich_path_of(dirs, y, m, d, name):
    return f"{IMMICH_ROOT}/{y}/{y}_{m:02d}/{y}_{m:02d}_{d:02d}/{name}"


# --- client ---------------------------------------------------------------
def test_client_finds_library_by_exact_and_parent_path(fake):
    client = ImmichClient(fake.url, "geheim")
    assert client.find_library_id(IMMICH_ROOT) == "lib-1"
    assert client.find_library_id(IMMICH_ROOT + "/") == "lib-1"
    fake.state.libraries[0]["importPaths"] = ["/mnt/nfs/nas1/photo"]
    assert client.find_library_id(IMMICH_ROOT) == "lib-1"  # bovenliggende map
    fake.state.libraries[0]["importPaths"] = ["/ergens/anders"]
    assert client.find_library_id(IMMICH_ROOT) is None


def test_client_accepts_url_with_or_without_api_suffix(fake):
    assert ImmichClient(fake.url + "/api", "geheim").ping()
    assert ImmichClient(fake.url + "/", "geheim").ping()


def test_client_reports_bad_key_with_hint(fake):
    with pytest.raises(ImmichError) as err:
        ImmichClient(fake.url, "fout").list_libraries()
    assert err.value.status == 401
    assert "rechten" in str(err.value)


def test_client_reports_unreachable_server():
    with pytest.raises(ImmichError, match="niet bereikbaar"):
        ImmichClient("http://127.0.0.1:9", "x", timeout=1).ping()


def test_client_search_requires_exact_path_match(fake):
    fake.state.assets[IMMICH_ROOT + "/a.jpg"] = "id-a"
    client = ImmichClient(fake.url, "geheim")
    assert client.find_asset_id(IMMICH_ROOT + "/a.jpg") == "id-a"
    assert client.find_asset_id(IMMICH_ROOT + "/b.jpg") is None


def test_path_mapping(env):
    cfg, _store, dirs = env
    cfg = dataclasses.replace(cfg, immich_target_path=IMMICH_ROOT)
    host = dirs["target"] / "2024" / "2024_07" / "2024_07_08" / "IMG_9.jpg"
    assert to_immich_path(cfg, host) == immich_path_of(dirs, 2024, 7, 8, "IMG_9.jpg")


# --- volledige run met Immich ---------------------------------------------
@needs_exiftool
def test_run_scans_library_and_marks_favorite(env, fake):
    cfg, store, dirs = env
    cfg = immich_cfg(cfg, fake)
    make_jpeg(dirs["fav"] / "IMG_9.jpg", (5, 5, 5), "2024:07:08 09:10:11")
    make_jpeg(dirs["other"] / "IMG_10.jpg", (6, 6, 6), "2024:07:08 09:10:12")
    fake.state.assets[immich_path_of(dirs, 2024, 7, 8, "IMG_9.jpg")] = "asset-9"
    fake.state.assets[immich_path_of(dirs, 2024, 7, 8, "IMG_10.jpg")] = "asset-10"

    result = run_import(cfg, store)

    assert result.status == "ok"
    assert fake.state.scan_calls == ["lib-1"]
    assert fake.state.favorite_calls == [["asset-9"]]  # alleen de favoriet, niet de gewone foto
    assert store.list_pending_favorites() == []
    assert result.stats.favorites_set == 1
    assert result.stats.favorites_waiting == 0
    assert set(fake.state.keys_seen) == {"geheim"}


@needs_exiftool
def test_favorite_not_yet_known_stays_pending_then_succeeds_next_run(env, fake):
    cfg, store, dirs = env
    cfg = immich_cfg(cfg, fake)  # wachttijd 0: een enkele poging
    make_jpeg(dirs["fav"] / "IMG_9.jpg", (5, 5, 5), "2024:07:08 09:10:11")
    immich_path = immich_path_of(dirs, 2024, 7, 8, "IMG_9.jpg")

    first = run_import(cfg, store)
    assert first.stats.favorites_waiting == 1
    assert fake.state.favorite_calls == []
    assert len(store.list_pending_favorites()) == 1

    fake.state.assets[immich_path] = "asset-9"  # Immich heeft de foto inmiddels geimporteerd
    second = run_import(cfg, store)  # geen nieuwe bestanden, wachtrij wordt toch verwerkt
    assert fake.state.favorite_calls == [["asset-9"]]
    assert second.stats.favorites_set == 1
    assert store.list_pending_favorites() == []
    assert fake.state.scan_calls == ["lib-1"]  # tweede run had niets nieuws, dus geen extra scan


@needs_exiftool
def test_waits_until_immich_knows_the_file(env, fake):
    cfg, store, dirs = env
    cfg = immich_cfg(cfg, fake, immich_wait_seconds=10)
    make_jpeg(dirs["fav"] / "IMG_9.jpg", (5, 5, 5), "2024:07:08 09:10:11")
    fake.state.assets[immich_path_of(dirs, 2024, 7, 8, "IMG_9.jpg")] = "asset-9"
    fake.state.hide_first_searches = 2  # eerste twee pogingen vinden niets

    result = run_import(cfg, store)

    assert fake.state.favorite_calls == [["asset-9"]]
    assert fake.state.search_calls == 3
    assert result.stats.favorites_waiting == 0


@needs_exiftool
def test_favorite_is_not_set_again_after_done(env, fake):
    """Haal je een foto in Immich zelf weer van favoriet, dan zetten wij hem niet terug."""
    cfg, store, dirs = env
    cfg = immich_cfg(cfg, fake)
    make_jpeg(dirs["fav"] / "IMG_9.jpg", (5, 5, 5), "2024:07:08 09:10:11")
    fake.state.assets[immich_path_of(dirs, 2024, 7, 8, "IMG_9.jpg")] = "asset-9"
    run_import(cfg, store)
    # dezelfde foto komt later opnieuw als favoriet binnen (bv. opnieuw gedownload)
    (dirs["fav"] / "IMG_9.jpg").write_bytes(
        (dirs["target"] / "2024" / "2024_07" / "2024_07_08" / "IMG_9.jpg").read_bytes()
    )
    run_import(cfg, store)
    assert fake.state.favorite_calls == [["asset-9"]]  # nog steeds maar een keer


@needs_exiftool
def test_immich_error_does_not_lose_files_and_keeps_pending(env, fake):
    cfg, store, dirs = env
    cfg = immich_cfg(cfg, fake, immich_api_key="verkeerd")
    make_jpeg(dirs["fav"] / "IMG_9.jpg", (5, 5, 5), "2024:07:08 09:10:11")

    result = run_import(cfg, store)

    assert result.status == "ok_met_fouten"
    assert (dirs["target"] / "2024" / "2024_07" / "2024_07_08" / "IMG_9.jpg").exists()
    assert len(store.list_pending_favorites()) == 1
    errors = [e for e in store.list_events(result.run_id) if e["level"] == "ERROR"]
    assert errors and "rechten" in errors[0]["message"]


@needs_exiftool
def test_missing_library_is_reported(env, fake):
    cfg, store, dirs = env
    fake.state.libraries[0]["importPaths"] = ["/ergens/anders"]
    cfg = immich_cfg(cfg, fake)
    make_jpeg(dirs["other"] / "IMG_1.jpg", (1, 1, 1), "2024:01:01 01:01:01")
    result = run_import(cfg, store)
    assert result.status == "ok_met_fouten"
    assert fake.state.scan_calls == []


@needs_exiftool
def test_dry_run_makes_no_immich_calls(env, fake):
    cfg, store, dirs = env
    cfg = immich_cfg(cfg, fake).with_dry_run(True)
    make_jpeg(dirs["fav"] / "IMG_9.jpg", (5, 5, 5), "2024:07:08 09:10:11")
    run_import(cfg, store)
    assert fake.state.keys_seen == []


@needs_exiftool
def test_without_immich_config_run_still_works(env):
    cfg, store, dirs = env
    make_jpeg(dirs["fav"] / "IMG_9.jpg", (5, 5, 5), "2024:07:08 09:10:11")
    result = run_import(cfg, store)
    assert result.status == "ok"
    assert len(store.list_pending_favorites()) == 1  # blijft klaarstaan voor later


# --- immich-check ---------------------------------------------------------
def test_immich_check_ok(env, fake):
    cfg, _store, _dirs = env
    assert immich_check(immich_cfg(cfg, fake)) == 0


def test_immich_check_wrong_key(env, fake):
    cfg, _store, _dirs = env
    assert immich_check(immich_cfg(cfg, fake, immich_api_key="fout")) == 1


def test_immich_check_missing_settings(env):
    cfg, _store, _dirs = env
    assert immich_check(cfg) == 2


# --- check_immich (knop 'Test Immich') ------------------------------------
def test_check_immich_ok(env, fake):
    from photoimporter.checks import check_immich

    cfg, _store, _dirs = env
    result = check_immich(immich_cfg(cfg, fake))
    assert result["ok"] is True
    assert any(s["name"] == "Externe bibliotheek" and s["ok"] for s in result["steps"])


def test_check_immich_wrong_key_stops_with_clear_step(env, fake):
    from photoimporter.checks import check_immich

    cfg, _store, _dirs = env
    result = check_immich(immich_cfg(cfg, fake, immich_api_key="fout"))
    assert result["ok"] is False
    assert "401" in result["steps"][-1]["message"]


def test_check_immich_lists_missing_fields(env):
    from photoimporter.checks import check_immich

    cfg, _store, _dirs = env
    result = check_immich(cfg)
    assert result["ok"] is False and "URL" in result["steps"][0]["message"]
