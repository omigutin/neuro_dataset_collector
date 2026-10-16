""" Состояние веб-инструмента: настройки, открытое видео, сборщик, фоновая автонарезка. """

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .._cv import get_cv2
from ..config.loader import dump_config, load_config, resolve_paths, save_config
from ..config.models import CollectorConfig
from ..core.results import ExportReport, SaveResult, SyncReport
from ..core.types import Frame, Region
from ..export.roboflow import export_dataset
from ..pipeline.collector import FULL_FRAME_ID, Collector
from ..pipeline.offline import OfflineReport, collect_videos
from ..predictors.base import create_predictor
from ..review.sync import sync_collection
from ..sources.video import VideoReader, list_videos

__all__ = ["AutoRunState", "WebSession"]

_HISTORY = 50


@dataclass
class AutoRunState:
    """ Ход фоновой автонарезки. """

    running: bool = False
    video: str = ""
    video_number: int = 0
    videos_total: int = 0
    saved: int = 0
    frames: int = 0
    summary: str = ""
    error: str = ""


@dataclass
class _Video:
    path: Path
    reader: VideoReader | None = None


@dataclass
class WebSession:
    """ Одно рабочее место: один файл настроек и один открытый набор видео. """

    config_path: Path | None = None
    config: CollectorConfig = field(default_factory=CollectorConfig)
    _collector: Collector | None = None
    _videos: list[_Video] = field(default_factory=list)
    _history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=_HISTORY))
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _auto: AutoRunState = field(default_factory=AutoRunState)
    _auto_stop: threading.Event = field(default_factory=threading.Event)
    _auto_thread: threading.Thread | None = None

    # ---- настройки ----------------------------------------------------------------------------------

    @classmethod
    def open(cls, config_path: Path | None) -> WebSession:
        """ Создаёт сессию; если файла настроек нет — с настройками по умолчанию. """
        session = cls(config_path=config_path)
        if config_path is not None and config_path.exists():
            session.config = load_config(config_path)
        return session

    def config_yaml(self) -> str:
        """ Настройки текстом YAML. """
        return dump_config(self.config)

    def update_config(self, data: dict[str, Any], save: bool = True) -> CollectorConfig:
        """ Проверяет и применяет новые настройки; сборщик пересоздаётся при следующем действии. """
        config = CollectorConfig.model_validate(data)
        base = self.config_path.parent if self.config_path else Path.cwd()
        config = resolve_paths(config, base)
        with self._lock:
            self._close_collector()
            self.config = config
            if save and self.config_path is not None:
                save_config(config, self.config_path)
        return config

    def update_config_yaml(self, text: str, save: bool = True) -> CollectorConfig:
        """ То же, но из текста YAML. """
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("Config root must be a mapping")
        return self.update_config(data, save)

    def load_config_file(self, path: Path) -> CollectorConfig:
        """ Открывает другой файл настроек. """
        config = load_config(path)
        with self._lock:
            self._close_collector()
            self.config_path = path
            self.config = config
        return config

    def save_config_as(self, path: Path) -> None:
        """ Сохраняет настройки в указанный файл и делает его текущим. """
        save_config(self.config, path)
        self.config_path = path

    # ---- сборщик ------------------------------------------------------------------------------------

    def collector(self) -> Collector:
        """ Сборщик для текущих настроек (создаётся при первом обращении). """
        with self._lock:
            if self._collector is None:
                self._collector = Collector(self.config)
                self._collector.add_listener(self._remember)
            return self._collector

    def _close_collector(self) -> None:
        if self._auto.running:
            raise RuntimeError("Stop automatic collection before changing settings")
        if self._collector is not None:
            self._collector.close()
            self._collector = None

    def _remember(self, result: SaveResult) -> None:
        self._history.appendleft(self.result_to_dict(result))

    def result_to_dict(self, result: SaveResult) -> dict[str, Any]:
        """ Результат для передачи в браузер. """
        root = self.config.catalog.root
        def rel(path: Path | None) -> str | None:
            if path is None:
                return None
            try:
                return path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                return None
        return {
            "ok": result.ok,
            "message": result.message,
            "region": result.region_id or FULL_FRAME_ID,
            "label": result.label,
            "reason": result.reason.value if result.reason else None,
            "path": rel(result.path),
            "similar_to": rel(result.similar_to),
            "distance": result.distance,
        }

    def history(self) -> list[dict[str, Any]]:
        """ Последние результаты сохранения. """
        return list(self._history)

    # ---- видео -------------------------------------------------------------------------------------

    def open_source(self, path: Path | None = None) -> list[str]:
        """ Открывает видео или папку с видео; возвращает имена файлов. """
        with self._lock:
            self._close_videos()
            target = path or self.config.source.path
            if target is None:
                raise ValueError("Video path is not set")
            files = list_videos(target, self.config.source.extensions, self.config.source.recursive)
            self._videos = [_Video(file) for file in files]
            if path is not None and self.config.source.path != path:
                self.config.source.path = path
            return [video.path.name for video in self._videos]

    def _close_videos(self) -> None:
        for video in self._videos:
            if video.reader is not None:
                video.reader.close()
        self._videos = []

    def _reader(self, number: int) -> VideoReader:
        if not 0 <= number < len(self._videos):
            raise IndexError(f"Video {number} is not opened")
        video = self._videos[number]
        if video.reader is None:
            video.reader = VideoReader(video.path)
        return video.reader

    def video_info(self, number: int) -> dict[str, Any]:
        """ Сведения о видео и его зонах. """
        with self._lock:
            reader = self._reader(number)
            frame = reader.read(0)
            regions = self.collector().regions_for(frame.width, frame.height) if frame else []
            return {
                "name": reader.path.name,
                "fps": reader.fps,
                "frames": reader.frame_count,
                "width": frame.width if frame else 0,
                "height": frame.height if frame else 0,
                "regions": [{"id": r.id, "polygon": [list(p) for p in r.polygon]} for r in regions],
            }

    def frame(self, number: int, index: int) -> Frame | None:
        """ Кадр видео. """
        with self._lock:
            return self._reader(number).read(index)

    def render(self, frame: Frame, max_width: int = 1280) -> bytes:
        """ JPEG кадра с нарисованными зонами для показа в браузере. """
        cv2 = get_cv2()
        image = frame.image.copy()
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

    def save(
        self, number: int, index: int, label: str | None, region_ids: list[str] | None, force: bool
    ) -> list[SaveResult]:
        """ Ручное сохранение кадра (все или выбранные зоны). """
        with self._lock:
            frame = self._reader(number).read(index)
            if frame is None:
                raise IndexError(f"Frame {index} not found")
            collector = self.collector()
            results = collector.save(frame, label, region_ids=region_ids, force=force)
            collector.flush()
            return results

    # ---- автонарезка, сверка, выгрузка ---------------------------------------------------------------

    def auto_state(self) -> AutoRunState:
        """ Ход автонарезки. """
        return self._auto

    def start_auto(self) -> None:
        """ Запускает автонарезку открытых видео в фоне. """
        with self._lock:
            if self._auto.running:
                return
            if not self._videos:
                self.open_source()
            videos = [video.path for video in self._videos]
            collector = self.collector()
            predictor = create_predictor(self.config.model, self.config.task)
            self._auto = AutoRunState(running=True, videos_total=len(videos))
            self._auto_stop.clear()

        def on_video(path: Path, number: int, total: int) -> None:
            self._auto.video, self._auto.video_number = path.name, number

        def on_result(result: SaveResult) -> None:
            if result.ok:
                self._auto.saved += 1

        def worker() -> None:
            report = OfflineReport()
            try:
                report = collect_videos(collector, videos, predictor=predictor, on_video=on_video,
                                        on_result=on_result, stop=self._auto_stop)
            except Exception as error:  # Ошибка показывается в интерфейсе, поток не должен падать молча.
                self._auto.error = str(error)
            finally:
                self._auto.frames = report.frames
                self._auto.summary = report.summary()
                self._auto.running = False

        self._auto_thread = threading.Thread(target=worker, name="nfc-auto", daemon=True)
        self._auto_thread.start()

    def stop_auto(self) -> None:
        """ Останавливает автонарезку. """
        self._auto_stop.set()
        if self._auto_thread is not None:
            self._auto_thread.join(timeout=30)

    def sync(self) -> SyncReport:
        """ Сверка после ручной проверки. """
        with self._lock:
            collector = self.collector()
            collector.flush()
            return sync_collection(self.config, collector.catalog)

    def export(self, out_dir: Path, only_new: bool, include_pending: bool) -> ExportReport:
        """ Выгрузка проверенных кадров. """
        with self._lock:
            collector = self.collector()
            return export_dataset(self.config, out_dir, only_new=only_new, include_pending=include_pending,
                                  catalog=collector.catalog)

    def stats(self) -> dict[str, Any]:
        """ Счётчики коллекции. """
        stats = self.collector().stats()
        return {
            "collection": self.config.catalog.collection,
            "root": str(self.config.catalog.root),
            "session": stats.session_by_label,
            "labels": stats.total_by_label,
            "statuses": stats.by_status,
            "rejected": stats.rejected_by_reason,
        }

    def catalog_file(self, relative: str) -> Path:
        """ Файл из корня каталога (без выхода за его пределы). """
        root = self.config.catalog.root.resolve()
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise FileNotFoundError(relative)
        return path

    def close(self) -> None:
        """ Освобождает ресурсы. """
        self.stop_auto()
        with self._lock:
            self._close_videos()
            if self._collector is not None:
                self._collector.close()
                self._collector = None
