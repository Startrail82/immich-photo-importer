"""Verbindingstests voor de knoppen 'Test iCloud' en 'Test Immich' in de webinterface.

Elke test geeft stap voor stap resultaat: {"ok": bool, "steps": [{"name", "ok", "message"}]}.
Er wordt niets gewijzigd: alleen gelezen.
"""

from __future__ import annotations

from .config import Config
from .immich import ImmichClient, ImmichError


class _Steps:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, name: str, ok: bool, message: str) -> bool:
        self.items.append({"name": name, "ok": ok, "message": message})
        return ok

    def result(self) -> dict:
        return {"ok": bool(self.items) and all(s["ok"] for s in self.items), "steps": self.items}


def check_immich(cfg: Config) -> dict:
    steps = _Steps()
    missing = [
        label
        for label, value in (
            ("URL", cfg.immich_url),
            ("API-sleutel", cfg.immich_api_key),
            ("doelpad zoals Immich het ziet (PI_IMMICH_TARGET_PATH in de stack)", cfg.immich_target_path),
        )
        if not value
    ]
    if not steps.add("Gegevens", not missing, "alles ingevuld" if not missing else "ontbreekt: " + ", ".join(missing)):
        return steps.result()

    client = ImmichClient(cfg.immich_url, cfg.immich_api_key, timeout=15)
    try:
        if not steps.add("Server bereikbaar", client.ping(), cfg.immich_url):
            return steps.result()
    except ImmichError as exc:
        steps.add("Server bereikbaar", False, str(exc))
        return steps.result()
    try:
        libraries = client.list_libraries()
        steps.add("API-sleutel (library.read)", True, f"{len(libraries)} bibliotheek/bibliotheken gevonden")
    except ImmichError as exc:
        steps.add("API-sleutel (library.read)", False, str(exc))
        return steps.result()
    try:
        library_id = cfg.immich_library_id or client.find_library_id(cfg.immich_target_path)
        steps.add(
            "Externe bibliotheek",
            library_id is not None,
            f"gevonden voor {cfg.immich_target_path}"
            if library_id
            else f"geen bibliotheek met importpad {cfg.immich_target_path}",
        )
        client.find_asset_id(cfg.immich_target_path + "/bestaat-niet.jpg")
        steps.add("Zoeken op bestandspad (asset.read)", True, "ok")
    except ImmichError as exc:
        steps.add("Zoeken op bestandspad (asset.read)", False, str(exc))
    steps.add(
        "Schrijfrechten",
        True,
        "niet getest (zou iets wijzigen): voor favorieten en scans heeft de sleutel ook "
        "asset.update en library.update nodig",
    )
    return steps.result()
