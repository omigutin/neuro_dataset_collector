from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from conftest import random_image
from neuro_frame_collector.config import CollectorConfig, save_config
from neuro_frame_collector.web.app import create_app
from neuro_frame_collector.web.session import WebSession


def _video(path: Path, frames: int = 12) -> Path:
    cv2 = pytest.importorskip("cv2")
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
    for i in range(frames):
        writer.write(random_image(i))
    writer.release()
    return path


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    video = _video(tmp_path / "cam1.avi")
    zones = {"imageWidth": 64, "imageHeight": 48, "shapes": [
        {"label": "crop", "points": [[0, 0], [32, 24]], "shape_type": "rectangle", "wz": 1},
        {"label": "crop", "points": [[32, 24], [64, 48]], "shape_type": "rectangle", "wz": 2},
    ]}
    (tmp_path / "zones.json").write_text(json.dumps(zones), encoding="utf-8")
    config = CollectorConfig.model_validate({
        "classes": ["EMPTY", "FULL"],
        "manual": {"keys": {"1": "EMPTY", "2": "FULL"}},
        "catalog": {"root": str(tmp_path / "data"), "collection": "web"},
        "regions": {"source": "file", "file": str(tmp_path / "zones.json"), "id_field": "wz"},
        "source": {"path": str(video), "step": 1},
        "auto": {"triggers": [{"type": "always"}], "limits": [{"type": "dedup"}]},
        "runtime": {"async_write": False},
        "storage": {"min_free_gb": 0},
    })
    path = tmp_path / "nfc.yaml"
    save_config(config, path)
    session = WebSession.open(path)
    with TestClient(create_app(session)) as test_client:
        yield test_client


def test_index_and_config(client: TestClient) -> None:
    assert "Сборщик кадров" in client.get("/").text
    data = client.get("/api/config").json()
    assert data["config"]["classes"] == ["EMPTY", "FULL"] and "classes:" in data["yaml"]


def test_bad_config_returns_errors(client: TestClient) -> None:
    config = client.get("/api/config").json()["config"]
    config["manual"]["keys"] = {"1": "NOPE"}
    response = client.put("/api/config", json=config)
    assert response.status_code == 422
    assert "Unknown classes" in " ".join(response.json()["detail"]["errors"])


def test_config_yaml_roundtrip(client: TestClient) -> None:
    text = client.get("/api/config").json()["yaml"].replace("collection: web", "collection: web2")
    data = client.put("/api/config/yaml", json={"text": text}).json()
    assert data["config"]["catalog"]["collection"] == "web2"


def test_slicing_flow(client: TestClient, tmp_path: Path) -> None:
    assert client.post("/api/source", json={"path": None}).json()["videos"] == ["cam1.avi"]
    info = client.get("/api/video/0").json()
    assert [r["id"] for r in info["regions"]] == ["1", "2"]
    frame = client.get("/api/video/0/frame/3")
    assert frame.headers["content-type"] == "image/jpeg" and len(frame.content) > 100
    saved = client.post("/api/save", json={"video": 0, "frame": 3, "label": "FULL"}).json()["results"]
    assert [r["ok"] for r in saved] == [True, True]
    again = client.post("/api/save", json={"video": 0, "frame": 3, "label": "FULL", "regions": ["1"]}).json()
    [rejected] = again["results"]
    assert not rejected["ok"] and rejected["similar_to"] == saved[0]["path"]
    assert client.get("/api/file", params={"path": saved[0]["path"]}).status_code == 200
    assert client.get("/api/file", params={"path": "../nfc.yaml"}).status_code == 404
    assert len(client.get("/api/history").json()["items"]) == 3

    data_dir = tmp_path / "data" / "web"
    name = Path(saved[1]["path"]).name
    (data_dir / "EMPTY").mkdir()
    shutil.move(data_dir / "FULL" / name, data_dir / "EMPTY" / name)
    sync = client.post("/api/sync").json()
    assert sync["verified"] == 2 and sync["relabeled"] == 1
    exported = client.post("/api/export", json={"out_dir": str(tmp_path / "out")}).json()
    assert exported["exported"] == 2
    stats = client.get("/api/stats").json()
    assert stats["statuses"] == {"verified": 2}


def test_auto_run(client: TestClient) -> None:
    client.post("/api/source", json={"path": None})
    client.post("/api/auto/start")
    client.post("/api/auto/stop")
    status = client.get("/api/auto/status").json()
    assert status["running"] is False and status["error"] == ""


def test_browse(client: TestClient, tmp_path: Path) -> None:
    data = client.get("/api/browse", params={"path": str(tmp_path), "kind": "video"}).json()
    assert data["files"] == ["cam1.avi"] and data["parent"] == str(tmp_path.parent)
    assert client.get("/api/browse").json()["dirs"]
