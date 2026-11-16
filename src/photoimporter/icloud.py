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
import re
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
DEFAULT_LIBRARY = "PrimarySync"  # de persoonlijke bibliotheek: icloudpd gebruikt die zonder --library

# Eén icloudpd tegelijk: ze delen de sessie (cookies) en de poort van de codepagina.
ICLOUD_LOCK = threading.Lock()
_LIBRARY_LINE = re.compile(r"^(PrimarySync|SharedSync-\S+)$")


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


def _base_command(cfg: Config, library: str | None = None) -> list[str]:
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
    if library and library != DEFAULT_LIBRARY:
        # bv. SharedSync-XXXX voor de gedeelde bibliotheek (zie 'icloudpd --list-libraries')
        cmd += ["--library", library]
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


def _download(
    cfg: Config,
    directory: Path,
    album: str | None,
    label: str,
    log,
    library: str | None = None,
    keep_days: int | None = None,
) -> bool:
    cmd = _base_command(cfg, library) + [
        "--directory", str(directory),
        "--folder-structure", "none",
        "--set-exif-datetime",
        "--live-photo-mov-filename-policy", "original",
    ]  # fmt: skip
    if album:
        cmd += ["--album", album]
    if keep_days is not None:
        # na het downloaden wissen in iCloud, behalve wat de laatste N dagen genomen is
        cmd += ["--keep-icloud-recent-days", str(keep_days)]
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


def _download_library(cfg: Config, library: str | None, name: str, log) -> bool:
    """Favorietenalbum en daarna alle foto's van één bibliotheek. Wissen gebeurt enkel in de tweede stap,
    zodat een favoriet pas uit iCloud verdwijnt nadat hij ook in de favorietenmap is binnengehaald."""
    suffix = f" [{name}]"
    if not _download(cfg, cfg.source_favorites, cfg.icloud_favorites_album, "favorieten" + suffix, log, library):
        log.warn(STEP, f"download 'overig' van {name} overgeslagen vanwege de fout hierboven")
        return False
    return _download(
        cfg, cfg.source_other, None, "overig" + suffix, log, library, keep_days=cfg.icloud_keep_days
    )


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
    libraries: tuple[str | None, ...] = cfg.icloud_libraries or (None,)
    if cfg.icloud_keep_days is not None:
        log.warn(
            STEP,
            f"wissen in iCloud staat AAN: wat ouder is dan {cfg.icloud_keep_days} dagen wordt na het "
            "downloaden uit iCloud gewist (en dus ook van je iPhone)",
        )
    if not ICLOUD_LOCK.acquire(blocking=False):
        log.warn(STEP, "er is al een iCloud-actie bezig (bv. bibliotheken ophalen); ik wacht tot die klaar is")
        ICLOUD_LOCK.acquire()
    try:
        ok = authenticate(cfg, store, log)
        if not ok:
            log.warn(STEP, "iCloud-downloads overgeslagen vanwege de fout hierboven")
        for library in libraries if ok else ():
            name = library or DEFAULT_LIBRARY
            if not _download_library(cfg, library, name, log):
                ok = False
    finally:
        ICLOUD_LOCK.release()

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
    status = 0
    for library in cfg.icloud_libraries or (None,):
        name = library or DEFAULT_LIBRARY
        cmd = _base_command(cfg, library) + ["--list-albums", "--directory", str(cfg.source_other)]
        result = run_process(cmd, 300, log, f"iCloud albums [{name}]", [cfg.icloud_password])
        if result.code != 0:
            log.error("iCloud albums", f"albums ophalen mislukt voor {name}")
            status = 1
            continue
        names = [line.strip() for line in result.lines]
        if cfg.icloud_favorites_album not in names:
            log.warn(
                "iCloud albums",
                f"album '{cfg.icloud_favorites_album}' staat niet in de lijst van {name}; "
                "pas de naam aan in Instellingen (bv. de Nederlandse naam)",
            )
            status = 1
        else:
            log.info("iCloud albums", f"album '{cfg.icloud_favorites_album}' gevonden in {name}")
    return status


def list_libraries(cfg: Config, store: Store | None, log) -> tuple[list[str] | None, str]:
    """Vraagt aan iCloud welke bibliotheken er zijn. Geeft (lijst, melding); lijst is None bij een fout."""
    if not cfg.icloud_enabled:
        return None, "iCloud is niet ingesteld (gebruikersnaam en wachtwoord ontbreken)"
    cfg.cookie_dir.mkdir(parents=True, exist_ok=True)
    if not ICLOUD_LOCK.acquire(blocking=False):
        return None, "er is al een iCloud-actie bezig (bv. een run); probeer het zo opnieuw"
    try:
        if not authenticate(cfg, store, log):
            if store is not None:
                store.set_state(STATE_KEY, "fout")
            return None, "inloggen bij iCloud mislukt of tweestapscode niet op tijd ingevoerd"
        if store is not None:
            store.set_state(STATE_KEY, "ok")  # wist 'wacht_op_code' zodra de code is ingevoerd
        cmd = _base_command(cfg) + ["--list-libraries", "--directory", str(cfg.source_other)]
        result = run_process(cmd, 300, log, "iCloud bibliotheken", [cfg.icloud_password])
    finally:
        ICLOUD_LOCK.release()
    found = [line.strip() for line in result.lines if _LIBRARY_LINE.match(line.strip())]
    if result.code != 0 or not found:
        errors = [ln for ln in result.lines if "ERROR" in ln or "exception" in ln.lower()]
        reason = errors[-1].split("ERROR", 1)[-1].strip() if errors else "geen bibliotheken gevonden"
        return None, f"bibliotheken ophalen mislukt: {reason}"
    return found, f"{len(found)} bibliotheek/bibliotheken gevonden"
