""" Модели для офлайн-сбора: необязательная часть. """

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..config.models import ModelConfig, TaskType
from ..core.types import BBox, Image, Prediction

__all__ = ["Predictor", "UltralyticsPredictor", "create_predictor"]


class Predictor(Protocol):
    """ Любая модель: картинка → список предсказаний. """

    def __call__(self, image: Image) -> list[Prediction]:
        """ Предсказания для картинки. """
        ...


class UltralyticsPredictor:
    """ Модель YOLO из пакета ultralytics (ставится отдельно). """

    def __init__(self, path: Path, task: TaskType, device: str | None = None, conf: float = 0.05) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as error:
            raise ImportError("Package 'ultralytics' is required to run a model: pip install ultralytics") from error
        self._model: Any = YOLO(str(path))
        self._task = task
        self._device = device
        self._conf = conf

    def __call__(self, image: Image) -> list[Prediction]:
        result = self._model.predict(image, verbose=False, device=self._device, conf=self._conf)[0]
        names: dict[int, str] = result.names
        if self._task == TaskType.CLASSIFY:
            probs = result.probs
            if probs is None:
                return []
            return [Prediction(names[int(probs.top1)], float(probs.top1conf))]
        predictions: list[Prediction] = []
        boxes = result.boxes
        if boxes is None:
            return predictions
        polygons = result.masks.xy if self._task == TaskType.SEGMENT and result.masks is not None else None
        for i, (xyxy, conf, cls) in enumerate(zip(boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist(),
                                                  strict=True)):
            polygon = tuple((float(x), float(y)) for x, y in polygons[i]) if polygons is not None else None
            predictions.append(Prediction(names[int(cls)], float(conf), BBox(*map(float, xyxy)), polygon))
        return predictions


def create_predictor(config: ModelConfig, task: TaskType) -> Predictor | None:
    """ Модель по настройкам или None, если путь не задан. """
    if config.path is None:
        return None
    return UltralyticsPredictor(config.path, task, config.device, config.conf)
