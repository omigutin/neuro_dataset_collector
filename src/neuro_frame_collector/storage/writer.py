""" Запись файлов на диск: сразу или в фоновом потоке. """

from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .._cv import get_cv2
from ..config.models import ImageFormat
from ..core.types import Image

__all__ = ["DiskGuard", "FileWriter", "MoveJob", "QueueFullError", "WriteJob"]

_log = logging.getLogger("neuro_frame_collector")


class QueueFullError(RuntimeError):
    """ Очередь фоновой записи переполнена. """


@dataclass
class WriteJob:
    """ Записать картинку (и, при необходимости, текстовую разметку). """

    path: Path
    image: Image
    image_format: ImageFormat
    jpeg_quality: int
    label_path: Path | None = None
    label_text: str | None = None
    skip_if_exists: bool = False
    on_done: Callable[[Exception | None], None] | None = None


@dataclass
class MoveJob:
    """ Переместить уже записанный файл. """

    source: Path
    target: Path
    on_done: Callable[[Exception | None], None] | None = None


Job = WriteJob | MoveJob


def _atomic_write(path: Path, data: bytes) -> None:
    """ Пишет во временный файл и подменяет: недописанный файл никогда не появится под настоящим именем. """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _execute(job: Job) -> None:
    """ Выполняет одно задание записи. """
    if isinstance(job, MoveJob):
        if not job.source.exists():
            raise FileNotFoundError(f"File to move not found: {job.source}")
        job.target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(job.source, job.target)
        return
    if job.skip_if_exists and job.path.exists():
        return
    cv2 = get_cv2()
    if job.image_format == ImageFormat.JPG:
        ok, buffer = cv2.imencode(".jpg", job.image, [int(cv2.IMWRITE_JPEG_QUALITY), job.jpeg_quality])
    else:
        ok, buffer = cv2.imencode(".png", job.image)
    if not ok:
        raise OSError(f"Failed to encode image: {job.path}")
    _atomic_write(job.path, buffer.tobytes())
    if job.label_path is not None and job.label_text is not None:
        _atomic_write(job.label_path, job.label_text.encode("utf-8"))


class FileWriter:
    """
        Выполняет задания записи по порядку. В фоновом режиме один поток и ограниченная очередь:
        при переполнении задание не ставится (QueueFullError), основной процесс не ждёт диск.
    """

    def __init__(self, background: bool, queue_size: int = 64) -> None:
        self._background = background
        self._queue: queue.Queue[Job | None] = queue.Queue(maxsize=queue_size)
        self._thread: threading.Thread | None = None
        if background:
            self._thread = threading.Thread(target=self._run, name="nfc-writer", daemon=True)
            self._thread.start()

    def submit(self, job: Job) -> None:
        """ Ставит задание; в синхронном режиме выполняет сразу. """
        if not self._background:
            self._finish(job)
            return
        try:
            self._queue.put_nowait(job)
        except queue.Full as error:
            raise QueueFullError("Writer queue is full") from error

    def flush(self) -> None:
        """ Ждёт выполнения всех поставленных заданий. """
        if self._background:
            self._queue.join()

    def close(self) -> None:
        """ Дописывает очередь и останавливает поток. """
        if self._thread is not None and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join()
        self._thread = None

    @staticmethod
    def _finish(job: Job) -> None:
        error: Exception | None = None
        try:
            _execute(job)
        except Exception as exc:  # Ошибка записи передаётся владельцу задания, а не роняет поток.
            error = exc
            _log.error("File operation failed: %s", exc)
        if job.on_done is not None:
            job.on_done(error)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._finish(job)
            finally:
                self._queue.task_done()


class DiskGuard:
    """ Следит за свободным местом; проверяет диск не чаще раза в interval секунд. """

    def __init__(self, root: Path, min_free_gb: float, interval: float = 10.0) -> None:
        self._root = root
        self._min_free = int(min_free_gb * 1024**3)
        self._interval = interval
        self._checked_at = 0.0
        self._ok = True

    def has_space(self) -> bool:
        """ Достаточно ли свободного места. """
        if self._min_free <= 0:
            return True
        now = time.monotonic()
        if now - self._checked_at >= self._interval:
            self._checked_at = now
            self._root.mkdir(parents=True, exist_ok=True)
            self._ok = shutil.disk_usage(self._root).free >= self._min_free
        return self._ok
