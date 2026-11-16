"""De kern: bronmappen scannen, datum bepalen, sorteren naar JAAR/JAAR_MM/JAAR_MM_DD."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .config import Config
from .icloud import download_icloud
from .immich_sync import sync_immich
from .media import classify
from .metadata import ExifToolError, ExifToolMissing, file_date, read_exif_dates
from .runlog import RunLog
from .sidecar import write_rating_sidecar
from .store import Store


class RunAlreadyActive(RuntimeError):
    """Er loopt al een run."""


@dataclass
class RunStats:
    downloaded: int = 0
    scanned: int = 0
    moved: int = 0
    duplicates_removed: int = 0
    renamed: int = 0
    skipped: int = 0
    unknown: int = 0
    date_fallbacks: int = 0
    sidecars: int = 0
    favorites_set: int = 0
    favorites_waiting: int = 0
    errors: int = 0

    def as_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class RunResult:
    run_id: int
    status: str  # ok | ok_met_fouten | mislukt
    stats: RunStats


@dataclass(frozen=True)
class Item:
    path: Path
    favorite: bool
    source: str  # favorieten | overig | inbox
    is_video: bool


@dataclass(frozen=True)
class PlannedItem:
    item: Item
    when: datetime
    how: str
    fallback: bool


# ----------------------------------------------------------------------------
# Hulpfuncties
# ----------------------------------------------------------------------------
@contextmanager
def run_lock(data_dir: Path):
    """Zorgt dat er nooit twee runs tegelijk lopen (nachtelijke run + knop)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = open(data_dir / "run.lock", "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunAlreadyActive("er loopt al een run") from exc
        yield
    finally:
        handle.close()  # geeft de lock vrij


def sha256_of(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def same_content(a: Path, b: Path) -> bool:
    """Identiek = zelfde grootte en zelfde inhoud (hash)."""
    if a.stat().st_size != b.stat().st_size:
        return False
    return sha256_of(a) == sha256_of(b)


def resolve_destination(
    src: Path, day_dir: Path, reserved: dict[Path, Path]
) -> tuple[Path, str, int]:
    """Bepaalt waar een bestand terechtkomt.

    Geeft (doelpad, status, volgnummer) terug. status is 'new' of 'duplicate'.
    Bij een naamconflict met andere inhoud wordt _1, _2, ... toegevoegd.
    `reserved` bevat doelpaden die in een droogloop al 'ingenomen' zijn.
    """
    stem, suffix = src.stem, src.suffix
    n = 0
    while True:
        name = f"{stem}{suffix}" if n == 0 else f"{stem}_{n}{suffix}"
        candidate = day_dir / name
        if candidate in reserved:
            if same_content(src, reserved[candidate]):
                return candidate, "duplicate", n
            n += 1
            continue
        if candidate.exists():
            if same_content(src, candidate):
                return candidate, "duplicate", n
            n += 1
            continue
        return candidate, "new", n


def move_file(src: Path, dest: Path) -> None:
    """Verplaatst veilig. Over bestandssystemen heen: kopieren, controleren, dan pas wissen."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
        return
    except OSError:
        pass  # ander bestandssysteem of share: val terug op kopieren
    tmp = dest.with_name(dest.name + ".partial")
    try:
        shutil.copy2(src, tmp)
        if sha256_of(src) != sha256_of(tmp):
            raise OSError("controle na kopieren mislukt (hash verschilt)")
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    src.unlink()


def remove_empty_dirs(root: Path) -> int:
    """Ruimt lege submappen op (de bronmap zelf blijft staan)."""
    removed = 0
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path == root:
            continue
        try:
            path.rmdir()
            removed += 1
        except OSError:
            pass
    return removed


# ----------------------------------------------------------------------------
# Stappen van een run
# ----------------------------------------------------------------------------
def collect_items(
    cfg: Config, store: Store, run_id: int, log: RunLog, stats: RunStats
) -> list[Item]:
    sources = [
        (cfg.source_favorites, True, "favorieten", cfg.min_age_icloud),
        (cfg.source_other, False, "overig", cfg.min_age_icloud),
        (cfg.source_inbox, False, "inbox", cfg.min_age_inbox),
    ]
    step = "2/6 scannen"
    items: list[Item] = []
    now = time.time()
    for root, favorite, name, min_age in sources:
        if not root.is_dir():
            log.warn(step, f"bronmap '{name}' bestaat niet of is niet bereikbaar: {root}")
            continue
        found = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for filename in sorted(filenames):
                path = Path(dirpath) / filename
                kind = classify(path)
                if kind == "ignored":
                    continue
                if kind == "unknown":
                    stats.unknown += 1
                    log.warn(step, f"onbekend bestandstype, blijft staan: {path.name}", path)
                    if not cfg.dry_run:
                        store.add_review(run_id, "unknown_type", str(path), "onbekend bestandstype")
                    continue
                st = path.stat()
                age = now - max(st.st_mtime, st.st_ctime)
                if age < min_age:
                    stats.skipped += 1
                    log.info(step, f"nog in gebruik (te recent gewijzigd), volgende run: {path.name}", path)
                    continue
                items.append(Item(path, favorite, name, kind == "video"))
                found += 1
        log.info(step, f"bron '{name}': {found} bestand(en) gevonden in {root}")
    stats.scanned = len(items)
    return items


def plan_dates(
    items: list[Item], cfg: Config, log: RunLog, stats: RunStats | None = None
) -> list[PlannedItem]:
    step = "3/6 datums bepalen"
    log.info(step, f"EXIF-datums lezen voor {len(items)} bestand(en)")
    failures = 0

    def on_error(exc: ExifToolError, batch: list[Path]) -> bool:
        # Nooit de hele run laten mislukken: zonder EXIF-datum is de bestandsdatum een goede
        # terugval, en anders sorteert een kapotte exiftool helemaal niets meer.
        nonlocal failures
        failures += 1
        log.error(
            step,
            f"EXIF-datums lezen mislukt voor {len(batch)} bestand(en) ({exc}); "
            "voor die bestanden wordt de bestandsdatum gebruikt",
        )
        return isinstance(exc, ExifToolMissing)  # ontbreekt het programma, stop dan met proberen

    exif = read_exif_dates([i.path for i in items], on_error=on_error)
    if failures and stats is not None:
        stats.errors += 1

    # Live Photos: een video met dezelfde naam als precies een foto in dezelfde map
    # volgt de datum van die foto.
    groups: dict[tuple[Path, str], list[Item]] = {}
    for item in items:
        groups.setdefault((item.path.parent, item.path.stem.lower()), []).append(item)
    live_image: dict[Path, Item] = {}
    for members in groups.values():
        images = [m for m in members if not m.is_video]
        videos = [m for m in members if m.is_video]
        if len(images) == 1 and videos:
            for video in videos:
                live_image[video.path] = images[0]

    def own_date(item: Item) -> tuple[datetime, str, bool]:
        found = exif.get(item.path)
        if found is not None:
            return found[0], found[1], False
        return file_date(item.path), "bestandsdatum", True

    plan: list[PlannedItem] = []
    for item in items:
        image = live_image.get(item.path)
        if image is not None:
            when, how, fallback = own_date(image)
            how = f"Live Photo, datum van {image.path.name} ({how})"
        else:
            when, how, fallback = own_date(item)
        plan.append(PlannedItem(item, when, how, fallback))
    log.info(step, f"{sum(1 for p in plan if p.fallback)} bestand(en) zonder EXIF-datum (bestandsdatum gebruikt)")
    return plan


def process_one(
    planned: PlannedItem,
    prefix: str,
    cfg: Config,
    store: Store,
    run_id: int,
    log: RunLog,
    stats: RunStats,
    reserved: dict[Path, Path],
) -> None:
    item, when = planned.item, planned.when
    dry = cfg.dry_run
    day_dir = cfg.target / f"{when:%Y}" / f"{when:%Y_%m}" / f"{when:%Y_%m_%d}"
    dest, status, number = resolve_destination(item.path, day_dir, reserved)
    shown = dest.relative_to(cfg.target)

    if status == "duplicate":
        verb = "zou verwijderd worden" if dry else "verwijderd"
        log.info(prefix, f"{item.path.name}: identiek aan {shown}, duplicaat {verb}", item.path)
        if not dry:
            item.path.unlink()
        stats.duplicates_removed += 1
    else:
        verb = "zou verplaatst worden naar" if dry else "verplaatst naar"
        log.info(prefix, f"{item.path.name}: {verb} {shown} (datum: {planned.how})", item.path)
        if dry:
            reserved[dest] = item.path
        else:
            move_file(item.path, dest)
        stats.moved += 1
        if number > 0:
            stats.renamed += 1
            log.warn(prefix, f"naamconflict met andere inhoud, hernoemd naar {dest.name}", item.path)
            if not dry:
                store.add_review(
                    run_id, "renamed", str(dest),
                    f"andere inhoud dan bestaand bestand met dezelfde naam; origineel heette {item.path.name}",
                )
        if planned.fallback:
            stats.date_fallbacks += 1
            if not dry:
                store.add_review(
                    run_id, "date_fallback", str(dest),
                    f"geen EXIF-datum; bestandsdatum gebruikt ({when:%Y-%m-%d %H:%M:%S})",
                )

    if item.favorite:
        if dry:
            log.info(prefix, f"sidecar met rating 5 zou geschreven worden voor {dest.name}", item.path)
        else:
            if write_rating_sidecar(dest):
                stats.sidecars += 1
                log.info(prefix, f"sidecar met rating 5 geschreven voor {dest.name}", dest)
            store.add_pending_favorite(str(dest))


def _run(cfg: Config, store: Store, run_id: int, log: RunLog, stats: RunStats) -> None:
    mode = "DROOGLOOP (er wordt niets gewijzigd)" if cfg.dry_run else "echte run"
    log.info("start", f"{mode}; doelmap: {cfg.target}")
    if not cfg.target.is_dir():
        # Bewust geen mkdir: als de NAS niet gemount is, mag er geen lokale kopie ontstaan.
        raise RuntimeError(f"doelmap bestaat niet of is niet bereikbaar: {cfg.target}")

    # Eerst iCloud binnenhalen. Mislukt dat, dan sorteren we gewoon wat er al staat (ook de inbox).
    download_icloud(cfg, store, log, stats)

    items = collect_items(cfg, store, run_id, log, stats)
    if items:
        plan = plan_dates(items, cfg, log, stats)
        _process_all(plan, cfg, store, run_id, log, stats)
        _cleanup(cfg, log)
    else:
        log.info("klaar", "geen nieuwe bestanden om te sorteren")

    # Ook zonder nieuwe bestanden: favorieten van eerdere runs kunnen nog in de wachtrij staan.
    sync_immich(cfg, store, log, stats, moved_any=stats.moved > 0)


def _process_all(plan, cfg, store, run_id, log, stats) -> None:
    step = "4/6 verwerken"
    reserved: dict[Path, Path] = {}
    total = len(plan)
    for index, planned in enumerate(plan, start=1):
        prefix = f"{step} {index}/{total}"
        try:
            process_one(planned, prefix, cfg, store, run_id, log, stats, reserved)
        except Exception as exc:  # een fout bij een bestand stopt de run niet
            stats.errors += 1
            log.error(prefix, f"{planned.item.path.name}: fout: {exc!r}", planned.item.path)
            if not cfg.dry_run:
                store.add_review(run_id, "error", str(planned.item.path), repr(exc))


def _cleanup(cfg, log) -> None:
    step = "5/6 opruimen"
    if cfg.dry_run:
        return
    removed = sum(
        remove_empty_dirs(root)
        for root in (cfg.source_favorites, cfg.source_other, cfg.source_inbox)
        if root.is_dir()
    )
    if removed:
        log.info(step, f"{removed} lege submap(pen) opgeruimd")


def run_import(cfg: Config, store: Store, trigger: str = "handmatig") -> RunResult:
    """Voert een volledige run uit. Gooit RunAlreadyActive als er al een loopt."""
    with run_lock(cfg.data_dir):
        run_id = store.start_run(trigger, cfg.dry_run)
        stats = RunStats()
        log = RunLog(store, run_id)
        status = "ok"
        try:
            _run(cfg, store, run_id, log, stats)
            if stats.errors:
                status = "ok_met_fouten"
        except Exception as exc:
            stats.errors += 1
            status = "mislukt"
            log.error("run", f"run afgebroken: {exc!r}")
        finally:
            if not cfg.dry_run:
                pruned = store.prune(cfg.log_retention_days)
                if any(pruned.values()):
                    log.info("opruimen", f"oude logregels opgeruimd: {pruned}")
            log.info(
                "klaar",
                f"status: {status}; {stats.downloaded} uit iCloud gedownload, {stats.moved} verplaatst, {stats.duplicates_removed} duplicaten verwijderd, "
                f"{stats.renamed} hernoemd, {stats.skipped} overgeslagen, {stats.unknown} onbekend, "
                f"{stats.date_fallbacks} met bestandsdatum, {stats.sidecars} sidecars, "
                f"{stats.favorites_set} favorieten in Immich gemarkeerd ({stats.favorites_waiting} wachtend), "
                f"{stats.errors} fouten",
            )
            store.finish_run(run_id, status, stats.as_json())
        return RunResult(run_id, status, stats)
