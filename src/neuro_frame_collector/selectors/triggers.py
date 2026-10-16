""" Поводы сохранить кадр: достаточно одного сработавшего. """

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Protocol

from ..config.models import AlwaysTrigger, AuditTrigger, ConfidenceTrigger, LabelOverride, Trigger
from .base import Candidate

__all__ = ["TriggerRule", "build_triggers", "first_trigger"]


class TriggerRule(Protocol):
    """ Правило-повод: возвращает своё имя, если сработало. """

    name: str

    def fires(self, candidate: Candidate) -> bool:
        """ Сработал ли повод для кандидата. """
        ...


class _Always:
    name = "always"

    def fires(self, candidate: Candidate) -> bool:
        return True


class _Confidence:
    name = "confidence"

    def __init__(self, config: ConfidenceTrigger, per_label: Mapping[str, LabelOverride]) -> None:
        self._config = config
        self._per_label = per_label

    def _band(self, label: str) -> tuple[float, float] | None:
        """ Диапазон уверенности для класса; None — класс исключён из автосбора. """
        override = self._per_label.get(label)
        if override is None:
            return self._config.min, self._config.max
        if not override.enabled:
            return None
        low = override.min_conf if override.min_conf is not None else self._config.min
        high = override.max_conf if override.max_conf is not None else self._config.max
        return low, high

    def fires(self, candidate: Candidate) -> bool:
        if not candidate.predictions:
            return self._config.include_empty
        # Для детекции и сегментации повод — хотя бы один сомнительный объект.
        for prediction in candidate.predictions:
            band = self._band(prediction.label)
            if band is not None and band[0] <= prediction.confidence <= band[1]:
                return True
        return False


class _Audit:
    name = "audit"

    def __init__(self, config: AuditTrigger) -> None:
        self._rate = config.rate
        self._random = random.Random(config.seed)

    def fires(self, candidate: Candidate) -> bool:
        return self._random.random() < self._rate


def build_triggers(
    configs: Sequence[Trigger], per_label: Mapping[str, LabelOverride] | None = None
) -> list[TriggerRule]:
    """ Создаёт правила-поводы по настройкам; per_label — особые диапазоны уверенности по классам. """
    rules: list[TriggerRule] = []
    for config in configs:
        if isinstance(config, AlwaysTrigger):
            rules.append(_Always())
        elif isinstance(config, ConfidenceTrigger):
            rules.append(_Confidence(config, per_label or {}))
        elif isinstance(config, AuditTrigger):
            rules.append(_Audit(config))
        else:
            raise TypeError(f"Unsupported trigger config: {config!r}")
    return rules


def first_trigger(rules: Sequence[TriggerRule], candidate: Candidate) -> str | None:
    """ Имя первого сработавшего повода или None. """
    for rule in rules:
        if rule.fires(candidate):
            return rule.name
    return None
