from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from photoimporter.config import Config, ConfigError
from photoimporter.media import classify
from photoimporter.metadata import parse_exif_datetime
from photoimporter.sidecar import ensure_rating, sidecar_path, stem_sidecar_path


def test_parse_valid_exif_date():
    assert parse_exif_datetime("2024:03:15 10:11:12") == datetime(2024, 3, 15, 10, 11, 12)


def test_parse_date_with_timezone_suffix_keeps_local_part():
    assert parse_exif_datetime("2024:03:15 10:11:12+01:00") == datetime(2024, 3, 15, 10, 11, 12)


@pytest.mark.parametrize(
    "value",
    ["0000:00:00 00:00:00", "", "niets", None, 12, "2024:13:45 10:00:00", "1960:01:01 00:00:00"],
)
def test_parse_rejects_unusable_values(value):
    assert parse_exif_datetime(value) is None


def test_parse_rejects_far_future():
    assert parse_exif_datetime("2999:01:01 00:00:00") is None


@pytest.mark.parametrize(
    "name,expected",
    [
        ("a.JPG", "image"), ("a.heic", "image"), ("a.MOV", "video"), ("a.mp4", "video"),
        ("a.xmp", "ignored"), (".DS_Store", "ignored"), ("._a.jpg", "ignored"),
        ("Thumbs.db", "ignored"), ("a.txt", "unknown"), ("a", "unknown"),
    ],
)
def test_classify(name, expected):
    assert classify(Path(name)) == expected


def test_sidecar_name_and_idempotence(tmp_path):
    media = tmp_path / "IMG_1.jpg"
    media.write_bytes(b"x")
    assert sidecar_path(media).name == "IMG_1.jpg.xmp"
    assert stem_sidecar_path(media).name == "IMG_1.xmp"
    assert ensure_rating(media) == "created"
    assert ensure_rating(media) == "unchanged"  # staat al op 5
    assert 'xmp:Rating="5"' in stem_sidecar_path(media).read_text()


def test_config_rejects_nested_directories(tmp_path):
    base = tmp_path / "x"
    with pytest.raises(ConfigError):
        Config(
            source_favorites=base / "fav",
            source_other=base / "fav" / "binnen",
            source_inbox=base / "inbox",
            target=base / "target",
            data_dir=base / "data",
        ).validate()


def test_config_rejects_target_inside_source(tmp_path):
    base = tmp_path / "x"
    with pytest.raises(ConfigError):
        Config(
            source_favorites=base / "fav",
            source_other=base / "other",
            source_inbox=base / "inbox",
            target=base / "inbox" / "doel",
            data_dir=base / "data",
        ).validate()
