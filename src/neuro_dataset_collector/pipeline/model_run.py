"""
    Прогон видео моделью: ответ модели по каждой зоне проверяемых кадров записывается в журнал
    (формат общий с Production Counter), по желанию вырезы, где модель «не прошла условия», сохраняются
    на проверку человеком.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..config.models import JournalRunConfig, PredictionsSpace
from ..core.results import SaveResult
from ..core.types import Frame, Image, Prediction, Region, top_prediction
from ..imaging.crop import crop_region
from ..journal.predictions import JournalMeta, JournalWriter, JournalZone
from ..predictors.base import Predictor
from ..sources.video import VideoReader
from .collector import FULL_FRAME_ID, Collector
from .offline import sampling_step

__all__ = ["JOURNAL_DIR", "ModelRunReport", "SaveRule", "ZoneAnswer", "journal_base", "predict_zones", "run_model"]

# Подпапка датасета, куда нарезчик пишет журналы своих прогонов.
JOURNAL_DIR = "_journal"


@dataclass(frozen=True)
class ZoneAnswer:
    """ Ответ модели в одной зоне: лучший класс, его уверенность и все предсказания. """

    zone: str
    label: str | None
    conf: float | None
    predictions: tuple[Prediction, ...] = ()


def predict_zones(
    image: Image,
    regions: Sequence[Region],
    predictor: Predictor,
    *,
    mask_outside: bool = False,
    padding: int = 0,
    space: PredictionsSpace = PredictionsSpace.REGION,
) -> list[ZoneAnswer]:
    """ Ответы модели по зонам кадра (без зон — по всему кадру). Зоны вне кадра пропускаются. """
    answers: list[ZoneAnswer] = []
    zones: list[Region | None] = list(regions) or [None]
    for region in zones:
        crop = crop_region(image, region, mask_outside, padding)
        if crop is None:
            continue
        predictions = predictor(crop.image)
        if space == PredictionsSpace.FRAME:
            predictions = [p.shifted(crop.offset_x, crop.offset_y) for p in predictions]
        best = top_prediction(predictions)
        answers.append(ZoneAnswer(region.id if region is not None else FULL_FRAME_ID,
                                  best.label if best else None, float(best.confidence) if best else None,
                                  tuple(predictions)))
    return answers


class SaveRule:
    """
        Условия «модель не прошла проверку» (любое из): уверенность ниже порога, класс сменился
        по сравнению с прошлым проверенным кадром той же зоны, ответ — один из отмеченных классов.
        Помнит прошлый ответ по зонам, поэтому один объект — на одно видео.
    """

    def __init__(self, config: JournalRunConfig) -> None:
        self._config = config
        self._labels = set(config.labels)
        self._previous: dict[str, str | None] = {}

    @property
    def empty(self) -> bool:
        """ Не задано ни одного условия. """
        return self._config.conf_below is None and not self._config.flips and not self._labels

    def reason(self, answer: ZoneAnswer) -> str | None:
        """ Почему вырез нужно сохранить, или None. """
        seen = answer.zone in self._previous
        previous = self._previous.get(answer.zone)
        self._previous[answer.zone] = answer.label
        below = self._config.conf_below
        if below is not None and answer.label is not None and answer.conf is not None and answer.conf < below:
            return f"уверенность {answer.conf:.2f} < {below:.2f}"
        if self._config.flips and seen and previous != answer.label:
            return f"класс сменился: {previous or '—'} → {answer.label or '—'}"
        if answer.label in self._labels:
            return f"ответ {answer.label}"
        return None


@dataclass
class ModelRunReport:
    """ Итог прогона моделью. """

    frames: int = 0
    rows: int = 0
    matched: int = 0
    saved: int = 0
    journal: Path | None = None

    def summary(self) -> str:
        """ Итог для человека. """
        text = f"Проверено кадров: {self.frames}, ответов модели: {self.rows}"
        if self.matched:
            text += f", не прошли условия: {self.matched}, сохранено: {self.saved}"
        return text


def journal_base(collector: Collector, video: Path) -> Path:
    """ Где нарезчик хранит журнал прогона видео: <папка датасета>/_journal/<имя видео>. """
    return collector.config.collection_dir / JOURNAL_DIR / video.stem


def run_model(
    collector: Collector,
    video: Path,
    predictor: Predictor,
    *,
    base: Path | None = None,
    model_name: str = "",
    stop: threading.Event | None = None,
    on_frame: Callable[[Frame, list[ZoneAnswer]], None] | None = None,
    on_result: Callable[[SaveResult], None] | None = None,
) -> ModelRunReport:
    """
        Прогоняет видео моделью и пишет журнал (base — общая часть имени файлов журнала,
        по умолчанию в папке датасета). Шаг — как у автонарезки с моделью.
        Если в настройках journal.save — вырезы, не прошедшие условия, сохраняются на проверку («_suspect»).
    """
    config = collector.config
    rule = SaveRule(config.journal)
    save = config.journal.save and not rule.empty
    base = base if base is not None else journal_base(collector, video)
    report = ModelRunReport()
    with VideoReader(video) as reader:
        first = reader.read(0)
        if first is None:
            raise ValueError(f"Video has no frames: {video}")
        regions = collector.regions_for(first.width, first.height)
        by_id = {region.id: region for region in regions}
        meta = JournalMeta(
            video=str(video), fps=reader.fps, width=first.width, height=first.height,
            zones=[JournalZone(id=r.id, polygon=list(r.polygon), label=str(r.attrs.get("label", ""))) for r in regions],
            classes=list(config.classes), model=model_name, source="collector",
        )
        with JournalWriter(base, meta) as writer:
            report.journal = writer.csv_path
            for frame in reader.frames(step=sampling_step(config, reader.fps, with_model=True)):
                if stop is not None and stop.is_set():
                    break
                report.frames += 1
                answers = predict_zones(frame.image, regions, predictor, mask_outside=config.regions.mask_outside,
                                        padding=config.regions.padding, space=config.predictions_space)
                seconds = frame.timestamp if frame.timestamp is not None else frame.index / (reader.fps or 25.0)
                for answer in answers:
                    writer.write(frame.index, seconds, answer.zone, answer.label, answer.conf)
                    report.rows += 1
                    reason = rule.reason(answer)
                    if reason is None:
                        continue
                    report.matched += 1
                    if not save:
                        continue
                    for result in collector.flag(frame, list(answer.predictions), region=by_id.get(answer.zone),
                                                 reason=reason):
                        if result.ok:
                            report.saved += 1
                        if on_result is not None:
                            on_result(result)
                if on_frame is not None:
                    on_frame(frame, answers)
            writer.close(frames=report.frames)
    collector.flush()
    return report
