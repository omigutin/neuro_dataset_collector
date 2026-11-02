""" Поставщики зон для вырезки. """

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from ..config.models import RegionsConfig, RegionSource
from ..core.types import AttrValue, Point, Region

__all__ = [
    "FullFrameRegions",
    "LabelMeRegions",
    "RegionProvider",
    "StaticRegions",
    "create_region_provider",
]

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
        ids: Sequence[str] = (),
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
        if ids:
            wanted = set(ids)
            self._regions = [region for region in self._regions if region.id in wanted]
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

    def ids(self) -> list[str]:
        """ Номера зон файла (после отбора по меткам и номерам). """
        return [region.id for region in self._regions]

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


def create_region_provider(config: RegionsConfig, api_regions: Sequence[Region] | None = None) -> RegionProvider:
    """ Создаёт поставщика зон по настройкам. """
    if config.source == RegionSource.NONE:
        return FullFrameRegions()
    if config.source == RegionSource.API:
        return StaticRegions(api_regions or [])
    if config.file is None or not config.file.exists():
        if config.fallback_to_full_frame:
            _log.warning("Regions file %s not found, the whole frame will be saved", config.file)
            return FullFrameRegions()
        raise FileNotFoundError(f"Regions file not found: {config.file}")
    return LabelMeRegions(config.file, config.labels, config.where, config.id_field, config.ids)
