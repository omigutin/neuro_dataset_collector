from __future__ import annotations

import json
import shutil
import time
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


def test_edit_regions_saved_next_to_video_and_used_first(client: TestClient, workspace: Path) -> None:
    video = workspace / "ml_cam25_0101.avi"
    original = (workspace / "jsons" / "norebo_cam25.json").read_text(encoding="utf-8")
    info = client.post("/api/video/open", json={"path": str(video)}).json()
    assert info["regions_origin"] == "camera" and info["regions_editable"] is True
    assert [r["shape"] for r in info["regions"]] == ["rectangle", "rectangle"]
    box = [[2, 2], [30, 2], [30, 20], [2, 20]]
    edited = client.post("/api/video/regions/edit", json={"regions": {"1": box}}).json()
    sidecar = workspace / "ml_cam25_0101.json"
    assert edited["regions_file"] == str(sidecar) and edited["regions_origin"] == "video"
    assert edited["regions"][0]["polygon"][0] == [2.0, 2.0]
    assert json.loads(sidecar.read_text(encoding="utf-8"))["shapes"][0]["points"] == [[2.0, 2.0], [30.0, 20.0]]
    assert (workspace / "jsons" / "norebo_cam25.json").read_text(encoding="utf-8") == original
    assert client.post("/api/video/regions/edit", json={"regions": {"1": [[5, 5], [5, 5]]}}).status_code == 400
    # При следующем открытии берётся json рядом с видео.
    client.post("/api/video/regions", json={"path": None})
    assert client.post("/api/video/open", json={"path": str(video)}).json()["regions_file"] == str(sidecar)
    # Сохранить в файл камеры (перезаписать).
    target = client.get("/api/video/regions/save-target").json()["path"]
    assert target == str(workspace / "jsons" / "norebo_cam25.json")
    assert client.post("/api/video/regions/save-as", json={"path": target}).status_code == 200
    assert json.loads(Path(target).read_text(encoding="utf-8"))["shapes"][0]["points"] == [[2.0, 2.0], [30.0, 20.0]]
    assert client.post("/api/video/regions/save-as", json={"path": str(workspace / "x.txt")}).status_code == 400


def test_frame_without_zones(client: TestClient, workspace: Path) -> None:
    client.post("/api/video/open", json={"path": str(workspace / "ml_cam25_0101.avi")})
    with_zones = client.get("/api/video/frame/2").content
    without = client.get("/api/video/frame/2", params={"zones": False}).content
    assert with_zones != without


def test_playlist_and_auto_for_all_videos(client: TestClient, workspace: Path) -> None:
    folder = workspace / "videos"
    (folder / "day2").mkdir(parents=True)
    _video(folder / "ml_cam25_a.avi", frames=4)
    _video(folder / "day2" / "ml_cam25_b.avi", frames=4)
    (folder / "ml_cam25_a_dataset").mkdir()
    _video(folder / "ml_cam25_a_dataset" / "copy.avi", frames=2)   # папки датасетов пропускаются
    data = client.post("/api/playlist", json={"path": str(folder)}).json()
    assert [item["name"] for item in data["items"]] == [str(Path("day2") / "ml_cam25_b.avi"), "ml_cam25_a.avi"]
    assert all(item["saved"] == 0 and item["regions"] == "camera" for item in data["items"])
    assert client.post("/api/playlist", json={"path": str(folder / "nope")}).status_code == 404

    client.post("/api/auto/start", json={"scope": "playlist"})
    for _ in range(100):
        status = client.get("/api/auto/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)
    assert status["error"] == "" and status["total"] == 2
    counts = {item["name"]: item["saved"] for item in client.get("/api/playlist").json()["items"]}
    assert all(count and count > 0 for count in counts.values())
    assert load_state().playlist_dir == str(folder.resolve())


def test_position_remembered_per_video(client: TestClient, workspace: Path) -> None:
    first, second = workspace / "ml_cam25_0101.avi", _video(workspace / "ml_cam25_0202.avi")
    client.post("/api/video/open", json={"path": str(first)})
    client.post("/api/video/position", json={"frame": 5})
    client.post("/api/video/open", json={"path": str(second)})
    assert client.post("/api/video/open", json={"path": str(first)}).json()["frame"] == 5


def test_pick_path_reads_utf8_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from neuro_dataset_collector.web import picker

    # Окно выбора возвращает путь с русскими буквами (в Windows консоль не в UTF-8).
    script = "import sys; sys.stdout.buffer.write('W:/Треска стейк/видео.avi'.encode('utf-8'))"
    monkeypatch.setattr(picker, "_SCRIPT", script)
    assert picker.pick_path("file", "video") == "W:/Треска стейк/видео.avi"


def test_single_video_fills_playlist_with_its_folder(client: TestClient, workspace: Path) -> None:
    video = workspace / "ml_cam25_0101.avi"
    info = client.post("/api/video/open", json={"path": str(video)}).json()
    assert info["playlist_changed"] is True
    data = client.get("/api/playlist").json()
    assert data["dir"] == str(workspace) and data["current"] == str(video)
    assert [item["name"] for item in data["items"]] == ["ml_cam25_0101.avi"]
    assert client.post("/api/video/open", json={"path": str(video)}).json()["playlist_changed"] is False


def test_interface_files_are_not_cached(client: TestClient) -> None:
    page = client.get("/")
    assert page.headers["cache-control"] == "no-cache"
    assert "/static/app.js?v=" in page.text and "{{version}}" not in page.text
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


def test_rectangle_becomes_polygon_and_points_change(client: TestClient, workspace: Path) -> None:
    video = workspace / "ml_cam25_0101.avi"
    client.post("/api/video/open", json={"path": str(video)})
    five = [[0, 0], [16, 0], [32, 0], [32, 24], [0, 24]]
    info = client.post("/api/video/regions/edit", json={"regions": {"1": {"points": five, "shape": "polygon"}}}).json()
    assert info["regions"][0]["shape"] == "polygon" and len(info["regions"][0]["polygon"]) == 5
    shape = json.loads((workspace / "ml_cam25_0101.json").read_text(encoding="utf-8"))["shapes"][0]
    assert shape["shape_type"] == "polygon" and len(shape["points"]) == 5 and shape["wz"] == 1
    # Точку удалили — осталось 4; меньше трёх нельзя.
    four = {"1": {"points": five[:2] + five[3:], "shape": "polygon"}}
    assert len(client.post("/api/video/regions/edit", json={"regions": four}).json()["regions"][0]["polygon"]) == 4
    two = {"1": {"points": five[:2], "shape": "polygon"}}
    assert client.post("/api/video/regions/edit", json={"regions": two}).status_code == 400


def test_history_relabel_delete_and_rejections(client: TestClient, workspace: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    video = workspace / "ml_cam25_0101.avi"
    client.post("/api/video/open", json={"path": str(video)})
    saved = client.post("/api/save", json={"frame": 3, "label": "EMPTY"}).json()["results"]
    client.post("/api/save", json={"frame": 3, "label": "EMPTY", "regions": ["1"]})   # отказ: дубль
    data = client.get("/api/history", params={"video": str(video)}).json()
    kinds = [item["kind"] for item in data["items"]]
    assert data["total"] == 2 and data["rejected"] == 1 and kinds.count("saved") == 2 and kinds[0] == "rejected"
    rejected = data["items"][0]
    assert rejected["frame"] == 3 and rejected["similar_to"] == saved[0]["path"]
    assert client.get(rejected["image"]).headers["content-type"] == "image/jpeg"
    sample = next(item for item in data["items"] if item["kind"] == "saved" and item["region"] == "1")

    moved = client.post(f"/api/sample/{sample['id']}/relabel", json={"video": str(video), "label": "FULL"}).json()
    assert Path(moved["path"]).parent.name == "FULL" and Path(moved["path"]).exists()
    after = client.get("/api/history", params={"video": str(video)}).json()
    item = next(i for i in after["items"] if i["kind"] == "saved" and i["id"] == sample["id"])
    assert item["label"] == "FULL" and item["status"] == "verified"
    bad = client.post(f"/api/sample/{sample['id']}/relabel", json={"video": str(video), "label": "X"})
    assert bad.status_code == 400

    trashed: list[str] = []
    monkeypatch.setattr("send2trash.send2trash", lambda path: trashed.append(path))
    assert client.post(f"/api/sample/{sample['id']}/delete", json={"video": str(video)}).status_code == 200
    assert trashed == [moved["path"]]
    assert client.get("/api/history", params={"video": str(video)}).json()["total"] == 1
    assert client.post(f"/api/sample/{sample['id']}/delete", json={"video": str(video)}).status_code == 400

    playlist = client.get("/api/playlist").json()["items"][0]
    assert playlist["labels"] == {"EMPTY": 1, "FULL": 1} and playlist["rejected"] == 1
    # Картинку из папки другого видео отдаём только с указанием видео.
    assert client.get("/api/file", params={"path": saved[1]["path"], "video": str(video)}).status_code == 200


def test_open_dataset_folder(client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from neuro_dataset_collector.web import session as session_module

    opened: list[Path] = []
    monkeypatch.setattr(session_module, "_open_in_explorer", opened.append)
    video = workspace / "ml_cam25_0101.avi"
    assert client.post("/api/open-folder", json={"video": str(video)}).status_code == 404
    client.post("/api/video/open", json={"path": str(video)})
    client.post("/api/save", json={"frame": 1, "label": "FULL"})
    folder = client.post("/api/open-folder", json={"video": str(video)}).json()["path"]
    assert folder == str(workspace / "ml_cam25_0101_dataset")
    assert opened == [workspace / "ml_cam25_0101_dataset"]


def test_auto_frame_shown_during_run(client: TestClient, workspace: Path) -> None:
    assert client.get("/api/auto/frame").status_code == 404
    client.post("/api/video/open", json={"path": str(workspace / "ml_cam25_0101.avi")})
    client.post("/api/auto/start", json={"scope": "video"})
    for _ in range(100):
        if not client.get("/api/auto/status").json()["running"]:
            break
        time.sleep(0.05)
    status = client.get("/api/auto/status").json()
    assert status["video_path"] == str(workspace / "ml_cam25_0101.avi") and status["frame"] > 0
    assert client.get("/api/auto/frame").headers["content-type"] == "image/jpeg"


def test_find_port_skips_busy_and_detects_running_collector(client: TestClient) -> None:
    import socket

    from neuro_dataset_collector.web import app as app_module

    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    port = busy.getsockname()[1]
    try:
        found, running = app_module.find_port("127.0.0.1", port)
        assert found != port and running is False
    finally:
        busy.close()
    assert client.get("/api/ping").json()["app"] == app_module.APP_ID
