""" Чтение видеофайлов. """

from __future__ import annotations

import bisect
import hashlib
import json
import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from true_fps import FpsDetector

from .._cv import get_cv2
from ..core.types import Frame

__all__ = ["VideoReader", "is_annotated_video", "list_videos"]

# Допустимое отклонение настоящей частоты кадров от заявленной в заголовке (доля заявленной).
_RATE_TOLERANCE = 0.05
# На сколько отступать назад по времени при переходе на кадр видео с переменной частотой, мс.
_SEEK_MARGIN_MS = 6000.0


def _windows_short_path(path: Path) -> str | None:
    """ Короткий путь Windows (только ASCII) или None, если не Windows или он недоступен. """
    if os.name != "nt" or str(path).isascii():
        return None
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))  # type: ignore[attr-defined]
    if length == 0 or length > len(buffer) or not buffer.value.isascii():
        return None
    return buffer.value


def list_videos(path: Path, extensions: Sequence[str], recursive: bool = False) -> list[Path]:
    """ Видео по пути: один файл или все подходящие файлы папки (отсортированы по имени, без «….annotated.*»). """
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"Video path not found: {path}")
    allowed = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in path.glob(pattern)
                  if p.is_file() and p.suffix.lower() in allowed and not is_annotated_video(p))


def is_annotated_video(path: Path) -> bool:
    """ Видео, записанное программой со счётчиками и разметкой («….annotated.mp4»): это не исходник, его не режем. """
    return Path(path).stem.lower().endswith(".annotated")


class VideoReader:
    """
        Последовательное и произвольное чтение кадров видео. Имя источника — имя файла без расширения.

        Номер кадра — порядковый номер кадра в файле. Настоящая частота кадров измеряется по временным
        меткам кадров (библиотека true_fps, нужен ffprobe). Бывают видео, у которых частота в заголовке
        не совпадает с настоящей (камера пишет с переменной частотой: в заголовке 60 к/с, на деле 15 и шаг
        «гуляет»). Для них OpenCV при переходе на кадр промахивается, поэтому такое видео один раз
        просматривается целиком и запоминается время каждого кадра; переход на кадр — немного назад по времени
        и дочитывание до нужного кадра. Частота и время кадров кладутся в кэш (index_dir), повторное открытие
        быстрое.
    """

    # Папка кэша частоты и времени кадров (None — только в памяти). Задаёт программа.
    index_dir: ClassVar[Path | None] = None
    # Путь к ffprobe (None — из системного PATH). Задаёт программа после поиска или установки инструментов.
    ffprobe_path: ClassVar[Path | None] = None
    # Детекторы частоты кадров по пути ffprobe: создаются один раз на процесс.
    _detectors: ClassVar[dict[Path | None, FpsDetector]] = {}

    def __init__(self, path: Path, source: str | None = None) -> None:
        cv2 = get_cv2()
        self._path = Path(path)
        self._cap: Any = cv2.VideoCapture(str(self._path))
        if not self._cap.isOpened():
            # OpenCV на Windows может не открыть путь с кириллицей — пробуем короткое имя файла (8.3).
            short = _windows_short_path(self._path)
            if short is not None:
                self._cap = cv2.VideoCapture(short)
        if not self._cap.isOpened():
            raise OSError(f"Cannot open video: {self._path}")
        self._source = source or self._path.stem
        self._fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._next_index = 0
        # Время каждого кадра, мс — только для видео с переменной частотой (строится при первой надобности).
        self._times: list[float] | None = None
        # Настоящая частота по меткам кадров (None — измерить не удалось, используется заголовок).
        self._real_fps: float | None = self._measure_fps()
        self._variable = self._is_variable_rate()

    # ---- переменная частота кадров ------------------------------------------------------------------

    def _position_ms(self) -> float:
        return float(self._cap.get(get_cv2().CAP_PROP_POS_MSEC))

    def _rewind(self) -> None:
        self._cap.set(get_cv2().CAP_PROP_POS_FRAMES, 0)
        self._next_index = 0

    @classmethod
    def _fps_detector(cls) -> FpsDetector:
        """ Детектор частоты кадров для текущего ffprobe (FfprobeNotFoundError, если ffprobe нет). """
        detector = cls._detectors.get(cls.ffprobe_path)
        if detector is None:
            detector = FpsDetector(cls.ffprobe_path)
            cls._detectors[cls.ffprobe_path] = detector
        return detector

    def _measure_fps(self) -> float | None:
        """ Настоящая частота кадров (из кэша или замером ffprobe) или None, если меток мало. """
        cache = self._cache_file(".fps.json")
        if cache is not None and cache.is_file():
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))["fps"]
                return None if cached is None else float(cached)
            except (OSError, ValueError, KeyError, TypeError):
                # Повреждённый кэш не мешает: частота измеряется заново и кэш перезаписывается.
                pass
        fps = self._fps_detector().detect(self._path).fps
        if cache is not None:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({"video": str(self._path), "fps": fps}), encoding="utf-8")
            except OSError:
                # Кэш — только ускорение: без него частота измеряется при каждом открытии.
                pass
        return fps

    def _is_variable_rate(self) -> bool:
        """ Настоящая частота не совпадает с заголовком (или в заголовке её нет). """
        if self._real_fps is None:
            return False
        if self._fps <= 0:
            return True
        return abs(self._real_fps - self._fps) / self._fps > _RATE_TOLERANCE

    @property
    def variable_rate(self) -> bool:
        """ Частота кадров в заголовке не совпадает с настоящей (номера кадров берутся из просмотра файла). """
        return self._variable

    def _cache_file(self, suffix: str = ".json") -> Path | None:
        """ Файл кэша этого видео (ключ — путь, размер и время изменения) или None без index_dir. """
        if self.index_dir is None:
            return None
        stat = self._path.stat()
        key = hashlib.sha1(f"{self._path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
        return self.index_dir / f"{key}{suffix}"

    def _frame_times(self) -> list[float]:
        """ Время каждого кадра, мс (из кэша или одним проходом по файлу). """
        if self._times is not None:
            return self._times
        cache = self._cache_file()
        if cache is not None and cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                self._times = [float(t) for t in data["times"]]
                return self._times
            except (OSError, ValueError, KeyError, TypeError):
                pass
        self._rewind()
        fallback = 1000.0 / (self._real_fps or self._fps or 25.0)
        times: list[float] = []
        while self._cap.grab():
            ms = self._position_ms()
            # В конце файла OpenCV иногда отдаёт 0 или прежнее время — продолжаем средним шагом.
            if times and ms <= times[-1]:
                ms = times[-1] + fallback
            times.append(ms)
        self._rewind()
        self._times = times
        if cache is not None:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({"video": str(self._path), "times": times}), encoding="utf-8")
            except OSError:
                pass
        return times

    def _seek_variable(self, index: int) -> tuple[bool, Any]:
        """
            Кадр index видео с переменной частотой: назад по времени с запасом, номер прочитанного кадра —
            по его времени, затем дочитывание до нужного кадра по счёту.
        """
        cv2 = get_cv2()
        times = self._frame_times()
        target = times[index]
        margin = _SEEK_MARGIN_MS
        while True:
            if margin >= target:
                self._rewind()
                ok, image = self._cap.read()
                current = 0
                break
            self._cap.set(cv2.CAP_PROP_POS_MSEC, target - margin)
            ok, image = self._cap.read()
            current = bisect.bisect_left(times, self._position_ms() - 0.5)
            if ok and current <= index:
                break
            # Перепрыгнули нужный кадр (или не прочитали) — отступаем дальше.
            margin *= 2
        while ok and current < index:
            ok, image = self._cap.read()
            current += 1
        return ok, image

    # ---- свойства -----------------------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """ Путь к файлу. """
        return self._path

    @property
    def source(self) -> str:
        """ Имя источника. """
        return self._source

    @property
    def fps(self) -> float:
        """ Настоящая частота кадров (0, если неизвестна); у видео с переменной частотой — средняя по всему файлу. """
        if self._variable and self._times is not None and len(self._times) > 1 and self._times[-1] > self._times[0]:
            return (len(self._times) - 1) * 1000.0 / (self._times[-1] - self._times[0])
        return self._real_fps if self._real_fps is not None else self._fps

    @property
    def frame_count(self) -> int:
        """ Число кадров (по данным файла, может быть неточным; у видео с переменной частотой — точное). """
        if self._variable:
            return len(self._frame_times())
        return self._count

    def _frame(self, image: np.ndarray, index: int, ms: float | None = None) -> Frame:
        if ms is not None:
            timestamp: float | None = ms / 1000.0
        else:
            fps = self.fps
            timestamp = index / fps if fps > 0 else None
        return Frame(image=image, source=self._source, index=index, timestamp=timestamp,
                     attrs={"video": self._path.name})

    def read(self, index: int) -> Frame | None:
        """ Кадр с заданным номером или None за концом видео. """
        cv2 = get_cv2()
        if index < 0:
            return None
        if self._variable:
            times = self._frame_times()
            if index >= len(times):
                return None
            if index == self._next_index:
                ok, image = self._cap.read()
            else:
                ok, image = self._seek_variable(index)
            if not ok:
                self._next_index = -1
                return None
            self._next_index = index + 1
            return self._frame(image, index, times[index])
        if index != self._next_index:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = self._cap.read()
        if not ok:
            self._next_index = -1
            return None
        self._next_index = index + 1
        return self._frame(image, index)

    def frames(self, step: int = 1, start: int = 0) -> Iterator[Frame]:
        """ Кадры подряд: каждый step-й, начиная с start. Промежуточные кадры пропускаются без декодирования. """
        cv2 = get_cv2()
        variable = self._variable
        index = start
        if start:
            first = self.read(start)
            if first is None:
                return
            yield first
            index = start + 1
        elif variable:
            self._rewind()
        else:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        while True:
            if (index - start) % step == 0:
                ok, image = self._cap.read()
                if not ok:
                    return
                self._next_index = index + 1
                yield self._frame(image, index, self._position_ms() if variable else None)
            elif not self._cap.grab():
                return
            index += 1

    def close(self) -> None:
        """ Закрывает файл. """
        self._cap.release()

    def __enter__(self) -> VideoReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
