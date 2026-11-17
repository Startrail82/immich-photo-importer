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
KEY_SECRETS = "secrets"  # apart bewaard: wachtwoord en API-sleutel komen nooit in een API-antwoord
KEY_LIBRARIES = "known_libraries"
SECRET_FIELDS = ("icloud_password", "immich_api_key")
DEFAULT_KEEP_DAYS = 14
# Moet met een letter of cijfer beginnen: een naam als "--optie" mag nooit als commando-optie gelezen worden.
_URL = re.compile(r"^https?://\S+$")
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
            "download_version": b.icloud_version,
            "recent_count": b.icloud_recent,
            "icloud_username": b.icloud_username,
            "icloud_web_url": b.icloud_web_url,
            "immich_url": b.immich_url,
        }

    def _secrets(self) -> dict:
        """Bewaarde geheimen, met de omgevingsvariabelen als terugval."""
        values = {"icloud_password": self._base.icloud_password, "immich_api_key": self._base.immich_api_key}
        raw = self._store.get_state(KEY_SECRETS, "")
        try:
            stored = json.loads(raw) if raw else {}
        except ValueError:
            stored = {}
        for key in SECRET_FIELDS:
            if stored.get(key):
                values[key] = stored[key]
        return values

    def secret_flags(self) -> dict:
        secrets = self._secrets()
        return {f"{key}_set": bool(secrets[key]) for key in SECRET_FIELDS}

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
            icloud_version=s["download_version"],
            icloud_recent=int(s["recent_count"]),
            icloud_username=s["icloud_username"],
            icloud_web_url=s["icloud_web_url"],
            immich_url=s["immich_url"],
            **self._secrets(),
        )

    def with_overrides(self, form: dict) -> Config:
        """De instellingen zoals ze nu zijn, met de (nog onbewaarde) waarden uit het formulier erover.
        Een leeg geheim in het formulier betekent: gebruik het bewaarde geheim."""
        cfg = self.effective()
        changes: dict = {}
        for field, key in (("icloud_username", "icloud_username"), ("immich_url", "immich_url")):
            if field in form:
                changes[key] = str(form[field]).strip()
        if "favorites_album" in form and str(form["favorites_album"]).strip():
            changes["icloud_favorites_album"] = str(form["favorites_album"]).strip()
        for key in SECRET_FIELDS:
            if str(form.get(key) or "").strip():
                changes[key] = str(form[key]).strip()
        libs = form.get("libraries")
        if isinstance(libs, list) and libs and all(isinstance(x, str) and _LIBRARY_NAME.match(x) for x in libs):
            changes["icloud_libraries"] = () if tuple(libs) == ("PrimarySync",) else tuple(libs)
        return replace(cfg, **changes)

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
            if "recent_count" in data:
                try:
                    recent = int(data["recent_count"])
                except (TypeError, ValueError):
                    raise SettingsError("het aantal recente foto's moet een geheel getal zijn") from None
                if not 0 <= recent <= 1_000_000:
                    raise SettingsError("het aantal recente foto's moet tussen 0 (alles) en 1.000.000 liggen")
                new["recent_count"] = recent
            if "download_version" in data:
                version = str(data["download_version"]).strip().lower()
                if version not in ("original", "adjusted", "both"):
                    raise SettingsError("de te downloaden versie moet original, adjusted of both zijn")
                new["download_version"] = version
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
            for field in ("icloud_username", "icloud_web_url", "immich_url"):
                if field in data:
                    value = str(data[field]).strip()
                    if len(value) > 300:
                        raise SettingsError(f"{field}: waarde is te lang")
                    if field.endswith("_url") and value and not _URL.match(value):
                        raise SettingsError(f"{field}: moet beginnen met http:// of https://")
                    new[field] = value
            secrets_update = {}
            for key in SECRET_FIELDS:
                value = str(data.get(key) or "").strip()
                if value:  # leeg = bewaarde waarde behouden
                    if len(value) > 500:
                        raise SettingsError(f"{key}: waarde is te lang")
                    secrets_update[key] = value
            self._store.set_state(KEY, json.dumps(new))
            if secrets_update:
                raw = self._store.get_state(KEY_SECRETS, "")
                try:
                    stored = json.loads(raw) if raw else {}
                except ValueError:
                    stored = {}
                stored.update(secrets_update)
                self._store.set_state(KEY_SECRETS, json.dumps(stored))
            return new
