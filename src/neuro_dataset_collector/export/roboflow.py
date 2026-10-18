""" Выгрузка проверенного набора в виде, который понимают Roboflow и YOLO. """

from __future__ import annotations

import logging
import shutil
from collections import Counter
from pathlib import Path

import yaml

from ..catalog.catalog import Catalog
from ..config.models import CollectorConfig, TaskType
from ..core.results import ExportReport
from ..core.types import SampleStatus
from ..storage.layout import IMAGES_DIR, LABELS_DIR

__all__ = ["export_dataset"]

_log = logging.getLogger("neuro_dataset_collector")


def export_dataset(
    config: CollectorConfig,
    out_dir: Path,
    *,
    only_new: bool = True,
    include_pending: bool = False,
    catalog: Catalog | None = None,
) -> ExportReport:
    """
        Копирует кадры коллекции в out_dir.
        Классификация: out_dir/<класс>/файлы — Roboflow сам разложит по классам по именам папок.
        Детекция и сегментация: out_dir/images, out_dir/labels и data.yaml (формат YOLO).
        По умолчанию — только проверенные (после sync) и ещё не выгруженные.
    """
    own = catalog is None
    catalog = catalog or Catalog(config.catalog.root)
    out_dir = Path(out_dir)
    report = ExportReport(out_dir=out_dir)
    statuses = [SampleStatus.VERIFIED] + ([SampleStatus.PENDING] if include_pending else [])
    counts: Counter[str] = Counter()
    exported_ids: list[int] = []
    try:
        for record in catalog.records(config.catalog.collection, statuses):
            if only_new and record.exported_at:
                continue
            source = catalog.absolute(record)
            if not source.exists():
                _log.warning("File is missing, skipped: %s", source)
                continue
            if config.task == TaskType.CLASSIFY:
                if not record.label:
                    continue
                target = out_dir / record.label / source.name
            else:
                target = out_dir / IMAGES_DIR / source.name
                label_file = config.collection_dir / LABELS_DIR / (source.stem + ".txt")
                if label_file.exists():
                    (out_dir / LABELS_DIR).mkdir(parents=True, exist_ok=True)
                    shutil.copy2(label_file, out_dir / LABELS_DIR / label_file.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            counts[record.label or ""] += 1
            if record.id is not None:
                exported_ids.append(record.id)
        if config.task != TaskType.CLASSIFY and exported_ids:
            names = dict(enumerate(config.classes))
            data = {"path": ".", "train": IMAGES_DIR, "val": IMAGES_DIR, "names": names}
            (out_dir / "data.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                               encoding="utf-8")
        catalog.mark_exported(exported_ids)
        catalog.add_export(config.catalog.collection, f"roboflow-{config.task.value}", out_dir, len(exported_ids))
    finally:
        if own:
            catalog.close()
    report.exported = len(exported_ids)
    report.by_label = {label: count for label, count in counts.items() if label}
    return report
