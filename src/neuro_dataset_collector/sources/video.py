""" Чтение видеофайлов. """

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .._cv import get_cv2
from ..core.types import Frame

__all__ = ["VideoReader", "list_videos"]


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
    """ Видео по пути: один файл или все подходящие файлы папки (отсортированы по имени). """
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"Video path not found: {path}")
    allowed = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in path.glob(pattern) if p.is_file() and p.suffix.lower() in allowed)


class VideoReader:
    """ Последовательное и произвольное чтение кадров видео. Имя источника — имя файла без расширения. """

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
        """ Кадров в секунду (0, если неизвестно). """
        return self._fps

    @property
    def frame_count(self) -> int:
        """ Число кадров (по данным файла, может быть неточным). """
        return self._count

    def _frame(self, image: np.ndarray, index: int) -> Frame:
        timestamp = index / self._fps if self._fps > 0 else None
        return Frame(image=image, source=self._source, index=index, timestamp=timestamp,
                     attrs={"video": self._path.name})

    def read(self, index: int) -> Frame | None:
        """ Кадр с заданным номером или None за концом видео. """
        cv2 = get_cv2()
        if index < 0:
            return None
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
        if start:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, start)
        index = start
        while True:
            if (index - start) % step == 0:
                ok, image = self._cap.read()
                if not ok:
                    return
                self._next_index = index + 1
                yield self._frame(image, index)
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
