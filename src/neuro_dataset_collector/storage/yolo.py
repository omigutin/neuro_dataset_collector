""" Файлы разметки в формате YOLO. """

from __future__ import annotations

import logging
from collections.abc import Sequence

from ..config.models import TaskType
from ..core.types import Prediction

__all__ = ["yolo_lines"]

_log = logging.getLogger("neuro_dataset_collector")


def yolo_lines(
    predictions: Sequence[Prediction], classes: Sequence[str], width: int, height: int, task: TaskType
) -> str:
    """
        Строки разметки YOLO в координатах картинки width×height.
        Детекция: «класс cx cy w h»; сегментация: «класс x1 y1 x2 y2 ...». Всё в долях от 0 до 1.
        Предсказания с неизвестным классом пропускаются.
    """
    lines: list[str] = []
    for prediction in predictions:
        if prediction.label not in classes:
            _log.warning("Prediction label %r is not in classes, skipped in YOLO labels", prediction.label)
            continue
        index = classes.index(prediction.label)
        if task == TaskType.SEGMENT:
            points = prediction.polygon
            if points is None and prediction.bbox is not None:
                b = prediction.bbox
                points = ((b.x1, b.y1), (b.x2, b.y1), (b.x2, b.y2), (b.x1, b.y2))
            if not points or len(points) < 3:
                continue
            coords = " ".join(f"{x / width:.6f} {y / height:.6f}" for x, y in points)
            lines.append(f"{index} {coords}")
        elif prediction.bbox is not None:
            b = prediction.bbox
            cx, cy = (b.x1 + b.x2) / 2 / width, (b.y1 + b.y2) / 2 / height
            lines.append(f"{index} {cx:.6f} {cy:.6f} {b.width / width:.6f} {b.height / height:.6f}")
    return "\n".join(lines) + ("\n" if lines else "")
