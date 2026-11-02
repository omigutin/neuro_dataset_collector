""" Данные сборщика для редактора разметки neuro_markup_lab. """

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from neuro_markup_lab.lab import MarkupLabHost

from ..regions.markup import production_key

__all__ = ["CollectorMarkupHost"]


class CollectorMarkupHost(MarkupLabHost):
    """ Боевая разметка видео подбирается так же, как при нарезке: по папке разметки из настроек сборщика. """

    def __init__(self, search_dir: Callable[[], Path | None]) -> None:
        # Папка разметки читается при каждом запросе: её меняют в настройках, не перезапуская сервер
        self._search_dir = search_dir

    def markup_key(self, video: Path) -> str | None:
        return production_key(video, self._search_dir())

    def factories(self) -> Sequence[str]:
        return ()
