from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest
from pydantic import ValidationError

from neuro_dataset_collector.config import (
    CollectorConfig,
    RuntimeProfile,
    dump_config,
    load_config,
    save_config,
)


def test_defaults_are_valid() -> None:
    config = CollectorConfig()
    assert config.task.value == "classify"
    assert config.catalog.collection == ""
    assert config.collection_dir == config.catalog.root


def test_collection_key_normalized() -> None:
    config = CollectorConfig.model_validate({"catalog": {"collection": " norebo \\ ML "}})
    assert config.catalog.collection == "norebo/ML"


@pytest.mark.parametrize("key", ["a//b", "../x", "a/./b"])
def test_bad_collection_rejected(key: str) -> None:
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"catalog": {"collection": key}})


def test_unknown_class_in_keys_rejected() -> None:
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"classes": ["A"], "manual": {"keys": {"1": "B"}}})


def test_typo_in_key_rejected() -> None:
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"catalgo": {}})


def test_bad_filename_template_rejected() -> None:
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"storage": {"filename": "{unknown}"}})


def test_interval_needs_one_unit() -> None:
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"auto": {"limits": [{"type": "interval", "seconds": 1, "frames": 5}]}})


def test_production_profile_defaults() -> None:
    config = CollectorConfig.model_validate({"runtime": {"profile": "production", "async_write": False}})
    assert config.runtime.profile == RuntimeProfile.PRODUCTION
    assert config.runtime.async_write is True
    assert config.runtime.disable_on_error is True
    assert config.runtime.max_saves_per_hour == 300


def test_file_regions_need_path() -> None:
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"regions": {"source": "file"}})


def test_roundtrip_and_relative_paths(tmp_path: Path) -> None:
    config = CollectorConfig.model_validate({"classes": ["A"], "catalog": {"root": "data"}})
    path = tmp_path / "cfg.yaml"
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.catalog.root == (tmp_path / "data").resolve()
    assert "classes" in dump_config(loaded)


def test_example_config_loads() -> None:
    example = resources.files("neuro_dataset_collector").joinpath("config.example.yaml")
    with resources.as_file(example) as path:
        config = load_config(path)
    assert config.classes == ["EMPTY", "FULL", "NONE", "UNKNOWN"]


@pytest.mark.parametrize("name", ["full", "FULL_TRAY", "A-1", "7UP"])
def test_good_class_names(name: str) -> None:
    assert CollectorConfig.model_validate({"classes": [name]}).classes == [name]


@pytest.mark.parametrize("name", ["", "ПОЛНЫЙ", "FULL TRAY", "_X", "-X", "A/B", "A.B"])
def test_bad_class_names(name: str) -> None:
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"classes": [name]})


def test_bad_shape_label() -> None:
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"regions": {"labels": ["зона"]}})


def test_for_video_next_to_video(tmp_path: Path) -> None:
    video = tmp_path / "cam25_0101.avi"
    plain = CollectorConfig.model_validate({"catalog": {"root": str(tmp_path / "data")}}).for_video(video)
    assert plain.catalog.root == tmp_path / "data" and plain.source.path == video
    near = CollectorConfig.model_validate({"catalog": {"next_to_video": True}}).for_video(video)
    assert near.catalog.root == tmp_path / "cam25_0101_dataset"
    assert near.collection_dir == tmp_path / "cam25_0101_dataset"
