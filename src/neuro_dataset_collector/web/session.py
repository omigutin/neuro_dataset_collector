""" Состояние веб-инструмента: настройки, плейлист, открытое видео, сборщик, фоновая автонарезка. """

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml

from .._cv import get_cv2
from ..catalog.catalog import CATALOG_FILENAME, Catalog
from ..config.loader import dump_config, load_config, resolve_paths, save_config
from ..config.models import DATASET_DIR_SUFFIX, CollectorConfig, RegionSource, TaskType
from ..core.results import ExportReport, SaveResult, SyncReport
from ..core.types import Frame, Point, Prediction, Region, SampleStatus
from ..export.roboflow import export_dataset
from ..pipeline.collector import FULL_FRAME_ID, Collector
from ..pipeline.model_run import JOURNAL_DIR, ModelRunReport, ZoneAnswer, run_model
from ..pipeline.offline import OfflineReport, collect_videos
from ..regions.providers import (
    LabelMeRegions,
    RegionsOrigin,
    ShapeKind,
    find_regions_file,
    sidecar_regions_file,
    suggest_regions_file,
    write_labelme,
)
from ..review.manual import reject_sample, relabel_sample, sample_files
from ..review.model_check import ModelCheckReport, check_with_model, cross_label_duplicates
from ..review.sync import sync_collection
from ..sources.video import VideoReader, list_videos
from ..storage.layout import IMAGES_DIR, SUSPECT_DIR
from .history import RejectionLog, sample_item
from .journal_view import JournalService
from .model_view import ModelView
from .state import SessionState, default_config_path, load_state, save_state

__all__ = ["AutoMode", "AutoRunState", "AutoScope", "CheckState", "WebSession"]

# Сколько последних позиций в видео помнить между запусками.
_MAX_POSITIONS = 500
_IMAGE_EXT = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})

# Автонарезка: только открытое видео или все видео плейлиста.
AutoScope = Literal["video", "playlist"]
# Что делает фоновый прогон: автонарезка по правилам или прогон моделью с записью журнала.
AutoMode = Literal["collect", "model"]
# Кадры, которые показываются в истории (удалённые и сбойные не показываются).
_HISTORY_STATUSES = (SampleStatus.PENDING, SampleStatus.SUSPECT, SampleStatus.VERIFIED)
# Сколько секунд зона подсвечивается после сохранения или отказа при показе автонарезки.
_MARK_SECONDS = 0.6


@dataclass
class AutoRunState:
    """ Ход фоновой автонарезки. """

    running: bool = False
    scope: str = "video"
    mode: str = "collect"
    video: str = ""
    video_path: str = ""
    frame: int = 0
    video_frames: int = 0
    index: int = 0
    total: int = 0
    saved: int = 0
    frames: int = 0
    matched: int = 0
    journal: str = ""
    summary: str = ""
    error: str = ""


@dataclass
class CheckState:
    """ Ход и итог проверки датасета моделью. """

    running: bool = False
    scope: str = "video"
    done: int = 0
    total: int = 0
    summary: str = ""
    error: str = ""
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WebSession:
    """
        Одно рабочее место: файл настроек, папка с видео (плейлист) и одно открытое видео.
        Всё это запоминается и восстанавливается при следующем запуске.
    """

    config_path: Path
    config: CollectorConfig = field(default_factory=CollectorConfig)
    playlist_dir: Path | None = None
    video: Path | None = None
    frame: int = 0
    _positions: dict[str, int] = field(default_factory=dict)
    _zones: dict[str, list[str]] = field(default_factory=dict)
    _reader: VideoReader | None = None
    _collector: Collector | None = None
    # Каталоги открываются один раз на корень: ручная нарезка и автонарезка пишут в одну историю
    # и видят дубли друг друга.
    _catalogs: dict[Path, Catalog] = field(default_factory=dict)
    _catalog_users: dict[Path, int] = field(default_factory=dict)
    _rejections: RejectionLog = field(default_factory=RejectionLog)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _auto: AutoRunState = field(default_factory=AutoRunState)
    _auto_stop: threading.Event = field(default_factory=threading.Event)
    _auto_thread: threading.Thread | None = None
    # Показ автонарезки: последний обработанный кадр, его зоны и итог по зонам.
    _auto_frame: Frame | None = None
    _auto_regions: list[Region] = field(default_factory=list)
    _auto_marks: dict[str, tuple[bool, float]] = field(default_factory=dict)
    _auto_answers: list[ZoneAnswer] = field(default_factory=list)
    _model: ModelView = field(default_factory=ModelView)
    _journals: JournalService = field(default_factory=JournalService)
    _check: CheckState = field(default_factory=CheckState)
    _check_stop: threading.Event = field(default_factory=threading.Event)
    _check_thread: threading.Thread | None = None

    # ---- запуск и сеанс -----------------------------------------------------------------------------

    @classmethod
    def open(cls, config_path: Path | None = None) -> WebSession:
        """
            Открывает сессию. Без пути — файл настроек прошлого сеанса или файл по умолчанию.
            Видео и кадр прошлого сеанса восстанавливаются, если файл видео на месте.
        """
        state = load_state()
        path = config_path or (Path(state.config_path) if state.config_path else default_config_path())
        session = cls(config_path=path.resolve())
        if session.config_path.exists():
            session.config = load_config(session.config_path)
        else:
            # Первый запуск: кадры рядом с видео, показывать каждый 5-й кадр.
            session.config.catalog.next_to_video = True
            session.config.source.step = 5
            session.config_path.parent.mkdir(parents=True, exist_ok=True)
            save_config(session.config, session.config_path)
        session._positions = dict(state.positions)
        session._zones = {key: list(value) for key, value in state.zones.items()}
        if state.playlist_dir and Path(state.playlist_dir).is_dir():
            session.playlist_dir = Path(state.playlist_dir)
        same_config = config_path is None or state.config_path == str(session.config_path)
        if same_config and state.video and Path(state.video).is_file():
            session.video, session.frame = Path(state.video), max(0, state.frame)
        session._save_state()
        return session

    def _save_state(self) -> None:
        if self.video is not None:
            self._positions.pop(str(self.video), None)
            self._positions[str(self.video)] = self.frame
            while len(self._positions) > _MAX_POSITIONS:
                self._positions.pop(next(iter(self._positions)))
        save_state(SessionState(
            config_path=str(self.config_path),
            playlist_dir=str(self.playlist_dir) if self.playlist_dir else None,
            video=str(self.video) if self.video else None,
            frame=self.frame,
            positions=dict(self._positions),
            zones=dict(self._zones),
        ))

    def remember_frame(self, frame: int) -> None:
        """ Запоминает текущий кадр (для продолжения с того же места). """
        self.frame = max(0, int(frame))
        self._save_state()

    # ---- настройки ----------------------------------------------------------------------------------

    def effective_config(self) -> CollectorConfig:
        """ Настройки с учётом открытого видео (папка датасета рядом с видео) и выбранных зон. """
        config = self.config.for_video(self.video) if self.video else self.config.model_copy(deep=True)
        return self._with_zones(config)

    def _with_zones(self, config: CollectorConfig) -> CollectorConfig:
        """
            Подставляет выбор зон для файла разметки: сохранённый для этой камеры,
            иначе фигуры с метками из настроек, иначе все фигуры.
        """
        regions = config.regions
        if regions.source != RegionSource.FILE or regions.file is None or not regions.file.is_file():
            return config
        chosen = self._zones.get(str(regions.file))
        if chosen is None:
            chosen = LabelMeRegions(regions.file, regions.labels, regions.where, regions.id_field).ids()
        regions.labels = []
        regions.ids = list(chosen)
        return config

    def zone_shapes(self) -> list[dict[str, Any]]:
        """ Все фигуры файла разметки в координатах кадра: какие режем, а какие нет. """
        labelme = self._labelme()
        if labelme is None or self.video is None:
            return []
        first = self._get_reader().read(0)
        if first is None:
            return []
        selected = set(self.effective_config().regions.ids)
        kinds = labelme.shape_types()
        return [{"id": r.id, "label": str(r.attrs.get("label", "")), "polygon": [list(p) for p in r.polygon],
                 "shape": kinds.get(r.id, "polygon"), "selected": r.id in selected}
                for r in labelme.regions(first.width, first.height)]

    def set_zones(self, ids: list[str]) -> dict[str, Any]:
        """ Какие зоны резать в этой камере (запоминается за файлом разметки). """
        with self._lock:
            labelme = self._labelme()
            if labelme is None:
                raise ValueError("Regions are taken from a LabelMe file only")
            known = set(labelme.ids())
            unknown = sorted(set(ids) - known)
            if unknown:
                raise ValueError(f"Unknown regions: {unknown}")
            if not ids:
                raise ValueError("Choose at least one region")
            self._close_collector()
            self._zones[str(self.config.regions.file)] = [i for i in ids if i in known]
            self._save_state()
        return self.video_info()

    def config_yaml(self) -> str:
        """ Настройки текстом YAML. """
        return dump_config(self.config)

    def update_config(self, data: dict[str, Any]) -> CollectorConfig:
        """ Проверяет, применяет и сохраняет настройки; сборщик пересоздаётся при следующем действии. """
        config = resolve_paths(CollectorConfig.model_validate(data), self.config_path.parent)
        with self._lock:
            self._close_collector(check_auto=True)
            self.config = config
            save_config(config, self.config_path)
        return config

    def update_config_yaml(self, text: str) -> CollectorConfig:
        """ То же, но из текста YAML. """
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("Config root must be a mapping")
        return self.update_config(data)

    def load_config_file(self, path: Path) -> CollectorConfig:
        """ Открывает другой файл настроек и делает его текущим. """
        config = load_config(path)
        with self._lock:
            self._close_collector(check_auto=True)
            self.config_path = path.resolve()
            self.config = config
            self._save_state()
        return config

    def save_config_as(self, path: Path) -> None:
        """ Сохраняет настройки в указанный файл и делает его текущим. """
        save_config(self.config, path)
        self.config_path = path.resolve()
        self._save_state()

    # ---- сборщик ------------------------------------------------------------------------------------

    def collector(self) -> Collector:
        """ Сборщик для текущих настроек и видео (создаётся при первом обращении). """
        with self._lock:
            if self._collector is None:
                config = self.effective_config()
                catalog = self._acquire_catalog(config.catalog.root)
                try:
                    self._collector = Collector(config, catalog=catalog)
                except Exception:
                    self._release_catalog(config.catalog.root)
                    raise
                video = str(self.video) if self.video else ""
                self._collector.add_rejection_listener(lambda result, image: self._rejections.add(video, result, image))
            return self._collector

    def _close_collector(self, check_auto: bool = False) -> None:
        """ Закрывает сборщик открытого видео. check_auto — запретить, пока идёт автонарезка (смена настроек). """
        if check_auto and self._auto.running:
            raise RuntimeError("Stop automatic collection before changing settings")
        if self._collector is not None:
            root = self._collector.config.catalog.root
            self._collector.close()
            self._collector = None
            self._release_catalog(root)

    def _acquire_catalog(self, root: Path) -> Catalog:
        """ Общий каталог корня (открывается при первом обращении). """
        key = Path(root).resolve()
        with self._lock:
            if key not in self._catalogs:
                self._catalogs[key] = Catalog(key)
            self._catalog_users[key] = self._catalog_users.get(key, 0) + 1
            return self._catalogs[key]

    def _release_catalog(self, root: Path) -> None:
        """ Закрывает каталог, когда он больше никому не нужен. """
        key = Path(root).resolve()
        with self._lock:
            users = self._catalog_users.get(key, 0) - 1
            if users > 0:
                self._catalog_users[key] = users
                return
            self._catalog_users.pop(key, None)
            catalog = self._catalogs.pop(key, None)
            if catalog is not None:
                catalog.close()

    def result_to_dict(self, result: SaveResult) -> dict[str, Any]:
        """ Результат для передачи в браузер. """
        return {
            "ok": result.ok,
            "message": result.message,
            "region": result.region_id or FULL_FRAME_ID,
            "label": result.label,
            "reason": result.reason.value if result.reason else None,
            "path": str(result.path) if result.path else None,
            "similar_to": str(result.similar_to) if result.similar_to else None,
            "distance": result.distance,
        }

    # ---- история -------------------------------------------------------------------------------------

    def history(self, video: Path, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """
            История видео: сохранённые кадры из каталога (постранично, новые первыми)
            и, на первой странице, отказы текущего запуска.
        """
        config = self.config_for_video(video)
        records, total = [], 0
        if (config.catalog.root / CATALOG_FILENAME).exists():
            catalog = self._acquire_catalog(config.catalog.root)
            try:
                found, total = catalog.page(config.catalog.collection, video.stem,
                                            _HISTORY_STATUSES, offset, limit)
                records = [sample_item(record, str(catalog.absolute(record))) for record in found]
            finally:
                self._release_catalog(config.catalog.root)
        rejected = self._rejections.items(str(video)) if offset == 0 else []
        items = sorted(records + rejected, key=lambda item: item["time"], reverse=True)
        return {"video": str(video), "total": total, "rejected": len(rejected), "offset": offset,
                "items": items, "classes": config.classes, "task": config.task.value}

    def rejected_image(self, rejection_id: int) -> bytes:
        """ Картинка выреза, который не сохранён. """
        return self._rejections.image(rejection_id)

    def _with_catalog(self, video: Path) -> tuple[CollectorConfig, Catalog]:
        """ Настройки и каталог видео; файлы открытого видео перед этим дописываются. """
        config = self.config_for_video(video)
        if not (config.catalog.root / CATALOG_FILENAME).exists():
            raise FileNotFoundError(f"No saved frames for {video.name}")
        if self._collector is not None:
            self._collector.flush()
        return config, self._acquire_catalog(config.catalog.root)

    def relabel(self, video: Path, sample_id: int, label: str) -> dict[str, Any]:
        """ Переносит сохранённый кадр видео в другой класс. """
        with self._lock:
            config, catalog = self._with_catalog(video)
            try:
                target = relabel_sample(config, catalog, sample_id, label)
            finally:
                self._release_catalog(config.catalog.root)
        return {"path": str(target), "label": label}

    def delete_sample(self, video: Path, sample_id: int) -> dict[str, Any]:
        """ Удаляет сохранённый кадр видео в корзину и отмечает его отклонённым. """
        from send2trash import send2trash

        with self._lock:
            config, catalog = self._with_catalog(video)
            try:
                files = sample_files(config, catalog, sample_id)
                for file in files:
                    send2trash(str(file))
                reject_sample(catalog, sample_id)
            finally:
                self._release_catalog(config.catalog.root)
        return {"deleted": [str(file) for file in files]}

    def open_dataset_folder(self, video: Path) -> Path:
        """ Открывает папку датасета видео в проводнике (на компьютере, где запущена программа). """
        folder = self.config_for_video(video).collection_dir
        if not folder.is_dir():
            raise FileNotFoundError(f"No dataset folder yet: {folder}")
        _open_in_explorer(folder)
        return folder

    # ---- видео и зоны --------------------------------------------------------------------------------

    def open_video(self, path: Path) -> dict[str, Any]:
        """
            Открывает видео: своя папка датасета (при next_to_video) и файл зон —
            json рядом с видео, иначе файл с тем же номером камеры из папки разметки.
            Кадр — тот, на котором остановились в этом видео в прошлый раз.
            Видео не из папки плейлиста — плейлистом становится папка этого видео.
        """
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Video not found: {path}")
        reader = VideoReader(path)
        found: tuple[Path, RegionsOrigin] | None = None
        with self._lock:
            self._close_collector()
            self._close_reader()
            self._reader = reader
            if self.video != path:
                self.frame = max(0, self._positions.get(str(path), 0))
            self.video = path
            playlist_changed = self.playlist_dir is None or self.playlist_dir not in path.parents
            if playlist_changed:
                self.playlist_dir = path.parent
            found = find_regions_file(path, self.config.regions.search_dir)
            if found is not None and (found[0] != self.config.regions.file
                                      or self.config.regions.source != RegionSource.FILE):
                self._use_regions_file(found[0])
            self._save_state()
        info = self.video_info()
        info["suggested_regions"] = str(found[0]) if found is not None and found[1] == "camera" else None
        info["playlist_changed"] = playlist_changed
        return info

    def _use_regions_file(self, path: Path) -> None:
        """ Делает файл зон текущим и сохраняет настройки. """
        # Сначала файл, потом источник: проверка настроек требует файл при source=file.
        self.config.regions.file = path
        self.config.regions.source = RegionSource.FILE
        save_config(self.config, self.config_path)

    def set_regions_file(self, path: Path | None) -> dict[str, Any]:
        """ Задаёт файл зон (None — весь кадр) и сохраняет настройки. """
        with self._lock:
            self._close_collector()
            if path is None:
                self.config.regions.source = RegionSource.NONE
            else:
                if not path.is_file():
                    raise FileNotFoundError(f"Regions file not found: {path}")
                self.config.regions.file = path.resolve()
                self.config.regions.source = RegionSource.FILE
            save_config(self.config, self.config_path)
        return self.video_info()

    def _close_reader(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def _get_reader(self) -> VideoReader:
        if self._reader is None:
            if self.video is None:
                raise FileNotFoundError("Video is not opened")
            self._reader = VideoReader(self.video)
        return self._reader

    def video_info(self) -> dict[str, Any]:
        """ Сведения о видео, его зонах и папке датасета. """
        with self._lock:
            effective = self.effective_config()
            regions_file = self.config.regions.file
            info: dict[str, Any] = {
                "video": str(self.video) if self.video else None,
                "frame": self.frame,
                "dataset_dir": str(effective.collection_dir),
                "regions_file": str(regions_file) if regions_file else None,
                "regions_source": self.config.regions.source.value,
                "regions_origin": self._regions_origin(),
            }
            if self.video is None:
                return info
            reader = self._get_reader()
            first = reader.read(0)
            regions = self.collector().regions_for(first.width, first.height) if first else []
            shapes = self._shape_types()
            info["shapes"] = self.zone_shapes()
            info.update({
                "name": reader.path.name,
                "fps": reader.fps,
                "frames": reader.frame_count,
                "width": first.width if first else 0,
                "height": first.height if first else 0,
                "regions": [{"id": r.id, "polygon": [list(p) for p in r.polygon], "shape": shapes.get(r.id, "polygon")}
                            for r in regions],
                "regions_editable": bool(shapes),
                "regions_sidecar": str(sidecar_regions_file(self.video)),
            })
            return info

    def _regions_origin(self) -> RegionsOrigin | None:
        """ Откуда взят текущий файл зон: рядом с видео или по номеру камеры. """
        file = self.config.regions.file
        if self.video is None or file is None or self.config.regions.source != RegionSource.FILE:
            return None
        if file == sidecar_regions_file(self.video):
            return "video"
        search_dir = self.config.regions.search_dir
        if search_dir is not None and file == suggest_regions_file(self.video, search_dir):
            return "camera"
        return None

    def _labelme(self) -> LabelMeRegions | None:
        """ Текущий файл зон (LabelMe), если зоны берутся из файла. """
        regions = self.config.regions
        if regions.source != RegionSource.FILE or regions.file is None or not regions.file.is_file():
            return None
        return LabelMeRegions(regions.file, (), regions.where, regions.id_field)

    def _shape_types(self) -> dict[str, str]:
        labelme = self._labelme()
        return labelme.shape_types() if labelme is not None else {}

    def edit_regions(self, edits: dict[str, list[Point]], shapes: dict[str, ShapeKind] | None = None) -> dict[str, Any]:
        """
            Применяет исправленные зоны (координаты кадра; shapes — новый тип фигуры, если менялся):
            файл разметки с ними сохраняется рядом с видео под его именем и становится текущим.
            Исходный файл не меняется.
        """
        with self._lock:
            if self.video is None:
                raise FileNotFoundError("Video is not opened")
            labelme = self._labelme()
            if labelme is None:
                raise ValueError("Regions are taken from a LabelMe file only")
            first = self._get_reader().read(0)
            if first is None:
                raise ValueError("Video has no frames")
            data = labelme.edited(edits, first.width, first.height, shapes)
            target = sidecar_regions_file(self.video)
            write_labelme(data, target)
            self._close_collector()
            self._use_regions_file(target)
        return self.video_info()

    def regions_save_target(self) -> Path | None:
        """ Куда предложить сохранить разметку: файл этой камеры в папке разметки (его можно перезаписать). """
        if self.video is None:
            return None
        search_dir = self.config.regions.search_dir
        if search_dir is not None:
            camera_file = suggest_regions_file(self.video, search_dir)
            return camera_file or search_dir / sidecar_regions_file(self.video).name
        return self.config.regions.file

    def save_regions_as(self, path: Path) -> Path:
        """ Сохраняет текущий файл зон в указанный файл (например, в папку разметки камер). """
        source = self.config.regions.file
        if self.config.regions.source != RegionSource.FILE or source is None or not source.is_file():
            raise ValueError("There is no regions file to save")
        if path.suffix.lower() != ".json":
            raise ValueError(f"Regions file must be .json: {path}")
        path = path.resolve()
        if path != source.resolve():
            write_labelme(json.loads(source.read_text(encoding="utf-8")), path)
        return path

    def frame_at(self, index: int) -> Frame | None:
        """ Кадр открытого видео. """
        with self._lock:
            return self._get_reader().read(index)

    def render(self, frame: Frame, max_width: int = 1280, zones: bool = True, model: bool = False) -> bytes:
        """
            JPEG кадра для показа в браузере; zones — нарисовать зоны (без них — для правки зон),
            model — нарисовать ответы модели по зонам.
        """
        image = frame.image.copy()
        if model:
            regions = self.collector().regions_for(frame.width, frame.height)
            ModelView.draw(image, regions, self._answers(frame, regions), self.config.classes)
        elif zones:
            selected = self.collector().regions_for(frame.width, frame.height)
            chosen = {region.id for region in selected}
            labelme = self._labelme()
            # Фигуры, которые не режем, — серым: их можно включить щелчком.
            for region in labelme.regions(frame.width, frame.height) if labelme is not None else []:
                if region.id not in chosen:
                    self._draw_region(image, region, (150, 150, 150))
            for region in selected:
                self._draw_region(image, region)
        return self._encode(image, max_width)

    @staticmethod
    def _encode(image: np.ndarray, max_width: int) -> bytes:
        """ JPEG для браузера, уменьшенный до max_width. """
        cv2 = get_cv2()
        if image.shape[1] > max_width:
            scale = max_width / image.shape[1]
            image = cv2.resize(image, (max_width, int(image.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise OSError("Failed to encode frame")
        return bytes(buffer.tobytes())

    @staticmethod
    def _draw_region(image: np.ndarray, region: Region, color: tuple[int, int, int] = (0, 255, 0),
                     filled: bool = False) -> None:
        cv2 = get_cv2()
        points = np.array(region.polygon, dtype=np.int32)
        if filled:
            overlay = image.copy()
            cv2.fillPoly(overlay, [points], color)
            cv2.addWeighted(overlay, 0.3, image, 0.7, 0, dst=image)
        cv2.polylines(image, [points], True, color, 3 if filled else 2)
        x, y = int(region.bbox.x1), int(region.bbox.y1)
        cv2.putText(image, region.id, (x + 4, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

    def _answers(self, frame: Frame, regions: list[Region]) -> list[ZoneAnswer]:
        """ Ответы модели по зонам кадра открытого видео. """
        return self._model.answers(self.effective_config(), str(self.video or ""), frame, regions)

    def model_answers(self, index: int) -> dict[str, Any]:
        """ Ответы модели по зонам кадра открытого видео. """
        with self._lock:
            frame = self._get_reader().read(index)
            if frame is None:
                raise IndexError(f"Frame {index} not found")
            regions = self.collector().regions_for(frame.width, frame.height)
            answers = self._answers(frame, regions)
        return {"frame": index, "answers": ModelView.to_dict(answers)}

    def save(self, index: int, label: str | None, region_ids: list[str] | None, force: bool,
             model: bool = False) -> list[SaveResult]:
        """ Ручное сохранение кадра (все или выбранные зоны); model — записать с кадром ответ модели. """
        with self._lock:
            frame = self._get_reader().read(index)
            if frame is None:
                raise IndexError(f"Frame {index} not found")
            collector = self.collector()
            predictions = None
            if model:
                answers = self._answers(frame, collector.regions_for(frame.width, frame.height))
                predictions = ModelView.predictions_by_zone(answers)
            results = collector.save(frame, label, region_ids=region_ids, force=force, predictions=predictions)
            collector.flush()
            self.remember_frame(index)
            return results

    # ---- автонарезка, сверка, выгрузка ---------------------------------------------------------------

    def auto_state(self) -> AutoRunState:
        """ Ход автонарезки. """
        return self._auto

    def start_auto(self, scope: AutoScope = "video", mode: AutoMode = "collect") -> None:
        """
            Запускает в фоне автонарезку или прогон моделью (mode="model": журнал ответов модели и,
            по настройкам, сохранение вырезов, где модель не прошла условия): открытого видео или всего плейлиста.
        """
        with self._lock:
            if self._auto.running:
                return
            if self._check.running:
                raise RuntimeError("Wait for the dataset check to finish")
            if scope == "playlist":
                videos = self.playlist_videos()
                if not videos:
                    raise FileNotFoundError("Playlist is empty: choose a folder with videos")
            else:
                if self.video is None:
                    raise FileNotFoundError("Video is not opened")
                videos = [self.video]
            if mode == "model" and self.config.model.path is None:
                raise ValueError("Model is not set: choose a model file in settings")
            predictor = self._model.predictor(self.config) if self.config.model.path is not None else None
            self._auto = AutoRunState(running=True, scope=scope, mode=mode, total=len(videos))
            self._auto_stop.clear()

        def worker() -> None:
            total = OfflineReport()
            model_total = ModelRunReport()
            try:
                for number, video in enumerate(videos, start=1):
                    if self._auto_stop.is_set():
                        break
                    self._auto.video, self._auto.video_path, self._auto.index = video.name, str(video), number
                    if mode == "model" and predictor is not None:
                        done = self._model_one(video, predictor)
                        model_total.frames += done.frames
                        model_total.rows += done.rows
                        model_total.matched += done.matched
                        model_total.saved += done.saved
                        self._auto.journal = str(done.journal or "")
                        continue
                    report = self._collect_one(video, predictor)
                    total.videos += report.videos
                    total.frames += report.frames
                    total.saved += report.saved
                    total.rejected_by_reason.update(report.rejected_by_reason)
                    self._auto.frames = total.frames
            except Exception as error:  # Ошибка показывается в интерфейсе, поток не должен падать молча.
                self._auto.error = str(error)
            finally:
                self._auto.summary = model_total.summary() if mode == "model" else total.summary()
                self._auto.running = False

        self._auto_thread = threading.Thread(target=worker, name="ndc-auto", daemon=True)
        self._auto_thread.start()

    def _collect_one(self, video: Path, predictor: Any) -> OfflineReport:
        """ Автонарезка одного видео своим сборщиком (общий каталог с ручной нарезкой). """
        config = self.config_for_video(video)
        try:
            with VideoReader(video) as reader:
                self._auto.video_frames = reader.frame_count
        except OSError:
            self._auto.video_frames = 0
        catalog = self._acquire_catalog(config.catalog.root)
        try:
            with Collector(config, catalog=catalog) as collector:
                self._auto_regions, self._auto_marks = [], {}

                def on_frame(frame: Frame) -> None:
                    if self._auto_frame is None or not self._auto_regions:
                        self._auto_regions = collector.regions_for(frame.width, frame.height)
                    self._auto_frame = frame
                    self._auto.frame = frame.index

                def on_result(result: SaveResult) -> None:
                    if result.ok:
                        self._auto.saved += 1
                    self._auto_marks[result.region_id or FULL_FRAME_ID] = (result.ok, time.monotonic())

                return collect_videos(collector, [video], predictor=predictor, on_result=on_result,
                                      stop=self._auto_stop, on_frame=on_frame)
        finally:
            self._release_catalog(config.catalog.root)

    def _model_one(self, video: Path, predictor: Any) -> ModelRunReport:
        """ Прогон одного видео моделью: журнал в папке датасета, вырезы — по условиям из настроек. """
        config = self.config_for_video(video)
        try:
            with VideoReader(video) as reader:
                self._auto.video_frames = reader.frame_count
        except OSError:
            self._auto.video_frames = 0
        catalog = self._acquire_catalog(config.catalog.root)
        try:
            with Collector(config, catalog=catalog) as collector:
                self._auto_regions, self._auto_marks, self._auto_answers = [], {}, []

                def on_frame(frame: Frame, answers: list[ZoneAnswer]) -> None:
                    if self._auto_frame is None or not self._auto_regions:
                        self._auto_regions = collector.regions_for(frame.width, frame.height)
                    self._auto_frame, self._auto_answers = frame, answers
                    self._auto.frame = frame.index
                    self._auto.frames += 1

                def on_result(result: SaveResult) -> None:
                    if result.ok:
                        self._auto.saved += 1
                    self._auto_marks[result.region_id or FULL_FRAME_ID] = (result.ok, time.monotonic())

                def on_match(frame: Frame, answer: ZoneAnswer, reason: str) -> None:
                    self._auto.matched += 1

                model_name = config.model.path.name if config.model.path is not None else ""
                return run_model(collector, video, predictor, model_name=model_name, stop=self._auto_stop,
                                 on_frame=on_frame, on_result=on_result, on_match=on_match)
        finally:
            self._release_catalog(config.catalog.root)

    def auto_frame(self, max_width: int = 1280) -> bytes | None:
        """
            Кадр, который сейчас обрабатывает автонарезка: зоны зелёные — только что сохранены,
            красные — только что отказ, жёлтые — без изменений.
        """
        frame = self._auto_frame
        if frame is None:
            return None
        cv2 = get_cv2()
        image = frame.image.copy()
        now = time.monotonic()
        if self._auto.mode == "model":
            ModelView.draw(image, self._auto_regions, self._auto_answers, self.config.classes)
            return self._encode(image, max_width)
        for region in self._auto_regions:
            mark = self._auto_marks.get(region.id)
            fresh = mark is not None and now - mark[1] <= _MARK_SECONDS
            color = ((0, 255, 0) if mark and mark[0] else (0, 0, 255)) if fresh else (0, 200, 255)
            self._draw_region(image, region, color, filled=fresh)
        if not self._auto_regions:
            mark = self._auto_marks.get(FULL_FRAME_ID)
            if mark is not None and now - mark[1] <= _MARK_SECONDS:
                h, w = image.shape[:2]
                cv2.rectangle(image, (2, 2), (w - 3, h - 3), (0, 255, 0) if mark[0] else (0, 0, 255), 6)
        return self._encode(image, max_width)

    def config_for_video(self, video: Path) -> CollectorConfig:
        """
            Настройки для видео: его папка датасета, его файл зон (рядом с видео или по номеру камеры)
            и выбранные в этой камере зоны.
        """
        config = self.config.for_video(video)
        found = find_regions_file(video, config.regions.search_dir)
        if found is not None:
            config.regions.file = found[0]
            config.regions.source = RegionSource.FILE
        return self._with_zones(config)

    def stop_auto(self) -> None:
        """ Останавливает автонарезку. """
        self._auto_stop.set()
        if self._auto_thread is not None:
            self._auto_thread.join(timeout=30)

    # ---- плейлист ------------------------------------------------------------------------------------

    def set_playlist(self, folder: Path | None) -> dict[str, Any]:
        """ Задаёт папку с видео (None — убрать плейлист). """
        if folder is not None and not folder.is_dir():
            raise FileNotFoundError(f"Folder not found: {folder}")
        with self._lock:
            self.playlist_dir = folder.resolve() if folder is not None else None
            self._save_state()
        return self.playlist()

    def playlist_videos(self) -> list[Path]:
        """ Видео папки плейлиста и её подпапок (без папок датасетов «…_dataset»). """
        if self.playlist_dir is None or not self.playlist_dir.is_dir():
            return []
        videos = list_videos(self.playlist_dir, self.config.source.extensions, recursive=True)
        return [v for v in videos if not any(part.endswith(DATASET_DIR_SUFFIX)
                                             for part in v.relative_to(self.playlist_dir).parts[:-1])]

    def playlist(self) -> dict[str, Any]:
        """ Плейлист для интерфейса: видео, сколько кадров уже сохранено, откуда возьмутся зоны. """
        search_dir = self.config.regions.search_dir
        items: list[dict[str, Any]] = []
        for video in self.playlist_videos():
            found = find_regions_file(video, search_dir)
            labels = self._label_counts(video)
            items.append({
                "path": str(video),
                "name": str(video.relative_to(self.playlist_dir)) if self.playlist_dir else video.name,
                "saved": sum(labels.values()) if labels is not None else None,
                "labels": labels,
                "rejected": self._rejections.count(str(video)),
                "regions": found[1] if found is not None else None,
            })
        return {"dir": str(self.playlist_dir) if self.playlist_dir else None, "items": items,
                "current": str(self.video) if self.video else None}

    def _label_counts(self, video: Path) -> dict[str, int] | None:
        """
            Сколько картинок в папке датасета видео по папкам классов (классификация)
            или всего (детекция, сегментация). Только при «рядом с видео»: иначе папка общая для всех видео.
        """
        if not self.config.catalog.next_to_video:
            return None
        folder = self.config.for_video(video).collection_dir
        if not folder.is_dir():
            return {}

        def images(path: Path, recursive: bool) -> int:
            files = path.rglob("*") if recursive else path.iterdir()
            return sum(1 for p in files if p.suffix.lower() in _IMAGE_EXT and p.is_file())

        if self.config.task != TaskType.CLASSIFY:
            target = folder / IMAGES_DIR
            return {IMAGES_DIR: images(target, False)} if target.is_dir() else {}
        counts: dict[str, int] = {}
        for child in sorted(folder.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                count = images(child, child.name == SUSPECT_DIR)
                if count:
                    counts[child.name] = count
        return counts

    # ---- итог, сверка, выгрузка ----------------------------------------------------------------------

    def _scope_videos(self, scope: AutoScope) -> list[Path]:
        """ Видео для итога: открытое или весь плейлист. """
        if scope == "playlist":
            videos = self.playlist_videos()
            if not videos:
                raise FileNotFoundError("Playlist is empty: choose a folder with videos")
            return videos
        if self.video is None:
            raise FileNotFoundError("Video is not opened")
        return [self.video]

    def _collections(self, scope: AutoScope) -> list[CollectorConfig]:
        """ Разные датасеты (корень + коллекция) видео из выбранного набора, у которых уже есть история. """
        seen: dict[tuple[Path, str], CollectorConfig] = {}
        for video in self._scope_videos(scope):
            config = self.config_for_video(video)
            key = (config.catalog.root.resolve(), config.catalog.collection)
            if key not in seen and (config.catalog.root / CATALOG_FILENAME).exists():
                seen[key] = config
        if self._collector is not None:
            self._collector.flush()
        return list(seen.values())

    def sync(self, scope: AutoScope = "video") -> SyncReport:
        """ Сверка после ручной проверки: открытое видео или все видео плейлиста. """
        total = SyncReport()
        with self._lock:
            for config in self._collections(scope):
                catalog = self._acquire_catalog(config.catalog.root)
                try:
                    total.merge(sync_collection(config, catalog))
                finally:
                    self._release_catalog(config.catalog.root)
        return total

    def export(self, out_dir: Path, only_new: bool, include_pending: bool, full_frames: bool = False,
               scope: AutoScope = "video") -> ExportReport:
        """ Выгрузка проверенных кадров в одну папку: открытого видео или всех видео плейлиста. """
        total = ExportReport(out_dir=out_dir)
        with self._lock:
            for config in self._collections(scope):
                catalog = self._acquire_catalog(config.catalog.root)
                try:
                    total.merge(export_dataset(config, out_dir, only_new=only_new, include_pending=include_pending,
                                               full_frames=full_frames, catalog=catalog))
                finally:
                    self._release_catalog(config.catalog.root)
        return total

    def start_check(self, scope: AutoScope = "video", min_conf: float = 0.0, threshold: int = 3) -> None:
        """
            Проверка датасета моделью в фоне: где модель не согласна с классом кадра (или уверена меньше min_conf)
            и почти одинаковые кадры в разных классах (отличие отпечатков не больше threshold).
        """
        with self._lock:
            if self._check.running:
                return
            if self._auto.running:
                raise RuntimeError("Stop automatic collection before checking the dataset")
            configs = self._collections(scope)
            if not configs:
                raise FileNotFoundError("No saved frames to check")
            predictor = self._model.predictor(self.config)
            source = self.video.stem if scope == "video" and self.video is not None else None
            self._check = CheckState(running=True, scope=scope)
            self._check_stop.clear()

        def worker() -> None:
            total = ModelCheckReport()
            try:
                for config in configs:
                    video = str(config.source.path or "")
                    catalog = self._acquire_catalog(config.catalog.root)
                    try:
                        def progress(done: int, count: int) -> None:
                            self._check.done, self._check.total = done, count

                        report = check_with_model(config, catalog, predictor, min_conf=min_conf, source=source,
                                                  stop=self._check_stop, on_progress=progress)
                        for pair in cross_label_duplicates(config, catalog, threshold, source=source):
                            self._check.duplicates.append({"video": video, "distance": pair.distance, "items": [
                                {"id": r.id, "label": r.label, "path": str(catalog.absolute(r)), "source": r.source,
                                 "frame": r.frame_index, "region": r.region_id} for r in (pair.first, pair.second)]})
                    finally:
                        self._release_catalog(config.catalog.root)
                    total.checked += report.checked
                    total.agreed += report.agreed
                    total.failed += report.failed
                    self._check.disagreements.extend(
                        {"id": d.sample_id, "video": video, "path": str(d.path), "label": d.label,
                         "model_label": d.model_label, "conf": d.conf, "status": d.status, "source": d.source,
                         "frame": d.frame, "region": d.region} for d in report.disagreements)
                    total.disagreements.extend(report.disagreements)
                    if self._check_stop.is_set():
                        break
            except Exception as error:  # Ошибка показывается в интерфейсе, поток не должен падать молча.
                self._check.error = str(error)
            finally:
                self._check.summary = total.summary() + f", похожих пар в разных классах: {len(self._check.duplicates)}"
                self._check.running = False

        self._check_thread = threading.Thread(target=worker, name="ndc-check", daemon=True)
        self._check_thread.start()

    def check_state(self) -> CheckState:
        """ Ход и итог проверки датасета моделью. """
        return self._check

    def stop_check(self) -> None:
        """ Останавливает проверку датасета моделью. """
        self._check_stop.set()
        if self._check_thread is not None:
            self._check_thread.join(timeout=30)

    def stats(self, scope: AutoScope = "video") -> dict[str, Any]:
        """ Сколько собрано: по видео (классы и состояния) и итог. """
        rows: list[dict[str, Any]] = []
        labels_total: dict[str, int] = {}
        statuses_total: dict[str, int] = {}
        for video in self._scope_videos(scope):
            config = self.config_for_video(video)
            labels: dict[str, int] = {}
            statuses: dict[str, int] = {}
            if (config.catalog.root / CATALOG_FILENAME).exists():
                catalog = self._acquire_catalog(config.catalog.root)
                try:
                    labels = catalog.label_counts(config.catalog.collection, source=video.stem)
                    statuses = catalog.status_counts(config.catalog.collection, source=video.stem)
                finally:
                    self._release_catalog(config.catalog.root)
            inside = self.playlist_dir is not None and self.playlist_dir in video.parents
            name = str(video.relative_to(self.playlist_dir)) if inside and self.playlist_dir else video.name
            rows.append({"video": str(video), "name": name, "labels": labels, "statuses": statuses,
                         "dataset_dir": str(config.collection_dir)})
            for key, value in labels.items():
                labels_total[key] = labels_total.get(key, 0) + value
            for key, value in statuses.items():
                statuses_total[key] = statuses_total.get(key, 0) + value
        return {"scope": scope, "rows": rows, "labels": labels_total, "statuses": statuses_total,
                "classes": self.config.classes, "unsorted": self.config.unsorted_label}

    def catalog_file(self, path: str, video: Path | None = None) -> Path:
        """ Картинка из папки датасета видео (по умолчанию открытого), без выхода за её пределы. """
        config = self.config.for_video(video) if video is not None else self.effective_config()
        root = config.catalog.root.resolve()
        file = Path(path)
        file = (file if file.is_absolute() else root / file).resolve()
        if root not in file.parents or not file.is_file():
            raise FileNotFoundError(path)
        return file

    # ---- журнал ответов модели и участки на проверку -------------------------------------------------

    @property
    def journals(self) -> JournalService:
        """ Открытые журналы и участки. """
        return self._journals

    def resolve_video(self, path: str) -> Path:
        """
            Видео из журнала или файла участков. Если по записанному пути видео нет (журнал с другого
            компьютера), ищется видео с тем же именем в плейлисте.
        """
        video = Path(path)
        if video.is_file():
            return video.resolve()
        for candidate in self.playlist_videos():
            if candidate.name == video.name:
                return candidate
        raise FileNotFoundError(f"Video not found: {path}")

    def journal_folders(self) -> list[Path]:
        """ Где искать журналы: папка плейлиста (с подпапками) и папка журналов общего датасета. """
        folders = [self.playlist_dir] if self.playlist_dir is not None else []
        if not self.config.catalog.next_to_video:
            folders.append(self.config.collection_dir / JOURNAL_DIR)
        return folders

    def zone_region(self, video: Path, zone: str, journal: Path | None) -> Region | None:
        """
            Зона для выреза: контур из журнала, иначе — из файла разметки видео; "full" — весь кадр.
        """
        if zone == FULL_FRAME_ID:
            return None
        region = self._journals.zone_region(journal, zone)
        if region is not None:
            return region
        frame = self._journals.frame(video, 0)
        config = self.config_for_video(video)
        labelme = None
        if config.regions.source == RegionSource.FILE and config.regions.file is not None:
            labelme = LabelMeRegions(config.regions.file, (), config.regions.where, config.regions.id_field)
        for found in labelme.regions(frame.width, frame.height) if labelme is not None else []:
            if found.id == zone:
                return found
        raise ValueError(f"Unknown zone {zone!r} for {video.name}")

    def journal_crop(self, video: str, frame: int, zone: str, journal: str | None) -> bytes:
        """ Вырез зоны на кадре видео для показа. """
        path = self.resolve_video(video)
        base = Path(journal) if journal else None
        return self._journals.crop(path, frame, self.zone_region(path, zone, base))

    def save_zone(self, video: str, frame: int, zone: str, label: str | None, journal: str | None,
                  force: bool = False) -> list[SaveResult]:
        """
            Сохраняет вырез одной зоны кадра (из журнала или участка): видео открывается, если открыто другое;
            ответ модели из журнала записывается вместе с кадром.
        """
        path = self.resolve_video(video)
        if self.video != path:
            self.open_video(path)
        base = Path(journal) if journal else None
        region = self.zone_region(path, zone, base)
        predictions: list[Prediction] = []
        if base is not None:
            answer = self._journals.table(base).answer(frame, zone)
            if answer is not None and answer.frame == frame and answer.label:
                predictions = [Prediction(answer.label, answer.conf if answer.conf is not None else 0.0)]
        with self._lock:
            image = self._get_reader().read(frame)
            if image is None:
                raise IndexError(f"Frame {frame} not found")
            collector = self.collector()
            results = collector.save(image, label, region=region, predictions=predictions, force=force)
            collector.flush()
        return results

    def close(self) -> None:
        """ Освобождает ресурсы. """
        self.stop_auto()
        self.stop_check()
        self._model.close()
        self._journals.close()
        with self._lock:
            self._close_reader()
            self._close_collector()
            for catalog in self._catalogs.values():
                catalog.close()
            self._catalogs.clear()
            self._catalog_users.clear()


def _open_in_explorer(folder: Path) -> None:
    """ Открывает папку в проводнике системы. """
    if os.name == "nt":
        os.startfile(folder)  # type: ignore[attr-defined]  # есть только в Windows
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
