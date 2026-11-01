from __future__ import annotations

from pathlib import Path

import pytest

from conftest import random_image
from neuro_dataset_collector.config import CollectorConfig, JournalRunConfig
from neuro_dataset_collector.core import Image, Prediction
from neuro_dataset_collector.journal import read_journal, read_journal_meta
from neuro_dataset_collector.pipeline import Collector, SaveRule, ZoneAnswer, predict_zones, run_model


def _video(path: Path, frames: int = 10) -> Path:
    cv2 = pytest.importorskip("cv2")
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
    for i in range(frames):
        writer.write(random_image(i))
    writer.release()
    return path


def _predictor(image: Image) -> list[Prediction]:
    """ Светлая картинка — FULL, тёмная — EMPTY; уверенность растёт с удалением от середины. """
    mean = float(image.mean())
    return [Prediction("FULL" if mean > 127 else "EMPTY", min(1.0, abs(mean - 127) / 64))]


def test_predict_zones_without_regions_answers_for_full_frame() -> None:
    answers = predict_zones(random_image(1), [], _predictor)
    assert len(answers) == 1 and answers[0].zone == "full" and answers[0].label in {"FULL", "EMPTY"}


def test_save_rule_reasons() -> None:
    rule = SaveRule(JournalRunConfig(conf_below=0.5, flips=True, labels=["UNKNOWN"]))
    assert rule.reason(ZoneAnswer("1", "FULL", 0.9)) is None
    assert "уверенность" in (rule.reason(ZoneAnswer("1", "FULL", 0.3)) or "")
    assert "сменился" in (rule.reason(ZoneAnswer("1", "EMPTY", 0.9)) or "")
    assert rule.reason(ZoneAnswer("2", "EMPTY", 0.9)) is None
    by_label = SaveRule(JournalRunConfig(flips=False, labels=["UNKNOWN"]))
    assert by_label.reason(ZoneAnswer("3", "UNKNOWN", 0.9)) == "ответ UNKNOWN"
    assert SaveRule(JournalRunConfig(conf_below=None, flips=False)).empty


def test_run_model_writes_journal_and_saves_doubtful(tmp_path: Path) -> None:
    video = _video(tmp_path / "cam1.avi")
    config = CollectorConfig.model_validate({
        "classes": ["EMPTY", "FULL", "UNKNOWN"],
        "catalog": {"next_to_video": True},
        "source": {"step": 1},
        "journal": {"save": True, "conf_below": 0.99, "flips": False},
        "runtime": {"async_write": False},
        "storage": {"min_free_gb": 0},
        "manual": {"on_duplicate": "allow"},
    }).for_video(video)
    with Collector(config) as collector:
        report = run_model(collector, video, _predictor, model_name="fake.pt")
    assert report.frames >= 9 and report.rows == report.frames and report.journal is not None
    rows = list(read_journal(report.journal))
    assert [r.frame for r in rows] == list(range(report.frames)) and all(r.zone == "full" for r in rows)
    meta = read_journal_meta(report.journal)
    assert meta.source == "collector" and meta.model == "fake.pt" and meta.video == str(video)
    assert meta.width == 64 and meta.model_extra and meta.model_extra.get("frames") == report.frames
    assert report.saved == report.matched > 0
    assert len(list((config.collection_dir / "_suspect").rglob("*.jpg"))) == report.saved
