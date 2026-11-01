from __future__ import annotations

from collections.abc import Callable

import pytest

from conftest import make_frame
from neuro_dataset_collector import Collector, CollectorConfig
from neuro_dataset_collector.catalog import Catalog
from neuro_dataset_collector.core import Image, Prediction
from neuro_dataset_collector.review import check_with_model, cross_label_duplicates

ConfigFactory = Callable[..., CollectorConfig]


def _predictor(image: Image) -> list[Prediction]:
    """ Светлая картинка — FULL, тёмная — EMPTY; уверенность растёт с удалением от середины. """
    mean = float(image.mean())
    return [Prediction("FULL" if mean > 127 else "EMPTY", min(1.0, abs(mean - 127) / 40))]


def test_check_finds_disagreements_and_cross_class_duplicates(make_config: ConfigFactory) -> None:
    config = make_config(manual={"on_duplicate": "allow"})
    with Collector(config) as collector:
        for seed in range(6):
            frame = make_frame(seed)
            truth = _predictor(frame.image)[0].label
            collector.save(frame, "EMPTY" if truth == "FULL" and seed % 2 else truth)
        collector.save(make_frame(0), "UNKNOWN")      # тот же кадр в другом классе
        collector.save(make_frame(7), None)            # без класса — не проверяется
    with Catalog(config.catalog.root) as catalog:
        report = check_with_model(config, catalog, _predictor)
        wrong = [d for d in report.disagreements if d.label != "UNKNOWN"]
        assert report.checked == 7 and all(d.model_label == "FULL" and d.label == "EMPTY" for d in wrong)
        assert report.agreed + len(report.disagreements) == report.checked
        strict = check_with_model(config, catalog, _predictor, min_conf=1.01)
        assert strict.agreed == 0 and len(strict.disagreements) == 7
        pairs = cross_label_duplicates(config, catalog, threshold=0)
        assert any({p.first.label, p.second.label} >= {"UNKNOWN"} and p.distance == 0 for p in pairs)


def test_check_is_for_classification_only(make_config: ConfigFactory) -> None:
    config = make_config(task="detect", classes=["box"])
    with Catalog(config.catalog.root) as catalog, pytest.raises(ValueError, match="classification"):
        check_with_model(config, catalog, _predictor)
