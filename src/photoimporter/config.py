"""Configuratie, volledig via omgevingsvariabelen (PI_*)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from .cron import CronError
from .cron import parse as parse_cron


class ConfigError(ValueError):
    pass


def _env_optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} moet een geheel getal zijn, kreeg: {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} moet een geheel getal zijn, kreeg: {raw!r}") from exc


def _read_secret(name: str) -> str:
    """Leest een geheim uit NAAM, of uit het bestand waar NAAM_FILE naar wijst (Docker secrets)."""
    file_path = os.environ.get(f"{name}_FILE", "").strip()
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"{name}_FILE kan niet gelezen worden: {exc}") from exc
    return os.environ.get(name, "").strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "ja", "on"}


@dataclass(frozen=True)
class Config:
    source_favorites: Path
    source_other: Path
    source_inbox: Path
    target: Path
    data_dir: Path
    # Inbox: bestanden die minder dan dit aantal seconden geleden gewijzigd
    # zijn, worden overgeslagen (mogelijk nog aan het kopieren).
    min_age_inbox: int = 300
    # iCloud-mappen worden alleen door onze eigen keten gevuld, dus 0.
    min_age_icloud: int = 0
    log_retention_days: int = 180
    dry_run: bool = False
    # Immich-koppeling (optioneel). Pas actief als url, sleutel en pad alle drie zijn ingesteld.
    immich_url: str = ""
    immich_api_key: str = ""
    # Het pad van de doelmap zoals IMMICH het ziet (verschilt van het pad in deze container).
    immich_target_path: str = ""
    immich_library_id: str = ""  # leeg = automatisch zoeken op importpad
    immich_wait_seconds: int = 600  # hoe lang wachten tot Immich nieuwe bestanden kent
    immich_poll_seconds: int = 15
    # Favorieten volgen het bestand: rating-5 sidecars (zie immich_sync). Uitzetbaar voor als Immich dit ooit zelf oplost.
    favorites_follow_file: bool = True

    # iCloud-download via icloudpd (optioneel). Actief als gebruikersnaam en wachtwoord ingesteld zijn.
    icloud_username: str = ""
    icloud_password: str = ""
    icloud_command: str = "icloudpd"  # mag extra woorden bevatten, bv. "python /pad/fake.py"
    icloud_cookie_dir: Path | None = None  # leeg = <data_dir>/icloud-cookies
    icloud_favorites_album: str = "Favorites"
    icloud_auth_wait_seconds: int = 1800  # hoe lang wachten op een tweestapscode
    icloud_download_timeout: int = 14400  # maximale duur van een download
    icloud_extra_args: str = ""
    # Welke iCloud-bibliotheken gedownload worden (namen uit 'icloudpd --list-libraries', bv. PrimarySync of
    # SharedSync-...). Leeg = alleen de persoonlijke bibliotheek.
    icloud_libraries: tuple[str, ...] = ()
    # Wissen in iCloud: None = nooit (alleen kopiëren); N = na het downloaden alles wissen wat ouder is dan N dagen
    icloud_keep_days: int | None = None
    # Welke versie van bewerkte foto's: "original" (onbewerkt, de standaard van icloudpd), "adjusted" (bewerkt, of het
    # origineel als er niets bewerkt is) of "both" (beide; de bewerkte krijgt -adjusted in de naam)
    icloud_version: str = "original"
    icloud_web_port: int = 8080  # poort van de icloudpd-webpagina voor de tweestapscode
    # Publiek adres van die codepagina (eigen Traefik-route), waarnaar de webinterface linkt
    icloud_web_url: str = ""
    web_port: int = 8000

    # Planner: dagelijks op dit uur (lokale tijd van de container, stel TZ in)
    schedule_time: str = "12:00"
    schedule_cron: str = ""  # indien ingevuld (5 velden) gaat dit voor op schedule_time
    schedule_enabled: bool = True

    @property
    def cron_expression(self) -> str:
        if self.schedule_cron:
            return self.schedule_cron
        hour, minute = self.schedule_time.split(":")
        return f"{int(minute)} {int(hour)} * * *"

    @property
    def icloud_enabled(self) -> bool:
        return bool(self.icloud_username and self.icloud_password)

    @property
    def cookie_dir(self) -> Path:
        return self.icloud_cookie_dir or (self.data_dir / "icloud-cookies")

    @property
    def immich_enabled(self) -> bool:
        return bool(self.immich_url and self.immich_api_key and self.immich_target_path)

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls(
            source_favorites=Path(os.environ.get("PI_SOURCE_FAVORITES", "/import/iCloud/favorieten")),
            source_other=Path(os.environ.get("PI_SOURCE_OTHER", "/import/iCloud/overig")),
            source_inbox=Path(os.environ.get("PI_SOURCE_INBOX", "/import/Inbox")),
            target=Path(os.environ.get("PI_TARGET", "/target")),
            data_dir=Path(os.environ.get("PI_DATA_DIR", "/data")),
            min_age_inbox=_env_int("PI_MIN_AGE_INBOX_SECONDS", 300),
            min_age_icloud=_env_int("PI_MIN_AGE_ICLOUD_SECONDS", 0),
            log_retention_days=_env_int("PI_LOG_RETENTION_DAYS", 180),
            dry_run=_env_bool("PI_DRY_RUN", False),
            immich_url=os.environ.get("PI_IMMICH_URL", "").strip(),
            immich_api_key=_read_secret("PI_IMMICH_API_KEY"),
            immich_target_path=os.environ.get("PI_IMMICH_TARGET_PATH", "").strip(),
            immich_library_id=os.environ.get("PI_IMMICH_LIBRARY_ID", "").strip(),
            immich_wait_seconds=_env_int("PI_IMMICH_WAIT_SECONDS", 600),
            immich_poll_seconds=_env_int("PI_IMMICH_POLL_SECONDS", 15),
            favorites_follow_file=_env_bool("PI_FAVORITES_FOLLOW_FILE", True),
            icloud_username=os.environ.get("PI_ICLOUD_USERNAME", "").strip(),
            icloud_password=_read_secret("PI_ICLOUD_PASSWORD"),
            icloud_command=os.environ.get("PI_ICLOUD_COMMAND", "icloudpd").strip() or "icloudpd",
            icloud_cookie_dir=Path(os.environ["PI_ICLOUD_COOKIE_DIR"])
            if os.environ.get("PI_ICLOUD_COOKIE_DIR", "").strip()
            else None,
            icloud_favorites_album=os.environ.get("PI_ICLOUD_FAVORITES_ALBUM", "Favorites").strip() or "Favorites",
            icloud_auth_wait_seconds=_env_int("PI_ICLOUD_AUTH_WAIT_SECONDS", 1800),
            icloud_download_timeout=_env_int("PI_ICLOUD_DOWNLOAD_TIMEOUT_SECONDS", 14400),
            icloud_extra_args=os.environ.get("PI_ICLOUD_EXTRA_ARGS", "").strip(),
            icloud_libraries=tuple(
                x.strip() for x in os.environ.get("PI_ICLOUD_LIBRARY", "").split(",") if x.strip()
            ),
            icloud_keep_days=_env_optional_int("PI_ICLOUD_KEEP_RECENT_DAYS"),
            icloud_version=os.environ.get("PI_ICLOUD_VERSION", "original").strip().lower() or "original",
            icloud_web_port=_env_int("PI_ICLOUD_WEB_PORT", 8080),
            icloud_web_url=os.environ.get("PI_ICLOUD_WEB_URL", "").strip(),
            web_port=_env_int("PI_WEB_PORT", 8000),
            schedule_time=os.environ.get("PI_SCHEDULE_TIME", "12:00").strip() or "12:00",
            schedule_cron=os.environ.get("PI_SCHEDULE_CRON", "").strip(),
            schedule_enabled=_env_bool("PI_SCHEDULE_ENABLED", True),
        )
        cfg.validate()
        return cfg

    def with_dry_run(self, dry_run: bool) -> "Config":
        return replace(self, dry_run=dry_run)

    def validate(self) -> None:
        """Voorkom gevaarlijke combinaties (geneste of gelijke mappen)."""
        named = {
            "favorieten": self.source_favorites,
            "overig": self.source_other,
            "inbox": self.source_inbox,
            "doelmap": self.target,
        }
        try:
            hour, minute = self.schedule_time.split(":")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError
        except ValueError:
            raise ConfigError(f"PI_SCHEDULE_TIME moet UU:MM zijn, kreeg: {self.schedule_time!r}") from None
        try:
            parse_cron(self.cron_expression)
        except CronError as exc:
            raise ConfigError(f"ongeldige planning: {exc}") from None
        if self.icloud_version not in ("original", "adjusted", "both"):
            raise ConfigError("PI_ICLOUD_VERSION moet original, adjusted of both zijn")
        if self.icloud_keep_days is not None and self.icloud_keep_days < 0:
            raise ConfigError("het aantal dagen om in iCloud te bewaren mag niet negatief zijn")
        names = list(named)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                pa, pb = _norm(named[a]), _norm(named[b])
                if pa == pb or _is_inside(pa, pb) or _is_inside(pb, pa):
                    raise ConfigError(
                        f"De mappen '{a}' ({named[a]}) en '{b}' ({named[b]}) mogen "
                        "niet gelijk zijn of in elkaar liggen."
                    )


def _norm(p: Path) -> Path:
    return Path(os.path.abspath(p))


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
