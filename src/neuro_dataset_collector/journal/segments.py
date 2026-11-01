"""
    Участки видео на проверку: где прогон ошибся (по сравнению с эталоном) или где человек отметил ошибку.

    Файл <видео>.review.json: видео, журнал ответов модели (если есть) и участки — зона, кадры с–по,
    что ожидалось и что получилось. Нарезчик показывает участки и кадры в них.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ReviewFile", "ReviewKind", "ReviewSegment", "build_segments", "read_review", "review_path", "write_review"]

_REVIEW_SUFFIX = ".review.json"


def _now() -> str:
    """ Текущее время в ISO-формате. """
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class ReviewKind(str, Enum):
    """ Почему участок нужно проверить. """

    MISSING = "missing"      # в эталоне событие есть, машина не посчитала
    EXTRA = "extra"          # машина посчитала там, где в эталоне ничего нет
    CHANGED = "changed"      # событие есть, но отличается (количество, операция, время вне допуска)
    MARKED = "marked"        # человек отметил событие как ошибку видеоаналитики


class ReviewSegment(BaseModel):
    """ Участок видео в одной зоне. """

    model_config = ConfigDict(extra="ignore")

    zone: str
    start_frame: int
    end_frame: int
    event_frame: int
    kind: ReviewKind
    expected: str = ""       # что должно было быть, например «+1»
    actual: str = ""         # что получилось, например «нет события»
    comment: str = ""
    done: bool = False       # человек разобрал участок


class ReviewFile(BaseModel):
    """ Участки одного видео на проверку. """

    model_config = ConfigDict(extra="allow")

    format_version: int = 1
    video: str
    journal: str | None = None        # общая часть имени журнала ответов модели (без .predictions.*)
    source: str = ""                  # откуда участки: 'regression'
    run_id: str = ""
    before_seconds: float = 5.0
    after_seconds: float = 2.0
    segments: list[ReviewSegment] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


def review_path(folder: Path, video: Path) -> Path:
    """ Файл участков видео в папке folder: <имя видео>.review.json. """
    return Path(folder) / f"{Path(video).stem}{_REVIEW_SUFFIX}"


def build_segments(
    events: Iterable[tuple[str, int, ReviewKind, str, str, str]],
    fps: float,
    before_seconds: float = 5.0,
    after_seconds: float = 2.0,
    frame_count: int | None = None,
) -> list[ReviewSegment]:
    """
        Участки вокруг событий: (зона, кадр, вид, ожидалось, получилось, комментарий).
        Окно — before_seconds до кадра события и after_seconds после; пересекающиеся окна одной зоны
        объединяются (в объединённом участке — первое событие и все комментарии).
    """
    fps = fps if fps > 0 else 25.0
    before, after = round(before_seconds * fps), round(after_seconds * fps)
    last = frame_count - 1 if frame_count else None
    ordered = sorted(events, key=lambda e: (e[0], e[1]))
    segments: list[ReviewSegment] = []
    for zone, frame, kind, expected, actual, comment in ordered:
        start = max(0, frame - before)
        end = frame + after if last is None else min(last, frame + after)
        previous = segments[-1] if segments and segments[-1].zone == zone else None
        if previous is not None and start <= previous.end_frame:
            previous.end_frame = max(previous.end_frame, end)
            if comment and comment not in previous.comment:
                previous.comment = f"{previous.comment}; {comment}" if previous.comment else comment
            continue
        segments.append(ReviewSegment(zone=zone, start_frame=start, end_frame=end, event_frame=frame, kind=kind,
                                      expected=expected, actual=actual, comment=comment))
    return segments


def write_review(path: Path, review: ReviewFile) -> Path:
    """ Записывает файл участков (через временный файл). """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(review.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temp, path)
    return path


def read_review(path: Path) -> ReviewFile:
    """ Читает файл участков. """
    return ReviewFile.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
