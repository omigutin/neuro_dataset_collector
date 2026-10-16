from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import yaml

from conftest import make_frame, random_image
from neuro_frame_collector import (
    Collector,
    CollectorConfig,
    Prediction,
    SampleStatus,
    collect_videos,
    export_dataset,
    sync_collection,
)
from neuro_frame_collector.catalog import Catalog
from neuro_frame_collector.cli.main import main
from neuro_frame_collector.config import save_config
from neuro_frame_collector.core import BBox

ConfigFactory = Callable[..., CollectorConfig]


def _collect_three(config: CollectorConfig) -> list[Path]:
    with Collector(config) as collector:
        paths = [collector.save(make_frame(i), "FULL")[0].path for i in range(3)]
    return [p for p in paths if p is not None]


def test_sync_move_delete_keep(make_config: ConfigFactory) -> None:
    config = make_config()
    moved, deleted, kept = _collect_three(config)
    (config.collection_dir / "EMPTY").mkdir()
    shutil.move(moved, config.collection_dir / "EMPTY" / moved.name)
    deleted.unlink()
    report = sync_collection(config)
    assert (report.verified, report.relabeled, report.rejected) == (2, 1, 1)
    with Catalog(config.catalog.root) as catalog:
        records = {r.filename: r for r in catalog.records(config.catalog.collection)}
        assert records[moved.name].label == "EMPTY" and records[moved.name].status == SampleStatus.VERIFIED
        assert records[deleted.name].status == SampleStatus.REJECTED
        assert records[kept.name].status == SampleStatus.VERIFIED
        history = catalog.history(records[moved.name].id or 0)
        assert history[-1]["origin"] == "sync" and history[-1]["new_label"] == "EMPTY"
    # Повторная сверка ничего не меняет.
    again = sync_collection(config)
    assert (again.verified, again.rejected) == (0, 0)


def test_rejected_frame_is_not_collected_again(make_config: ConfigFactory) -> None:
    config = make_config()
    [path] = _collect_three(config)[:1]
    path.unlink()
    sync_collection(config)
    with Collector(config) as collector:
        [result] = collector.save(make_frame(0, index=500), "FULL")
    assert not result.ok and "отклонённый" in result.message


def test_sync_keeps_suspect_and_reports_unknown(make_config: ConfigFactory) -> None:
    config = make_config(auto={"triggers": [{"type": "always"}], "limits": []})
    with Collector(config) as collector:
        collector.offer(make_frame(1), [Prediction("EMPTY", 0.9)], reference_label="FULL")
    stray = config.collection_dir / "FULL" / "stray.jpg"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"x")
    (config.collection_dir / "OTHER").mkdir()
    report = sync_collection(config)
    assert report.still_suspect == 1 and stray in report.unknown_files


def test_unknown_label_folder_reported(make_config: ConfigFactory) -> None:
    config = make_config()
    [path] = _collect_three(config)[:1]
    (config.collection_dir / "NEWCLASS").mkdir()
    shutil.move(path, config.collection_dir / "NEWCLASS" / path.name)
    report = sync_collection(config)
    assert "NEWCLASS" in report.unknown_labels


def test_export_classify_only_verified_and_new(make_config: ConfigFactory, tmp_path: Path) -> None:
    config = make_config()
    _collect_three(config)
    out = tmp_path / "export"
    assert export_dataset(config, out).exported == 0
    sync_collection(config)
    report = export_dataset(config, out)
    assert report.exported == 3 and report.by_label == {"FULL": 3}
    assert len(list((out / "FULL").iterdir())) == 3
    assert export_dataset(config, tmp_path / "again").exported == 0
    assert export_dataset(config, tmp_path / "all", only_new=False).exported == 3


def test_export_detect_yolo(make_config: ConfigFactory, tmp_path: Path) -> None:
    config = make_config(task="detect", auto={"triggers": [{"type": "always"}], "limits": []})
    with Collector(config) as collector:
        collector.offer(make_frame(1), [Prediction("EMPTY", 0.6, BBox(1, 1, 10, 10))])
    sync_collection(config)
    out = tmp_path / "yolo"
    report = export_dataset(config, out)
    assert report.exported == 1
    assert len(list((out / "images").iterdir())) == 1 and len(list((out / "labels").iterdir())) == 1
    data = yaml.safe_load((out / "data.yaml").read_text(encoding="utf-8"))
    assert data["names"] == {0: "EMPTY", 1: "FULL", 2: "UNKNOWN"}


def _write_video(path: Path, frames: int = 20) -> Path:
    cv2 = pytest.importorskip("cv2")
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
    for i in range(frames):
        writer.write(random_image(i // 5))  # каждые 5 кадров картинка меняется
    writer.release()
    return path


def test_offline_collect_cold_start(make_config: ConfigFactory, tmp_path: Path) -> None:
    video = _write_video(tmp_path / "cam1.avi")
    config = make_config(
        auto={"triggers": [{"type": "always"}], "limits": [{"type": "scene_change", "min_diff": 10},
                                                         {"type": "dedup", "threshold": 3}]},
        source={"path": str(video), "step": 1},
    )
    with Collector(config) as collector:
        report = collect_videos(collector)
    assert report.videos == 1 and report.frames == 20
    assert report.saved == 4
    assert sorted(p.parent.name for p in config.collection_dir.rglob("*.jpg")) == ["_unsorted"] * 4


def test_offline_with_predictor(make_config: ConfigFactory, tmp_path: Path) -> None:
    video = _write_video(tmp_path / "cam1.avi", frames=10)
    config = make_config(auto={"triggers": [{"type": "always"}], "limits": []}, source={"path": str(video), "step": 5})

    def predictor(image: np.ndarray) -> list[Prediction]:
        return [Prediction("FULL", 0.5)]

    with Collector(config) as collector:
        report = collect_videos(collector, predictor=predictor)
    assert report.saved == 2
    assert len(list((config.collection_dir / "FULL").iterdir())) == 2


def test_cli_end_to_end(make_config: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    video = _write_video(tmp_path / "cam1.avi", frames=10)
    config = make_config(auto={"triggers": [{"type": "always"}], "limits": [{"type": "dedup"}]},
                         source={"path": str(video), "step": 1})
    cfg = tmp_path / "nfc.yaml"
    save_config(config, cfg)
    assert main(["collect", "-c", str(cfg)]) == 0
    unsorted = config.collection_dir / "_unsorted"
    (config.collection_dir / "FULL").mkdir()
    for path in list(unsorted.iterdir()):
        shutil.move(path, config.collection_dir / "FULL" / path.name)
    assert main(["sync", "-c", str(cfg)]) == 0
    assert main(["export", "-c", str(cfg), "-o", str(tmp_path / "out")]) == 0
    assert main(["stats", "-c", str(cfg)]) == 0
    output = capsys.readouterr().out
    assert "Подтверждено: 2" in output and "Выгружено 2" in output


def test_cli_init(tmp_path: Path) -> None:
    target = tmp_path / "new.yaml"
    assert main(["init", str(target)]) == 0
    assert "classes" in target.read_text(encoding="utf-8")
    assert main(["init", str(target)]) == 1
