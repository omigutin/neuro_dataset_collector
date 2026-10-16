""" Поводы сохранить кадр: достаточно одного сработавшего. """

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Protocol

from ..config.models import AlwaysTrigger, AuditTrigger, ConfidenceTrigger, Trigger
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

    def __init__(self, config: ConfidenceTrigger) -> None:
        self._config = config

    def fires(self, candidate: Candidate) -> bool:
        if not candidate.predictions:
            return self._config.include_empty
        # Для детекции и сегментации повод — хотя бы один сомнительный объект.
        return any(self._config.min <= p.confidence <= self._config.max for p in candidate.predictions)


class _Audit:
    name = "audit"

    def __init__(self, config: AuditTrigger) -> None:
        self._rate = config.rate
        self._random = random.Random(config.seed)

    def fires(self, candidate: Candidate) -> bool:
        return self._random.random() < self._rate


def build_triggers(configs: Sequence[Trigger]) -> list[TriggerRule]:
    """ Создаёт правила-поводы по настройкам. """
    rules: list[TriggerRule] = []
    for config in configs:
        if isinstance(config, AlwaysTrigger):
            rules.append(_Always())
        elif isinstance(config, ConfidenceTrigger):
            rules.append(_Confidence(config))
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
