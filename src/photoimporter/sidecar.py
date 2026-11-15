"""XMP-sidecars. Immich leest xmp:Rating uit <bestand>.<ext>.xmp."""

from __future__ import annotations

import os
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
