""" Сверка: человек проверил папки глазами, теперь их состояние записывается в историю. """

from __future__ import annotations

import logging
from pathlib import Path

from ..catalog.catalog import Catalog, SampleRecord
from ..config.models import CollectorConfig, TaskType
from ..core.results import SyncReport
from ..core.types import SampleStatus
from ..storage.layout import FRAMES_DIR, IMAGES_DIR, LABELS_DIR, SUSPECT_DIR

__all__ = ["sync_collection"]

_log = logging.getLogger("neuro_dataset_collector")

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
_SKIP_DIRS = {FRAMES_DIR, LABELS_DIR}


def _scan(collection_dir: Path) -> dict[str, list[Path]]:
    """ Все картинки коллекции: имя файла → пути (на случай повторов). """
    found: dict[str, list[Path]] = {}
    if not collection_dir.exists():
        return found
    for path in collection_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_EXTENSIONS or path.name.startswith("."):
            continue
        relative = path.relative_to(collection_dir).parts
        if relative and relative[0] in _SKIP_DIRS:
            continue
        found.setdefault(path.name, []).append(path)
    return found


def _place(config: CollectorConfig, path: Path) -> tuple[str, str | None]:
    """ Где лежит файл: label (с классом), suspect, unsorted, images или other. """
    parts = path.relative_to(config.collection_dir).parts
    if config.task != TaskType.CLASSIFY:
        return ("images", None) if len(parts) == 2 and parts[0] == IMAGES_DIR else ("other", None)
    if parts[0] == SUSPECT_DIR:
        return "suspect", None
    if len(parts) != 2:
        return "other", None
    if parts[0] == config.unsorted_label:
        return "unsorted", None
    return "label", parts[0]


def sync_collection(config: CollectorConfig, catalog: Catalog | None = None) -> SyncReport:
    """
        Сверяет папки коллекции с историей. Запускать только после ручной проверки.
        Файл в папке класса → «проверен» (если перенесён в другую папку — класс исправлен).
        Удалённый файл → «отклонён». Файлы в «подозрительных» и «без класса» остаются непроверенными.
    """
    own = catalog is None
    catalog = catalog or Catalog(config.catalog.root)
    report = SyncReport()
    try:
        on_disk = _scan(config.collection_dir)
        known: set[str] = set()
        records = [r for r in catalog.records(config.catalog.collection) if r.status != SampleStatus.FAILED]
        for record in records:
            known.add(record.filename)
            paths = on_disk.get(record.filename, [])
            if len(paths) > 1:
                _log.warning("File %s found several times, the first one is used: %s", record.filename, paths)
            _sync_record(config, catalog, record, paths[0] if paths else None, report)
        for name, paths in on_disk.items():
            if name not in known:
                report.unknown_files.extend(paths)
    finally:
        if own:
            catalog.close()
    return report


def _sync_record(
    config: CollectorConfig, catalog: Catalog, record: SampleRecord, path: Path | None, report: SyncReport
) -> None:
    """ Сверяет одну запись с найденным файлом. """
    assert record.id is not None
    if path is None:
        if record.status != SampleStatus.REJECTED:
            catalog.update(record.id, origin="sync", status=SampleStatus.REJECTED, reviewed=True)
            report.rejected += 1
        return
    relative = catalog.relative(path)
    place, label = _place(config, path)
    if place == "suspect":
        report.still_suspect += 1
        if relative != record.path or record.status != SampleStatus.SUSPECT:
            catalog.update(record.id, origin="sync", path=relative, status=SampleStatus.SUSPECT)
        return
    if place in ("unsorted", "other"):
        report.unsorted += 1
        if relative != record.path:
            catalog.update(record.id, origin="sync", path=relative, status=SampleStatus.PENDING)
        return
    if place == "label" and label is not None:
        if config.classes and label not in config.classes:
            report.unknown_labels.add(label)
        if label != record.label:
            report.relabeled += 1
    if record.status == SampleStatus.VERIFIED and relative == record.path and (label is None or label == record.label):
        return
    catalog.update(record.id, origin="sync", path=relative, label=label, status=SampleStatus.VERIFIED, reviewed=True)
    report.verified += 1
