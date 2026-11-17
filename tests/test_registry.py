"""Administratie van binnengehaalde bestanden, --recent en het opruimcommando."""

from __future__ import annotations

import dataclasses
import os
import subprocess

from conftest import make_jpeg, needs_exiftool
from test_icloud import icloud  # noqa: F401  (fixture)

from photoimporter.dedupe import find_duplicates, run_find_duplicates
from photoimporter.organizer import run_import
from photoimporter.scheduler import Scheduler
from photoimporter.sidecar import sidecar_path


def day(dirs, y, m, d):
    return dirs["target"] / f"{y}" / f"{y}_{m:02d}" / f"{y}_{m:02d}_{d:02d}"


@needs_exiftool
def test_registry_detects_duplicate_after_nas_file_was_modified(env):
    cfg, store, dirs = env
    make_jpeg(dirs["fav"] / "IMG_1.jpg", (1, 2, 3), "2024:01:02 03:04:05")
    run_import(cfg, store)
    placed = day(dirs, 2024, 1, 2) / "IMG_1.jpg"
    assert store.count_imported() == 1
    # Immich schrijft later een rating in het bestand zelf: de hash op de NAS verandert
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-XMP:Rating=5", str(placed)], check=True)
    make_jpeg(dirs["fav"] / "IMG_1.jpg", (1, 2, 3), "2024:01:02 03:04:05")
    result = run_import(cfg, store)
    assert result.stats.duplicates_removed == 1
    assert sorted(p.name for p in day(dirs, 2024, 1, 2).iterdir() if p.suffix == ".jpg") == ["IMG_1.jpg"]


@needs_exiftool
def test_registry_detects_duplicate_under_other_name_and_extension(env):
    cfg, store, dirs = env
    first = make_jpeg(dirs["other"] / "IMG_2.jpg", (9, 9, 9), "2024:02:03 04:05:06")
    data = first.read_bytes()
    run_import(cfg, store)
    (dirs["other"] / "IMG_2-adjusted.JPEG").write_bytes(data)
    result = run_import(cfg, store)
    assert result.stats.duplicates_removed == 1
    assert [p.name for p in day(dirs, 2024, 2, 3).iterdir()] == ["IMG_2.jpg"]


@needs_exiftool
def test_same_image_with_extra_metadata_is_not_a_new_copy_without_registry(env):
    cfg, store, dirs = env
    make_jpeg(dirs["other"] / "IMG_3.jpg", (4, 4, 4), "2024:03:04 05:06:07")
    run_import(cfg, store)
    placed = day(dirs, 2024, 3, 4) / "IMG_3.jpg"
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-XMP:Rating=5", str(placed)], check=True)
    store._conn.execute("DELETE FROM imported")  # bestand van voor de administratie
    make_jpeg(dirs["other"] / "IMG_3.jpg", (4, 4, 4), "2024:03:04 05:06:07")
    result = run_import(cfg, store)
    assert result.stats.duplicates_removed == 1
    assert [p.name for p in day(dirs, 2024, 3, 4).iterdir()] == ["IMG_3.jpg"]


@needs_exiftool
def test_favorite_duplicate_still_marks_pending_favorite(env):
    cfg, store, dirs = env
    make_jpeg(dirs["other"] / "IMG_4.jpg", (7, 7, 7), "2024:04:05 06:07:08")
    run_import(cfg, store)
    make_jpeg(dirs["fav"] / "IMG_4.jpg", (7, 7, 7), "2024:04:05 06:07:08")
    run_import(cfg, store)
    placed = day(dirs, 2024, 4, 5) / "IMG_4.jpg"
    assert sidecar_path(placed).exists()
    assert store.list_pending_favorites() == [str(placed)]


@needs_exiftool
def test_moved_or_deleted_file_is_never_placed_again(env):
    cfg, store, dirs = env
    make_jpeg(dirs["other"] / "IMG_5.jpg", (3, 3, 3), "2024:05:06 07:08:09")
    run_import(cfg, store)
    (day(dirs, 2024, 5, 6) / "IMG_5.jpg").rename(dirs["data"] / "ergens-anders.jpg")  # bewust verplaatst
    again = make_jpeg(dirs["other"] / "IMG_5.jpg", (3, 3, 3), "2024:05:06 07:08:09")
    result = run_import(cfg, store)
    assert result.stats.duplicates_removed == 1
    assert not again.exists()
    assert not (day(dirs, 2024, 5, 6) / "IMG_5.jpg").exists()


@needs_exiftool
def test_same_file_dropped_in_inbox_is_a_duplicate(env):
    cfg, store, dirs = env
    first = make_jpeg(dirs["other"] / "IMG_7.jpg", (8, 8, 8), "2024:07:08 09:10:11")
    data = first.read_bytes()
    run_import(cfg, store)
    (dirs["inbox"] / "handmatig.jpg").write_bytes(data)
    result = run_import(cfg, store)
    assert result.stats.duplicates_removed == 1


@needs_exiftool
def test_dry_run_records_nothing(env):
    cfg, store, dirs = env
    make_jpeg(dirs["other"] / "IMG_6.jpg", (2, 2, 2), "2024:06:07 08:09:10")
    run_import(cfg.with_dry_run(True), store)
    assert store.count_imported() == 0


@needs_exiftool
def test_find_duplicates_lists_first_and_deletes_only_with_apply(env, capsys):
    cfg, store, dirs = env
    d = dirs["target"] / "2024" / "2024_01" / "2024_01_02"
    clean = make_jpeg(d / "IMG_1.JPG", (1, 2, 3), "2024:01:02 03:04:05")
    copy = d / "IMG_1_1.JPG"
    copy.write_bytes(clean.read_bytes())
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-XMP:Rating=5", str(copy)], check=True)
    other = d / "IMG_1-adjusted.JPEG"
    other.write_bytes(clean.read_bytes())
    different = make_jpeg(d / "IMG_9.JPG", (200, 0, 0), "2024:01:02 03:05:05")

    groups = find_duplicates(dirs["target"], lambda *_: None)
    assert len(groups) == 1 and groups[0].keep == clean
    assert sorted(p.name for p in groups[0].remove) == ["IMG_1-adjusted.JPEG", "IMG_1_1.JPG"]

    run_find_duplicates(dirs["target"], apply=False, log=lambda *_: None)
    assert copy.exists() and other.exists()

    run_find_duplicates(dirs["target"], apply=True, log=lambda *_: None)
    assert clean.exists() and different.exists()
    assert not copy.exists() and not other.exists()


@needs_exiftool
def test_find_duplicates_prefers_file_with_sidecar_and_removes_loser_sidecar(env):
    cfg, store, dirs = env
    d = dirs["target"] / "2024" / "2024_01" / "2024_01_02"
    a = make_jpeg(d / "IMG_1.JPG", (1, 2, 3), "2024:01:02 03:04:05")
    b = d / "IMG_1_1.JPG"
    b.write_bytes(a.read_bytes())
    sidecar_path(b).write_text("x")
    groups = find_duplicates(dirs["target"], lambda *_: None)
    assert groups[0].keep == b
    run_find_duplicates(dirs["target"], apply=True, log=lambda *_: None)
    assert b.exists() and not a.exists()


@needs_exiftool
def test_find_duplicates_ignores_synology_metadata_folders(env):
    cfg, store, dirs = env
    d = dirs["target"] / "2024" / "2024_01" / "2024_01_02"
    a = make_jpeg(d / "IMG_1.JPG", (1, 2, 3), "2024:01:02 03:04:05")
    (d / "@eaDir").mkdir()
    (d / "@eaDir" / "IMG_1.JPG").write_bytes(a.read_bytes())
    assert find_duplicates(dirs["target"], lambda *_: None) == []


@needs_exiftool
def test_find_duplicates_keeps_the_clean_name_even_if_the_copy_is_older(env):
    cfg, store, dirs = env
    d = dirs["target"] / "2024" / "2024_01" / "2024_01_02"
    plain = make_jpeg(d / "IMG_5357.JPG", (1, 2, 3), "2024:01:02 03:04:05")
    copy = d / "IMG_5357_1.JPG"
    copy.write_bytes(plain.read_bytes())
    os.utime(copy, (1_000_000, 1_000_000))  # de kopie is ouder
    groups = find_duplicates(dirs["target"], lambda *_: None)
    assert groups[0].keep == plain and groups[0].remove == [copy]


def test_every_run_reads_all_photos(icloud):  # noqa: F811
    configure, calls, store, dirs, tmp = icloud
    cfg = configure({"downloads": {}})
    run_import(cfg, store)
    run_import(cfg, store)
    assert all("--recent" not in c for c in calls())
