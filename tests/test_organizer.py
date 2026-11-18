from __future__ import annotations

from datetime import datetime

from conftest import make_jpeg, needs_exiftool, set_mtime

from photoimporter.organizer import RunAlreadyActive, run_import, run_lock
from photoimporter.sidecar import sidecar_path

import pytest


def day_dir(dirs, y, m, d):
    return dirs["target"] / f"{y}" / f"{y}_{m:02d}" / f"{y}_{m:02d}_{d:02d}"


@needs_exiftool
def test_exif_date_decides_folder(env):
    cfg, store, dirs = env
    make_jpeg(dirs["other"] / "IMG_0001.jpg", (255, 0, 0), "2024:03:15 10:11:12")
    result = run_import(cfg, store)
    assert result.status == "ok"
    assert (day_dir(dirs, 2024, 3, 15) / "IMG_0001.jpg").exists()
    assert not (dirs["other"] / "IMG_0001.jpg").exists()
    assert result.stats.moved == 1
    assert result.stats.date_fallbacks == 0


@needs_exiftool
def test_no_exif_uses_file_date_and_creates_review_item(env):
    cfg, store, dirs = env
    photo = make_jpeg(dirs["other"] / "whatsapp.jpg", (0, 255, 0))
    set_mtime(photo, datetime(2023, 5, 6, 12, 0, 0))
    result = run_import(cfg, store)
    assert (day_dir(dirs, 2023, 5, 6) / "whatsapp.jpg").exists()
    assert result.stats.date_fallbacks == 1
    review = store.list_review()
    assert [r["kind"] for r in review] == ["date_fallback"]


@needs_exiftool
def test_identical_duplicate_is_removed(env):
    cfg, store, dirs = env
    original = make_jpeg(dirs["other"] / "IMG_1.jpg", (1, 2, 3), "2024:01:02 03:04:05")
    run_import(cfg, store)
    # dezelfde foto komt opnieuw binnen (bv. uit het album Favorieten)
    again = dirs["fav"] / "IMG_1.jpg"
    again.write_bytes((day_dir(dirs, 2024, 1, 2) / "IMG_1.jpg").read_bytes())
    result = run_import(cfg, store)
    assert result.stats.duplicates_removed == 1
    assert not again.exists()
    assert sorted(p.name for p in day_dir(dirs, 2024, 1, 2).iterdir() if p.suffix == ".jpg") == ["IMG_1.jpg"]


@needs_exiftool
def test_same_name_different_content_gets_number(env):
    cfg, store, dirs = env
    make_jpeg(dirs["other"] / "IMG_7.jpg", (10, 10, 10), "2024:06:01 08:00:00")
    run_import(cfg, store)
    make_jpeg(dirs["other"] / "IMG_7.jpg", (200, 200, 200), "2024:06:01 09:00:00")
    result = run_import(cfg, store)
    names = sorted(p.name for p in day_dir(dirs, 2024, 6, 1).iterdir())
    assert names == ["IMG_7.jpg", "IMG_7_1.jpg"]
    assert result.stats.renamed == 1
    assert "renamed" in [r["kind"] for r in store.list_review()]


@needs_exiftool
def test_favorite_gets_sidecar_and_pending_entry(env):
    cfg, store, dirs = env
    make_jpeg(dirs["fav"] / "IMG_9.jpg", (5, 5, 5), "2024:07:08 09:10:11")
    result = run_import(cfg, store)
    dest = day_dir(dirs, 2024, 7, 8) / "IMG_9.jpg"
    assert dest.exists()
    sidecar = sidecar_path(dest)
    assert sidecar.exists()
    assert 'xmp:Rating="5"' in sidecar.read_text()
    assert store.list_pending_favorites() == [str(dest)]
    assert result.stats.sidecars == 1


@needs_exiftool
def test_non_favorite_gets_no_sidecar(env):
    cfg, store, dirs = env
    make_jpeg(dirs["other"] / "IMG_10.jpg", (6, 6, 6), "2024:07:08 09:10:11")
    run_import(cfg, store)
    dest = day_dir(dirs, 2024, 7, 8) / "IMG_10.jpg"
    assert not sidecar_path(dest).exists()
    assert store.list_pending_favorites() == []


@needs_exiftool
def test_later_favorite_of_already_imported_photo_gets_sidecar(env):
    """Foto eerst als gewone foto verwerkt, later alsnog favoriet gemaakt in iCloud."""
    cfg, store, dirs = env
    make_jpeg(dirs["other"] / "IMG_11.jpg", (7, 7, 7), "2024:08:01 10:00:00")
    run_import(cfg, store)
    dest = day_dir(dirs, 2024, 8, 1) / "IMG_11.jpg"
    (dirs["fav"] / "IMG_11.jpg").write_bytes(dest.read_bytes())
    result = run_import(cfg, store)
    assert result.stats.duplicates_removed == 1
    assert sidecar_path(dest).exists()
    assert store.list_pending_favorites() == [str(dest)]


@needs_exiftool
def test_live_photo_video_follows_photo_date(env):
    cfg, store, dirs = env
    make_jpeg(dirs["other"] / "IMG_20.jpg", (9, 9, 9), "2024:09:10 11:12:13")
    video = dirs["other"] / "IMG_20.MOV"
    video.write_bytes(b"geen echte video")
    set_mtime(video, datetime(2020, 1, 1))  # moet genegeerd worden
    run_import(cfg, store)
    assert (day_dir(dirs, 2024, 9, 10) / "IMG_20.jpg").exists()
    assert (day_dir(dirs, 2024, 9, 10) / "IMG_20.MOV").exists()


@needs_exiftool
def test_unknown_type_stays_and_is_reported(env):
    cfg, store, dirs = env
    odd = dirs["other"] / "notities.txt"
    odd.write_text("hallo")
    result = run_import(cfg, store)
    assert odd.exists()
    assert result.stats.unknown == 1
    assert [r["kind"] for r in store.list_review()] == ["unknown_type"]


@needs_exiftool
def test_dry_run_changes_nothing(env):
    cfg, store, dirs = env
    photo = make_jpeg(dirs["fav"] / "IMG_30.jpg", (3, 3, 3), "2024:10:11 12:13:14")
    result = run_import(cfg.with_dry_run(True), store)
    assert photo.exists()
    assert list(dirs["target"].iterdir()) == []
    assert store.list_pending_favorites() == []
    assert store.list_review() == []
    assert result.stats.moved == 1  # telt wat er zou gebeuren


@needs_exiftool
def test_dry_run_detects_planned_name_conflicts(env):
    cfg, store, dirs = env
    make_jpeg(dirs["fav"] / "IMG_40.jpg", (1, 1, 1), "2024:11:01 10:00:00")
    make_jpeg(dirs["other"] / "IMG_40.jpg", (250, 1, 1), "2024:11:01 11:00:00")
    result = run_import(cfg.with_dry_run(True), store)
    assert result.stats.renamed == 1


@needs_exiftool
def test_inbox_skips_recent_files(env):
    cfg, store, dirs = env
    import dataclasses

    cfg = dataclasses.replace(cfg, min_age_inbox=3600)
    make_jpeg(dirs["inbox"] / "IMG_50.jpg", (4, 4, 4), "2024:12:01 10:00:00")
    result = run_import(cfg, store)
    assert (dirs["inbox"] / "IMG_50.jpg").exists()
    assert result.stats.skipped == 1
    assert result.stats.moved == 0


@needs_exiftool
def test_second_run_is_idempotent(env):
    cfg, store, dirs = env
    make_jpeg(dirs["other"] / "IMG_60.jpg", (8, 8, 8), "2024:12:24 18:00:00")
    run_import(cfg, store)
    result = run_import(cfg, store)
    assert result.stats.scanned == 0
    assert result.status == "ok"


@needs_exiftool
def test_empty_inbox_subfolder_is_cleaned_up(env):
    cfg, store, dirs = env
    make_jpeg(dirs["inbox"] / "sd-kaart" / "DCIM" / "IMG_70.jpg", (2, 2, 2), "2024:05:05 05:05:05")
    run_import(cfg, store)
    assert dirs["inbox"].exists()
    # de vaste submappen blijven staan, de rest is opgeruimd
    assert sorted(p.name for p in dirs["inbox"].iterdir()) == ["favorieten", "overig"]


def test_missing_target_aborts_without_creating_it(env):
    cfg, store, dirs = env
    import dataclasses
    import shutil

    shutil.rmtree(dirs["target"])
    result = run_import(dataclasses.replace(cfg), store)
    assert result.status == "mislukt"
    assert not dirs["target"].exists()


def test_only_one_run_at_a_time(env):
    cfg, store, dirs = env
    with run_lock(cfg.data_dir):
        with pytest.raises(RunAlreadyActive):
            run_import(cfg, store)
