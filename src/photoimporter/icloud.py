"""iCloud-download via icloudpd (als los proces).

Volgorde per run:
  1. inloggen (--auth-only): wacht zo nodig op een tweestapscode via de icloudpd-webpagina
  2. album Favorites  -> bronmap 'favorieten'
  3. alle foto's      -> bronmap 'overig'   (bevat ook de favorieten, die worden later als
                                             duplicaat herkend en verwijderd)

Mislukt stap 1 of 2, dan wordt stap 3 overgeslagen: zo belandt een favoriet nooit zonder
sterrenmarkering in de doelmap omdat alleen de 'overig'-download slaagde.
"""

from __future__ import annotations

import os
import queue
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .media import classify
from .store import Store

STEP_AUTH = "1/6 iCloud inloggen"
STEP = "1/6 iCloud downloaden"

HINT_AFTER_SECONDS = 30  # zo lang zonder resultaat = vermoedelijk wacht icloudpd op een code
STATE_KEY = "icloud_status"  # ok | wacht_op_code | fout | uit


@dataclass
class ProcResult:
    code: int | None  # None = afgebroken
    timed_out: bool = False
    lines: list[str] = field(default_factory=list)


def _mask(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def run_process(
    cmd: list[str],
    timeout: float,
    log,
    step: str,
    secrets: list[str],
    hint_after: float | None = None,
    on_hint=None,
) -> ProcResult:
    """Draait een proces, stuurt elke uitvoerregel naar het log en bewaakt de maximale duur."""
    log.info(step, "uitvoeren: " + _mask(" ".join(shlex.quote(c) for c in cmd), secrets))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        log.error(step, f"kan het programma niet starten: {exc}")
        return ProcResult(code=None)

    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.put(raw.rstrip("\r\n"))
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()

    result = ProcResult(code=None)
    started = time.monotonic()
    hinted = False
    finished_reading = False
    while not finished_reading:
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            line = ""
            elapsed = time.monotonic() - started
            if hint_after is not None and not hinted and elapsed >= hint_after:
                hinted = True
                if on_hint:
                    on_hint()
            if elapsed >= timeout:
                result.timed_out = True
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            continue
        if line is None:
            finished_reading = True
            continue
        if line.strip():
            line = _mask(line, secrets)
            result.lines.append(line)
            log.info(step, line)
    if not result.timed_out:
        result.code = proc.wait()
    return result


def _base_command(cfg: Config) -> list[str]:
    cmd = shlex.split(cfg.icloud_command)
    cmd += [
        # globale opties
        "--password-provider", "parameter",
        "--mfa-provider", "webui",
        "--no-progress-bar",
        "--log-level", "info",
        # gebruiker
        "--username", cfg.icloud_username,
        "--password", cfg.icloud_password,
        "--cookie-directory", str(cfg.cookie_dir),
    ]  # fmt: skip
    if cfg.icloud_library:
        # bv. SharedSync-XXXX voor de gedeelde bibliotheek (zie 'icloudpd --list-libraries')
        cmd += ["--library", cfg.icloud_library]
    return cmd


def _count_media(root: Path) -> int:
    total = 0
    for _dirpath, _dirs, files in os.walk(root):
        total += sum(1 for f in files if classify(Path(f)) in ("image", "video"))
    return total


def authenticate(cfg: Config, store: Store | None, log) -> bool:
    """Controleert de sessie en wacht zo nodig tot de tweestapscode via de webpagina is ingevoerd."""

    def hint() -> None:
        if store is not None:
            store.set_state(STATE_KEY, "wacht_op_code")
        log.warn(
            STEP_AUTH,
            "icloudpd wacht vermoedelijk op een tweestapscode: open de webinterface en voer de code "
            f"in (icloudpd luistert op poort {cfg.icloud_web_port}). "
            f"Ik wacht maximaal {cfg.icloud_auth_wait_seconds // 60} minuten.",
        )

    cmd = _base_command(cfg) + ["--auth-only", "--directory", str(cfg.source_other)]
    result = run_process(
        cmd,
        cfg.icloud_auth_wait_seconds,
        log,
        STEP_AUTH,
        [cfg.icloud_password],
        hint_after=HINT_AFTER_SECONDS,
        on_hint=hint,
    )
    if result.timed_out:
        log.error(STEP_AUTH, "geen tweestapscode ontvangen binnen de wachttijd; iCloud wordt deze run overgeslagen")
        return False
    if result.code != 0:
        tail = " | ".join(result.lines[-3:]) or "geen uitvoer"
        log.error(STEP_AUTH, f"inloggen mislukt (exitcode {result.code}): {tail}")
        return False
    log.info(STEP_AUTH, "ingelogd")
    return True


def _download(cfg: Config, directory: Path, album: str | None, label: str, log) -> bool:
    cmd = _base_command(cfg) + [
        "--directory", str(directory),
        "--folder-structure", "none",
        "--set-exif-datetime",
        "--live-photo-mov-filename-policy", "original",
    ]  # fmt: skip
    if album:
        cmd += ["--album", album]
    if cfg.icloud_extra_args:
        cmd += shlex.split(cfg.icloud_extra_args)

    before = _count_media(directory) if directory.is_dir() else 0
    log.info(STEP, f"download '{label}' naar {directory}")
    result = run_process(cmd, cfg.icloud_download_timeout, log, STEP, [cfg.icloud_password])
    after = _count_media(directory) if directory.is_dir() else before
    new_files = max(0, after - before)
    if result.timed_out:
        log.error(STEP, f"download '{label}' afgebroken: maximale duur overschreden ({new_files} nieuwe bestanden)")
        return False
    if result.code != 0:
        tail = " | ".join(result.lines[-3:]) or "geen uitvoer"
        log.error(STEP, f"download '{label}' mislukt (exitcode {result.code}): {tail}")
        return False
    log.info(STEP, f"download '{label}' klaar: {new_files} nieuw(e) bestand(en)")
    return True


def download_icloud(cfg: Config, store: Store, log, stats) -> bool:
    """Draait de volledige iCloud-stap. Geeft True als alles gelukt is (of niet van toepassing was)."""
    if not cfg.icloud_enabled:
        store.set_state(STATE_KEY, "uit")
        log.info(STEP, "iCloud-download niet ingesteld, overgeslagen")
        return True
    if cfg.dry_run:
        log.info(STEP, "droogloop: iCloud-download overgeslagen")
        return True
    for folder in (cfg.source_favorites, cfg.source_other):
        if not folder.is_dir():
            log.error(STEP, f"downloadmap bestaat niet of is niet bereikbaar: {folder}")
            stats.errors += 1
            return False
    cfg.cookie_dir.mkdir(parents=True, exist_ok=True)

    total_before = _count_media(cfg.source_favorites) + _count_media(cfg.source_other)
    ok = authenticate(cfg, store, log)
    if ok:
        ok = _download(cfg, cfg.source_favorites, cfg.icloud_favorites_album, "favorieten", log)
    if ok:
        ok = _download(cfg, cfg.source_other, None, "overig", log)
    else:
        log.warn(STEP, "volgende iCloud-downloads overgeslagen vanwege de fout hierboven")

    stats.downloaded = max(
        0, _count_media(cfg.source_favorites) + _count_media(cfg.source_other) - total_before
    )
    store.set_state(STATE_KEY, "ok" if ok else "fout")
    if not ok:
        stats.errors += 1
    return ok


def check_icloud(cfg: Config, log) -> int:
    """Voor 'photoimporter icloud-check': inloggen (met tweestapscode) en de albums tonen."""
    if not cfg.icloud_enabled:
        log.error(STEP_AUTH, "PI_ICLOUD_USERNAME en PI_ICLOUD_PASSWORD zijn niet ingesteld")
        return 2
    cfg.cookie_dir.mkdir(parents=True, exist_ok=True)
    if not authenticate(cfg, None, log):
        return 1
    cmd = _base_command(cfg) + ["--list-albums", "--directory", str(cfg.source_other)]
    result = run_process(cmd, 300, log, "iCloud albums", [cfg.icloud_password])
    if result.code != 0:
        log.error("iCloud albums", "albums ophalen mislukt")
        return 1
    names = [line.strip() for line in result.lines]
    if cfg.icloud_favorites_album not in names:
        log.warn(
            "iCloud albums",
            f"album '{cfg.icloud_favorites_album}' staat niet in de lijst hierboven; "
            "pas PI_ICLOUD_FAVORITES_ALBUM aan naar de exacte naam (bv. de Nederlandse)",
        )
        return 1
    log.info("iCloud albums", f"album '{cfg.icloud_favorites_album}' gevonden")
    return 0
