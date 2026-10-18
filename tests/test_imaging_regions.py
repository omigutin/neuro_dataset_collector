from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from conftest import random_image
from neuro_dataset_collector.config import RegionsConfig
from neuro_dataset_collector.core import BBox, Prediction, Region
from neuro_dataset_collector.imaging import crop_region, dhash, hamming_many, predictions_to_crop
from neuro_dataset_collector.regions import (
    FullFrameRegions,
    LabelMeRegions,
    create_region_provider,
    suggest_regions_file,
)


def test_crop_full_frame_when_no_region() -> None:
    image = random_image(1)
    crop = crop_region(image, None)
    assert crop is not None and crop.image is image


def test_crop_bbox_and_offset() -> None:
    image = random_image(1, 100, 60)
    crop = crop_region(image, Region.from_bbox("a", BBox(10, 5, 30, 25)))
    assert crop is not None
    assert (crop.offset_x, crop.offset_y, crop.width, crop.height) == (10, 5, 20, 20)


def test_crop_outside_returns_none() -> None:
    image = random_image(1, 100, 60)
    assert crop_region(image, Region.from_bbox("a", BBox(200, 200, 300, 300))) is None


def test_mask_outside_polygon() -> None:
    image = np.full((40, 40, 3), 200, dtype=np.uint8)
    triangle = Region("t", ((0, 0), (39, 0), (0, 39)))
    crop = crop_region(image, triangle, mask_outside=True)
    assert crop is not None
    assert crop.image[1, 1].tolist() == [200, 200, 200]
    assert crop.image[38, 38].tolist() == [0, 0, 0]


def test_predictions_to_crop_shift_and_clip() -> None:
    image = random_image(1, 100, 60)
    crop = crop_region(image, Region.from_bbox("a", BBox(10, 10, 50, 50)))
    assert crop is not None
    inside = Prediction("A", 0.9, BBox(20, 20, 30, 30))
    outside = Prediction("A", 0.9, BBox(80, 0, 90, 5))
    result = predictions_to_crop([inside, outside], crop)
    assert len(result) == 1 and result[0].bbox == BBox(10, 10, 20, 20)


def test_dhash_similarity() -> None:
    a = random_image(1)
    b = a.copy()
    b[0, 0] = 0
    c = random_image(2)
    ha, hb, hc = dhash(a), dhash(b), dhash(c)
    distances = hamming_many(ha, np.array([hb, hc], dtype=np.uint64))
    assert distances[0] <= 2 < distances[1]


def _labelme(tmp_path: Path) -> Path:
    data = {
        "imageWidth": 200,
        "imageHeight": 100,
        "shapes": [
            {"label": "crop", "points": [[10, 10], [50, 40]], "shape_type": "polygon", "wz": 1, "position": "left"},
            {"label": "crop", "points": [[60, 10], [90, 10], [90, 40]], "shape_type": "polygon", "wz": 2,
             "position": "right"},
            {"label": "input", "points": [[0, 0], [5, 5]], "shape_type": "line", "wz": 1},
            {"label": "crop", "points": [[100, 10], [150, 60]], "shape_type": "rectangle", "wz": 3},
        ],
    }
    path = tmp_path / "zones.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_labelme_filters_and_ids(tmp_path: Path) -> None:
    provider = LabelMeRegions(_labelme(tmp_path), labels=["crop"], id_field="wz")
    regions = provider.regions(200, 100)
    assert [r.id for r in regions] == ["1", "2", "3"]
    assert regions[0].attrs["position"] == "left"
    only_left = LabelMeRegions(_labelme(tmp_path), labels=["crop"], where={"position": "left"}, id_field="wz")
    assert [r.id for r in only_left.regions(200, 100)] == ["1"]


def test_labelme_scales_to_frame(tmp_path: Path) -> None:
    provider = LabelMeRegions(_labelme(tmp_path), labels=["crop"], id_field="wz")
    region = provider.regions(400, 200)[0]
    assert region.bbox == BBox(20, 20, 100, 80)


def test_missing_file_falls_back(tmp_path: Path) -> None:
    config = RegionsConfig(source="file", file=tmp_path / "nope.json")
    assert isinstance(create_region_provider(config), FullFrameRegions)
    strict = RegionsConfig(source="file", file=tmp_path / "nope.json", fallback_to_full_frame=False)
    with pytest.raises(FileNotFoundError):
        create_region_provider(strict)


def test_yolo_segment_and_unknown_label() -> None:
    from neuro_dataset_collector.config import TaskType
    from neuro_dataset_collector.storage import yolo_lines

    predictions = [
        Prediction("B", 0.9, polygon=((0, 0), (10, 0), (10, 10))),
        Prediction("A", 0.9, bbox=BBox(0, 0, 5, 5)),
        Prediction("Z", 0.9, bbox=BBox(0, 0, 5, 5)),
    ]
    lines = yolo_lines(predictions, ["A", "B"], 10, 10, TaskType.SEGMENT).splitlines()
    assert lines == ["1 0.000000 0.000000 1.000000 0.000000 1.000000 1.000000",
                     "0 0.000000 0.000000 0.500000 0.000000 0.500000 0.500000 0.000000 0.500000"]


def test_hash_index_grow_and_remove() -> None:
    from neuro_dataset_collector.catalog import HashIndex

    index = HashIndex(capacity=2)
    for i in range(5):
        index.add(i, i)
    assert len(index) == 5 and index.nearest(4) == (4, 0)
    index.remove(4)
    assert len(index) == 4 and index.nearest(4)[0] != 4
    assert HashIndex().nearest(1) is None


def test_labelme_labels_case_insensitive(tmp_path: Path) -> None:
    provider = LabelMeRegions(_labelme(tmp_path), labels=["CROP"], id_field="wz")
    assert [r.id for r in provider.regions(200, 100)] == ["1", "2", "3"]


def test_suggest_regions_file(tmp_path: Path) -> None:
    for name in ["norebo_cam25.json", "norebo_cam2.json", "norebo_cam250.json", "other.json"]:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert suggest_regions_file(Path("ml_cam25_22092026_1315.avi"), tmp_path) == tmp_path / "norebo_cam25.json"
    assert suggest_regions_file(Path("ml_cam2_x.avi"), tmp_path) == tmp_path / "norebo_cam2.json"
    assert suggest_regions_file(Path("ml_cam7_x.avi"), tmp_path) is None
    assert suggest_regions_file(Path("no_camera.avi"), tmp_path) is None
