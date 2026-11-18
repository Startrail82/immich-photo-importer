"""XMP-sidecars. Immich leest xmp:Rating uit <bestand>.<ext>.xmp."""

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


def write_rating_sidecar(media: Path, rating: int = 5) -> bool:
    """Schrijft de sidecar als die nog niet bestaat. Geeft True terug als er een is aangemaakt."""
    target = sidecar_path(media)
    if target.exists():
        return False
    tmp = target.with_name(target.name + ".partial")
    tmp.write_text(_TEMPLATE.format(rating=int(rating)), encoding="utf-8")
    os.replace(tmp, target)
    return True


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
