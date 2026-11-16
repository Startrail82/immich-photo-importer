"""EXIF-fouten mogen een run niet tegenhouden: de bestandsdatum is de terugval."""

from __future__ import annotations

from pathlib import Path

import pytest

from photoimporter.metadata import ExifToolError, ExifToolMissing, read_exif_dates
from photoimporter.organizer import plan_dates, run_import


def test_read_exif_dates_reports_a_missing_tool_and_stops(tmp_path):
    files = [tmp_path / f"f{i}.jpg" for i in range(3)]
    for path in files:
        path.write_bytes(b"x")
    seen: list[tuple[str, int]] = []
    result = read_exif_dates(
        files,
        exiftool=str(tmp_path / "bestaat-niet"),
        chunk_size=2,
        on_error=lambda exc, batch: seen.append((type(exc).__name__, len(batch))) or True,
    )
    assert result == {path: None for path in files}
    assert seen == [("ExifToolMissing", 2)]  # na de eerste mislukking stopt het lezen


def test_read_exif_dates_keeps_going_after_a_failed_batch(tmp_path, monkeypatch):
    files = [tmp_path / f"f{i}.jpg" for i in range(4)]
    for path in files:
        path.write_bytes(b"x")
    calls: list[list[Path]] = []

    def flaky(batch, exiftool):
        calls.append(list(batch))
        if len(calls) == 1:
            raise ExifToolError("deze batch ging fout")
        return {path: None for path in batch}

    monkeypatch.setattr("photoimporter.metadata._read_batch", flaky)
    errors: list[str] = []
    result = read_exif_dates(files, chunk_size=2, on_error=lambda exc, batch: errors.append(str(exc)))
    assert result == {path: None for path in files}
    assert errors == ["deze batch ging fout"] and len(calls) == 2


def test_read_exif_dates_still_raises_without_a_callback(tmp_path):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"x")
    with pytest.raises(ExifToolMissing):
        read_exif_dates([path], exiftool=str(tmp_path / "bestaat-niet"))


def test_plan_dates_falls_back_instead_of_raising(tmp_path, monkeypatch):
    from photoimporter.organizer import Item, RunStats

    image = tmp_path / "a.jpg"
    image.write_bytes(b"x")
    item = Item(path=image, favorite=False, source="inbox", is_video=False)

    def failing_read(paths, on_error=None):
        on_error(ExifToolMissing("weg"), list(paths))
        return {Path(p): None for p in paths}

    monkeypatch.setattr("photoimporter.organizer.read_exif_dates", failing_read)
    messages: list[tuple[str, str]] = []

    class Log:
        def info(self, step, message, path=None):
            pass

        def warn(self, step, message, path=None):
            messages.append(("warn", message))

        def error(self, step, message, path=None):
            messages.append(("error", message))

    stats = RunStats()
    plan = plan_dates([item], None, Log(), stats)
    assert len(plan) == 1 and plan[0].fallback is True
    assert stats.errors == 1
    assert messages and messages[0][0] == "error" and "bestandsdatum" in messages[0][1]


def test_a_run_still_sorts_when_exiftool_is_missing(env, monkeypatch):
    """Zonder exiftool hoort een run te sorteren met bestandsdatums, niet af te breken."""
    cfg, store, dirs = env
    monkeypatch.setattr(
        "photoimporter.metadata._read_batch",
        lambda batch, exiftool: (_ for _ in ()).throw(ExifToolMissing("exiftool is niet gevonden")),
    )
    (dirs["inbox"] / "2020-05-06 foto.jpg").write_bytes(b"\xff\xd8\xff\xe0 nep")
    result = run_import(cfg, store, trigger="test")
    assert result.status == "ok_met_fouten"
    assert result.stats.moved == 1
    assert result.stats.errors == 1
    moved = [p for p in dirs["target"].rglob("*.jpg")]
    assert len(moved) == 1 and moved[0].parent.parent.parent.name.isdigit()
    events = store.list_events(result.run_id, 0, 200)
    assert any(e["level"] == "ERROR" and "bestandsdatum" in e["message"] for e in events)
