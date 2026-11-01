from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from neuro_dataset_collector.core import Image, Prediction
from neuro_dataset_collector.journal import read_journal, read_journal_meta


def _predictor(image: Image) -> list[Prediction]:
    """ Светлая картинка — FULL, тёмная — EMPTY. """
    mean = float(image.mean())
    return [Prediction("FULL" if mean > 127 else "EMPTY", 0.55)]


@pytest.fixture
def with_model(client: Any, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """ В настройках задана модель; вместо ultralytics — простая функция. """
    from neuro_dataset_collector.web import model_view

    loads: list[str] = []

    def fake_create(config: Any, task: Any) -> Any:
        loads.append(str(config.path))
        return _predictor

    monkeypatch.setattr(model_view, "create_predictor", fake_create)
    config = client.get("/api/config").json()["config"]
    config["model"]["path"] = str(workspace / "model.pt")
    assert client.put("/api/config", json=config).status_code == 200
    client.post("/api/video/open", json={"path": str(workspace / "ml_cam25_0101.avi")})
    client.loads = loads
    return client


def test_answers_frame_overlay_and_save_with_model(with_model: Any) -> None:
    client = with_model
    answers = client.get("/api/model/answers/2").json()["answers"]
    assert [a["zone"] for a in answers] == ["1", "2"] and all(a["label"] in {"FULL", "EMPTY"} for a in answers)
    image = client.get("/api/video/frame/2", params={"model": 1})
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert len(client.loads) == 1   # модель загружается один раз

    saved = client.post("/api/save", json={"frame": 2, "label": "EMPTY", "model": True}).json()["results"]
    assert all(r["ok"] for r in saved)
    video = client.get("/api/video").json()["video"]
    items = client.get("/api/history", params={"video": video}).json()["items"]
    by_zone = {item["region"]: item for item in items}
    assert {by_zone[a["zone"]]["model_label"] for a in answers} == {a["label"] for a in answers}
    # «Без класса» с включённой моделью — всё равно без класса: ответ модели только записывается.
    unsorted = client.post("/api/save", json={"frame": 6, "label": None, "model": True}).json()["results"]
    assert all(r["ok"] and "_unsorted" in r["path"] for r in unsorted)


def test_without_model_answers_are_refused(client: Any, workspace: Path) -> None:
    client.post("/api/video/open", json={"path": str(workspace / "ml_cam25_0101.avi")})
    assert client.get("/api/model/answers/0").status_code == 400
    assert client.post("/api/auto/start", json={"mode": "model"}).status_code == 400


def test_model_run_writes_journal_and_saves(with_model: Any) -> None:
    client = with_model
    config = client.get("/api/config").json()["config"]
    config["journal"].update({"save": True, "conf_below": 0.6, "flips": False})
    config["manual"]["on_duplicate"] = "allow"
    client.put("/api/config", json=config)
    assert client.post("/api/auto/start", json={"mode": "model"}).status_code == 200
    for _ in range(100):
        status = client.get("/api/auto/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)
    assert status["error"] == "" and status["mode"] == "model" and status["journal"]
    meta = read_journal_meta(Path(status["journal"]))
    assert meta.source == "collector" and meta.model == "model.pt" and [z.id for z in meta.zones] == ["1", "2"]
    rows = list(read_journal(Path(status["journal"])))
    assert len(rows) == 2 * status["frames"] and status["saved"] == len(rows)
    assert Path(status["journal"]).parent.name == "_journal"


def test_dataset_check_with_model(with_model: Any) -> None:
    client = with_model
    for frame in (1, 3, 5):
        client.post("/api/save", json={"frame": frame, "label": "EMPTY", "regions": ["1"]})
    assert client.post("/api/check/start", json={"scope": "video", "min_conf": 0.9}).status_code == 200
    for _ in range(100):
        status = client.get("/api/check/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)
    assert status["error"] == "" and status["total"] == 3
    # Модель уверена на 0.55 — меньше 0.9: все три кадра в списке на проверку.
    assert len(status["disagreements"]) == 3 and "Проверено кадров: 3" in status["summary"]
    item = status["disagreements"][0]
    assert client.get("/api/file", params={"path": item["path"], "video": item["video"]}).status_code == 200
