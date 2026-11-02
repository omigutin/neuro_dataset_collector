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

from .._cv import get_cv2
from ..core.types import Frame

__all__ = ["VideoReader", "is_annotated_video", "list_videos"]

# Сколько первых кадров смотреть, чтобы понять, совпадает ли частота кадров с заголовком.
_PROBE_FRAMES = 30
# Допустимое отклонение шага между кадрами от заявленного в заголовке (доля шага).
_STEP_TOLERANCE = 0.25
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

        Номер кадра — порядковый номер кадра в файле. Бывают видео, у которых частота кадров в заголовке
        не совпадает с настоящей (камера пишет с переменной частотой: в заголовке 60 к/с, на деле 15 и шаг
        «гуляет»). Для них OpenCV при переходе на кадр промахивается, поэтому такое видео один раз
        просматривается целиком и запоминается время каждого кадра; переход на кадр — немного назад по времени
        и дочитывание до нужного кадра. Время кадров кладётся в кэш (index_dir), повторное открытие быстрое.
    """

    # Папка кэша времени кадров для видео с переменной частотой (None — только в памяти). Задаёт программа.
    index_dir: ClassVar[Path | None] = None

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
        self._probe_fps: float | None = self._probe()

    # ---- переменная частота кадров ------------------------------------------------------------------

    def _position_ms(self) -> float:
        return float(self._cap.get(get_cv2().CAP_PROP_POS_MSEC))

    def _rewind(self) -> None:
        self._cap.set(get_cv2().CAP_PROP_POS_FRAMES, 0)
        self._next_index = 0

    def _probe(self) -> float | None:
        """
            Частота по первым кадрам, если она не совпадает с заголовком (видео с переменной частотой),
            иначе None — видео читается как обычно.
        """
        if self._fps <= 0:
            return None
        times: list[float] = []
        for _ in range(_PROBE_FRAMES):
            if not self._cap.grab():
                break
            times.append(self._position_ms())
        self._rewind()
        if len(times) < 3:
            return None
        deltas = np.diff(np.array(times, dtype=np.float64))
        step = 1000.0 / self._fps
        if not np.any(np.abs(deltas - step) > step * _STEP_TOLERANCE) or float(deltas.mean()) <= 0:
            return None
        return 1000.0 / float(deltas.mean())

    @property
    def variable_rate(self) -> bool:
        """ Частота кадров в заголовке не совпадает с настоящей (номера кадров берутся из просмотра файла). """
        return self._probe_fps is not None

    def _cache_file(self) -> Path | None:
        if self.index_dir is None:
            return None
        stat = self._path.stat()
        key = hashlib.sha1(f"{self._path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
        return self.index_dir / f"{key}.json"

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
        fallback = 1000.0 / (self._probe_fps or self._fps or 25.0)
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
        """ Кадров в секунду (0, если неизвестно); у видео с переменной частотой — средняя настоящая. """
        if self._probe_fps is None:
            return self._fps
        if self._times is not None and len(self._times) > 1 and self._times[-1] > self._times[0]:
            return (len(self._times) - 1) * 1000.0 / (self._times[-1] - self._times[0])
        return self._probe_fps

    @property
    def frame_count(self) -> int:
        """ Число кадров (по данным файла, может быть неточным; у видео с переменной частотой — точное). """
        if self._probe_fps is not None:
            return len(self._frame_times())
        return self._count

    def _frame(self, image: np.ndarray, index: int, ms: float | None = None) -> Frame:
        if ms is not None:
            timestamp: float | None = ms / 1000.0
        else:
            timestamp = index / self._fps if self._fps > 0 else None
        return Frame(image=image, source=self._source, index=index, timestamp=timestamp,
                     attrs={"video": self._path.name})

    def read(self, index: int) -> Frame | None:
        """ Кадр с заданным номером или None за концом видео. """
        cv2 = get_cv2()
        if index < 0:
            return None
        if self._probe_fps is not None:
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
        variable = self._probe_fps is not None
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
