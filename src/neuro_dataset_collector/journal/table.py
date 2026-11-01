"""
    Журнал ответов модели в памяти: выборки по зоне, классу, уверенности и сменам класса.
    Строки хранятся столбцами numpy — часовой журнал на несколько зон занимает единицы мегабайт.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

from .predictions import JournalMeta, JournalRow, journal_paths, read_journal_meta

__all__ = ["JournalFilter", "JournalPage", "JournalTable", "find_journals", "journal_base"]

_META_SUFFIX = ".predictions.json"
# Номер «нет ответа» в столбцах классов и состояний.
_NONE = -1


class JournalFilter(BaseModel):
    """
        Выборка из журнала (все условия вместе). labels: "" — кадры, где модель ничего не нашла.
        flips — только кадры, где класс в зоне сменился по сравнению с прошлой строкой этой зоны.
    """

    model_config = ConfigDict(extra="ignore")

    zones: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    conf_min: float | None = None
    conf_max: float | None = None
    flips: bool = False
    frame_from: int | None = None
    frame_to: int | None = None


@dataclass(frozen=True)
class JournalPage:
    """ Страница выборки: строки, сколько всего подходит и сколько строк по классам в выборке. """

    rows: list[JournalRow]
    total: int
    labels: dict[str, int]


def journal_base(path: Path) -> Path:
    """ Общая часть имени журнала по любому из его файлов (или сама общая часть). """
    path = Path(path)
    for suffix in (".predictions.csv", _META_SUFFIX):
        if path.name.endswith(suffix):
            return path.with_name(path.name[: -len(suffix)])
    return path


def find_journals(folder: Path, recursive: bool = True) -> list[Path]:
    """ Журналы в папке (общие части имён), по имени. """
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = folder.rglob(f"*{_META_SUFFIX}") if recursive else folder.glob(f"*{_META_SUFFIX}")
    bases = [journal_base(p) for p in files if p.is_file() and not p.name.startswith(".")]
    return sorted((b for b in bases if journal_paths(b)[0].is_file()), key=lambda b: (b.name.lower(), str(b)))


def _index(values: list[str], value: str) -> int:
    """ Номер значения в справочнике (добавляет новое). """
    try:
        return values.index(value)
    except ValueError:
        values.append(value)
        return len(values) - 1


class JournalTable:
    """ Журнал целиком в памяти. """

    def __init__(self, base: Path, meta: JournalMeta, frames: npt.NDArray[np.int64], times: npt.NDArray[np.float64],
                 zone_ids: npt.NDArray[np.int32], label_ids: npt.NDArray[np.int32], confs: npt.NDArray[np.float64],
                 state_ids: npt.NDArray[np.int32], zones: list[str], labels: list[str], states: list[str]) -> None:
        self.base = base
        self.meta = meta
        self._frames, self._times, self._zone_ids = frames, times, zone_ids
        self._label_ids, self._confs, self._state_ids = label_ids, confs, state_ids
        self.zones, self.labels, self.states = zones, labels, states
        self._flips = self._find_flips()

    @classmethod
    def load(cls, path: Path) -> JournalTable:
        """ Читает журнал (path — любой из двух файлов или общая часть имени). """
        base = journal_base(path)
        meta = read_journal_meta(base)
        csv_path, _ = journal_paths(base)
        zones = [zone.id for zone in meta.zones]
        labels: list[str] = list(meta.classes)
        states: list[str] = []
        frames: list[int] = []
        times: list[float] = []
        zone_ids: list[int] = []
        label_ids: list[int] = []
        confs: list[float] = []
        state_ids: list[int] = []
        with csv_path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.reader(file)
            header = next(reader, None)
            if header is None:
                raise ValueError(f"Empty predictions journal: {csv_path}")
            column = {name: i for i, name in enumerate(header)}
            missing = {"frame", "zone", "label"} - set(column)
            if missing:
                raise ValueError(f"Predictions journal has no columns {sorted(missing)}: {csv_path}")
            i_frame, i_zone, i_label = column["frame"], column["zone"], column["label"]
            i_time, i_conf, i_state = column.get("time"), column.get("conf"), column.get("state")
            for record in reader:
                if not record:
                    continue
                frames.append(int(record[i_frame]))
                times.append(float(record[i_time]) if i_time is not None and record[i_time] else 0.0)
                zone_ids.append(_index(zones, record[i_zone]))
                label = record[i_label]
                label_ids.append(_index(labels, label) if label else _NONE)
                conf = record[i_conf] if i_conf is not None else ""
                confs.append(float(conf) if conf else np.nan)
                state = record[i_state] if i_state is not None else ""
                state_ids.append(_index(states, state) if state else _NONE)
        return cls(base, meta, np.array(frames, dtype=np.int64), np.array(times, dtype=np.float64),
                   np.array(zone_ids, dtype=np.int32), np.array(label_ids, dtype=np.int32),
                   np.array(confs, dtype=np.float64), np.array(state_ids, dtype=np.int32), zones, labels, states)

    def __len__(self) -> int:
        return int(self._frames.size)

    def _find_flips(self) -> npt.NDArray[np.bool_]:
        """ Строки, где класс зоны отличается от прошлой строки этой зоны. """
        flips = np.zeros(self._frames.size, dtype=bool)
        for zone in range(len(self.zones)):
            rows = np.flatnonzero(self._zone_ids == zone)
            if rows.size > 1:
                labels = self._label_ids[rows]
                flips[rows[1:]] = labels[1:] != labels[:-1]
        return flips

    def _mask(self, flt: JournalFilter) -> npt.NDArray[np.bool_]:
        mask = np.ones(self._frames.size, dtype=bool)

        def ids(values: Sequence[str], names: list[str], empty: bool) -> list[int]:
            return [(_NONE if empty and not v else names.index(v)) for v in values if (empty and not v) or v in names]

        if flt.zones:
            mask &= np.isin(self._zone_ids, ids(flt.zones, self.zones, False))
        if flt.labels:
            mask &= np.isin(self._label_ids, ids(flt.labels, self.labels, True))
        if flt.states:
            mask &= np.isin(self._state_ids, ids(flt.states, self.states, True))
        if flt.conf_min is not None:
            mask &= self._confs >= flt.conf_min
        if flt.conf_max is not None:
            mask &= self._confs <= flt.conf_max
        if flt.flips:
            mask &= self._flips
        if flt.frame_from is not None:
            mask &= self._frames >= flt.frame_from
        if flt.frame_to is not None:
            mask &= self._frames <= flt.frame_to
        return mask

    def _row(self, i: int) -> JournalRow:
        label, state, conf = int(self._label_ids[i]), int(self._state_ids[i]), float(self._confs[i])
        return JournalRow(frame=int(self._frames[i]), time=round(float(self._times[i]), 3),
                          zone=self.zones[int(self._zone_ids[i])], label=self.labels[label] if label != _NONE else None,
                          conf=None if np.isnan(conf) else conf, state=self.states[state] if state != _NONE else None)

    def query(self, flt: JournalFilter | None = None, offset: int = 0, limit: int = 100) -> JournalPage:
        """ Строки по выборке, по порядку кадров. """
        rows = np.flatnonzero(self._mask(flt or JournalFilter()))
        found, counts = np.unique(self._label_ids[rows], return_counts=True)
        labels = {(self.labels[int(k)] if k != _NONE else ""): int(n) for k, n in zip(found, counts, strict=True)}
        page = rows[max(0, offset): max(0, offset) + max(0, limit)]
        return JournalPage([self._row(int(i)) for i in page], int(rows.size), labels)

    def runs(self, zone: str, max_runs: int = 2000) -> list[tuple[int, int, str | None]]:
        """
            Ответы модели в зоне отрезками «кадр с, кадр по, класс» (для шкалы времени).
            Если отрезков больше max_runs, короткие сливаются с соседними: на шкале их всё равно не видно.
        """
        if zone not in self.zones:
            return []
        rows = np.flatnonzero(self._zone_ids == self.zones.index(zone))
        if rows.size == 0:
            return []
        frames, labels = self._frames[rows], self._label_ids[rows]
        starts = np.concatenate(([0], np.flatnonzero(labels[1:] != labels[:-1]) + 1))
        ends = np.concatenate((starts[1:], [rows.size])) - 1
        result = [(int(frames[s]), int(frames[e]), self.labels[int(labels[s])] if labels[s] != _NONE else None)
                  for s, e in zip(starts, ends, strict=True)]
        if len(result) > max_runs:
            step = (result[-1][1] - result[0][0] + 1) / max_runs
            merged: list[tuple[int, int, str | None]] = []
            for start, end, label in result:
                if merged and (end - start + 1 < step or merged[-1][2] == label):
                    merged[-1] = (merged[-1][0], end, merged[-1][2])
                else:
                    merged.append((start, end, label))
            result = merged
        return result

    def answer(self, frame: int, zone: str) -> JournalRow | None:
        """ Ответ модели в зоне на кадре или на ближайшем предыдущем проверенном кадре. """
        if zone not in self.zones:
            return None
        rows = np.flatnonzero((self._zone_ids == self.zones.index(zone)) & (self._frames <= frame))
        return self._row(int(rows[-1])) if rows.size else None
