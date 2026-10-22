""" Ручные исправления сохранённых кадров: перенос в другой класс и удаление. """

from __future__ import annotations

import os
from pathlib import Path

from ..catalog.catalog import Catalog, SampleRecord
from ..config.models import CollectorConfig, TaskType
from ..core.types import SampleStatus
from ..storage.layout import PathPlanner, frame_twin

__all__ = ["reject_sample", "relabel_sample", "sample_files"]

# Записи, которые ещё можно исправлять.
_EDITABLE = (SampleStatus.PENDING, SampleStatus.SUSPECT, SampleStatus.VERIFIED)


def _editable(catalog: Catalog, sample_id: int) -> SampleRecord:
    """ Запись, которую можно исправить; иначе ошибка с понятным текстом. """
    record = catalog.get(sample_id)
    if record is None:
        raise FileNotFoundError(f"Sample {sample_id} not found")
    if record.status not in _EDITABLE:
        raise ValueError(f"Sample {sample_id} is {record.status.value} and cannot be changed")
    return record


def sample_files(config: CollectorConfig, catalog: Catalog, sample_id: int) -> list[Path]:
    """ Файлы кадра на диске: картинка и, для детекции и сегментации, файл разметки YOLO. """
    record = _editable(catalog, sample_id)
    image = catalog.absolute(record)
    files = [image] if image.exists() else []
    twin = frame_twin(config.collection_dir, image)
    if twin.exists():
        files.append(twin)
    if config.task != TaskType.CLASSIFY:
        label = PathPlanner(config, set()).label_path(image)
        if label.exists():
            files.append(label)
    return files


def relabel_sample(config: CollectorConfig, catalog: Catalog, sample_id: int, label: str) -> Path:
    """
        Переносит кадр в папку другого класса (только классификация) и отмечает его проверенным человеком.
        Возвращает новый путь файла.
    """
    if config.task != TaskType.CLASSIFY:
        raise ValueError("Changing the class of a saved frame is available for classification only")
    if label not in config.classes:
        raise ValueError(f"Unknown class {label!r}; known classes: {config.classes}")
    record = _editable(catalog, sample_id)
    source = catalog.absolute(record)
    if not source.exists():
        raise FileNotFoundError(f"File not found: {source}")
    target = PathPlanner(config, set()).image_dir(label) / source.name
    if target != source:
        if target.exists():
            raise FileExistsError(f"File already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        _move_twin(config, source, target)
    catalog.update(sample_id, origin="manual_relabel", path=catalog.relative(target), label=label,
                   status=SampleStatus.VERIFIED, reviewed=True)
    return target


def _move_twin(config: CollectorConfig, source: Path, target: Path) -> None:
    """ Переносит полный кадр вслед за вырезом (если он сохранялся). """
    old, new = frame_twin(config.collection_dir, source), frame_twin(config.collection_dir, target)
    if old.exists() and not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old, new)


def reject_sample(catalog: Catalog, sample_id: int) -> None:
    """ Отмечает кадр отклонённым (файлы удаляет вызывающий: например, в корзину). """
    _editable(catalog, sample_id)
    catalog.update(sample_id, origin="manual_delete", status=SampleStatus.REJECTED, reviewed=True)
