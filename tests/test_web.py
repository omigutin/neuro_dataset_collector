from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from conftest import random_image
from neuro_dataset_collector.config import CollectorConfig, load_config, save_config
from neuro_dataset_collector.web.app import create_app
from neuro_dataset_collector.web.session import WebSession
from neuro_dataset_collector.web.state import load_state


def _video(path: Path, frames: int = 12) -> Path:
    cv2 = pytest.importorskip("cv2")
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
    _video(tmp_path / "ml_cam25_0101.avi")
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
def client(workspace: Path) -> Iterator[TestClient]:
    session = WebSession.open(workspace / "ndc.yaml")
    with TestClient(create_app(session)) as test_client:
        yield test_client


def test_index_and_config(client: TestClient, workspace: Path) -> None:
    assert "Сборщик датасета" in client.get("/").text
    data = client.get("/api/config").json()
    assert data["config"]["classes"] == ["EMPTY", "FULL"] and data["path"] == str(workspace / "ndc.yaml")
    assert client.get("/api/video").json()["video"] is None


def test_bad_config_returns_errors(client: TestClient) -> None:
    config = client.get("/api/config").json()["config"]
    config["classes"] = ["ПОЛНЫЙ"]
    response = client.put("/api/config", json=config)
    assert response.status_code == 422
    assert "Invalid class name" in " ".join(response.json()["detail"]["errors"])


def test_config_saved_to_file(client: TestClient, workspace: Path) -> None:
    config = client.get("/api/config").json()["config"]
    config["classes"] = ["EMPTY", "FULL", "UNKNOWN"]
    assert client.put("/api/config", json=config).status_code == 200
    assert load_config(workspace / "ndc.yaml").classes == ["EMPTY", "FULL", "UNKNOWN"]


def test_open_video_suggests_regions_and_saves_next_to_video(client: TestClient, workspace: Path) -> None:
    video = workspace / "ml_cam25_0101.avi"
    info = client.post("/api/video/open", json={"path": str(video)}).json()
    assert info["suggested_regions"] == str(workspace / "jsons" / "norebo_cam25.json")
    assert [r["id"] for r in info["regions"]] == ["1", "2"]
    assert info["dataset_dir"] == str(workspace / "ml_cam25_0101_dataset")
    frame = client.get("/api/video/frame/3")
    assert frame.headers["content-type"] == "image/jpeg"

    saved = client.post("/api/save", json={"frame": 3, "label": "FULL"}).json()["results"]
    assert [r["ok"] for r in saved] == [True, True]
    assert Path(saved[0]["path"]).parent == workspace / "ml_cam25_0101_dataset" / "FULL"
    [again] = client.post("/api/save", json={"frame": 3, "label": "FULL", "regions": ["1"]}).json()["results"]
    assert not again["ok"] and again["similar_to"] == saved[0]["path"]
    assert client.get("/api/file", params={"path": saved[0]["path"]}).status_code == 200
    assert client.get("/api/file", params={"path": str(workspace / "ndc.yaml")}).status_code == 404

    dataset = workspace / "ml_cam25_0101_dataset"
    (dataset / "EMPTY").mkdir()
    name = Path(saved[1]["path"]).name
    shutil.move(dataset / "FULL" / name, dataset / "EMPTY" / name)
    sync = client.post("/api/sync").json()
    assert sync["verified"] == 2 and sync["relabeled"] == 1
    assert client.post("/api/export", json={"out_dir": str(workspace / "out")}).json()["exported"] == 2


def test_session_restored_on_next_start(workspace: Path) -> None:
    video = workspace / "ml_cam25_0101.avi"
    session = WebSession.open(workspace / "ndc.yaml")
    with TestClient(create_app(session)) as client:
        client.post("/api/video/open", json={"path": str(video)})
        client.post("/api/video/position", json={"frame": 7})
    assert load_state().video == str(video)
    restored = WebSession.open()
    assert restored.config_path == workspace / "ndc.yaml"
    assert restored.video == video and restored.frame == 7
    restored.close()


def test_first_start_creates_default_config(home: Path) -> None:
    session = WebSession.open()
    assert session.config_path == home / "settings.yaml" and session.config_path.exists()
    assert session.config.catalog.next_to_video is True
    session.close()


def test_regions_file_and_full_frame(client: TestClient, workspace: Path) -> None:
    client.post("/api/video/open", json={"path": str(workspace / "ml_cam25_0101.avi")})
    info = client.post("/api/video/regions", json={"path": None}).json()
    assert info["regions"] == [] and info["regions_source"] == "none"
    info = client.post("/api/video/regions", json={"path": str(workspace / "jsons" / "norebo_cam25.json")}).json()
    assert len(info["regions"]) == 2
    assert client.post("/api/video/regions", json={"path": str(workspace / "nope.json")}).status_code == 404


def test_auto_run(client: TestClient, workspace: Path) -> None:
    assert client.post("/api/auto/start").status_code == 404  # видео не открыто
    client.post("/api/video/open", json={"path": str(workspace / "ml_cam25_0101.avi")})
    client.post("/api/auto/start")
    client.post("/api/auto/stop")
    status = client.get("/api/auto/status").json()
    assert status["running"] is False and status["error"] == ""


def test_browse_and_pick_fallback(client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = client.get("/api/browse", params={"path": str(workspace), "kind": "video"}).json()
    assert data["files"] == ["ml_cam25_0101.avi"]
    from neuro_dataset_collector.web import app as app_module

    monkeypatch.setattr(app_module, "pick_path", lambda *args: None)
    assert client.post("/api/pick", json={"kind": "file", "filter": "video"}).json() == {"path": "", "available": False}
    monkeypatch.setattr(app_module, "pick_path", lambda *args: "C:/video.avi")
    assert client.post("/api/pick", json={"kind": "file"}).json() == {"path": "C:/video.avi", "available": True}
