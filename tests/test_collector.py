from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import make_frame
from neuro_frame_collector import Collector, CollectorConfig, Prediction, RejectReason, SampleStatus
from neuro_frame_collector.core import BBox, Region

ConfigFactory = Callable[..., CollectorConfig]


def test_manual_save_into_class_folder(make_config: ConfigFactory) -> None:
    config = make_config()
    with Collector(config) as collector:
        [result] = collector.save(make_frame(1), "FULL")
    assert result.ok and result.path is not None and result.path.exists()
    assert result.path.parent == config.collection_dir / "FULL"
    assert result.sample_status == SampleStatus.PENDING


def test_manual_save_without_label_goes_unsorted(make_config: ConfigFactory) -> None:
    config = make_config()
    with Collector(config) as collector:
        [result] = collector.save(make_frame(1))
    assert result.path is not None and result.path.parent.name == "_unsorted"


def test_manual_unknown_class_raises(make_config: ConfigFactory) -> None:
    with Collector(make_config()) as collector, pytest.raises(ValueError):
        collector.save(make_frame(1), "NOPE")


def test_duplicate_rejected_with_reference(make_config: ConfigFactory) -> None:
    with Collector(make_config()) as collector:
        [first] = collector.save(make_frame(1), "FULL")
        [second] = collector.save(make_frame(1, index=99), "EMPTY")
    assert not second.ok
    assert second.reason == RejectReason.DUPLICATE
    assert second.similar_to == first.path
    assert "Похож" in second.message


def test_duplicate_warn_policy_saves(make_config: ConfigFactory) -> None:
    config = make_config(manual={"on_duplicate": "warn"})
    with Collector(config) as collector:
        collector.save(make_frame(1), "FULL")
        [second] = collector.save(make_frame(1, index=5), "FULL")
    assert second.ok and second.similar_to is not None and "внимание" in second.message


def test_force_ignores_duplicate(make_config: ConfigFactory) -> None:
    with Collector(make_config()) as collector:
        collector.save(make_frame(1), "FULL")
        [second] = collector.save(make_frame(1, index=5), "FULL", force=True)
    assert second.ok


def test_unique_names_for_same_frame_number(make_config: ConfigFactory) -> None:
    with Collector(make_config()) as collector:
        [a] = collector.save(make_frame(1, index=10), "FULL")
        [b] = collector.save(make_frame(2, index=10), "FULL")
    assert a.path != b.path


def test_regions_saved_separately(make_config: ConfigFactory) -> None:
    regions = [Region.from_bbox("1", BBox(0, 0, 32, 24)), Region.from_bbox("2", BBox(32, 24, 64, 48))]
    config = make_config(regions={"source": "api"})
    with Collector(config, regions=regions) as collector:
        results = collector.save(make_frame(1), "FULL")
        only_second = collector.save(make_frame(7), "EMPTY", region_ids=["2"])
    assert [r.region_id for r in results] == ["1", "2"]
    assert [r.region_id for r in only_second] == ["2"]


def test_auto_low_confidence_and_interval(make_config: ConfigFactory) -> None:
    config = make_config(auto={"triggers": [{"type": "confidence", "max": 0.8}],
                               "limits": [{"type": "interval", "seconds": 1.0}]})
    with Collector(config) as collector:
        confident = collector.offer(make_frame(1, index=0), [Prediction("FULL", 0.99)])
        first = collector.offer(make_frame(2, index=1), [Prediction("FULL", 0.5)])
        too_soon = collector.offer(make_frame(3, index=2), [Prediction("FULL", 0.5)])
        later = collector.offer(make_frame(4, index=30), [Prediction("FULL", 0.5)])
    assert confident == []
    assert first[0].ok and first[0].label == "FULL" and first[0].trigger == "confidence"
    assert too_soon[0].reason == RejectReason.INTERVAL
    assert later[0].ok


def test_quota_per_label(make_config: ConfigFactory) -> None:
    config = make_config(auto={"triggers": [{"type": "always"}], "limits": [{"type": "quota", "per_label": 2}]})
    with Collector(config) as collector:
        results = [collector.offer(make_frame(i), [Prediction("FULL", 0.9)])[0] for i in range(3)]
    assert [r.ok for r in results] == [True, True, False]
    assert results[2].reason == RejectReason.QUOTA


def test_scene_change_limit(make_config: ConfigFactory) -> None:
    config = make_config(auto={"triggers": [{"type": "always"}], "limits": [{"type": "scene_change", "min_diff": 5}]})
    with Collector(config) as collector:
        first = collector.offer(make_frame(1, index=0))
        same = collector.offer(make_frame(1, index=1))
        other = collector.offer(make_frame(2, index=2))
    assert first[0].ok and same[0].reason == RejectReason.SCENE_UNCHANGED and other[0].ok


def test_consistency_moves_saved_flip_to_suspect(make_config: ConfigFactory) -> None:
    config = make_config(
        auto={"triggers": [{"type": "confidence", "max": 0.8}], "limits": []},
        consistency={"enabled": True, "context": 2, "max_run": 1},
    )
    labels = ["FULL", "FULL", "EMPTY", "FULL", "FULL"]
    confs = [0.99, 0.99, 0.5, 0.99, 0.99]
    with Collector(config) as collector:
        results = []
        for i, (label, conf) in enumerate(zip(labels, confs, strict=True)):
            results.extend(collector.offer(make_frame(i), [Prediction(label, conf)]))
        collector.flush()
        records = collector.catalog.records(config.catalog.collection)
    assert len(records) == 1
    record = records[0]
    assert record.status == SampleStatus.SUSPECT and record.proposed_label == "FULL"
    assert "_suspect/EMPTY__FULL" in record.path
    assert (config.catalog.root / record.path).exists()
    assert results[-1].trigger == "consistency"


def test_consistency_saves_confident_flip(make_config: ConfigFactory) -> None:
    config = make_config(
        auto={"triggers": [{"type": "confidence", "max": 0.8}], "limits": []},
        consistency={"enabled": True, "context": 2, "max_run": 1, "trusted_flips": {"EMPTY": ["FULL"]}},
    )
    labels = ["FULL", "FULL", "EMPTY", "FULL", "FULL"]
    with Collector(config) as collector:
        for i, label in enumerate(labels):
            collector.offer(make_frame(i), [Prediction(label, 0.97)])
        records = collector.catalog.records(config.catalog.collection)
    assert len(records) == 1
    assert records[0].label == "FULL" and records[0].model_label == "EMPTY"
    assert records[0].path.split("/")[-2] == "FULL"


def test_reference_label_mismatch_is_suspect(make_config: ConfigFactory) -> None:
    config = make_config(auto={"triggers": [{"type": "confidence", "max": 0.1}], "limits": []})
    with Collector(config) as collector:
        [result] = collector.offer(make_frame(1), [Prediction("EMPTY", 0.97)], reference_label="FULL")
    assert result.ok and result.sample_status == SampleStatus.SUSPECT


def test_detect_writes_yolo_labels(make_config: ConfigFactory) -> None:
    config = make_config(task="detect", auto={"triggers": [{"type": "always"}], "limits": []})
    with Collector(config) as collector:
        [result] = collector.offer(make_frame(1), [Prediction("FULL", 0.6, BBox(0, 0, 32, 24))])
    assert result.path is not None and result.path.parent.name == "images"
    label_file = config.collection_dir / "labels" / (result.path.stem + ".txt")
    assert label_file.read_text().strip() == "1 0.250000 0.250000 0.500000 0.500000"


def test_several_regions_need_mapping(make_config: ConfigFactory) -> None:
    regions = [Region.from_bbox("1", BBox(0, 0, 32, 24)), Region.from_bbox("2", BBox(32, 24, 64, 48))]
    config = make_config(regions={"source": "api"}, auto={"triggers": [{"type": "always"}], "limits": []})
    with Collector(config, regions=regions) as collector:
        with pytest.raises(ValueError):
            collector.offer(make_frame(1), [Prediction("FULL", 0.5)])
        results = collector.offer(make_frame(1), {"1": [Prediction("FULL", 0.5)], "2": [Prediction("EMPTY", 0.5)]})
    assert [r.label for r in results] == ["FULL", "EMPTY"]


def test_disable_on_error(make_config: ConfigFactory) -> None:
    config = make_config(runtime={"async_write": False, "disable_on_error": True},
                         auto={"triggers": [{"type": "always"}], "limits": []})
    with Collector(config) as collector:
        [result] = collector.offer(make_frame(1), "not predictions")  # type: ignore[arg-type]
        assert result.reason == RejectReason.DISABLED and not collector.enabled
        assert collector.offer(make_frame(2)) == []


def test_async_writer(make_config: ConfigFactory) -> None:
    config = make_config(runtime={"async_write": True}, auto={"triggers": [{"type": "always"}], "limits": []})
    with Collector(config) as collector:
        results = [collector.offer(make_frame(i))[0] for i in range(5)]
        collector.flush()
    assert all(r.path is not None and r.path.exists() for r in results)


def test_rate_limit(make_config: ConfigFactory) -> None:
    config = make_config(runtime={"async_write": False, "max_saves_per_hour": 2},
                         auto={"triggers": [{"type": "always"}], "limits": []})
    with Collector(config) as collector:
        results = [collector.offer(make_frame(i))[0] for i in range(3)]
    assert results[2].reason == RejectReason.RATE_LIMIT


def test_catalog_survives_restart(make_config: ConfigFactory, tmp_path: Path) -> None:
    config = make_config()
    with Collector(config) as collector:
        collector.save(make_frame(1), "FULL")
    with Collector(config) as collector:
        [again] = collector.save(make_frame(1, index=3), "FULL")
        stats = collector.stats()
    assert again.reason == RejectReason.DUPLICATE
    assert stats.total_by_label == {"FULL": 1}


def test_per_label_overrides(make_config: ConfigFactory) -> None:
    config = make_config(
        auto={
            "triggers": [{"type": "confidence", "max": 0.8}],
            "limits": [{"type": "interval", "seconds": 1.0}],
            "per_label": {"EMPTY": {"enabled": False}, "FULL": {"max_conf": 0.95, "interval_seconds": 5}},
        }
    )
    with Collector(config) as collector:
        assert collector.offer(make_frame(1, index=0), [Prediction("EMPTY", 0.1)]) == []
        assert collector.offer(make_frame(2, index=1), [Prediction("FULL", 0.9)])[0].ok
        too_soon = collector.offer(make_frame(3, index=30), [Prediction("FULL", 0.9)])[0]
        later = collector.offer(make_frame(4, index=60), [Prediction("FULL", 0.9)])[0]
        unknown = collector.offer(make_frame(5, index=61), [Prediction("UNKNOWN", 0.9)])
    assert too_soon.reason == RejectReason.INTERVAL and later.ok
    assert unknown == []


def test_per_label_unknown_class_rejected(make_config: ConfigFactory) -> None:
    with pytest.raises(ValueError):
        make_config(auto={"per_label": {"NOPE": {"enabled": False}}})
