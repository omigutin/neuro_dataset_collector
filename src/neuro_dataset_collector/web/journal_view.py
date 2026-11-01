"""
    Журнал ответов модели и участки на проверку в веб-интерфейсе: выборки из журнала, вырезы из видео
    по номеру кадра и зоне (виртуальный датасет: картинки не хранятся, достаются из видео по требованию).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from .._cv import get_cv2
from ..core.types import Frame, Region
from ..imaging.crop import crop_region
from ..journal.segments import ReviewFile, read_review, write_review
from ..journal.table import JournalFilter, JournalTable, find_journals, journal_base
from ..sources.video import VideoReader

__all__ = ["JournalService"]

# Сколько журналов держать в памяти и сколько кадров помнить для вырезов.
_TABLES = 4
_FRAMES = 48
# Сколько вырезов показывать на участке (кадры берутся равномерно).
_SEGMENT_ITEMS = 48
_KIND_NAMES = {"missing": "пропуск", "extra": "лишнее", "changed": "изменено", "marked": "отмечено"}


class JournalService:
    """ Открытые журналы, участки и вырезы для показа. """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tables: OrderedDict[str, tuple[float, JournalTable]] = OrderedDict()
        self._opened: list[Path] = []
        self._reader: VideoReader | None = None
        self._frames: OrderedDict[tuple[str, int], Frame] = OrderedDict()

    # ---- журналы ----------------------------------------------------------------------------------

    def table(self, path: Path) -> JournalTable:
        """ Журнал из памяти (перечитывается, если файл изменился). """
        base = journal_base(path)
        csv_path = base.with_name(base.name + ".predictions.csv")
        if not csv_path.is_file():
            raise FileNotFoundError(f"Predictions journal not found: {csv_path}")
        stamp = csv_path.stat().st_mtime
        key = str(base)
        with self._lock:
            cached = self._tables.get(key)
            if cached is not None and cached[0] == stamp:
                self._tables.move_to_end(key)
                return cached[1]
            table = JournalTable.load(base)
            self._tables[key] = (stamp, table)
            while len(self._tables) > _TABLES:
                self._tables.popitem(last=False)
            return table

    def remember(self, path: Path) -> Path:
        """ Журнал, открытый вручную, — в начало списка журналов. """
        base = journal_base(path)
        self.table(base)
        with self._lock:
            self._opened = [base] + [p for p in self._opened if p != base]
        return base

    def journals(self, folders: list[Path]) -> list[dict[str, Any]]:
        """ Журналы: открытые вручную и найденные в папках (с подпапками). """
        seen: dict[str, Path] = {}
        for base in self._opened:
            seen.setdefault(str(base), base)
        for folder in folders:
            for base in find_journals(folder):
                seen.setdefault(str(base), base)
        items = []
        for base in seen.values():
            try:
                meta = self.table(base).meta
            except (OSError, ValueError) as error:
                items.append({"path": str(base), "name": base.name, "error": str(error)})
                continue
            items.append({"path": str(base), "name": base.name, "video": meta.video, "model": meta.model,
                          "source": meta.source, "run_id": meta.run_id, "created_at": meta.created_at})
        return items

    def query(self, path: Path, flt: JournalFilter, offset: int, limit: int) -> dict[str, Any]:
        """ Строки журнала по выборке и сведения о журнале. """
        table = self.table(path)
        page = table.query(flt, offset, limit)
        meta = table.meta
        return {
            "path": str(table.base),
            "video": meta.video, "model": meta.model, "source": meta.source, "run_id": meta.run_id,
            "fps": meta.fps, "rows": len(table), "zones": table.zones, "labels": table.labels,
            "states": table.states, "classes": meta.classes,
            "total": page.total, "counts": page.labels, "offset": offset,
            "items": [row.model_dump() for row in page.rows],
        }

    # ---- участки ----------------------------------------------------------------------------------

    def review(self, path: Path) -> dict[str, Any]:
        """ Участки на проверку и, если есть, журнал к ним. """
        review = read_review(path)
        journal = self._review_journal(path, review)
        return {
            "path": str(path), "video": review.video, "journal": str(journal) if journal else None,
            "source": review.source, "run_id": review.run_id,
            "segments": [{**s.model_dump(mode="json"), "index": i, "kind_name": _KIND_NAMES.get(s.kind.value, "")}
                         for i, s in enumerate(review.segments)],
        }

    @staticmethod
    def _review_journal(path: Path, review: ReviewFile) -> Path | None:
        """ Журнал к участкам: указанный в файле (путь — абсолютный или от папки файла участков). """
        if not review.journal:
            return None
        base = journal_base(Path(review.journal))
        if not base.is_absolute():
            base = path.parent / base
        return base if base.with_name(base.name + ".predictions.csv").is_file() else None

    def set_done(self, path: Path, index: int, done: bool) -> dict[str, Any]:
        """ Отмечает участок разобранным (или снимает отметку). """
        with self._lock:
            review = read_review(path)
            if not 0 <= index < len(review.segments):
                raise IndexError(f"No segment {index}")
            review.segments[index].done = done
            write_review(path, review)
        return self.review(path)

    def segment_items(self, path: Path, index: int) -> list[dict[str, Any]]:
        """ Кадры участка (не больше _SEGMENT_ITEMS, равномерно) с ответами модели из журнала. """
        review = read_review(path)
        if not 0 <= index < len(review.segments):
            raise IndexError(f"No segment {index}")
        segment = review.segments[index]
        count = segment.end_frame - segment.start_frame + 1
        frames = sorted({int(f) for f in np.linspace(segment.start_frame, segment.end_frame,
                                                      num=min(count, _SEGMENT_ITEMS))} | {segment.event_frame})
        journal = self._review_journal(path, review)
        table = self.table(journal) if journal is not None else None
        items = []
        for frame in frames:
            answer = table.answer(frame, segment.zone) if table is not None else None
            items.append({"frame": frame, "zone": segment.zone, "label": answer.label if answer else None,
                          "conf": answer.conf if answer else None, "state": answer.state if answer else None,
                          "event": frame == segment.event_frame})
        return items

    # ---- вырезы -----------------------------------------------------------------------------------

    def zone_region(self, journal: Path | None, zone: str) -> Region | None:
        """ Зона из сведений журнала (контур в координатах кадра). """
        if journal is None:
            return None
        found = next((z for z in self.table(journal).meta.zones if z.id == zone), None)
        if found is None or len(found.polygon) < 3:
            return None
        return Region(found.id, tuple(found.polygon))

    def frame(self, video: Path, index: int) -> Frame:
        """ Кадр видео (последние кадры — из памяти). """
        key = (str(video), index)
        with self._lock:
            cached = self._frames.get(key)
            if cached is not None:
                self._frames.move_to_end(key)
                return cached
            if self._reader is None or self._reader.path != video:
                if self._reader is not None:
                    self._reader.close()
                self._reader = VideoReader(video)
            frame = self._reader.read(index)
            if frame is None:
                raise IndexError(f"Frame {index} not found in {video.name}")
            self._frames[key] = frame
            while len(self._frames) > _FRAMES:
                self._frames.popitem(last=False)
            return frame

    def crop(self, video: Path, index: int, region: Region | None, max_side: int = 220, padding: int = 6) -> bytes:
        """ JPEG выреза зоны на кадре (без зоны — весь кадр), уменьшенный до max_side. """
        frame = self.frame(video, index)
        cut = crop_region(frame.image, region, padding=padding)
        if cut is None:
            raise ValueError(f"Zone {region.id if region else ''} is outside the frame")
        cv2 = get_cv2()
        image = cut.image
        scale = max_side / max(image.shape[:2])
        if scale < 1:
            image = cv2.resize(image, (max(1, int(image.shape[1] * scale)), max(1, int(image.shape[0] * scale))),
                               interpolation=cv2.INTER_AREA)
        ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise OSError("Failed to encode crop")
        return bytes(buffer.tobytes())

    def close(self) -> None:
        """ Освобождает видео и журналы. """
        with self._lock:
            if self._reader is not None:
                self._reader.close()
                self._reader = None
            self._frames.clear()
            self._tables.clear()
