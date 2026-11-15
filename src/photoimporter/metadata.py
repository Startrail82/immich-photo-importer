"""Datums bepalen: eerst EXIF/QuickTime (via exiftool), anders de bestandsdatum."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# Volgorde van voorkeur. CreationDate is de Apple-QuickTime-variant met tijdzone.
DATE_TAGS = (
    "DateTimeOriginal",
    "CreationDate",
    "CreateDate",
    "MediaCreateDate",
    "TrackCreateDate",
)

_EXIF_RE = re.compile(r"^\s*(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


class ExifToolError(RuntimeError):
    pass


def parse_exif_datetime(value: object, now: datetime | None = None) -> datetime | None:
    """Zet een EXIF-datumtekst om naar een datetime, of None als hij onbruikbaar is."""
    if not isinstance(value, str):
        return None
    match = _EXIF_RE.match(value)
    if not match:
        return None
    year, month, day, hour, minute, second = (int(g) for g in match.groups())
    if year < 1970:  # o.a. "0000:00:00 00:00:00"
        return None
    try:
        result = datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
    limit = (now or datetime.now()) + timedelta(days=2)
    if result > limit:
        return None
    return result


def read_exif_dates(
    paths: list[Path], exiftool: str = "exiftool", chunk_size: int = 200
) -> dict[Path, tuple[datetime, str] | None]:
    """Leest per bestand de beste EXIF-datum. Waarde None = geen bruikbare datum."""
    results: dict[Path, tuple[datetime, str] | None] = {}
    for start in range(0, len(paths), chunk_size):
        batch = paths[start : start + chunk_size]
        results.update(_read_batch(batch, exiftool))
    return results


def _read_batch(
    batch: list[Path], exiftool: str
) -> dict[Path, tuple[datetime, str] | None]:
    cmd = [
        exiftool,
        "-json",
        "-m",
        "-api", "QuickTimeUTC=1",
        "-charset", "filename=utf8",
        *[f"-{tag}" for tag in DATE_TAGS],
        "-@", "-",
    ]
    stdin = "\n".join(str(p) for p in batch).encode("utf-8")
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise ExifToolError("exiftool is niet gevonden (is het geinstalleerd?)") from exc

    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    if not stdout:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise ExifToolError(f"exiftool gaf geen uitvoer (code {proc.returncode}): {stderr}")
    try:
        records = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ExifToolError(f"exiftool gaf ongeldige JSON: {exc}") from exc

    by_source = {rec.get("SourceFile"): rec for rec in records}
    out: dict[Path, tuple[datetime, str] | None] = {}
    for path in batch:
        rec = by_source.get(str(path)) or {}
        out[path] = _best_date(rec)
    return out


def _best_date(record: dict) -> tuple[datetime, str] | None:
    for tag in DATE_TAGS:
        parsed = parse_exif_datetime(record.get(tag))
        if parsed is not None:
            return parsed, f"EXIF {tag}"
    return None


def file_date(path: Path) -> datetime:
    """Terugvaldatum: de oudste van wijzigings- en (indien beschikbaar) aanmaakdatum."""
    st = path.stat()
    candidates = [st.st_mtime]
    birth = getattr(st, "st_birthtime", None)
    if birth and birth > 0:
        candidates.append(birth)
    return datetime.fromtimestamp(min(candidates))
