from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from neuro_markup_lab.storage import SaveMode, VersionStore

from conftest import write_video as _video
from neuro_dataset_collector.catalog.catalog import Catalog
from neuro_dataset_collector.config import load_config
from neuro_dataset_collector.web.app import create_app
from neuro_dataset_collector.web.session import WebSession
from neuro_dataset_collector.web.state import load_state


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


def test_open_video_finds_production_markup_and_saves_next_to_video(client: TestClient, workspace: Path) -> None:
    video = workspace / "ml_cam25_0101.avi"
    info = client.post("/api/video/open", json={"path": str(video)}).json()
    assert info["markup"]["variant"] == "production" and info["markup"]["key"] == "norebo_cam25"
    assert info["regions_file"] == str(workspace / "jsons" / "norebo_cam25.json")
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


def _moved(document: dict[str, Any], x: float) -> dict[str, Any]:
    """ Копия разметки: первая зона сдвинута в точку x. """
    moved = json.loads(json.dumps(document))
    moved["shapes"][0]["points"] = [[x, x], [30, 20]]
    return moved


def test_markup_variants_and_versions(client: TestClient, workspace: Path) -> None:
    video = workspace / "ml_cam25_0101.avi"
    production = workspace / "jsons" / "norebo_cam25.json"
    original = json.loads(production.read_text(encoding="utf-8"))
    info = client.post("/api/video/open", json={"path": str(video)}).json()
    assert info["markup"]["variant"] == "production" and info["markup_sidecar"] is False
    assert info["markup_production_key"] == "norebo_cam25" and info["jsons_dir"] == str(workspace / "jsons")
    # Редактор сохранил разметку рядом с видео двумя версиями
    store = VersionStore(video)
    store.save(_moved(original, 2), SaveMode.NEW_VERSION)
    store.save(_moved(original, 4), SaveMode.NEW_VERSION)
    info = client.post("/api/video/markup/saved", json={"variant": "sidecar"}).json()
    assert info["markup"]["variant"] == "sidecar" and info["markup"]["version"] == 2
    assert info["regions"][0]["polygon"][0] == [4.0, 4.0]
    assert [v["number"] for v in info["markup_versions"]] == [1, 2] and info["markup_current"] == 2
    info = client.post("/api/video/markup", json={"variant": "sidecar", "version": 1}).json()
    assert info["markup"]["version"] == 1 and info["regions"][0]["polygon"][0] == [2.0, 2.0]
    assert client.post("/api/save", json={"frame": 3, "label": "FULL", "regions": ["1"]}).json()["results"][0]["ok"]
    with Catalog(workspace / "ml_cam25_0101_dataset") as catalog:
        [record] = catalog.records(catalog.collections()[0])
    assert record.markup_variant == "sidecar" and record.markup_version == 1
    info = client.post("/api/video/markup", json={"variant": "production"}).json()
    assert info["markup"]["variant"] == "production" and info["regions"][0]["polygon"][0] == [0.0, 0.0]
    assert client.post("/api/video/markup", json={"variant": "production", "version": 1}).status_code == 400
    info = client.post("/api/video/markup", json={"variant": "sidecar", "version": 9}).json()
    assert info["markup"] is None and "v9" in info["regions_warning"]
    # Выбор запоминается за видео; auto — снова как в настройках (рядом с видео, текущая версия)
    assert load_state().markup[str(video)].version == 9
    info = client.post("/api/video/markup", json={"variant": "auto"}).json()
    assert info["markup"]["variant"] == "sidecar" and info["markup"]["version"] == 2
    assert production.read_text(encoding="utf-8") == json.dumps(original)


def test_legacy_markup_migrated_on_open(client: TestClient, workspace: Path) -> None:
    video = workspace / "ml_cam25_0101.avi"
    legacy = workspace / "ml_cam25_0101.json"
    zones = json.loads((workspace / "jsons" / "norebo_cam25.json").read_text(encoding="utf-8"))
    legacy.write_text(json.dumps(_moved(zones, 3)), encoding="utf-8")
    playlist = client.post("/api/playlist", json={"path": str(workspace)}).json()
    assert playlist["items"][0]["regions"] == "sidecar"
    info = client.post("/api/video/open", json={"path": str(video)}).json()
    assert info["markup"]["variant"] == "sidecar" and info["markup"]["version"] == 1
    assert info["markup_versions"][0]["comment"] == "перенос из ml_cam25_0101.json"
    assert info["regions"][0]["polygon"][0] == [3.0, 3.0] and legacy.is_file()


def test_markup_editor_mounted(client: TestClient, workspace: Path) -> None:
    assert client.get("/markup/").status_code == 200
    assert client.get("/markup/static/neuro_markup_lab_editor.js").status_code == 200
    video = workspace / "ml_cam25_0101.avi"
    locations = client.get("/markup/api/locations", params={"video": str(video), "jsons": str(workspace / "jsons")})
    assert locations.status_code == 200 and "norebo_cam25" in locations.text


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
    assert all(item["saved"] == 0 and item["regions"] == "production" for item in data["items"])
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
    assert '"static/js/main.js?v=' in page.text and "{{version}}" not in page.text
    assert client.get("/static/js/main.js").headers["cache-control"] == "no-cache"


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


def test_zone_selection_remembered_per_camera(client: TestClient, workspace: Path) -> None:
    first, second = workspace / "ml_cam25_0101.avi", _video(workspace / "ml_cam25_0202.avi")
    info = client.post("/api/video/open", json={"path": str(first)}).json()
    assert [(s["id"], s["selected"]) for s in info["shapes"]] == [("1", True), ("2", True)]
    info = client.post("/api/video/zones", json={"ids": ["2"]}).json()
    assert [r["id"] for r in info["regions"]] == ["2"]
    assert [(s["id"], s["selected"]) for s in info["shapes"]] == [("1", False), ("2", True)]
    saved = client.post("/api/save", json={"frame": 2, "label": "FULL"}).json()["results"]
    assert [r["region"] for r in saved] == ["2"]
    # Та же камера в другом видео — тот же выбор.
    assert [r["id"] for r in client.post("/api/video/open", json={"path": str(second)}).json()["regions"]] == ["2"]
    assert client.post("/api/video/zones", json={"ids": []}).status_code == 400
    assert client.post("/api/video/zones", json={"ids": ["9"]}).status_code == 400


def test_stats_sync_export_for_playlist(client: TestClient, workspace: Path) -> None:
    first, second = workspace / "ml_cam25_0101.avi", _video(workspace / "ml_cam25_0202.avi")
    client.post("/api/playlist", json={"path": str(workspace)})
    for video, label in ((first, "EMPTY"), (second, "FULL")):
        client.post("/api/video/open", json={"path": str(video)})
        client.post("/api/save", json={"frame": 1, "label": label})
    stats = client.get("/api/stats", params={"scope": "playlist"}).json()
    assert [row["labels"] for row in stats["rows"]] == [{"EMPTY": 2}, {"FULL": 2}]
    assert stats["labels"] == {"EMPTY": 2, "FULL": 2} and stats["statuses"] == {"pending": 4}
    assert client.get("/api/stats").json()["labels"] == {"FULL": 2}
    assert client.post("/api/sync", json={"scope": "playlist"}).json()["verified"] == 4
    out = workspace / "export"
    exported = client.post("/api/export", json={"out_dir": str(out), "scope": "playlist"}).json()
    assert exported["exported"] == 4 and exported["by_label"] == {"EMPTY": 2, "FULL": 2}
    assert len(list((out / "FULL").iterdir())) == 2 and len(list((out / "EMPTY").iterdir())) == 2


def test_mounted_into_another_app(home: Path) -> None:
    from fastapi import FastAPI

    from neuro_dataset_collector.web.app import mount_app

    host = FastAPI()
    session = mount_app(host, "/dataset/")
    try:
        with TestClient(host) as test_client:
            redirect = test_client.get("/dataset", follow_redirects=False)
            assert redirect.status_code == 307 and redirect.headers["location"].endswith("/dataset/")
            page = test_client.get("/dataset/")
            assert "Сборщик датасета" in page.text and page.headers["cache-control"] == "no-cache"
            # Адреса на странице относительные — работают под префиксом.
            assert 'src="/static' not in page.text and "api(\"GET\", \"/api" not in page.text
            assert test_client.get("/dataset/static/js/core.js").headers["cache-control"] == "no-cache"
            assert test_client.get("/dataset/api/ping").json()["app"] == "neuro_dataset_collector"
    finally:
        session.close()


def test_playlist_without_subfolders_is_remembered(client: TestClient, workspace: Path, home: Path) -> None:
    folder = workspace / "videos"
    (folder / "day2").mkdir(parents=True)
    _video(folder / "ml_cam25_a.avi", frames=4)
    _video(folder / "day2" / "ml_cam25_b.avi", frames=4)
    data = client.post("/api/playlist", json={"path": str(folder), "recursive": False}).json()
    assert data["recursive"] is False and [item["name"] for item in data["items"]] == ["ml_cam25_a.avi"]
    assert load_state().playlist_recursive is False
    # Без recursive — прежний выбор остаётся.
    data = client.post("/api/playlist", json={"path": str(folder)}).json()
    assert data["recursive"] is False and len(data["items"]) == 1
    data = client.post("/api/playlist", json={"path": str(folder), "recursive": True}).json()
    assert data["recursive"] is True and len(data["items"]) == 2
