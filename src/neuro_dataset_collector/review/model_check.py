"""
    Проверка уже собранного датасета: где выбранная модель не согласна с классом кадра
    и какие почти одинаковые кадры лежат в разных классах.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .._cv import get_cv2
from ..catalog.catalog import Catalog, SampleRecord
from ..config.models import CollectorConfig, TaskType
from ..core.types import Image, SampleStatus, top_prediction
from ..imaging.hashing import hamming_many
from ..predictors.base import Predictor

__all__ = ["CrossDuplicate", "Disagreement", "ModelCheckReport", "check_with_model", "cross_label_duplicates"]

# Какие кадры проверять по умолчанию: всё, что лежит в датасете.
CHECK_STATUSES = (SampleStatus.PENDING, SampleStatus.VERIFIED, SampleStatus.SUSPECT)


@dataclass(frozen=True)
class Disagreement:
    """ Кадр, где модель ответила не тем классом, под которым кадр лежит в датасете (или не уверена). """

    sample_id: int
    path: Path
    label: str | None
    model_label: str | None
    conf: float | None
    status: str
    source: str
    frame: int
    region: str | None = None


@dataclass(frozen=True)
class CrossDuplicate:
    """ Два почти одинаковых кадра в разных классах. """

    first: SampleRecord
    second: SampleRecord
    distance: int


@dataclass
class ModelCheckReport:
    """ Итог проверки датасета моделью. """

    checked: int = 0
    agreed: int = 0
    failed: int = 0
    disagreements: list[Disagreement] = field(default_factory=list)

    def summary(self) -> str:
        """ Итог для человека. """
        text = (f"Проверено кадров: {self.checked}, модель согласна: {self.agreed}, "
                f"не согласна или не уверена: {len(self.disagreements)}")
        return text + (f", не прочитано: {self.failed}" if self.failed else "")


def _read(path: Path) -> Image | None:
    """ Картинка с диска (путь может быть с русскими буквами). """
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    image = get_cv2().imdecode(data, get_cv2().IMREAD_COLOR) if data.size else None
    return image if image is not None else None


def check_with_model(
    config: CollectorConfig,
    catalog: Catalog,
    predictor: Predictor,
    *,
    statuses: Iterable[SampleStatus] = CHECK_STATUSES,
    min_conf: float = 0.0,
    source: str | None = None,
    stop: threading.Event | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ModelCheckReport:
    """
        Прогоняет моделью кадры коллекции (только классификация). Несогласие — модель ответила другим
        классом или уверенность ниже min_conf. Кадры «без класса» не проверяются. source — только одно видео.
    """
    if config.task != TaskType.CLASSIFY:
        raise ValueError("Dataset check with a model supports classification only")
    records = [r for r in catalog.records(config.catalog.collection, statuses)
               if r.label and (source is None or r.source == source)]
    report = ModelCheckReport()
    for number, record in enumerate(records, start=1):
        if stop is not None and stop.is_set():
            break
        if on_progress is not None:
            on_progress(number, len(records))
        path = catalog.absolute(record)
        image = _read(path)
        if image is None:
            report.failed += 1
            continue
        report.checked += 1
        best = top_prediction(predictor(image))
        label = best.label if best else None
        conf = float(best.confidence) if best else None
        if label == record.label and (conf or 0.0) >= min_conf:
            report.agreed += 1
            continue
        report.disagreements.append(Disagreement(record.id or 0, path, record.label, label, conf, record.status.value,
                                                 record.source, record.frame_index, record.region_id))
    return report


def cross_label_duplicates(
    config: CollectorConfig,
    catalog: Catalog,
    threshold: int = 3,
    *,
    statuses: Iterable[SampleStatus] = CHECK_STATUSES,
    source: str | None = None,
    limit: int = 500,
) -> list[CrossDuplicate]:
    """ Почти одинаковые кадры (по отпечатку) с разными классами — один из них, скорее всего, ошибочен. """
    records = [r for r in catalog.records(config.catalog.collection, statuses)
               if r.label and (source is None or r.source == source)]
    if len(records) < 2:
        return []
    hashes = np.array([r.hash & 0xFFFFFFFFFFFFFFFF for r in records], dtype=np.uint64)
    labels = np.array([r.label for r in records])
    found: list[CrossDuplicate] = []
    for i in range(len(records) - 1):
        distances = hamming_many(int(hashes[i]), hashes[i + 1:])
        close = np.flatnonzero((distances <= threshold) & (labels[i + 1:] != labels[i]))
        for j in close:
            found.append(CrossDuplicate(records[i], records[i + 1 + int(j)], int(distances[j])))
            if len(found) >= limit:
                return sorted(found, key=lambda d: d.distance)
    return sorted(found, key=lambda d: d.distance)
