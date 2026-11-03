""" Общие заготовки для тестов. """

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from neuro_dataset_collector.config import CollectorConfig, save_config
from neuro_dataset_collector.core import Frame


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


def write_video(path: Path, frames: int = 12) -> Path:
    """ Короткое видео 64×48 со случайными кадрами. """
    cv2 = pytest.importorskip("cv2")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is required to read videos")
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
    for i in range(frames):
        writer.write(random_image(i))
    writer.release()
    return path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """ Папка программы (прошлый сеанс) — во временной папке теста. """
    path = tmp_path / "app_home"
    monkeypatch.setenv("NDC_HOME", str(path))
    return path


@pytest.fixture
def workspace(tmp_path: Path, home: Path) -> Path:
    """ Видео камеры 25, папка с разметкой и файл настроек. """
    write_video(tmp_path / "ml_cam25_0101.avi")
    marks = tmp_path / "jsons"
    marks.mkdir()
    zones = {"imageWidth": 64, "imageHeight": 48, "shapes": [
        {"label": "crop", "points": [[0, 0], [32, 24]], "shape_type": "rectangle", "wz": 1},
        {"label": "crop", "points": [[32, 24], [64, 48]], "shape_type": "rectangle", "wz": 2},
    ]}
    (marks / "norebo_cam25.json").write_text(json.dumps(zones), encoding="utf-8")
    config = CollectorConfig.model_validate({
        "classes": ["EMPTY", "FULL"],
        "manual": {"keys": {"1": "EMPTY", "2": "FULL"}},
        "catalog": {"next_to_video": True},
        "regions": {"labels": ["CROP"], "id_field": "wz", "search_dir": str(marks)},
        "source": {"step": 1},
        "auto": {"triggers": [{"type": "always"}], "limits": [{"type": "dedup"}]},
        "runtime": {"async_write": False},
        "storage": {"min_free_gb": 0},
    })
    save_config(config, tmp_path / "ndc.yaml")
    return tmp_path


@pytest.fixture
def client(workspace: Path) -> Iterator[Any]:
    """ Веб-приложение для настроек workspace. """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from neuro_dataset_collector.web.app import create_app
    from neuro_dataset_collector.web.session import WebSession

    session = WebSession.open(workspace / "ndc.yaml")
    with TestClient(create_app(session)) as test_client:
        yield test_client
