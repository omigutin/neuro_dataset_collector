""" Вырезка зоны из кадра и пересчёт координат предсказаний. """

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .._cv import get_cv2
from ..core.types import BBox, Image, Prediction, Region

__all__ = ["Crop", "crop_region", "predictions_to_crop"]


@dataclass(frozen=True)
class Crop:
    """ Вырезанная картинка и её смещение относительно кадра. """

    image: Image
    offset_x: int
    offset_y: int

    @property
    def width(self) -> int:
        """ Ширина выреза. """
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        """ Высота выреза. """
        return int(self.image.shape[0])


def crop_region(image: Image, region: Region | None, mask_outside: bool = False, padding: int = 0) -> Crop | None:
    """
        Вырезает описанный прямоугольник зоны (с отступом) из кадра.
        Без зоны возвращает весь кадр. Если зона вне кадра — None.
    """
    if region is None:
        return Crop(image, 0, 0)
    height, width = image.shape[:2]
    box = region.bbox
    x1 = max(0, int(np.floor(box.x1)) - padding)
    y1 = max(0, int(np.floor(box.y1)) - padding)
    x2 = min(width, int(np.ceil(box.x2)) + padding)
    y2 = min(height, int(np.ceil(box.y2)) + padding)
    if x2 <= x1 or y2 <= y1:
        return None
    # Копия нужна: вырез уходит в фоновую запись, а исходный кадр хозяин может переиспользовать.
    cut = image[y1:y2, x1:x2].copy()
    if mask_outside and not region.is_rectangle:
        cv2 = get_cv2()
        mask = np.zeros(cut.shape[:2], dtype=np.uint8)
        points = np.array([[x - x1, y - y1] for x, y in region.polygon], dtype=np.int32)
        cv2.fillPoly(mask, [points], 255)
        cut[mask == 0] = 0
    return Crop(cut, x1, y1)


def predictions_to_crop(predictions: Sequence[Prediction], crop: Crop) -> list[Prediction]:
    """ Переводит предсказания из координат кадра в координаты выреза, отбрасывая ушедшие за край. """
    result: list[Prediction] = []
    for prediction in predictions:
        shifted = prediction.shifted(-crop.offset_x, -crop.offset_y)
        bbox: BBox | None = None
        if shifted.bbox is not None:
            bbox = shifted.bbox.clipped(crop.width, crop.height)
            if bbox is None:
                continue
        polygon = shifted.polygon
        if polygon is not None:
            polygon = tuple(
                (min(max(x, 0.0), float(crop.width)), min(max(y, 0.0), float(crop.height))) for x, y in polygon
            )
        result.append(Prediction(shifted.label, shifted.confidence, bbox, polygon, shifted.track_id))
    return result
