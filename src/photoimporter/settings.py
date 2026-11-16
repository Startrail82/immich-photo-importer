"""Instellingen die je in de webinterface wijzigt (tabblad 'Instellingen').

De omgevingsvariabelen (Dockhand) zijn de beginwaarden. Zodra je in de interface opslaat, worden
de waarden bewaard in de database (datavolume) en gaan die voor. Een run gebruikt de instellingen
zoals ze op het moment van starten zijn.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import replace
from datetime import datetime

from .config import Config
from .cron import CronError, describe, parse
from .store import Store

KEY = "settings"
KEY_LIBRARIES = "known_libraries"
DEFAULT_KEEP_DAYS = 14
# Moet met een letter of cijfer beginnen: een naam als "--optie" mag nooit als commando-optie gelezen worden.
_LIBRARY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


class SettingsError(ValueError):
    pass


class SettingsManager:
    def __init__(self, base: Config, store: Store):
        self._base = base
        self._store = store
        self._lock = threading.Lock()

    # --- lezen ---------------------------------------------------------------
    def defaults(self) -> dict:
        b = self._base
        return {
            "schedule_cron": b.cron_expression,
            "schedule_enabled": b.schedule_enabled,
            "libraries": list(b.icloud_libraries) or ["PrimarySync"],
            "favorites_album": b.icloud_favorites_album,
            "delete_enabled": b.icloud_keep_days is not None,
            "keep_days": b.icloud_keep_days if b.icloud_keep_days is not None else DEFAULT_KEEP_DAYS,
        }

    def current(self) -> dict:
        values = self.defaults()
        raw = self._store.get_state(KEY, "")
        if raw:
            try:
                stored = json.loads(raw)
            except ValueError:
                stored = {}
            for key in values:
                if key in stored:
                    values[key] = stored[key]
        return values

    def effective(self) -> Config:
        s = self.current()
        libs = tuple(x for x in s["libraries"] if x)
        return replace(
            self._base,
            schedule_cron=s["schedule_cron"],
            schedule_enabled=bool(s["schedule_enabled"]),
            icloud_libraries=() if libs == ("PrimarySync",) else libs,
            icloud_favorites_album=s["favorites_album"],
            icloud_keep_days=int(s["keep_days"]) if s["delete_enabled"] else None,
        )

    def known_libraries(self) -> dict:
        raw = self._store.get_state(KEY_LIBRARIES, "")
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return {"names": data.get("names", []), "message": data.get("message", ""), "at": data.get("at", "")}

    def remember_libraries(self, names: list[str] | None, message: str) -> None:
        data = self.known_libraries()
        if names is not None:
            data["names"] = names
        data["message"] = message
        data["at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._store.set_state(KEY_LIBRARIES, json.dumps(data))

    def describe_schedule(self, now: datetime | None = None, expression: str | None = None) -> dict:
        expr = expression if expression is not None else self.current()["schedule_cron"]
        try:
            cron = parse(expr)
        except CronError as exc:
            return {"valid": False, "error": str(exc), "text": "", "upcoming": []}
        upcoming = [t.strftime("%Y-%m-%d %H:%M") for t in cron.upcoming(now or datetime.now(), 3)]
        return {"valid": True, "error": "", "text": describe(expr), "upcoming": upcoming}

    # --- schrijven ------------------------------------------------------------
    def update(self, data: dict) -> dict:
        """Valideert en bewaart. Onbekende sleutels worden genegeerd; ontbrekende sleutels blijven zoals ze zijn."""
        with self._lock:
            new = self.current()
            if "schedule_cron" in data:
                expr = str(data["schedule_cron"]).strip()
                try:
                    new["schedule_cron"] = parse(expr).expression
                except CronError as exc:
                    raise SettingsError(f"planning: {exc}") from None
            if "schedule_enabled" in data:
                new["schedule_enabled"] = bool(data["schedule_enabled"])
            if "libraries" in data:
                libs = data["libraries"]
                if not isinstance(libs, list) or not libs:
                    raise SettingsError("kies minstens één bibliotheek")
                clean: list[str] = []
                for lib in libs:
                    if not isinstance(lib, str) or not _LIBRARY_NAME.match(lib):
                        raise SettingsError(f"ongeldige bibliotheeknaam: {lib!r}")
                    if lib not in clean:
                        clean.append(lib)
                new["libraries"] = clean
            if "favorites_album" in data:
                album = str(data["favorites_album"]).strip()
                if not album or len(album) > 120:
                    raise SettingsError("de naam van het favorietenalbum mag niet leeg zijn")
                new["favorites_album"] = album
            if "delete_enabled" in data:
                new["delete_enabled"] = bool(data["delete_enabled"])
            if "keep_days" in data:
                try:
                    days = int(data["keep_days"])
                except (TypeError, ValueError):
                    raise SettingsError("het aantal dagen moet een geheel getal zijn") from None
                if not 1 <= days <= 3650:
                    raise SettingsError("het aantal dagen om in iCloud te bewaren moet tussen 1 en 3650 liggen")
                new["keep_days"] = days
            self._store.set_state(KEY, json.dumps(new))
            return new
