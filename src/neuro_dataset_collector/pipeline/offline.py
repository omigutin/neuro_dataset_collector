""" Офлайн-нарезка: прогон видеофайлов через сборщик (с моделью или без). """

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..config.models import PredictionsSpace
from ..core.results import SaveResult
from ..core.types import Frame, Region
from ..imaging.crop import crop_region
from ..predictors.base import Predictor
from ..sources.video import VideoReader, list_videos
from .collector import Collector

__all__ = ["OfflineReport", "collect_videos"]

_log = logging.getLogger("neuro_dataset_collector")


@dataclass
class OfflineReport:
    """ Итог офлайн-нарезки. """

    videos: int = 0
    frames: int = 0
    saved: int = 0
    rejected_by_reason: Counter[str] = field(default_factory=Counter)

    def summary(self) -> str:
        """ Итог для человека. """
        reasons = ", ".join(f"{k}: {v}" for k, v in self.rejected_by_reason.most_common())
        return (f"Видео: {self.videos}, просмотрено кадров: {self.frames}, сохранено: {self.saved}"
                + (f", отклонено: {reasons}" if reasons else ""))


def _offer_with_model(collector: Collector, frame: Frame, predictor: Predictor) -> list[SaveResult]:
    """ Запускает модель на каждой зоне и предлагает зоны сборщику. """
    config = collector.config
    regions: list[Region | None] = list(collector.regions_for(frame.width, frame.height)) or [None]
    results: list[SaveResult] = []
    for region in regions:
        crop = crop_region(frame.image, region, config.regions.mask_outside, config.regions.padding)
        if crop is None:
            continue
        predictions = predictor(crop.image)
        if config.predictions_space == PredictionsSpace.FRAME:
            predictions = [p.shifted(crop.offset_x, crop.offset_y) for p in predictions]
        if region is None:
            results.extend(collector.offer(frame, predictions))
        else:
            results.extend(collector.offer(frame, predictions, region=region))
    return results


def collect_videos(
    collector: Collector,
    videos: Sequence[Path] | None = None,
    *,
    predictor: Predictor | None = None,
    on_result: Callable[[SaveResult], None] | None = None,
    on_video: Callable[[Path, int, int], None] | None = None,
    stop: threading.Event | None = None,
) -> OfflineReport:
    """
        Прогоняет видео через сборщик. Без модели кадры отбираются только по поводам вроде «always»
        и ограничениям (интервал, изменение картинки, дубли) — так собирается стартовый набор.
    """
    config = collector.config
    if videos is None:
        if config.source.path is None:
            raise ValueError("source.path is not set")
        videos = list_videos(config.source.path, config.source.extensions, config.source.recursive)
    report = OfflineReport()
    for number, video in enumerate(videos, start=1):
        if stop is not None and stop.is_set():
            break
        if on_video is not None:
            on_video(video, number, len(videos))
        try:
            reader = VideoReader(video)
        except OSError as error:
            _log.error("%s", error)
            continue
        report.videos += 1
        with reader:
            for frame in reader.frames(step=config.source.step):
                if stop is not None and stop.is_set():
                    break
                report.frames += 1
                results = _offer_with_model(collector, frame, predictor) if predictor else collector.offer(frame)
                for result in results:
                    if result.ok:
                        report.saved += 1
                    elif result.reason is not None:
                        report.rejected_by_reason[result.reason.value] += 1
                    if on_result is not None:
                        on_result(result)
    collector.flush()
    return report
