"""Duplicaten herkennen op de eigenlijke beeldinhoud (ImageDataHash van exiftool).

De hash van het hele bestand verandert zodra er metadata bij komt (bv. een ingebouwde rating).
De hash van enkel de beeldgegevens niet; daarom herkennen we daarmee dezelfde foto onder een andere
naam, extensie of met aangepaste metadata.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .media import classify

_COPY = re.compile(r"(_\d{1,2}|-[A-Za-z]+(-\d+)?|-\d{5,})$")  # _1, _2, -adjusted, -1650727
_BATCH = 200


def image_md5(path: Path) -> str | None:
    """Hash van enkel de beeldgegevens, of None als exiftool die niet kan geven."""
    return image_md5_many([path]).get(path)


def image_md5_many(paths: list[Path]) -> dict[Path, str]:
    result: dict[Path, str] = {}
    for start in range(0, len(paths), _BATCH):
        chunk = paths[start : start + _BATCH]
        cmd = ["exiftool", "-q", "-q", "-j", "-api", "ImageHashType=MD5", "-ImageDataHash", "-ImageDataMD5", "-SourceFile"]
        try:
            proc = subprocess.run(cmd + [str(p) for p in chunk], capture_output=True, timeout=600)
            rows = json.loads(proc.stdout or b"[]")
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        by_name = {str(p): p for p in chunk}
        for row in rows:
            digest = row.get("ImageDataHash") or row.get("ImageDataMD5")  # nieuwe en oude tagnaam
            src = by_name.get(row.get("SourceFile", ""))
            if digest and src is not None:
                result[src] = str(digest)
    return result


def _clean_rank(path: Path) -> int:
    """0 = nette naam (IMG_1234.JPG), 1 = naam met kopie-achtervoegsel (_1, -adjusted, -1650727)."""
    return 1 if _COPY.search(path.stem) else 0


def sidecar_of(path: Path) -> Path:
    return path.with_name(path.name + ".xmp")


@dataclass
class Group:
    keep: Path
    remove: list[Path] = field(default_factory=list)


def find_duplicates(root: Path, log=print) -> list[Group]:
    media = [
        p for p in sorted(root.rglob("*"))
        if p.is_file()
        and not any(part.startswith("@") or part == "#recycle" for part in p.relative_to(root).parts)  # Synology
        and classify(p) in ("image", "video")
    ]
    log(f"{len(media)} bestanden gevonden, beeldinhoud wordt vergeleken...")
    hashes: dict[Path, str] = {}
    for start in range(0, len(media), 500):
        hashes.update(image_md5_many(media[start : start + 500]))
        log(f"voortgang: {min(start + 500, len(media))} van {len(media)}")
    buckets: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for path in media:
        digest = hashes.get(path)
        if digest:
            buckets[(path.parent.parent.as_posix(), digest)].append(path)  # per maandmap: dagmappen kunnen verschillen
    groups: list[Group] = []
    for paths in buckets.values():
        if len(paths) < 2:
            continue
        ranked = sorted(
            paths,
            key=lambda p: (not sidecar_of(p).exists(), _clean_rank(p), p.stat().st_mtime, p.name),
        )
        groups.append(Group(keep=ranked[0], remove=ranked[1:]))
    return sorted(groups, key=lambda g: str(g.keep))


def run_find_duplicates(root: Path, apply: bool, log=print) -> int:
    groups = find_duplicates(root, log)
    if not groups:
        log("geen duplicaten gevonden")
        return 0
    total = sum(len(g.remove) for g in groups)
    for group in groups:
        log(f"BEWAREN  {group.keep}")
        for path in group.remove:
            extra = " (+ .xmp)" if sidecar_of(path).exists() else ""
            log(f"  WISSEN {path}{extra}")
    log(f"{len(groups)} groepen, {total} bestanden zouden gewist worden")
    if not apply:
        log("er is NIETS gewist. Bekijk de lijst; wis pas met: photoimporter find-duplicates --apply")
        return 0
    for group in groups:
        for path in group.remove:
            path.unlink()
            side = sidecar_of(path)
            if side.exists():
                side.unlink()
    log(f"{total} duplicaten gewist. Start in Immich een scan van de bibliotheek.")
    return 0
