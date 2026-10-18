""" Базовые типы данных: кадр, зона, предсказание. """

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import numpy.typing as npt

__all__ = [
    "AttrValue",
    "BBox",
    "Frame",
    "Image",
    "Point",
    "Prediction",
    "Region",
    "SampleStatus",
    "SaveMode",
    "top_prediction",
]

Image = npt.NDArray[np.uint8]
Point = tuple[float, float]
AttrValue = str | int | float | bool | None


class SampleStatus(str, Enum):
    """ Состояние сохранённого кадра. """

    PENDING = "pending"
    SUSPECT = "suspect"
    VERIFIED = "verified"
    REJECTED = "rejected"
    FAILED = "failed"


class SaveMode(str, Enum):
    """ Как кадр попал в набор. """

    MANUAL = "manual"
    AUTO = "auto"


@dataclass(frozen=True)
class BBox:
    """ Прямоугольник в пикселях: левый верхний и правый нижний углы. """

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if self.x2 < self.x1 or self.y2 < self.y1:
            raise ValueError(f"Invalid bbox: {self}")

    @property
    def width(self) -> float:
        """ Ширина. """
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        """ Высота. """
        return self.y2 - self.y1

    @property
    def center(self) -> Point:
        """ Центр прямоугольника. """
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    def shifted(self, dx: float, dy: float) -> BBox:
        """ Сдвигает прямоугольник. """
        return BBox(self.x1 + dx, self.y1 + dy, self.x2 + dx, self.y2 + dy)

    def clipped(self, width: float, height: float) -> BBox | None:
        """ Обрезает по границам картинки; None, если ничего не осталось. """
        x1, y1 = max(0.0, self.x1), max(0.0, self.y1)
        x2, y2 = min(float(width), self.x2), min(float(height), self.y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return BBox(x1, y1, x2, y2)

    def contains(self, point: Point) -> bool:
        """ Лежит ли точка внутри. """
        return self.x1 <= point[0] <= self.x2 and self.y1 <= point[1] <= self.y2

    def to_list(self) -> list[float]:
        """ Список [x1, y1, x2, y2]. """
        return [self.x1, self.y1, self.x2, self.y2]


@dataclass(frozen=True)
class Region:
    """ Зона кадра для вырезки: многоугольник в координатах кадра и свободные атрибуты. """

    id: str
    polygon: tuple[Point, ...]
    attrs: Mapping[str, AttrValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.polygon) < 3:
            raise ValueError(f"Region {self.id!r} needs at least 3 points")

    @classmethod
    def from_bbox(cls, region_id: str, bbox: BBox, attrs: Mapping[str, AttrValue] | None = None) -> Region:
        """ Создаёт прямоугольную зону. """
        polygon = ((bbox.x1, bbox.y1), (bbox.x2, bbox.y1), (bbox.x2, bbox.y2), (bbox.x1, bbox.y2))
        return cls(region_id, polygon, dict(attrs or {}))

    @property
    def bbox(self) -> BBox:
        """ Описанный прямоугольник. """
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return BBox(min(xs), min(ys), max(xs), max(ys))

    @property
    def is_rectangle(self) -> bool:
        """ Является ли зона прямоугольником со сторонами по осям. """
        if len(self.polygon) != 4:
            return False
        box = self.bbox
        corners = {(box.x1, box.y1), (box.x2, box.y1), (box.x2, box.y2), (box.x1, box.y2)}
        return set(self.polygon) == corners

    def scaled(self, sx: float, sy: float) -> Region:
        """ Масштабирует координаты зоны. """
        return Region(self.id, tuple((x * sx, y * sy) for x, y in self.polygon), self.attrs)


@dataclass(frozen=True)
class Prediction:
    """ Один ответ модели: класс, уверенность и, при наличии, бокс или контур. """

    label: str
    confidence: float
    bbox: BBox | None = None
    polygon: tuple[Point, ...] | None = None
    track_id: int | None = None

    def shifted(self, dx: float, dy: float) -> Prediction:
        """ Сдвигает координаты предсказания. """
        bbox = self.bbox.shifted(dx, dy) if self.bbox else None
        polygon = tuple((x + dx, y + dy) for x, y in self.polygon) if self.polygon else None
        return Prediction(self.label, self.confidence, bbox, polygon, self.track_id)

    def to_dict(self) -> dict[str, object]:
        """ Словарь для хранения в каталоге. """
        data: dict[str, object] = {"label": self.label, "confidence": round(float(self.confidence), 4)}
        if self.bbox:
            data["bbox"] = [round(v, 2) for v in self.bbox.to_list()]
        if self.polygon:
            data["polygon"] = [[round(x, 2), round(y, 2)] for x, y in self.polygon]
        if self.track_id is not None:
            data["track_id"] = self.track_id
        return data


@dataclass
class Frame:
    """ Кадр видео с информацией об источнике. """

    image: Image
    source: str
    index: int
    timestamp: float | None = None
    attrs: dict[str, AttrValue] = field(default_factory=dict)

    @property
    def width(self) -> int:
        """ Ширина кадра. """
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        """ Высота кадра. """
        return int(self.image.shape[0])


def top_prediction(predictions: Sequence[Prediction]) -> Prediction | None:
    """ Самое уверенное предсказание или None. """
    return max(predictions, key=lambda p: p.confidence) if predictions else None
