"""
    Журнал ответов модели: что модель ответила на каждый обработанный кадр в каждой зоне.

    Два файла рядом:
      <имя>.predictions.csv  — строки: кадр, время, зона, класс, уверенность, состояние машины состояний;
      <имя>.predictions.json — сведения: видео, FPS, размер кадра, зоны (контуры в координатах кадра), модель.
    Кадры не сохраняются: картинку любого кадра и выреза можно достать из видео по номеру кадра и контуру зоны.
"""

from __future__ import annotations

import csv
import io
import json
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "JOURNAL_VERSION",
    "JournalMeta",
    "JournalRow",
    "JournalWriter",
    "JournalZone",
    "journal_paths",
    "read_journal",
    "read_journal_meta",
]

JOURNAL_VERSION = 1
_CSV_SUFFIX = ".predictions.csv"
_META_SUFFIX = ".predictions.json"
_COLUMNS = ("frame", "time", "zone", "label", "conf", "state")


def _now() -> str:
    """ Текущее время в ISO-формате. """
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class JournalZone(BaseModel):
    """ Зона, для которой модель давала ответы: номер и контур в координатах кадра. """

    model_config = ConfigDict(extra="ignore")

    id: str
    polygon: list[tuple[float, float]]
    label: str = ""


class JournalMeta(BaseModel):
    """ Сведения о журнале: какое видео, какая модель, какие зоны. """

    model_config = ConfigDict(extra="allow")

    format_version: int = JOURNAL_VERSION
    video: str
    fps: float = 0.0
    width: int = 0
    height: int = 0
    zones: list[JournalZone] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)
    model: str = ""
    source: str = ""          # кто записал: 'regression' (прогон PC), 'collector' (нарезчик)
    run_id: str = ""
    factory: str = ""
    module: str = ""
    cam_id: str = ""
    created_at: str = Field(default_factory=_now)


class JournalRow(BaseModel):
    """ Ответ модели на один кадр в одной зоне. label=None — модель ничего не нашла. """

    model_config = ConfigDict(frozen=True)

    frame: int
    time: float
    zone: str
    label: str | None = None
    conf: float | None = None
    state: str | None = None


def journal_paths(base: Path) -> tuple[Path, Path]:
    """ Файлы журнала по общей части имени: (csv, json). base — путь без суффикса, например <папка>/<видео>. """
    base = Path(base)
    return base.with_name(base.name + _CSV_SUFFIX), base.with_name(base.name + _META_SUFFIX)


def _base_of(path: Path) -> Path:
    """ Общая часть имени по любому из двух файлов журнала. """
    name = Path(path).name
    for suffix in (_CSV_SUFFIX, _META_SUFFIX):
        if name.endswith(suffix):
            return Path(path).with_name(name[: -len(suffix)])
    raise ValueError(f"Not a predictions journal file: {path}")


class JournalWriter:
    """
        Запись журнала во время прогона. Строки пишутся в CSV с буфером (дёшево на каждый кадр),
        сведения — в JSON при открытии и при закрытии.
    """

    def __init__(self, base: Path, meta: JournalMeta, flush_every: int = 500) -> None:
        self._csv_path, self._meta_path = journal_paths(base)
        self._csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._meta = meta
        self._flush_every = max(1, flush_every)
        self._pending = 0
        self._file: io.TextIOWrapper | None = self._csv_path.open("w", encoding="utf-8", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow(_COLUMNS)
        self._write_meta()

    @property
    def csv_path(self) -> Path:
        """ Файл строк журнала. """
        return self._csv_path

    @property
    def meta_path(self) -> Path:
        """ Файл сведений журнала. """
        return self._meta_path

    def write(self, frame: int, time: float, zone: str, label: str | None, conf: float | None,
              state: str | None = None) -> None:
        """ Добавляет ответ модели на кадр в зоне. """
        if self._file is None:
            raise RuntimeError("Journal is closed")
        self._writer.writerow((int(frame), f"{time:.3f}", zone, label or "",
                               "" if conf is None else f"{conf:.4f}", state or ""))
        self._pending += 1
        if self._pending >= self._flush_every:
            self._file.flush()
            self._pending = 0

    def close(self, **meta_updates: Any) -> None:
        """ Дописывает строки и сведения (meta_updates — поля, известные только в конце). """
        if self._file is None:
            return
        self._file.close()
        self._file = None
        if meta_updates:
            self._meta = self._meta.model_copy(update=meta_updates)
        self._write_meta()

    def _write_meta(self) -> None:
        temp = self._meta_path.with_name(f".{self._meta_path.name}.tmp")
        temp.write_text(self._meta.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temp, self._meta_path)

    def __enter__(self) -> JournalWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def read_journal_meta(path: Path) -> JournalMeta:
    """ Сведения журнала (path — любой из двух файлов или общая часть имени). """
    path = Path(path)
    base = _base_of(path) if path.name.endswith((_CSV_SUFFIX, _META_SUFFIX)) else path
    _, meta_path = journal_paths(base)
    return JournalMeta.model_validate(json.loads(meta_path.read_text(encoding="utf-8")))


def read_journal(path: Path) -> Iterator[JournalRow]:
    """ Строки журнала по порядку (path — любой из двух файлов или общая часть имени). """
    path = Path(path)
    base = _base_of(path) if path.name.endswith((_CSV_SUFFIX, _META_SUFFIX)) else path
    csv_path, _ = journal_paths(base)
    with csv_path.open("r", encoding="utf-8", newline="") as file:
        for record in csv.DictReader(file):
            yield JournalRow(
                frame=int(record["frame"]),
                time=float(record["time"] or 0),
                zone=record["zone"],
                label=record.get("label") or None,
                conf=float(record["conf"]) if record.get("conf") else None,
                state=record.get("state") or None,
            )
