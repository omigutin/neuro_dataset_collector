""" История нарезки для веб-инструмента: отказы текущего запуска с картинками и записи каталога по видео. """

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from .._cv import get_cv2
from ..catalog.catalog import SampleRecord
from ..core.results import SaveResult

__all__ = ["RejectionLog", "sample_item"]

# Сколько отказов с картинками держать в памяти (только текущий запуск программы).
_MAX_REJECTIONS = 300
_PREVIEW_WIDTH = 480


@dataclass(frozen=True)
class _Rejection:
    """ Отказ при ручном сохранении: что пытались сохранить и почему не вышло. """

    id: int
    video: str
    result: SaveResult
    time: float
    jpeg: bytes


class RejectionLog:
    """ Отказы текущего запуска вместе с картинками вырезов; потокобезопасен. """

    def __init__(self, size: int = _MAX_REJECTIONS) -> None:
        self._items: deque[_Rejection] = deque(maxlen=size)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def add(self, video: str, result: SaveResult, image: np.ndarray) -> None:
        """ Запоминает отказ и уменьшенную картинку выреза. """
        cv2 = get_cv2()
        if image.shape[1] > _PREVIEW_WIDTH:
            scale = _PREVIEW_WIDTH / image.shape[1]
            size = (_PREVIEW_WIDTH, max(1, int(image.shape[0] * scale)))
            image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise OSError("Failed to encode rejected crop")
        with self._lock:
            self._items.append(_Rejection(next(self._ids), video, result, time.time(), bytes(buffer.tobytes())))

    def image(self, rejection_id: int) -> bytes:
        """ Картинка выреза, который не сохранён. """
        with self._lock:
            for item in self._items:
                if item.id == rejection_id:
                    return item.jpeg
        raise FileNotFoundError(f"Rejected crop {rejection_id} is no longer kept")

    def count(self, video: str) -> int:
        """ Сколько отказов у видео. """
        with self._lock:
            return sum(1 for item in self._items if item.video == video)

    def items(self, video: str) -> list[dict[str, Any]]:
        """ Отказы видео для интерфейса, новые первыми. """
        with self._lock:
            found = [item for item in self._items if item.video == video]
        return [{
            "kind": "rejected",
            "id": item.id,
            "time": item.time,
            "label": item.result.label,
            "region": item.result.region_id,
            "frame": item.result.frame_index,
            "reason": item.result.reason.value if item.result.reason else None,
            "message": item.result.message,
            "similar_to": str(item.result.similar_to) if item.result.similar_to else None,
            "image": f"api/rejected/{item.id}.jpg",
        } for item in reversed(found)]


def sample_item(record: SampleRecord, path: str) -> dict[str, Any]:
    """ Сохранённый кадр для истории. """
    try:
        created = datetime.fromisoformat(record.created_at).timestamp() if record.created_at else 0.0
    except ValueError:
        created = 0.0
    return {
        "kind": "saved",
        "id": record.id,
        "time": created,
        "label": record.label,
        "status": record.status.value,
        "mode": record.mode.value,
        "region": record.region_id,
        "frame": record.frame_index,
        "path": path,
        "model_label": record.model_label,
        "confidence": record.confidence,
    }
