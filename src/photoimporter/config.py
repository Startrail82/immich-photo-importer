"""Configuratie, volledig via omgevingsvariabelen (PI_*)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path


class ConfigError(ValueError):
    pass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} moet een geheel getal zijn, kreeg: {raw!r}") from exc


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
