"""Vooraf controleren of de volgende run kan slagen: `photoimporter doctor`.

De klassieke storingen van deze container zijn een doelmap die niet gemount is, een ontbrekende
exiftool en een iCloud-wachtwoord dat verlopen is. De run zelf meldt die pas als hij al bezig is
(en bij een ontbrekende doelmap breekt hij meteen af). Doctor controleert ze vooraf, leest alleen
en wijzigt niets: geen bestanden verplaatsen, geen iCloud-actie, geen instellingen opslaan.

Elke controle heeft een niveau: `ok`, `warning` (de run gaat door, met minder resultaat) of
`error` (de run kan niet sorteren). De uitvoer is Nederlands, net als de rest van de opdrachtregel.
"""

from __future__ import annotations

import json
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import TextIO

from .config import Config, ConfigError
from .cron import CronError, ensure_satisfiable
from .immich import ImmichClient, ImmichError

OK, WARNING, ERROR = "ok", "warning", "error"
MIN_FREE_BYTES = 1024**3  # 1 GB: een import van een vakantie past daar ruim in


@dataclass(frozen=True)
class Check:
    name: str
    level: str
    message: str

    def as_dict(self) -> dict:
        return {"name": self.name, "level": self.level, "message": self.message}


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, level: str, message: str) -> bool:
        self.checks.append(Check(name, level, message))
        return level == OK

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if c.level == ERROR]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def exit_code(self) -> int:
        """0 = niets aan de hand, 1 = alleen waarschuwingen, 2 = de run kan niet sorteren."""
        if self.errors:
            return 2
        return 1 if self.warnings else 0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "checks": [c.as_dict() for c in self.checks],
        }


def _human_bytes(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def _tool_version(command: str, timeout: float = 20.0) -> tuple[bool, str]:
    """Draait `<tool> -ver`; geeft (gevonden, versie-of-foutmelding)."""
    try:
        proc = subprocess.run([command, "-ver"], capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return False, "niet gevonden"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"kon niet gestart worden: {exc}"
    version = (proc.stdout or proc.stderr).strip().splitlines()
    if proc.returncode != 0 or not version:
        return False, f"gaf een fout (code {proc.returncode})"
    return True, version[0][:60]


def check_config(cfg: Config, report: Report) -> None:
    try:
        cfg.validate()
    except ConfigError as exc:
        report.add("Configuratie", ERROR, str(exc))
        return
    report.add("Configuratie", OK, "paden en planning zijn geldig")


def check_target(cfg: Config, report: Report) -> None:
    target = cfg.target
    if not target.is_dir():
        report.add("Doelmap", ERROR, f"{target} bestaat niet of is niet bereikbaar (is de NAS gemount?)")
        return
    if not os.access(target, os.W_OK):
        report.add("Doelmap", ERROR, f"{target} is niet schrijfbaar voor deze gebruiker")
        return
    try:
        free = shutil.disk_usage(target).free
    except OSError as exc:
        report.add("Doelmap", WARNING, f"{target} is schrijfbaar, vrije ruimte onbekend ({exc})")
        return
    if free < MIN_FREE_BYTES:
        report.add("Doelmap", WARNING, f"{target} is schrijfbaar, maar er is nog maar {_human_bytes(free)} vrij")
    else:
        report.add("Doelmap", OK, f"{target} is schrijfbaar ({_human_bytes(free)} vrij)")


def check_sources(cfg: Config, report: Report) -> None:
    sources = (
        ("favorieten", cfg.source_favorites, cfg.icloud_enabled),
        ("overig", cfg.source_other, cfg.icloud_enabled),
        ("inbox", cfg.source_inbox, True),
    )
    for name, path, needed in sources:
        if path.is_dir():
            report.add(f"Bron: {name}", OK, f"{path}")
        elif needed:
            report.add(
                f"Bron: {name}",
                WARNING,
                f"{path} bestaat niet of is niet bereikbaar; de run slaat deze map over",
            )
        else:
            report.add(f"Bron: {name}", OK, f"{path} bestaat niet en is niet nodig (iCloud staat uit)")


def check_store(cfg: Config, report: Report, store) -> None:
    data_dir = cfg.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        report.add("Datastore", ERROR, f"{data_dir} is niet aan te maken: {exc}")
        return
    if not os.access(data_dir, os.W_OK):
        report.add("Datastore", ERROR, f"{data_dir} is niet schrijfbaar; runs en logboek kunnen niet bewaard worden")
        return
    if store is None:
        report.add("Datastore", OK, f"{data_dir} is schrijfbaar")
        return
    try:
        runs = store.list_runs(1)
    except Exception as exc:  # noqa: BLE001 - een kapotte database is precies wat we hier willen melden
        report.add("Datastore", ERROR, f"de database in {data_dir} is niet leesbaar: {exc}")
        return
    last = f", laatste run {dict(runs[0])['started']}" if runs else ", nog geen runs"
    report.add("Datastore", OK, f"database leesbaar ({data_dir}{last})")


def check_exiftool(report: Report, command: str = "exiftool") -> None:
    found, detail = _tool_version(command)
    if found:
        report.add("exiftool", OK, f"versie {detail}")
    else:
        report.add(
            "exiftool",
            ERROR,
            f"{command}: {detail}; zonder exiftool wordt elke foto op zijn bestandsdatum gesorteerd "
            "(Debian/Ubuntu: apt install libimage-exiftool-perl)",
        )


def check_icloudpd(cfg: Config, report: Report) -> None:
    if not cfg.icloud_enabled:
        report.add("icloudpd", OK, "iCloud is niet ingesteld, wordt overgeslagen")
        return
    command = (cfg.icloud_command.split() or ["icloudpd"])[0]
    path = shutil.which(command)
    if path is None:
        report.add(
            "icloudpd",
            WARNING,
            f"{command} is niet gevonden; de iCloud-downloads worden overgeslagen en alleen de inbox wordt gesorteerd",
        )
        return
    report.add("icloudpd", OK, path)


def check_schedule(cfg: Config, report: Report) -> None:
    if not cfg.schedule_enabled:
        report.add("Planning", OK, "uitgeschakeld; er wordt alleen op verzoek een run gestart")
        return
    try:
        cron = ensure_satisfiable(cfg.cron_expression)
    except CronError as exc:
        report.add("Planning", ERROR, f"{cfg.cron_expression!r} is onbruikbaar: {exc}")
        return
    upcoming = ", ".join(t.strftime("%Y-%m-%d %H:%M") for t in cron.upcoming(datetime.now(), 2))
    report.add("Planning", OK, f"{cfg.cron_expression!r}; de volgende runs: {upcoming}")


def check_delete(cfg: Config, report: Report) -> None:
    if cfg.icloud_keep_days is None:
        report.add("Wissen in iCloud", OK, "staat uit; alleen kopiëren")
        return
    report.add(
        "Wissen in iCloud",
        WARNING,
        f"staat AAN: wat ouder is dan {cfg.icloud_keep_days} dagen wordt na het downloaden uit iCloud "
        "gewist (en dus ook van de iPhone)",
    )


def check_immich(cfg: Config, report: Report, network: bool) -> None:
    if not cfg.immich_enabled:
        report.add("Immich", OK, "niet ingesteld, wordt overgeslagen")
        return
    if not network:
        report.add("Immich", WARNING, "niet gecontroleerd (--offline)")
        return
    try:
        client = ImmichClient(cfg.immich_url, cfg.immich_api_key, timeout=8)
        if not client.ping():
            report.add("Immich", WARNING, f"{cfg.immich_url} antwoordt niet zoals verwacht op /server/ping")
            return
        library_id = cfg.immich_library_id or client.find_library_id(cfg.immich_target_path)
    except ImmichError as exc:
        report.add("Immich", WARNING, f"niet bereikbaar: {exc}")
        return
    if library_id is None:
        report.add(
            "Immich",
            WARNING,
            f"geen bibliotheek met importpad {cfg.immich_target_path} gevonden; stel PI_IMMICH_LIBRARY_ID in",
        )
        return
    report.add("Immich", OK, f"bereikbaar, bibliotheek {library_id}")


def run_checks(
    cfg: Config,
    *,
    store=None,
    exiftool: str = "exiftool",
    network: bool = True,
) -> Report:
    """Voert alle controles uit. Wijzigt niets, behalve de datamap aanmaken als die ontbreekt."""
    report = Report()
    check_config(cfg, report)
    check_target(cfg, report)
    check_sources(cfg, report)
    check_store(cfg, report, store)
    check_exiftool(report, exiftool)
    check_icloudpd(cfg, report)
    check_schedule(cfg, report)
    check_delete(cfg, report)
    check_immich(cfg, report, network)
    return report


def format_report(report: Report) -> str:
    labels = {OK: "ok    ", WARNING: "let op", ERROR: "fout  "}
    lines = [f"{labels[check.level]}  {check.name:20s} {check.message}" for check in report.checks]
    errors, warnings = len(report.errors), len(report.warnings)
    lines.append("")
    if errors or warnings:
        parts: list[str] = []
        if errors:
            parts.append(f"{errors} probleem" + ("" if errors == 1 else "en"))
        if warnings:
            parts.append(f"{warnings} waarschuwing" + ("" if warnings == 1 else "en"))
        lines.append(", ".join(parts) + " gevonden")
    else:
        lines.append("geen problemen gevonden: een run kan starten")
    return "\n".join(lines)


def print_report(report: Report, out: TextIO | None = None) -> None:
    stream = out or sys.stdout
    stream.write(format_report(report) + "\n")


def main(cfg: Config, *, store=None, as_json: bool = False, network: bool = True, out: TextIO | None = None) -> int:
    """Voert doctor uit en geeft de exitcode terug (0 goed, 1 waarschuwingen, 2 problemen)."""
    report = run_checks(cfg, store=store, network=network)
    stream = out or sys.stdout
    if as_json:
        stream.write(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        print_report(report, stream)
    return report.exit_code()
