from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from neuro_dataset_collector.journal import (
    JournalMeta,
    JournalWriter,
    JournalZone,
    ReviewFile,
    ReviewKind,
    build_segments,
    review_path,
    write_review,
)


@pytest.fixture
def run_dir(workspace: Path) -> Path:
    """ Папка видео регрессионного прогона: журнал и участки (как пишет Production Counter). """
    folder = workspace / "runs" / "run1" / "ml_cam25_0101"
    video = workspace / "ml_cam25_0101.avi"
    zones = [JournalZone(id="1", polygon=[(0, 0), (32, 0), (32, 24), (0, 24)]),
             JournalZone(id="2", polygon=[(32, 24), (64, 24), (64, 48), (32, 48)])]
    meta = JournalMeta(video=str(video), fps=10.0, width=64, height=48, zones=zones, classes=["EMPTY", "FULL"],
                       model="ml.pt", source="regression", run_id="run1")
    with JournalWriter(folder / video.stem, meta) as writer:
        for frame in range(12):
            writer.write(frame, frame / 10, "1", "FULL" if frame in (5, 6) else "EMPTY", 0.8)
            writer.write(frame, frame / 10, "2", "EMPTY", 0.3)
    segments = build_segments([("1", 5, ReviewKind.EXTRA, "", "+1", "лишний счёт")], fps=10.0,
                              before_seconds=0.3, after_seconds=0.2, frame_count=12)
    write_review(review_path(folder, video), ReviewFile(video=str(video), journal=video.stem, source="regression",
                                                        run_id="run1", segments=segments))
    return folder


def test_journals_found_in_playlist_and_queried(client: Any, workspace: Path, run_dir: Path) -> None:
    client.post("/api/playlist", json={"path": str(workspace)})
    items = client.get("/api/journals").json()["items"]
    assert [i["name"] for i in items] == ["ml_cam25_0101"] and items[0]["run_id"] == "run1"
    data = client.post("/api/journal/query", json={"path": items[0]["path"], "filter": {"flips": True, "zones": ["1"]}})
    data = data.json()
    assert data["total"] == 2 and [i["frame"] for i in data["items"]] == [5, 7] and data["model"] == "ml.pt"
    crop = client.get("/api/journal/crop", params={"video": data["video"], "frame": 5, "zone": "1",
                                                   "journal": data["path"]})
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/jpeg"
    assert client.get("/api/journal/crop", params={"video": data["video"], "frame": 5, "zone": "9"}).status_code == 400


def test_open_journal_by_file_and_save_crop(client: Any, workspace: Path, run_dir: Path) -> None:
    opened = client.post("/api/journals/open", json={"path": str(run_dir / "ml_cam25_0101.predictions.csv")}).json()
    assert opened["path"] == str(run_dir / "ml_cam25_0101")
    saved = client.post("/api/journal/save", json={"video": str(workspace / "ml_cam25_0101.avi"), "frame": 5,
                                                   "zone": "1", "label": "EMPTY", "journal": opened["path"]})
    results = saved.json()["results"]
    assert len(results) == 1 and results[0]["ok"] and results[0]["region"] == "1"
    video = client.get("/api/video").json()
    assert video["video"] == str(workspace / "ml_cam25_0101.avi")   # видео открылось для сохранения
    item = client.get("/api/history", params={"video": video["video"]}).json()["items"][0]
    assert item["label"] == "EMPTY" and item["model_label"] == "FULL" and item["frame"] == 5


def test_review_segments_items_and_done(client: Any, workspace: Path, run_dir: Path) -> None:
    path = str(run_dir / "ml_cam25_0101.review.json")
    data = client.get("/api/review", params={"path": path}).json()
    assert data["journal"] == str(run_dir / "ml_cam25_0101") and len(data["segments"]) == 1
    segment = data["segments"][0]
    assert (segment["start_frame"], segment["end_frame"], segment["kind_name"]) == (2, 7, "лишнее")
    items = client.get("/api/review/items", params={"path": path, "index": 0}).json()["items"]
    assert [i["frame"] for i in items] == [2, 3, 4, 5, 6, 7] and [i["event"] for i in items].count(True) == 1
    assert items[3]["label"] == "FULL"
    done = client.post("/api/review/done", json={"path": path, "index": 0, "done": True}).json()
    assert done["segments"][0]["done"] is True
    assert client.post("/api/review/done", json={"path": path, "index": 5}).status_code == 404


def test_video_from_another_computer_found_in_playlist(client: Any, workspace: Path) -> None:
    client.post("/api/playlist", json={"path": str(workspace)})
    found = client.get("/api/resolve-video", params={"path": "D:/other/ml_cam25_0101.avi"}).json()
    assert found["path"] == str(workspace / "ml_cam25_0101.avi")
    assert client.get("/api/resolve-video", params={"path": "D:/other/none.avi"}).status_code == 404


def test_timeline_with_saved_frames_and_journal(client: Any, workspace: Path, run_dir: Path) -> None:
    video = str(workspace / "ml_cam25_0101.avi")
    client.post("/api/video/open", json={"path": video})
    client.post("/api/save", json={"frame": 4, "label": "FULL", "regions": ["1"]})
    data = client.get("/api/video/timeline").json()
    assert data["saved"] == [[4, "FULL"]] and data["model"] is None and data["frames"] > 0
    base = str(run_dir / "ml_cam25_0101")
    data = client.get("/api/video/timeline", params={"journal": base, "zone": "1"}).json()
    assert data["model"]["zone"] == "1" and data["model"]["runs"] == [[0, 4, "EMPTY"], [5, 6, "FULL"], [7, 11, "EMPTY"]]
    frame = client.get("/api/journal/frame", params={"video": video, "frame": 5, "zone": "1", "journal": base})
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"


def test_regions_from_another_camera_are_flagged(client: Any, workspace: Path) -> None:
    other = workspace / "jsons" / "norebo_cam30.json"
    other.write_text((workspace / "jsons" / "norebo_cam25.json").read_text(encoding="utf-8"), encoding="utf-8")
    client.post("/api/video/open", json={"path": str(workspace / "ml_cam25_0101.avi")})
    assert client.get("/api/video").json()["regions_warning"] is None
    info = client.post("/api/video/regions", json={"path": str(other)}).json()
    assert "камеры 30" in info["regions_warning"] and "камера 25" in info["regions_warning"]
    playlist = client.get("/api/playlist").json()["items"][0]
    assert playlist["journal"] is False
