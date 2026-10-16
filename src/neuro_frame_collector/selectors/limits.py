""" Ограничения: кадр сохраняется, только если прошёл все. """

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Protocol

import numpy as np
import numpy.typing as npt

from ..catalog.catalog import Catalog
from ..config.models import DedupLimit, IntervalLimit, Limit, QuotaLimit, SceneChangeLimit
from ..core.results import RejectReason
from ..imaging.hashing import mean_abs_diff
from .base import Candidate, Rejection

__all__ = ["DedupRule", "LimitRule", "build_limits", "check_limits", "commit_limits"]


class LimitRule(Protocol):
    """ Правило-ограничение: check — проверка, commit — учёт сохранённого кадра. """

    def check(self, candidate: Candidate) -> Rejection | None:
        """ Причина отказа или None. """
        ...

    def commit(self, candidate: Candidate) -> None:
        """ Учитывает сохранённый кадр. """
        ...


class _Interval:
    def __init__(self, config: IntervalLimit) -> None:
        self._config = config
        self._last: dict[tuple[str, ...], tuple[str, float]] = {}

    def _key(self, candidate: Candidate) -> tuple[str, ...]:
        values = {"source": candidate.source, "region": candidate.region_id or "", "label": candidate.label or ""}
        return tuple(values[name] for name in self._config.per)

    def _position(self, candidate: Candidate) -> float:
        if self._config.frames is not None:
            return float(candidate.frame_index)
        # Без метки времени (живой поток) берём часы компьютера.
        return candidate.timestamp if candidate.timestamp is not None else time.monotonic()

    def check(self, candidate: Candidate) -> Rejection | None:
        last = self._last.get(self._key(candidate))
        if last is None or last[0] != candidate.source:
            return None
        step = self._config.frames if self._config.frames is not None else self._config.seconds
        assert step is not None
        passed = self._position(candidate) - last[1]
        if 0 <= passed < step:
            unit = "кадров" if self._config.frames is not None else "с"
            return Rejection(RejectReason.INTERVAL, f"Слишком часто: прошло {passed:g} {unit}, нужно {step:g} {unit}")
        return None

    def commit(self, candidate: Candidate) -> None:
        self._last[self._key(candidate)] = (candidate.source, self._position(candidate))


class DedupRule:
    """ Отказ, если в каталоге уже есть почти такая же картинка. """

    def __init__(self, config: DedupLimit, catalog: Catalog, collection: str | None) -> None:
        self._threshold = config.threshold
        self._catalog = catalog
        self._collection = collection

    def check(self, candidate: Candidate) -> Rejection | None:
        found = self._catalog.find_similar(candidate.hash, self._threshold, self._collection)
        if found is None:
            return None
        record, distance = found
        path = self._catalog.absolute(record)
        state = "отклонённый ранее" if record.status.value == "rejected" else "уже сохранённый"
        return Rejection(
            RejectReason.DUPLICATE,
            f"Похож на {state} кадр (отличие {distance} из 64): {record.path}",
            similar_to=path,
            distance=distance,
        )

    def commit(self, candidate: Candidate) -> None:
        # Отпечаток попадает в индекс при добавлении записи в каталог.
        return None


class _Quota:
    def __init__(self, config: QuotaLimit, catalog: Catalog, collection: str) -> None:
        self._config = config
        self._counts = dict(catalog.label_counts(collection))
        self._total = sum(self._counts.values())

    def check(self, candidate: Candidate) -> Rejection | None:
        if self._config.total is not None and self._total >= self._config.total:
            return Rejection(RejectReason.QUOTA, f"Набрано достаточно кадров: {self._total}")
        label = candidate.label or ""
        count = self._counts.get(label, 0)
        if self._config.per_label is not None and count >= self._config.per_label:
            return Rejection(RejectReason.QUOTA, f"Для класса «{label}» набрано достаточно кадров: {count}")
        return None

    def commit(self, candidate: Candidate) -> None:
        label = candidate.label or ""
        self._counts[label] = self._counts.get(label, 0) + 1
        self._total += 1


class _SceneChange:
    def __init__(self, config: SceneChangeLimit) -> None:
        self._min_diff = config.min_diff
        self._last: dict[tuple[str, str], npt.NDArray[np.float32]] = {}

    def check(self, candidate: Candidate) -> Rejection | None:
        last = self._last.get(candidate.stream_key)
        if last is None:
            return None
        diff = mean_abs_diff(last, candidate.small)
        if diff < self._min_diff:
            return Rejection(
                RejectReason.SCENE_UNCHANGED,
                f"Картинка почти не изменилась с прошлого сохранения (разница {diff:.1f}, нужно {self._min_diff:g})",
            )
        return None

    def commit(self, candidate: Candidate) -> None:
        self._last[candidate.stream_key] = candidate.small


def build_limits(configs: Sequence[Limit], catalog: Catalog, collection: str, dedup_collection: str | None) -> list[
    LimitRule
]:
    """ Создаёт правила-ограничения по настройкам. """
    rules: list[LimitRule] = []
    for config in configs:
        if isinstance(config, IntervalLimit):
            rules.append(_Interval(config))
        elif isinstance(config, DedupLimit):
            rules.append(DedupRule(config, catalog, dedup_collection))
        elif isinstance(config, QuotaLimit):
            rules.append(_Quota(config, catalog, collection))
        elif isinstance(config, SceneChangeLimit):
            rules.append(_SceneChange(config))
        else:
            raise TypeError(f"Unsupported limit config: {config!r}")
    return rules


def check_limits(rules: Sequence[LimitRule], candidate: Candidate) -> Rejection | None:
    """ Первая причина отказа или None. Дешёвые проверки стоят раньше дорогих по порядку в настройках. """
    for rule in rules:
        rejection = rule.check(candidate)
        if rejection is not None:
            return rejection
    return None


def commit_limits(rules: Sequence[LimitRule], candidate: Candidate) -> None:
    """ Учитывает сохранённый кадр во всех ограничениях. """
    for rule in rules:
        rule.commit(candidate)
