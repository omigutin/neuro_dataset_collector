"""
Разметка видео из библиотеки neuro_markup_lab: какая разметка режет видео и откуда она взята.

Варианты (``regions.variant``): рядом с видео — ``<имя>.markup.json`` с историей версий; боевая — файл из папки
разметки (``regions.search_dir``), подобранный по имени и пути видео; явный файл (``regions.file``); ``auto`` —
рядом с видео, иначе боевая, иначе явный файл. Старый файл сборщика ``<имя>.json`` рядом с видео при первом
открытии переносится в разметку рядом с видео первой версией (сам файл не меняется).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from neuro_markup_lab.storage import (
    VERSIONS_DIR_SUFFIX,
    ProductionMarkupSource,
    SaveMode,
    VersionOrigin,
    VersionStore,
    file_hash,
    match_markup_key,
)

from ..config.models import CollectorConfig, MarkupChoice, RegionsConfig, RegionSource

__all__ = [
    "MarkupInfo",
    "describe_markup",
    "legacy_sidecar_file",
    "migrate_legacy_sidecar",
    "production_key",
    "resolve_markup",
    "uses_video_markup",
    "with_video_markup",
]

# Номер камеры в имени: прежнее правило сборщика, если подбор по частям имени файла ничего не дал
_CAMERA = re.compile(r"cam0*(\d+)", re.IGNORECASE)
# Номер версии в имени файла версии: v<N>_<время>.json
_VERSION_FILE = re.compile(r"^v(\d+)_")


@dataclass(frozen=True)
class MarkupInfo:
    """ Какой разметкой режется видео. """

    # sidecar — рядом с видео, production — боевая, file — явный файл
    variant: str
    path: Path
    # Версия разметки рядом с видео; None — не версия (боевая, явный файл или файл вне истории версий)
    version: int | None = None
    # Ключ боевой разметки (имя файла без .json)
    key: str | None = None

    @property
    def hash(self) -> str | None:
        """ Хэш содержимого файла разметки (SHA-256); None — файла нет. """
        return file_hash(self.path)

    def to_dict(self) -> dict[str, object]:
        return {"variant": self.variant, "path": str(self.path), "version": self.version, "key": self.key}


def legacy_sidecar_file(video: Path) -> Path:
    """ Старый файл разметки сборщика рядом с видео: то же имя, расширение .json. """
    return Path(video).with_suffix(".json")


def migrate_legacy_sidecar(video: Path) -> Path | None:
    """
        Переносит старый ``<имя>.json`` в разметку рядом с видео (первая версия «перенос из …»),
        если разметки рядом с видео ещё нет. Возвращает путь разметки рядом с видео или None.
    """
    legacy = legacy_sidecar_file(video)
    store = VersionStore(Path(video))
    if not legacy.is_file() or store.working_path.exists() or store.history().versions:
        return None
    try:
        document = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("shapes"), list):
        return None
    store.save(document, SaveMode.NEW_VERSION, comment=f"перенос из {legacy.name}", origin=VersionOrigin.INITIAL)
    return store.working_path


def production_key(video: Path, search_dir: Path | None) -> str | None:
    """
        Ключ боевой разметки видео в папке разметки: подбор библиотеки по частям имени и пути видео,
        иначе единственный файл с тем же номером камеры (прежнее правило сборщика).
    """
    if search_dir is None or not search_dir.is_dir():
        return None
    keys = ProductionMarkupSource(search_dir).catalog_keys()
    match = match_markup_key(Path(video), keys)
    if match.key:
        return match.key
    camera = _CAMERA.search(Path(video).stem)
    if camera is None:
        return None
    pattern = re.compile(rf"(^|[^0-9a-z])cam0*{int(camera.group(1))}([^0-9]|$)", re.IGNORECASE)
    found = [key for key in keys if pattern.search(key)]
    return found[0] if len(found) == 1 else None


def _sidecar(video: Path, version: int | None) -> MarkupInfo | None:
    store = VersionStore(video)
    history = store.history()
    if version is not None:
        chosen = history.get(version)
        return MarkupInfo("sidecar", store.directory / chosen.file, version) if chosen else None
    if not store.working_path.is_file():
        return None
    current = history.current if store.working_matches_current(history) else None
    return MarkupInfo("sidecar", store.working_path, current)


def _production(video: Path, search_dir: Path | None) -> MarkupInfo | None:
    key = production_key(video, search_dir)
    if key is None or search_dir is None:
        return None
    path = search_dir / f"{key}.json"
    return MarkupInfo("production", path, key=key) if path.is_file() else None


def _file(regions: RegionsConfig) -> MarkupInfo | None:
    return MarkupInfo("file", regions.file) if regions.file is not None and regions.file.is_file() else None


def resolve_markup(video: Path, regions: RegionsConfig) -> MarkupInfo | None:
    """ Разметка видео по настройкам ``regions.variant`` и ``regions.version``; None — не найдена. """
    video = Path(video)
    choice = regions.variant
    if choice == MarkupChoice.SIDECAR:
        return _sidecar(video, regions.version)
    if choice == MarkupChoice.PRODUCTION:
        return _production(video, regions.search_dir)
    if choice == MarkupChoice.FILE:
        return _file(regions)
    return _sidecar(video, regions.version) or _production(video, regions.search_dir) or _file(regions)


def describe_markup(path: Path | None, video: Path | None, search_dir: Path | None) -> MarkupInfo | None:
    """ Что за файл разметки ``path`` для видео: рядом с видео (и какая версия), боевая или явный файл. """
    if path is None:
        return None
    path = Path(path)
    if video is not None:
        store = VersionStore(Path(video))
        if path == store.working_path:
            history = store.history()
            return MarkupInfo("sidecar", path, history.current if store.working_matches_current(history) else None)
        version = _VERSION_FILE.match(path.name)
        if path.parent == store.directory and path.parent.name.endswith(VERSIONS_DIR_SUFFIX) and version:
            return MarkupInfo("sidecar", path, int(version.group(1)))
    if search_dir is not None and path.parent.resolve() == Path(search_dir).resolve():
        return MarkupInfo("production", path, key=path.stem)
    return MarkupInfo("file", path)


def uses_video_markup(regions: RegionsConfig) -> bool:
    """ Разметка подбирается для каждого видео (а не один явный файл на все видео). """
    return regions.source == RegionSource.FILE and regions.variant != MarkupChoice.FILE


def with_video_markup(config: CollectorConfig, video: Path) -> CollectorConfig:
    """
        Настройки с разметкой этого видео: переносит старый ``<имя>.json`` и подставляет файл разметки.
        Разметка не найдена — режется весь кадр (если разрешено ``fallback_to_full_frame``).
    """
    if not uses_video_markup(config.regions):
        return config
    migrate_legacy_sidecar(video)
    found = resolve_markup(video, config.regions)
    if found is not None:
        config.regions.file = found.path
    elif config.regions.fallback_to_full_frame:
        config.regions.source = RegionSource.NONE
    else:
        raise FileNotFoundError(f"Markup not found for video {video}")
    return config
