""" Проверка последовательности ответов модели: поиск коротких «выбросов» среди устойчивого класса. """

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

from ..config.models import ConsistencyConfig

__all__ = ["FlipAction", "FlipDecision", "StreamConsistency"]

T = TypeVar("T")


class FlipAction(str, Enum):
    """ Что делать с выбросом. """

    SUSPECT = "suspect"
    AUTO_FIX = "auto_fix"


@dataclass(frozen=True)
class FlipDecision(Generic[T]):
    """ Решение по одному кадру выброса. proposed — предполагаемый верный класс (None — решает человек). """

    item: T
    label: str
    proposed: str | None
    action: FlipAction
    reason: str


@dataclass
class _Stream(Generic[T]):
    stable_label: str | None = None
    stable_count: int = 0
    run_items: list[tuple[T, str]] = field(default_factory=list)
    run_labels: list[str] = field(default_factory=list)
    after_count: int = 0

    @property
    def in_run(self) -> bool:
        return bool(self.run_labels)


class StreamConsistency(Generic[T]):
    """
        Следит за метками по каждому потоку (источник + зона).
        Устойчивый класс A (не меньше context кадров подряд), затем короткий участок других классов
        (не больше max_run кадров, можно вперемешку), затем снова A×context — кадры участка считаются выбросом.
        Решение выдаётся с задержкой: после context кадров класса A за выбросом.
        Классы из review_always никогда не исправляются автоматически.
        Автоисправление — только для пар из trusted_flips (B → A).
    """

    def __init__(self, config: ConsistencyConfig) -> None:
        self._config = config
        self._streams: dict[tuple[str, str], _Stream[T]] = {}

    def reset(self, key: tuple[str, str] | None = None) -> None:
        """ Сбрасывает состояние потока (или всех): нерешённые выбросы забываются. """
        if key is None:
            self._streams.clear()
        else:
            self._streams.pop(key, None)

    def observe(self, key: tuple[str, str], label: str, item: T | None) -> list[FlipDecision[T]]:
        """
            Учитывает очередную метку потока. item — то, что нужно вернуть в решении
            (например, вырез и сведения о кадре); None, если сохранять кадр не нужно.
        """
        stream = self._streams.setdefault(key, _Stream())
        if stream.stable_label is None:
            stream.stable_label, stream.stable_count = label, 1
            return []

        if label == stream.stable_label:
            if not stream.in_run:
                stream.stable_count += 1
                return []
            stream.after_count += 1
            if stream.after_count < self._config.context:
                return []
            decisions = self._decide(stream) if stream.stable_count >= self._config.context else []
            self._become_stable(stream, label, stream.after_count)
            return decisions

        # Кадр не устойчивого класса: начало или продолжение участка-кандидата в выбросы.
        stream.after_count = 0
        stream.run_labels.append(label)
        if item is not None:
            stream.run_items.append((item, label))
        if len(stream.run_labels) > self._config.max_run:
            # Слишком длинно для выброса: устойчивым становится последний класс.
            trailing = 0
            for value in reversed(stream.run_labels):
                if value != label:
                    break
                trailing += 1
            self._become_stable(stream, label, trailing)
        return []

    @staticmethod
    def _become_stable(stream: _Stream[T], label: str, count: int) -> None:
        stream.stable_label = label
        stream.stable_count = count
        stream.run_items = []
        stream.run_labels = []
        stream.after_count = 0

    def _decide(self, stream: _Stream[T]) -> list[FlipDecision[T]]:
        """ Решения по кадрам подтверждённого выброса. """
        assert stream.stable_label is not None
        context = stream.stable_label
        decisions: list[FlipDecision[T]] = []
        for item, flip in stream.run_items:
            review_only = flip in self._config.review_always or context in self._config.review_always
            trusted = not review_only and context in self._config.trusted_flips.get(flip, [])
            proposed = None if flip in self._config.review_always else context
            action = FlipAction.AUTO_FIX if trusted else FlipAction.SUSPECT
            decisions.append(FlipDecision(item, flip, proposed, action, f"{flip} среди {context}"))
        return decisions
