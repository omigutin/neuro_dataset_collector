""" Модель в веб-интерфейсе: загружается один раз, отвечает по зонам кадра и рисует ответы на кадре. """

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

import numpy as np

from .._cv import get_cv2
from ..config.models import CollectorConfig
from ..core.types import Frame, Prediction, Region
from ..pipeline.model_run import ZoneAnswer, predict_zones
from ..predictors.base import Predictor, create_predictor

__all__ = ["ModelView", "class_color"]

# Цвета классов (BGR): по порядку класса в настройках; без ответа — серый.
_PALETTE: tuple[tuple[int, int, int], ...] = (
    (80, 200, 80), (60, 120, 255), (255, 160, 40), (200, 80, 220), (40, 220, 220),
    (255, 90, 160), (150, 150, 255), (90, 200, 160),
)
_NO_ANSWER = (150, 150, 150)


def class_color(label: str | None, classes: Sequence[str]) -> tuple[int, int, int]:
    """ Цвет класса для рисования на кадре. """
    if not label:
        return _NO_ANSWER
    index = list(classes).index(label) if label in classes else sum(map(ord, label))
    return _PALETTE[index % len(_PALETTE)]


class ModelView:
    """
        Модель из настроек для показа ответов на кадре. Загружается при первом обращении и заново —
        только если в настройках сменилась модель. Последние ответы запоминаются, чтобы показ кадра
        и список ответов не запускали модель дважды.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple[str, str | None, float, str] | None = None
        self._predictor: Predictor | None = None
        self._cached: tuple[tuple[str, int, str], list[ZoneAnswer]] | None = None

    def predictor(self, config: CollectorConfig) -> Predictor:
        """ Модель по настройкам; ошибка, если модель не задана. """
        if config.model.path is None:
            raise ValueError("Model is not set: choose a model file in settings")
        key = (str(config.model.path), config.model.device, config.model.conf, config.task.value)
        with self._lock:
            if self._predictor is None or self._key != key:
                self._cached = None
                self._predictor = create_predictor(config.model, config.task)
                self._key = key
            if self._predictor is None:
                raise ValueError("Model is not set: choose a model file in settings")
            return self._predictor

    def answers(self, config: CollectorConfig, video: str, frame: Frame, regions: Sequence[Region]) -> list[ZoneAnswer]:
        """ Ответы модели по зонам кадра (повторный запрос того же кадра — из памяти). """
        predictor = self.predictor(config)
        zones_key = ",".join(region.id for region in regions)
        key = (video, frame.index, zones_key)
        cached = self._cached
        if cached is not None and cached[0] == key:
            return cached[1]
        result = predict_zones(frame.image, regions, predictor, mask_outside=config.regions.mask_outside,
                               padding=config.regions.padding, space=config.predictions_space)
        self._cached = (key, result)
        return result

    @staticmethod
    def predictions_by_zone(answers: Sequence[ZoneAnswer]) -> dict[str, list[Prediction]]:
        """ Предсказания для сохранения кадра: номер зоны → список. """
        return {answer.zone: list(answer.predictions) for answer in answers}

    @staticmethod
    def to_dict(answers: Sequence[ZoneAnswer]) -> list[dict[str, Any]]:
        """ Ответы для передачи в браузер. """
        return [{"zone": a.zone, "label": a.label, "conf": round(a.conf, 4) if a.conf is not None else None}
                for a in answers]

    @staticmethod
    def draw(image: np.ndarray, regions: Sequence[Region], answers: Sequence[ZoneAnswer],
             classes: Sequence[str]) -> None:
        """ Рисует ответы модели: зона в цвете класса и подпись «класс уверенность». """
        cv2 = get_cv2()
        by_zone = {answer.zone: answer for answer in answers}
        height, width = image.shape[:2]
        targets: list[tuple[Region | None, ZoneAnswer]] = [(r, by_zone[r.id]) for r in regions if r.id in by_zone]
        if not regions and answers:
            targets = [(None, answers[0])]
        scale = max(0.5, min(1.2, width / 1600))
        for region, answer in targets:
            color = class_color(answer.label, classes)
            if region is not None:
                points = np.array(region.polygon, dtype=np.int32)
                overlay = image.copy()
                cv2.fillPoly(overlay, [points], color)
                cv2.addWeighted(overlay, 0.18, image, 0.82, 0, dst=image)
                cv2.polylines(image, [points], True, color, 3)
                x, y = int(region.bbox.x1), int(region.bbox.y2)
            else:
                cv2.rectangle(image, (2, 2), (width - 3, height - 3), color, 4)
                x, y = 8, height - 8
            text = f"{answer.label or '-'} {answer.conf:.2f}" if answer.conf is not None else (answer.label or "-")
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
            y = min(max(th + 6, y - 6), height - 4)
            cv2.rectangle(image, (x, y - th - 6), (x + tw + 8, y + 4), color, -1)
            cv2.putText(image, text, (x + 4, y - 2), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 2)

    def close(self) -> None:
        """ Освобождает модель. """
        with self._lock:
            self._predictor = None
            self._key = None
            self._cached = None
