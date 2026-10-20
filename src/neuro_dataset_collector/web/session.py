""" Состояние веб-инструмента: настройки, плейлист, открытое видео, сборщик, фоновая автонарезка. """

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml

from .._cv import get_cv2
from ..catalog.catalog import Catalog
from ..config.loader import dump_config, load_config, resolve_paths, save_config
from ..config.models import DATASET_DIR_SUFFIX, CollectorConfig, RegionSource, dataset_dir_for_video
from ..core.results import ExportReport, SaveResult, SyncReport
from ..core.types import Frame, Point, Region
from ..export.roboflow import export_dataset
from ..pipeline.collector import FULL_FRAME_ID, Collector
from ..pipeline.offline import OfflineReport, collect_videos
from ..predictors.base import create_predictor
from ..regions.providers import (
    LabelMeRegions,
    RegionsOrigin,
    find_regions_file,
    sidecar_regions_file,
    suggest_regions_file,
    write_labelme,
)
from ..review.sync import sync_collection
from ..sources.video import VideoReader, list_videos
from .state import SessionState, default_config_path, load_state, save_state

__all__ = ["AutoRunState", "AutoScope", "WebSession"]

_HISTORY = 50
# Сколько последних позиций в видео помнить между запусками.
_MAX_POSITIONS = 500
_IMAGE_EXT = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})

# Автонарезка: только открытое видео или все видео плейлиста.
AutoScope = Literal["video", "playlist"]


@dataclass
class AutoRunState:
    """ Ход фоновой автонарезки. """

    running: bool = False
    scope: str = "video"
    video: str = ""
    index: int = 0
    total: int = 0
    saved: int = 0
    frames: int = 0
    summary: str = ""
    error: str = ""


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
    _reader: VideoReader | None = None
    _collector: Collector | None = None
    # Каталоги открываются один раз на корень: ручная нарезка и автонарезка пишут в одну историю
    # и видят дубли друг друга.
    _catalogs: dict[Path, Catalog] = field(default_factory=dict)
    _catalog_users: dict[Path, int] = field(default_factory=dict)
    _history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=_HISTORY))
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _auto: AutoRunState = field(default_factory=AutoRunState)
    _auto_stop: threading.Event = field(default_factory=threading.Event)
    _auto_thread: threading.Thread | None = None

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
        ))

    def remember_frame(self, frame: int) -> None:
        """ Запоминает текущий кадр (для продолжения с того же места). """
        self.frame = max(0, int(frame))
        self._save_state()

    # ---- настройки ----------------------------------------------------------------------------------

    def effective_config(self) -> CollectorConfig:
        """ Настройки с учётом открытого видео (папка датасета рядом с видео). """
        return self.config.for_video(self.video) if self.video else self.config

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
                self._collector.add_listener(self._remember)
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

    def _remember(self, result: SaveResult) -> None:
        self._history.appendleft(self.result_to_dict(result))

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

    def history(self) -> list[dict[str, Any]]:
        """ Последние результаты сохранения. """
        return list(self._history)

    # ---- видео и зоны --------------------------------------------------------------------------------

    def open_video(self, path: Path) -> dict[str, Any]:
        """
            Открывает видео: своя папка датасета (при next_to_video) и файл зон —
            json рядом с видео, иначе файл с тем же номером камеры из папки разметки.
            Кадр — тот, на котором остановились в этом видео в прошлый раз.
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
            found = find_regions_file(path, self.config.regions.search_dir)
            if found is not None and (found[0] != self.config.regions.file
                                      or self.config.regions.source != RegionSource.FILE):
                self._use_regions_file(found[0])
            self._history.clear()
            self._save_state()
        info = self.video_info()
        info["suggested_regions"] = str(found[0]) if found is not None and found[1] == "camera" else None
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
        return LabelMeRegions(regions.file, regions.labels, regions.where, regions.id_field)

    def _shape_types(self) -> dict[str, str]:
        labelme = self._labelme()
        return labelme.shape_types() if labelme is not None else {}

    def edit_regions(self, edits: dict[str, list[Point]]) -> dict[str, Any]:
        """
            Применяет исправленные зоны (координаты кадра): файл разметки с ними сохраняется
            рядом с видео под его именем и становится текущим. Исходный файл не меняется.
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
            data = labelme.edited(edits, first.width, first.height)
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

    def render(self, frame: Frame, max_width: int = 1280, zones: bool = True) -> bytes:
        """ JPEG кадра для показа в браузере; zones — нарисовать зоны (без них — для правки зон). """
        cv2 = get_cv2()
        image = frame.image.copy()
        if zones:
            for region in self.collector().regions_for(frame.width, frame.height):
                self._draw_region(image, region)
        if image.shape[1] > max_width:
            scale = max_width / image.shape[1]
            image = cv2.resize(image, (max_width, int(image.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise OSError("Failed to encode frame")
        return bytes(buffer.tobytes())

    @staticmethod
    def _draw_region(image: np.ndarray, region: Region) -> None:
        cv2 = get_cv2()
        points = np.array(region.polygon, dtype=np.int32)
        cv2.polylines(image, [points], True, (0, 255, 0), 2)
        x, y = int(region.bbox.x1), int(region.bbox.y1)
        cv2.putText(image, region.id, (x + 4, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

    def save(self, index: int, label: str | None, region_ids: list[str] | None, force: bool) -> list[SaveResult]:
        """ Ручное сохранение кадра (все или выбранные зоны). """
        with self._lock:
            frame = self._get_reader().read(index)
            if frame is None:
                raise IndexError(f"Frame {index} not found")
            collector = self.collector()
            results = collector.save(frame, label, region_ids=region_ids, force=force)
            collector.flush()
            self.remember_frame(index)
            return results

    # ---- автонарезка, сверка, выгрузка ---------------------------------------------------------------

    def auto_state(self) -> AutoRunState:
        """ Ход автонарезки. """
        return self._auto

    def start_auto(self, scope: AutoScope = "video") -> None:
        """ Запускает автонарезку в фоне: открытого видео или всех видео плейлиста. """
        with self._lock:
            if self._auto.running:
                return
            if scope == "playlist":
                videos = self.playlist_videos()
                if not videos:
                    raise FileNotFoundError("Playlist is empty: choose a folder with videos")
            else:
                if self.video is None:
                    raise FileNotFoundError("Video is not opened")
                videos = [self.video]
            predictor = create_predictor(self.config.model, self.config.task)
            self._auto = AutoRunState(running=True, scope=scope, total=len(videos))
            self._auto_stop.clear()

        def worker() -> None:
            total = OfflineReport()
            try:
                for number, video in enumerate(videos, start=1):
                    if self._auto_stop.is_set():
                        break
                    self._auto.video, self._auto.index = video.name, number
                    report = self._collect_one(video, predictor)
                    total.videos += report.videos
                    total.frames += report.frames
                    total.saved += report.saved
                    total.rejected_by_reason.update(report.rejected_by_reason)
                    self._auto.frames = total.frames
            except Exception as error:  # Ошибка показывается в интерфейсе, поток не должен падать молча.
                self._auto.error = str(error)
            finally:
                self._auto.summary = total.summary()
                self._auto.running = False

        self._auto_thread = threading.Thread(target=worker, name="ndc-auto", daemon=True)
        self._auto_thread.start()

    def _collect_one(self, video: Path, predictor: Any) -> OfflineReport:
        """ Автонарезка одного видео своим сборщиком (общий каталог с ручной нарезкой). """
        config = self.config_for_video(video)
        catalog = self._acquire_catalog(config.catalog.root)
        try:
            with Collector(config, catalog=catalog) as collector:
                if video == self.video:
                    collector.add_listener(self._remember)

                def on_result(result: SaveResult) -> None:
                    if result.ok:
                        self._auto.saved += 1

                return collect_videos(collector, [video], predictor=predictor, on_result=on_result,
                                      stop=self._auto_stop)
        finally:
            self._release_catalog(config.catalog.root)

    def config_for_video(self, video: Path) -> CollectorConfig:
        """ Настройки для видео: его папка датасета и его файл зон (рядом с видео или по номеру камеры). """
        config = self.config.for_video(video)
        found = find_regions_file(video, config.regions.search_dir)
        if found is not None:
            config.regions.file = found[0]
            config.regions.source = RegionSource.FILE
        return config

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
            items.append({
                "path": str(video),
                "name": str(video.relative_to(self.playlist_dir)) if self.playlist_dir else video.name,
                "saved": self._saved_count(video),
                "regions": found[1] if found is not None else None,
            })
        return {"dir": str(self.playlist_dir) if self.playlist_dir else None, "items": items,
                "current": str(self.video) if self.video else None}

    def _saved_count(self, video: Path) -> int | None:
        """ Число сохранённых картинок в папке датасета видео (только при «рядом с видео»). """
        if not self.config.catalog.next_to_video:
            return None
        folder = dataset_dir_for_video(video)
        if not folder.is_dir():
            return 0
        return sum(1 for p in folder.rglob("*") if p.suffix.lower() in _IMAGE_EXT and p.is_file())

    def sync(self) -> SyncReport:
        """ Сверка после ручной проверки. """
        with self._lock:
            collector = self.collector()
            collector.flush()
            return sync_collection(collector.config, collector.catalog)

    def export(self, out_dir: Path, only_new: bool, include_pending: bool) -> ExportReport:
        """ Выгрузка проверенных кадров. """
        with self._lock:
            collector = self.collector()
            return export_dataset(collector.config, out_dir, only_new=only_new, include_pending=include_pending,
                                  catalog=collector.catalog)

    def stats(self) -> dict[str, Any]:
        """ Счётчики коллекции. """
        collector = self.collector()
        stats = collector.stats()
        return {
            "collection": collector.config.catalog.collection,
            "root": str(collector.config.collection_dir),
            "session": stats.session_by_label,
            "labels": stats.total_by_label,
            "statuses": stats.by_status,
            "rejected": stats.rejected_by_reason,
        }

    def catalog_file(self, path: str) -> Path:
        """ Картинка из папки датасета (без выхода за её пределы). """
        root = self.effective_config().catalog.root.resolve()
        file = Path(path)
        file = (file if file.is_absolute() else root / file).resolve()
        if root not in file.parents or not file.is_file():
            raise FileNotFoundError(path)
        return file

    def close(self) -> None:
        """ Освобождает ресурсы. """
        self.stop_auto()
        with self._lock:
            self._close_reader()
            self._close_collector()
            for catalog in self._catalogs.values():
                catalog.close()
            self._catalogs.clear()
            self._catalog_users.clear()
