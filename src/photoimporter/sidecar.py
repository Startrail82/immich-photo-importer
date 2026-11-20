"""XMP-sidecars. Nieuwe sidecars heten <bestand>.xmp (zoals Lightroom ze noemt, zodat ze bij het sorteren in Lightroom
meeverhuizen); Immich leest ook <bestand>.<ext>.xmp en verkiest die als beide bestaan."""

from __future__ import annotations

import os
import re
from pathlib import Path

_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="{rating}"/>
 </rdf:RDF>
</x:xmpmeta>
"""


def sidecar_path(media: Path) -> Path:
    """IMG_0001.jpg -> IMG_0001.jpg.xmp (de naamgeving die Immich verkiest)."""
    return media.with_name(media.name + ".xmp")


def stem_sidecar_path(media: Path) -> Path:
    """IMG_0001.HEIC -> IMG_0001.xmp (de naamgeving van Lightroom; Immich leest ze ook)."""
    return media.with_suffix(".xmp")


def _write_atomic(target: Path, text: str) -> None:
    tmp = target.with_name(target.name + ".partial")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


# xmp:Rating kan als attribuut (xmp:Rating="5") of als element (<xmp:Rating>5</xmp:Rating>) staan.
_RATING_ATTR = re.compile(r'(xmp:Rating\s*=\s*)(["\'])\s*5\s*\2')
_RATING_ELEM = re.compile(r"(<xmp:Rating>)\s*5\s*(</xmp:Rating>)")


def existing_sidecars(media: Path) -> list[Path]:
    """De sidecars die naast het bestand staan (<bestand>.<ext>.xmp en/of <bestand>.xmp)."""
    return [p for p in (sidecar_path(media), media.with_suffix(".xmp")) if p.is_file()]


def clear_rating(media: Path) -> bool:
    """Zet xmp:Rating 5 op 0 in de bestaande sidecar(s), zodat een foto niet meer als 5 sterren verhuist.
    Alleen het ratingveld wordt aangepast; de rest van de sidecar blijft staan. True als er iets veranderd is."""
    changed = False
    for target in existing_sidecars(media):
        text = target.read_text(encoding="utf-8")
        new = _RATING_ATTR.sub(r'\g<1>"0"', text)
        new = _RATING_ELEM.sub(r"\g<1>0\g<2>", new)
        if new == text:
            continue
        tmp = target.with_name(target.name + ".partial")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, target)
        changed = True
    return changed


def read_rating(sidecar: Path) -> int | None:
    """Het xmp:Rating in een sidecar, of None als er geen staat (of het bestand niet leesbaar is)."""
    try:
        text = sidecar.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for pattern in (_RATING_ANY_ATTR, _RATING_ANY_ELEM):
        m = pattern.search(text)
        if m:
            return int(m.group(2))
    return None


_RATING_ANY_ATTR = re.compile(r'(xmp:Rating\s*=\s*["\'])\s*(-?\d+)\s*["\']')
_RATING_ANY_ELEM = re.compile(r"(<xmp:Rating>)\s*(-?\d+)\s*</xmp:Rating>")
_ANY_ATTR_SUB = re.compile(r'(xmp:Rating\s*=\s*)(["\'])\s*-?\d+\s*\2')
_ANY_ELEM_SUB = re.compile(r"(<xmp:Rating>)\s*-?\d+\s*(</xmp:Rating>)")
_DESCRIPTION = re.compile(r"<rdf:Description\b")
_XMP_NS = 'xmlns:xmp="http://ns.adobe.com/xap/1.0/"'


def _with_rating(text: str, rating: int) -> str | None:
    """De tekst van een sidecar met xmp:Rating op `rating`; None als dat niet veilig kan."""
    new, n = _ANY_ATTR_SUB.subn(lambda m: f'{m.group(1)}"{rating}"', text, count=1)
    if n:
        return new
    new, n = _ANY_ELEM_SUB.subn(lambda m: f"{m.group(1)}{rating}{m.group(2)}", text, count=1)
    if n:
        return new
    if not _DESCRIPTION.search(text):
        return None
    ns = "" if "xmlns:xmp=" in text else f" {_XMP_NS}"
    return _DESCRIPTION.sub(lambda m: f'{m.group(0)} xmp:Rating="{rating}"{ns}', text, count=1)


def ensure_rating(media: Path, rating: int = 5) -> str:
    """Zorgt dat de sidecar van `media` rating `rating` heeft, zonder iets anders aan te raken.

    - geen sidecar: nieuwe <bestand>.xmp aanmaken ("created")
    - sidecar zonder rating of met 0 (bv. van Lightroom): alleen het ratingveld zetten ("updated")
    - rating staat al goed: niets ("unchanged")
    - een eigen rating van 1 tot 4 (of -1) blijft ongemoeid ("kept"), want die zou verloren gaan
    - sidecar die we niet veilig kunnen aanpassen: niets ("skipped")
    Bestaan er twee sidecars, dan geldt degene die Immich verkiest (<bestand>.<ext>.xmp).
    """
    found = existing_sidecars(media)
    if not found:
        _write_atomic(stem_sidecar_path(media), _TEMPLATE.format(rating=int(rating)))
        return "created"
    target = found[0]
    current = read_rating(target)
    if current == rating:
        return "unchanged"
    if current not in (None, 0):
        return "kept"
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "skipped"
    new = _with_rating(text, rating)
    if new is None:
        return "skipped"
    _write_atomic(target, new)
    return "updated"


def removable_sidecars(media: Path) -> list[Path]:
    """Sidecars die samen met `media` mogen verdwijnen. <bestand>.xmp alleen als er geen ander mediabestand
    met dezelfde naam (bv. IMG_1.HEIC naast IMG_1.JPG) is dat hem ook gebruikt."""
    result = [p for p in (sidecar_path(media),) if p.is_file()]
    stem = stem_sidecar_path(media)
    if stem.is_file():
        shared = any(
            other != media and other.stem == media.stem and other.suffix.lower() != ".xmp"
            for other in media.parent.iterdir()
        )
        if not shared:
            result.append(stem)
    return result
