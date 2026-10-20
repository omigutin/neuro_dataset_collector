""" Поставщики зон для вырезки. """

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from ..config.models import RegionsConfig, RegionSource
from ..core.types import AttrValue, Point, Region

__all__ = [
    "FullFrameRegions",
    "LabelMeRegions",
    "RegionProvider",
    "RegionsOrigin",
    "StaticRegions",
    "create_region_provider",
    "find_regions_file",
    "sidecar_regions_file",
    "suggest_regions_file",
    "write_labelme",
]

# Откуда взят файл разметки видео: рядом с видео или из папки разметки по номеру камеры.
RegionsOrigin = Literal["video", "camera"]

_log = logging.getLogger("neuro_dataset_collector")


class RegionProvider(Protocol):
    """ Даёт список зон для кадра заданного размера. Пустой список — значит, берётся весь кадр. """

    def regions(self, width: int, height: int) -> list[Region]:
        """ Зоны в координатах кадра. """
        ...


class FullFrameRegions:
    """ Без зон: сохраняется весь кадр. """

    def regions(self, width: int, height: int) -> list[Region]:
        """ Всегда пусто. """
        return []


class StaticRegions:
    """ Постоянный набор зон, переданный программой или заданный вручную. """

    def __init__(self, regions: Sequence[Region]) -> None:
        ids = [region.id for region in regions]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Region ids must be unique: {ids}")
        self._regions: list[Region] = list(regions)

    def regions(self, width: int, height: int) -> list[Region]:
        """ Возвращает заданные зоны. """
        return list(self._regions)


class LabelMeRegions:
    """
        Зоны из файла разметки LabelMe.
        Берутся фигуры типа polygon и rectangle; линии и точки пропускаются.
        Дополнительные поля фигуры (например, 'wz', 'position') становятся атрибутами зоны.
        Если размер видео отличается от размера в файле, координаты масштабируются.
    """

    _SKIP_KEYS = frozenset({"points", "shape_type", "label", "flags", "mask", "description"})

    def __init__(
        self,
        path: Path,
        labels: Sequence[str] = (),
        where: Mapping[str, AttrValue] | None = None,
        id_field: str | None = None,
    ) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "shapes" not in data:
            raise ValueError(f"Not a LabelMe file: {path}")
        self._path = Path(path)
        self._data: dict[str, Any] = data
        self._image_size: tuple[int, int] | None = None
        if data.get("imageWidth") and data.get("imageHeight"):
            self._image_size = (int(data["imageWidth"]), int(data["imageHeight"]))
        # Номер фигуры в файле для каждой зоны: нужен, чтобы записать исправленные координаты.
        self._shape_index: dict[str, int] = {}
        self._regions = self._parse(data["shapes"], list(labels), dict(where or {}), id_field, self._shape_index)
        if not self._regions:
            _log.warning("No suitable shapes found in %s (labels=%s, where=%s)", path, list(labels), where)

    @classmethod
    def _parse(
        cls,
        shapes: list[dict[str, object]],
        labels: list[str],
        where: dict[str, AttrValue],
        id_field: str | None,
        shape_index: dict[str, int],
    ) -> list[Region]:
        """ Отбирает подходящие фигуры и превращает их в зоны. """
        regions: list[Region] = []
        used_ids: set[str] = set()
        wanted = {name.lower() for name in labels}
        for index, shape in enumerate(shapes):
            label = str(shape.get("label", ""))
            if wanted and label.lower() not in wanted:
                continue
            attrs: dict[str, AttrValue] = {"label": label}
            for key, value in shape.items():
                if key not in cls._SKIP_KEYS and isinstance(value, str | int | float | bool | type(None)):
                    attrs[key] = value
            if any(str(attrs.get(key)) != str(value) for key, value in where.items()):
                continue
            polygon = cls._polygon(shape)
            if polygon is None:
                continue
            region_id = cls._make_id(attrs, id_field, label, index, used_ids)
            used_ids.add(region_id)
            shape_index[region_id] = index
            regions.append(Region(region_id, polygon, attrs))
        return regions

    @staticmethod
    def _polygon(shape: Mapping[str, object]) -> tuple[Point, ...] | None:
        """ Контур фигуры или None для неподходящих типов. """
        raw_points = shape.get("points")
        if not isinstance(raw_points, list):
            return None
        points = [(float(p[0]), float(p[1])) for p in raw_points if isinstance(p, list | tuple) and len(p) >= 2]
        shape_type = str(shape.get("shape_type", "polygon"))
        if shape_type == "rectangle" or (shape_type == "polygon" and len(points) == 2):
            # В LabelMe прямоугольник задаётся двумя углами.
            (xa, ya), (xb, yb) = points[0], points[1]
            x1, x2, y1, y2 = min(xa, xb), max(xa, xb), min(ya, yb), max(ya, yb)
            if x2 <= x1 or y2 <= y1:
                return None
            return (x1, y1), (x2, y1), (x2, y2), (x1, y2)
        if shape_type == "polygon" and len(points) >= 3:
            return tuple(points)
        return None

    @staticmethod
    def _make_id(attrs: Mapping[str, AttrValue], id_field: str | None, label: str, index: int, used: set[str]) -> str:
        """ Идентификатор зоны: значение поля id_field или «метка + номер». """
        value = attrs.get(id_field) if id_field else None
        base = str(value) if value is not None else f"{label}{index}"
        region_id, suffix = base, 1
        while region_id in used:
            region_id, suffix = f"{base}_{suffix}", suffix + 1
        return region_id

    def regions(self, width: int, height: int) -> list[Region]:
        """ Зоны, при необходимости масштабированные под размер кадра. """
        if self._image_size is None or self._image_size == (width, height):
            return list(self._regions)
        sx, sy = width / self._image_size[0], height / self._image_size[1]
        return [region.scaled(sx, sy) for region in self._regions]

    def shape_types(self) -> dict[str, str]:
        """ Тип фигуры каждой зоны: 'rectangle' или 'polygon'. """
        shapes = self._data["shapes"]
        return {region_id: "rectangle" if self._is_rectangle(shapes[index]) else "polygon"
                for region_id, index in self._shape_index.items()}

    @staticmethod
    def _is_rectangle(shape: Mapping[str, Any]) -> bool:
        """ Прямоугольник LabelMe: тип rectangle или многоугольник из двух углов. """
        points = shape.get("points")
        shape_type = str(shape.get("shape_type", "polygon"))
        return shape_type == "rectangle" or (shape_type == "polygon" and isinstance(points, list) and len(points) == 2)

    def edited(self, edits: Mapping[str, Sequence[Point]], width: int, height: int) -> dict[str, Any]:
        """
            Содержимое файла разметки с исправленными зонами.
            edits — новые контуры зон в координатах кадра width × height; остальные фигуры не меняются.
            Прямоугольник остаётся прямоугольником (записывается двумя углами).
        """
        data: dict[str, Any] = json.loads(json.dumps(self._data))
        image_w, image_h = self._image_size or (width, height)
        sx, sy = image_w / width, image_h / height
        for region_id, points in edits.items():
            index = self._shape_index.get(region_id)
            if index is None:
                raise ValueError(f"Unknown region: {region_id}")
            scaled = [(min(max(float(x) * sx, 0.0), image_w), min(max(float(y) * sy, 0.0), image_h))
                      for x, y in points]
            shape = data["shapes"][index]
            if self._is_rectangle(shape):
                xs, ys = [x for x, _ in scaled], [y for _, y in scaled]
                if not xs or max(xs) <= min(xs) or max(ys) <= min(ys):
                    raise ValueError(f"Region {region_id} has zero area")
                shape["points"] = [[min(xs), min(ys)], [max(xs), max(ys)]]
            else:
                if len(scaled) < 3:
                    raise ValueError(f"Region {region_id} must have at least 3 points")
                shape["points"] = [[x, y] for x, y in scaled]
        return data


def create_region_provider(config: RegionsConfig, api_regions: Sequence[Region] | None = None) -> RegionProvider:
    """ Создаёт поставщика зон по настройкам. """
    if config.source == RegionSource.NONE:
        return FullFrameRegions()
    if config.source == RegionSource.API:
        return StaticRegions(api_regions or [])
    assert config.file is not None
    if not config.file.exists():
        if config.fallback_to_full_frame:
            _log.warning("Regions file %s not found, the whole frame will be saved", config.file)
            return FullFrameRegions()
        raise FileNotFoundError(f"Regions file not found: {config.file}")
    return LabelMeRegions(config.file, config.labels, config.where, config.id_field)


def write_labelme(data: Mapping[str, Any], path: Path) -> None:
    """ Записывает файл разметки LabelMe (через временный файл, чтобы не оставить его испорченным). """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def sidecar_regions_file(video: Path) -> Path:
    """ Файл разметки рядом с видео: то же имя, расширение .json. """
    return Path(video).with_suffix(".json")


def find_regions_file(video: Path, search_dir: Path | None) -> tuple[Path, RegionsOrigin] | None:
    """
        Файл разметки для видео: сначала json рядом с видео (с тем же именем),
        затем файл с тем же номером камеры из папки разметки.
    """
    sidecar = sidecar_regions_file(video)
    if sidecar.is_file():
        return sidecar, "video"
    if search_dir is not None:
        suggestion = suggest_regions_file(video, search_dir)
        if suggestion is not None:
            return suggestion, "camera"
    return None


def suggest_regions_file(video: Path, search_dir: Path) -> Path | None:
    """
        Подсказка файла разметки для видео: json из search_dir, в имени которого тот же номер камеры
        (часть «cam<N>» в имени видео). None — если номера нет или подходящих файлов не ровно один.
    """
    match = re.search(r"cam(\d+)", video.stem, re.IGNORECASE)
    if match is None or not search_dir.is_dir():
        return None
    pattern = re.compile(rf"(^|[^0-9a-z])cam0*{int(match.group(1))}([^0-9]|$)", re.IGNORECASE)
    found = [path for path in sorted(search_dir.glob("*.json")) if pattern.search(path.stem)]
    return found[0] if len(found) == 1 else None
