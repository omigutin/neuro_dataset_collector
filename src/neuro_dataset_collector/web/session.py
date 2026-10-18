""" Состояние веб-инструмента: настройки, одно открытое видео, сборщик, фоновая автонарезка. """

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
from ..config.models import CollectorConfig, RegionSource
from ..core.results import ExportReport, SaveResult, SyncReport
from ..core.types import Frame, Region
from ..export.roboflow import export_dataset
from ..pipeline.collector import FULL_FRAME_ID, Collector
from ..pipeline.offline import OfflineReport, collect_videos
from ..predictors.base import create_predictor
from ..regions.providers import suggest_regions_file
from ..review.sync import sync_collection
from ..sources.video import VideoReader
from .state import SessionState, default_config_path, load_state, save_state

__all__ = ["AutoRunState", "WebSession"]

_HISTORY = 50


@dataclass
class AutoRunState:
    """ Ход фоновой автонарезки. """

    running: bool = False
    video: str = ""
    saved: int = 0
    frames: int = 0
    summary: str = ""
    error: str = ""


@dataclass
class WebSession:
    """
        Одно рабочее место: файл настроек и одно открытое видео.
        Настройки и открытое видео запоминаются и восстанавливаются при следующем запуске.
    """

    config_path: Path
    config: CollectorConfig = field(default_factory=CollectorConfig)
    video: Path | None = None
    frame: int = 0
    _reader: VideoReader | None = None
    _collector: Collector | None = None
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
        same_config = config_path is None or state.config_path == str(session.config_path)
        if same_config and state.video and Path(state.video).is_file():
            session.video, session.frame = Path(state.video), max(0, state.frame)
        session._save_state()
        return session

    def _save_state(self) -> None:
        save_state(SessionState(config_path=str(self.config_path),
                                video=str(self.video) if self.video else None, frame=self.frame))

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
            self._close_collector()
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
            self._close_collector()
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
                self._collector = Collector(self.effective_config())
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
        """ Открывает видео: своя папка датасета (при next_to_video) и подсказка файла зон по номеру камеры. """
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Video not found: {path}")
        reader = VideoReader(path)
        suggestion: Path | None = None
        with self._lock:
            self._close_collector()
            self._close_reader()
            self._reader = reader
            if self.video != path:
                self.frame = 0
            self.video = path
            search_dir = self.config.regions.search_dir
            if search_dir is not None:
                suggestion = suggest_regions_file(path, search_dir)
                if suggestion is not None and suggestion != self.config.regions.file:
                    # Сначала файл, потом источник: проверка настроек требует файл при source=file.
                    self.config.regions.file = suggestion
                    self.config.regions.source = RegionSource.FILE
                    save_config(self.config, self.config_path)
                else:
                    suggestion = None
            self._history.clear()
            self._save_state()
        info = self.video_info()
        info["suggested_regions"] = str(suggestion) if suggestion else None
        return info

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
            info: dict[str, Any] = {
                "video": str(self.video) if self.video else None,
                "frame": self.frame,
                "dataset_dir": str(effective.collection_dir),
                "regions_file": str(self.config.regions.file) if self.config.regions.file else None,
                "regions_source": self.config.regions.source.value,
            }
            if self.video is None:
                return info
            reader = self._get_reader()
            first = reader.read(0)
            regions = self.collector().regions_for(first.width, first.height) if first else []
            info.update({
                "name": reader.path.name,
                "fps": reader.fps,
                "frames": reader.frame_count,
                "width": first.width if first else 0,
                "height": first.height if first else 0,
                "regions": [{"id": r.id, "polygon": [list(p) for p in r.polygon]} for r in regions],
            })
            return info

    def frame_at(self, index: int) -> Frame | None:
        """ Кадр открытого видео. """
        with self._lock:
            return self._get_reader().read(index)

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

    def start_auto(self) -> None:
        """ Запускает автонарезку открытого видео в фоне. """
        with self._lock:
            if self._auto.running:
                return
            if self.video is None:
                raise FileNotFoundError("Video is not opened")
            video = self.video
            collector = self.collector()
            predictor = create_predictor(self.config.model, self.config.task)
            self._auto = AutoRunState(running=True, video=video.name)
            self._auto_stop.clear()

        def on_result(result: SaveResult) -> None:
            if result.ok:
                self._auto.saved += 1

        def worker() -> None:
            report = OfflineReport()
            try:
                report = collect_videos(collector, [video], predictor=predictor, on_result=on_result,
                                        stop=self._auto_stop)
            except Exception as error:  # Ошибка показывается в интерфейсе, поток не должен падать молча.
                self._auto.error = str(error)
            finally:
                self._auto.frames = report.frames
                self._auto.summary = report.summary()
                self._auto.running = False

        self._auto_thread = threading.Thread(target=worker, name="ndc-auto", daemon=True)
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
            if self._collector is not None:
                self._collector.close()
                self._collector = None
