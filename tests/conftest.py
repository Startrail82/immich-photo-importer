from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from photoimporter.config import Config
from photoimporter.store import Store

needs_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool ontbreekt")


@pytest.fixture
def env(tmp_path: Path):
    dirs = {
        "fav": tmp_path / "import" / "favorieten",
        "other": tmp_path / "import" / "overig",
        "inbox": tmp_path / "import" / "inbox",
        "target": tmp_path / "target",
        "data": tmp_path / "data",
    }
    for key in ("fav", "other", "inbox", "target"):
        dirs[key].mkdir(parents=True)
    cfg = Config(
        source_favorites=dirs["fav"],
        source_other=dirs["other"],
        source_inbox=dirs["inbox"],
        target=dirs["target"],
        data_dir=dirs["data"],
        min_age_inbox=0,
        min_age_icloud=0,
    )
    store = Store(dirs["data"] / "test.db")
    yield cfg, store, dirs
    store.close()


def make_jpeg(path: Path, color: tuple[int, int, int], exif_date: str | None = None) -> Path:
    """Maakt een klein JPEG-bestand, eventueel met EXIF-datum ('YYYY:MM:DD HH:MM:SS')."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path, "JPEG")
    if exif_date:
        subprocess.run(
            ["exiftool", "-q", "-overwrite_original", f"-DateTimeOriginal={exif_date}", str(path)],
            check=True,
        )
    return path


def set_mtime(path: Path, when: datetime) -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))
