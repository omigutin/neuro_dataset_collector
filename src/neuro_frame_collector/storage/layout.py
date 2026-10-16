""" Раскладка файлов по папкам и имена файлов. """

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from ..config.models import CollectorConfig, TaskType

__all__ = ["FRAMES_DIR", "IMAGES_DIR", "LABELS_DIR", "SUSPECT_DIR", "PathPlanner", "safe_name"]

SUSPECT_DIR = "_suspect"
FRAMES_DIR = "_frames"
IMAGES_DIR = "images"
LABELS_DIR = "labels"
_UNSAFE = re.compile(r'[^\w.\-]+', re.UNICODE)


def safe_name(value: str) -> str:
    """ Делает строку пригодной для имени файла: всё лишнее заменяется на '-'. """
    cleaned = _UNSAFE.sub("-", value.strip()).strip("-.")
    return cleaned or "x"


class PathPlanner:
    """
        Строит пути для новых файлов. Раскладка фиксированная, чтобы сверка однозначно понимала класс:
          классификация: <коллекция>/<класс>/, сомнительные — <коллекция>/_suspect/<класс>__<предложенный>/
          детекция и сегментация: <коллекция>/images/ и <коллекция>/labels/
        Имена уникальны в пределах коллекции.
    """

    def __init__(self, config: CollectorConfig, existing_names: Iterable[str] = ()) -> None:
        self._config = config
        self._dir = config.collection_dir
        self._reserved: set[str] = set(existing_names)

    @property
    def collection_dir(self) -> Path:
        """ Папка коллекции. """
        return self._dir

    @property
    def extension(self) -> str:
        """ Расширение картинок. """
        return "." + self._config.storage.image_format.value

    def image_dir(self, label: str | None, suspect_proposed: str | None = None, suspect: bool = False) -> Path:
        """ Папка для картинки. """
        if self._config.task != TaskType.CLASSIFY:
            return self._dir / IMAGES_DIR
        name = label or self._config.unsorted_label
        if suspect:
            return self._dir / SUSPECT_DIR / f"{name}__{suspect_proposed or 'check'}"
        return self._dir / name

    def label_path(self, image_path: Path) -> Path:
        """ Файл разметки YOLO для картинки (детекция/сегментация). """
        return self._dir / LABELS_DIR / (image_path.stem + ".txt")

    def frame_path(self, source: str, frame_index: int) -> Path:
        """ Путь для полного кадра (опция keep_source_frame). """
        return self._dir / FRAMES_DIR / f"{safe_name(source)}_f{frame_index:06d}{self.extension}"

    def filename(
        self,
        *,
        source: str,
        region: str | None,
        frame: int,
        label: str | None,
        conf: float | None,
        mode: str,
        now: datetime | None = None,
    ) -> str:
        """ Уникальное имя файла по шаблону из настроек. """
        moment = now or datetime.now()
        fields: dict[str, str | int | float] = {
            "source": safe_name(source),
            "region": safe_name(region or "full"),
            "frame": frame,
            "label": safe_name(label or self._config.unsorted_label),
            "conf": round(conf, 2) if conf is not None else 0.0,
            "mode": mode,
            "date": moment.strftime("%Y%m%d"),
            "time": moment.strftime("%H%M%S"),
            "collection": safe_name(self._config.catalog.collection.replace("/", "-")),
        }
        stem = safe_name(self._config.storage.filename.format(**fields))
        name, suffix = stem + self.extension, 2
        while name in self._reserved:
            name, suffix = f"{stem}_{suffix}{self.extension}", suffix + 1
        self._reserved.add(name)
        return name

    def release(self, name: str) -> None:
        """ Освобождает имя, если сохранение не состоялось. """
        self._reserved.discard(name)
