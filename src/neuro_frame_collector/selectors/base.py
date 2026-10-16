""" Кандидат на сохранение и общие типы отбора. """

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from ..core.results import RejectReason
from ..core.types import Image, Prediction, top_prediction
from ..imaging.hashing import dhash, small_gray

__all__ = ["Candidate", "Rejection"]


@dataclass
class Candidate:
    """ Вырез, который предлагается сохранить, со всеми сведениями для правил отбора. """

    image: Image
    source: str
    frame_index: int
    timestamp: float | None
    region_id: str | None
    label: str | None
    predictions: list[Prediction] = field(default_factory=list)
    _hash: int | None = field(default=None, repr=False)
    _small: npt.NDArray[np.float32] | None = field(default=None, repr=False)

    @property
    def confidence(self) -> float | None:
        """ Уверенность самого уверенного предсказания. """
        top = top_prediction(self.predictions)
        return top.confidence if top else None

    @property
    def hash(self) -> int:
        """ Отпечаток картинки (считается один раз). """
        if self._hash is None:
            self._hash = dhash(self.image)
        return self._hash

    @property
    def small(self) -> npt.NDArray[np.float32]:
        """ Уменьшенная серая копия (считается один раз). """
        if self._small is None:
            self._small = small_gray(self.image)
        return self._small

    @property
    def stream_key(self) -> tuple[str, str]:
        """ Поток — это пара «источник + зона». """
        return self.source, self.region_id or ""


@dataclass(frozen=True)
class Rejection:
    """ Почему кандидат отклонён. """

    reason: RejectReason
    message: str
    similar_to: Path | None = None
    distance: int | None = None
