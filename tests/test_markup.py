""" Разметка видео через neuro_markup_lab: выбор варианта, перенос старого файла, запись в каталог. """

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from neuro_markup_lab.storage import SaveMode, VersionStore

from conftest import make_frame
from neuro_dataset_collector import Collector, CollectorConfig
from neuro_dataset_collector.catalog.catalog import CATALOG_FILENAME, Catalog
from neuro_dataset_collector.config import MarkupChoice, RegionsConfig
from neuro_dataset_collector.regions import (
    describe_markup,
    legacy_sidecar_file,
    migrate_legacy_sidecar,
    production_key,
    resolve_markup,
    with_video_markup,
)

ConfigFactory = Callable[..., CollectorConfig]

_ZONES = {"imageWidth": 64, "imageHeight": 48, "shapes": [
    {"label": "crop", "points": [[0, 0], [32, 24]], "shape_type": "rectangle", "wz": 1},
]}


def _video(folder: Path, name: str = "line1_cam5_0101.avi") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / name
    video.write_bytes(b"")
    return video


def _production(folder: Path, *keys: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for key in keys:
        (folder / f"{key}.json").write_text(json.dumps(_ZONES), encoding="utf-8")
    return folder


def _regions(**values: object) -> RegionsConfig:
    return RegionsConfig.model_validate({"source": "file", **values})


def test_legacy_sidecar_migrated_once(tmp_path: Path) -> None:
    video = _video(tmp_path)
    legacy = legacy_sidecar_file(video)
    legacy.write_text(json.dumps(_ZONES), encoding="utf-8")
    original = legacy.read_bytes()
    working = migrate_legacy_sidecar(video)
    store = VersionStore(video)
    assert working == store.working_path and working.is_file()
    [version] = store.history().versions
    assert version.number == 1 and version.comment == f"перенос из {legacy.name}"
    assert legacy.read_bytes() == original
    assert migrate_legacy_sidecar(video) is None
    assert len(store.history().versions) == 1


def test_legacy_without_shapes_not_migrated(tmp_path: Path) -> None:
    video = _video(tmp_path)
    legacy_sidecar_file(video).write_text(json.dumps({"other": 1}), encoding="utf-8")
    assert migrate_legacy_sidecar(video) is None
    assert not VersionStore(video).working_path.exists()


def test_production_key_by_name_and_camera(tmp_path: Path) -> None:
    jsons = _production(tmp_path / "jsons", "site_a_cam5", "site_a_cam6")
    assert production_key(_video(tmp_path / "v", "line1_cam05_0101.avi"), jsons) == "site_a_cam5"
    assert production_key(_video(tmp_path / "v", "line1_0101.avi"), jsons) is None
    assert production_key(_video(tmp_path / "v"), None) is None


def test_auto_prefers_sidecar_then_production_then_file(tmp_path: Path) -> None:
    video = _video(tmp_path / "videos")
    jsons = _production(tmp_path / "jsons", "site_a_cam5")
    explicit = _production(tmp_path / "other", "zones") / "zones.json"
    regions = _regions(search_dir=str(jsons), file=str(explicit))
    found = resolve_markup(video, regions)
    assert found is not None and found.variant == "production" and found.key == "site_a_cam5"
    VersionStore(video).save(dict(_ZONES), SaveMode.NEW_VERSION)
    found = resolve_markup(video, regions)
    assert found is not None and found.variant == "sidecar" and found.version == 1
    regions.variant = MarkupChoice.FILE
    found = resolve_markup(video, regions)
    assert found is not None and found.variant == "file" and found.path == explicit
    regions.variant = MarkupChoice.AUTO
    regions.search_dir = None
    VersionStore(video).working_path.unlink()
    found = resolve_markup(video, regions)
    assert found is not None and found.variant == "file"


def test_sidecar_version_and_description(tmp_path: Path) -> None:
    video = _video(tmp_path)
    store = VersionStore(video)
    store.save(dict(_ZONES), SaveMode.NEW_VERSION)
    store.save({**_ZONES, "shapes": []}, SaveMode.NEW_VERSION)
    found = resolve_markup(video, _regions(variant="sidecar", version=1))
    assert found is not None and found.version == 1 and found.path.parent == store.directory
    described = describe_markup(found.path, video, None)
    assert described is not None and described.variant == "sidecar" and described.version == 1
    current = describe_markup(store.working_path, video, None)
    assert current is not None and current.version == 2
    assert resolve_markup(video, _regions(variant="sidecar", version=7)) is None


def test_missing_markup_falls_back_to_full_frame(make_config: ConfigFactory, tmp_path: Path) -> None:
    video = _video(tmp_path / "videos")
    config = with_video_markup(make_config(regions={"source": "file"}), video)
    assert config.regions.source.value == "none"


def test_records_keep_markup(make_config: ConfigFactory, tmp_path: Path) -> None:
    video = _video(tmp_path / "videos")
    legacy_sidecar_file(video).write_text(json.dumps(_ZONES), encoding="utf-8")
    config = make_config(regions={"source": "file", "labels": ["CROP"], "id_field": "wz"})
    config.source.path = video
    config = with_video_markup(config, video)
    with Collector(config) as collector:
        [result] = collector.save(make_frame(1), "FULL")
        record = collector.catalog.get(result.sample_id or 0)
    assert record is not None and record.region_id == "1"
    assert record.markup_variant == "sidecar" and record.markup_version == 1
    assert record.markup_path == str(VersionStore(video).working_path)
    assert record.markup_hash is not None and len(record.markup_hash) == 64


def test_old_catalog_gets_markup_columns(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    with sqlite3.connect(root / CATALOG_FILENAME) as conn:
        conn.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY AUTOINCREMENT, collection TEXT NOT NULL, "
                     "status TEXT NOT NULL, hash INTEGER NOT NULL)")
    with Catalog(root):
        pass
    with sqlite3.connect(root / CATALOG_FILENAME) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(samples)")}
    assert {"markup_variant", "markup_path", "markup_version", "markup_hash"} <= columns
