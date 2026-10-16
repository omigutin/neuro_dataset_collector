""" Общие заготовки для тестов. """

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from neuro_frame_collector.config import CollectorConfig
from neuro_frame_collector.core import Frame


@pytest.fixture
def make_config(tmp_path: Path) -> Callable[..., CollectorConfig]:
    """ Настройки с корнем во временной папке и синхронной записью. """
    def factory(**overrides: object) -> CollectorConfig:
        data: dict[str, object] = {
            "classes": ["EMPTY", "FULL", "UNKNOWN"],
            "catalog": {"root": str(tmp_path / "data"), "collection": "test/line"},
            "runtime": {"async_write": False},
            "storage": {"min_free_gb": 0},
        }
        data.update(overrides)
        return CollectorConfig.model_validate(data)
    return factory


def random_image(seed: int, width: int = 64, height: int = 48) -> np.ndarray:
    """ Случайная картинка: разные seed дают заметно разные картинки. """
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, size=(6, 8, 3), dtype=np.uint8)
    return np.kron(small, np.ones((height // 6, width // 8, 1), dtype=np.uint8))


def make_frame(seed: int, index: int | None = None, source: str = "video1", fps: float = 10.0) -> Frame:
    """ Кадр со случайной картинкой. """
    idx = seed if index is None else index
    return Frame(image=random_image(seed), source=source, index=idx, timestamp=idx / fps)
