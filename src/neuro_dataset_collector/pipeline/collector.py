""" Сборщик кадров: автоматический отбор и ручное сохранение. """

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..catalog.catalog import Catalog, SampleRecord
from ..config.loader import dump_config
from ..config.models import (
    CollectorConfig,
    DedupLimit,
    DedupScope,
    DuplicatePolicy,
    PredictionsSpace,
    TaskType,
)
from ..consistency.tracker import FlipAction, FlipDecision, StreamConsistency
from ..core.results import RejectReason, SaveResult, SaveStatus
from ..core.types import Frame, Prediction, Region, SampleStatus, SaveMode, top_prediction
from ..imaging.crop import Crop, crop_region, predictions_to_crop
from ..regions.providers import RegionProvider, create_region_provider
from ..selectors.base import Candidate, Rejection
from ..selectors.limits import DedupRule, build_limits, check_limits, commit_limits
from ..selectors.triggers import build_triggers, first_trigger
from ..storage.layout import PathPlanner
from ..storage.writer import DiskGuard, FileWriter, MoveJob, QueueFullError, WriteJob
from ..storage.yolo import yolo_lines

__all__ = ["FULL_FRAME_ID", "Collector", "CollectorStats", "PredictionsInput"]

_log = logging.getLogger("neuro_dataset_collector")

FULL_FRAME_ID = "full"

PredictionsInput = Sequence[Prediction] | Mapping[str, Sequence[Prediction]] | None


@dataclass
class CollectorStats:
    """ Счётчики: за текущий сеанс и всего в коллекции. """

    session_by_label: dict[str, int]
    total_by_label: dict[str, int]
    by_status: dict[str, int]
    rejected_by_reason: dict[str, int]


@dataclass
class _Pending:
    """ Вырез вместе со сведениями о кадре; живёт, пока по нему может прийти решение. """

    candidate: Candidate
    frame: Frame
    region: Region | None
    crop: Crop
    sample_id: int | None = None
    attrs: dict[str, object] = field(default_factory=dict)


class Collector:
    """
        Главный объект библиотеки.
        offer() — автоматический отбор (кадр + предсказания модели);
        save() — ручное сохранение (по клавише) в указанный класс.
        Каждый вызов возвращает список SaveResult с понятным пояснением.
    """

    def __init__(
        self,
        config: CollectorConfig,
        *,
        regions: Sequence[Region] | None = None,
        region_provider: RegionProvider | None = None,
        catalog: Catalog | None = None,
    ) -> None:
        self._config = config
        self._lock = threading.RLock()
        self._owns_catalog = catalog is None
        self._catalog = catalog or Catalog(config.catalog.root)
        collection = config.catalog.collection
        self._provider = region_provider or create_region_provider(config.regions, regions)
        self._planner = PathPlanner(config, self._catalog.filenames(collection))
        self._writer = FileWriter(config.runtime.async_write, config.runtime.queue_size)
        self._disk = DiskGuard(config.catalog.root, config.storage.min_free_gb)
        dedup_collection = collection if config.catalog.dedup_scope == DedupScope.COLLECTION else None
        self._triggers = build_triggers(config.auto.triggers, config.auto.per_label)
        self._limits = build_limits(config.auto.limits, self._catalog, collection, dedup_collection,
                                    config.auto.per_label)
        dedup_threshold = next((lim.threshold for lim in config.auto.limits if isinstance(lim, DedupLimit)), 5)
        self._dedup = DedupRule(DedupLimit(threshold=dedup_threshold), self._catalog, dedup_collection)
        self._consistency: StreamConsistency[_Pending] | None = None
        if config.consistency.enabled and config.task == TaskType.CLASSIFY:
            self._consistency = StreamConsistency(config.consistency)
        self._session_id = self._catalog.start_session(collection, dump_config(config))
        self._enabled = True
        self._auto_save_times: deque[float] = deque()
        self._last_source: str | None = None
        self._session_counts: Counter[str] = Counter()
        self._rejections: Counter[str] = Counter()
        self._listeners: list[Callable[[SaveResult], None]] = []

    # ---- свойства -------------------------------------------------------------------------------

    @property
    def config(self) -> CollectorConfig:
        """ Текущие настройки. """
        return self._config

    @property
    def catalog(self) -> Catalog:
        """ Каталог с историей. """
        return self._catalog

    @property
    def enabled(self) -> bool:
        """ Работает ли сборщик (в боевом профиле отключается при ошибке). """
        return self._enabled

    @property
    def session_id(self) -> int:
        """ Номер сеанса в каталоге. """
        return self._session_id

    def add_listener(self, listener: Callable[[SaveResult], None]) -> None:
        """ Подписка на каждый результат сохранения (для интерфейсов и логов). """
        self._listeners.append(listener)

    def regions_for(self, width: int, height: int) -> list[Region]:
        """ Зоны для кадра заданного размера (пусто — весь кадр). """
        return self._provider.regions(width, height)

    # ---- автоматический отбор ---------------------------------------------------------------------

    def offer(
        self,
        frame: Frame,
        predictions: PredictionsInput = None,
        *,
        region: Region | None = None,
        reference_label: str | None = None,
    ) -> list[SaveResult]:
        """
            Предлагает кадр для автоматического отбора.
            predictions — список (если зона одна или весь кадр) или словарь «id зоны → список».
            reference_label — класс, который считает верным программа-хозяин (например, итог машины состояний);
            при расхождении с моделью вырез сохраняется как подозрительный.
        """
        with self._lock:
            if not self._enabled or not self._config.auto.enabled:
                return []
            try:
                return self._offer(frame, predictions, region, reference_label)
            except Exception as error:
                if not self._config.runtime.disable_on_error:
                    raise
                # На боевом сервере сбор кадров не должен ронять основной процесс: выключаемся и пишем в лог.
                _log.exception("Frame collector disabled after error: %s", error)
                self._enabled = False
                return [SaveResult(SaveStatus.REJECTED, f"Сборщик отключён из-за ошибки: {error}",
                                   reason=RejectReason.DISABLED)]

    def _offer(
        self, frame: Frame, predictions: PredictionsInput, region: Region | None, reference_label: str | None
    ) -> list[SaveResult]:
        self._on_source(frame.source)
        results: list[SaveResult] = []
        for zone, zone_predictions in self._pairs(frame, predictions, region):
            pending = self._make_pending(frame, zone, zone_predictions)
            if pending is None:
                continue
            candidate = pending.candidate
            model_label = candidate.label if self._config.task == TaskType.CLASSIFY else None
            decisions: list[FlipDecision[_Pending]] = []
            if self._consistency is not None and model_label is not None:
                decisions = self._consistency.observe(candidate.stream_key, model_label, pending)

            if reference_label is not None and model_label is not None and model_label != reference_label:
                results.append(self._save_flagged(pending, model_label, reference_label, FlipAction.SUSPECT,
                                                  f"модель: {model_label}, программа: {reference_label}"))
            else:
                trigger = first_trigger(self._triggers, candidate) if self._label_enabled(candidate) else None
                if trigger is not None:
                    rejection = self._runtime_rejection(auto=True) or check_limits(self._limits, candidate)
                    if rejection is not None:
                        results.append(self._rejected(rejection, candidate, trigger))
                    else:
                        result = self._store(pending, SaveMode.AUTO, trigger, SampleStatus.PENDING,
                                             label=candidate.label, model_label=model_label)
                        if result.ok:
                            commit_limits(self._limits, candidate)
                        results.append(result)

            for decision in decisions:
                results.append(self._apply_decision(decision))
        return results

    def _apply_decision(self, decision: FlipDecision[_Pending]) -> SaveResult:
        """ Применяет решение по выбросу: переносит уже сохранённый вырез или сохраняет новый. """
        pending = decision.item
        if pending.sample_id is None:
            return self._save_flagged(pending, decision.label, decision.proposed, decision.action, decision.reason)
        record = self._catalog.get(pending.sample_id)
        if record is None or record.status not in (SampleStatus.PENDING, SampleStatus.SUSPECT):
            return SaveResult(SaveStatus.REJECTED, "Запись уже недоступна для переноса", region_id=record.region_id
                              if record else None)
        source_path = self._catalog.absolute(record)
        if decision.action == FlipAction.AUTO_FIX and decision.proposed is not None:
            target = self._planner.image_dir(decision.proposed) / source_path.name
            status, label, origin = SampleStatus.PENDING, decision.proposed, "auto_fix"
            message = f"Класс исправлен автоматически ({decision.reason}): {decision.label} → {decision.proposed}"
        else:
            target = self._planner.image_dir(decision.label, decision.proposed, suspect=True) / source_path.name
            status, label, origin = SampleStatus.SUSPECT, decision.label, "consistency"
            message = f"Перенесён в подозрительные ({decision.reason})"
        self._catalog.update(record.id or 0, origin=origin, path=self._catalog.relative(target), label=label,
                             status=status, proposed_label=decision.proposed)
        sample_id = record.id or 0
        self._submit(MoveJob(source_path, target, on_done=lambda err: self._on_written(sample_id, err)))
        result = SaveResult(SaveStatus.SAVED, message, region_id=record.region_id, label=label,
                            sample_id=record.id, path=target, sample_status=status, trigger="consistency")
        self._notify(result)
        return result

    def _save_flagged(
        self, pending: _Pending, label: str, proposed: str | None, action: FlipAction, reason: str
    ) -> SaveResult:
        """ Сохраняет новый вырез, отмеченный правилами последовательности. """
        candidate = pending.candidate
        rejection = self._runtime_rejection(auto=True) or self._dedup.check(candidate)
        if rejection is not None:
            return self._rejected(rejection, candidate, "consistency")
        if action == FlipAction.AUTO_FIX and proposed is not None:
            return self._store(pending, SaveMode.AUTO, "consistency", SampleStatus.PENDING, label=proposed,
                               model_label=label, proposed=proposed, note=f"класс исправлен ({reason})")
        return self._store(pending, SaveMode.AUTO, "consistency", SampleStatus.SUSPECT, label=label,
                           model_label=label, proposed=proposed, note=f"подозрительный ({reason})")

    # ---- ручное сохранение ------------------------------------------------------------------------

    def save(
        self,
        frame: Frame,
        label: str | None = None,
        *,
        region: Region | None = None,
        region_ids: Sequence[str] | None = None,
        predictions: PredictionsInput = None,
        force: bool = False,
    ) -> list[SaveResult]:
        """
            Ручное сохранение. Для классификации label — класс (папка); без него — в папку «без класса».
            Без region и region_ids сохраняются все зоны (или весь кадр).
            force=True сохраняет даже похожий кадр.
        """
        if label is not None and self._config.classes and label not in self._config.classes:
            raise ValueError(f"Unknown class {label!r}; known classes: {self._config.classes}")
        with self._lock:
            results: list[SaveResult] = []
            for zone, zone_predictions in self._pairs(frame, predictions, region):
                if region_ids is not None and (zone.id if zone else FULL_FRAME_ID) not in region_ids:
                    continue
                pending = self._make_pending(frame, zone, zone_predictions, label=label)
                if pending is None:
                    results.append(SaveResult(SaveStatus.REJECTED, "Зона вне кадра", region_id=zone.id if zone else
                                              None, reason=RejectReason.EMPTY_REGION))
                    continue
                results.append(self._save_manual(pending, force))
            return results

    def _save_manual(self, pending: _Pending, force: bool) -> SaveResult:
        candidate = pending.candidate
        runtime = self._runtime_rejection(auto=False)
        if runtime is not None:
            return self._rejected(runtime, candidate, "manual")
        policy = self._config.manual.on_duplicate
        warning: Rejection | None = None
        if not force and policy != DuplicatePolicy.ALLOW:
            warning = self._dedup.check(candidate)
            if warning is not None and policy == DuplicatePolicy.REJECT:
                return self._rejected(warning, candidate, "manual")
        model = top_prediction(candidate.predictions)
        result = self._store(pending, SaveMode.MANUAL, "manual", SampleStatus.PENDING, label=candidate.label,
                             model_label=model.label if model else None,
                             note=f"внимание: {warning.message}" if warning else None)
        if warning is not None and result.ok:
            result = replace(result, similar_to=warning.similar_to, distance=warning.distance)
        return result

    # ---- общее --------------------------------------------------------------------------------------

    def _on_source(self, source: str) -> None:
        """ Новое видео — нерешённые выбросы прошлого видео забываются. """
        if source != self._last_source:
            if self._consistency is not None:
                self._consistency.reset()
            self._last_source = source

    def _pairs(
        self, frame: Frame, predictions: PredictionsInput, region: Region | None
    ) -> list[tuple[Region | None, list[Prediction]]]:
        """ Разбивает предсказания по зонам. """
        zones: list[Region | None] = [region] if region is not None else list(
            self._provider.regions(frame.width, frame.height)
        )
        if not zones:
            zones = [None]
        if predictions is None:
            return [(zone, []) for zone in zones]
        if isinstance(predictions, Mapping):
            return [(zone, list(predictions.get(zone.id if zone else FULL_FRAME_ID, ()))) for zone in zones]
        if len(zones) == 1:
            return [(zones[0], list(predictions))]
        if self._config.predictions_space == PredictionsSpace.FRAME and self._config.task != TaskType.CLASSIFY:
            # Объекты в координатах кадра раздаём зонам по центру бокса.
            pairs: list[tuple[Region | None, list[Prediction]]] = []
            for zone in zones:
                assert zone is not None
                box = zone.bbox
                inside = [p for p in predictions if p.bbox is not None and box.contains(p.bbox.center)]
                pairs.append((zone, inside))
            return pairs
        raise ValueError("Predictions for several regions must be a mapping: region id -> predictions")

    def _make_pending(
        self, frame: Frame, zone: Region | None, predictions: list[Prediction], label: str | None = None
    ) -> _Pending | None:
        """ Вырезает зону и готовит кандидата. """
        crop = crop_region(frame.image, zone, self._config.regions.mask_outside, self._config.regions.padding)
        if crop is None:
            return None
        local = predictions_to_crop(predictions, crop) if self._config.predictions_space == PredictionsSpace.FRAME \
            else list(predictions)
        if label is None and self._config.task == TaskType.CLASSIFY:
            top = top_prediction(local)
            label = top.label if top else None
        candidate = Candidate(
            image=crop.image,
            source=frame.source,
            frame_index=frame.index,
            timestamp=frame.timestamp,
            region_id=zone.id if zone else None,
            label=label if self._config.task == TaskType.CLASSIFY else None,
            predictions=local,
        )
        return _Pending(candidate, frame, zone, crop)

    def _label_enabled(self, candidate: Candidate) -> bool:
        """ Разрешён ли автосбор для класса кандидата (настройка auto.per_label, только классификация). """
        if self._config.task != TaskType.CLASSIFY:
            return True
        return self._config.auto.is_label_enabled(candidate.label or self._config.unsorted_label)

    def _runtime_rejection(self, auto: bool) -> Rejection | None:
        """ Ограничения режима работы: свободное место и лимит в час (только для автосбора). """
        if not self._disk.has_space():
            return Rejection(RejectReason.DISK_FULL, "Мало свободного места на диске")
        limit = self._config.runtime.max_saves_per_hour
        if auto and limit is not None:
            now = time.monotonic()
            while self._auto_save_times and now - self._auto_save_times[0] > 3600:
                self._auto_save_times.popleft()
            if len(self._auto_save_times) >= limit:
                return Rejection(RejectReason.RATE_LIMIT, f"Достигнут лимит {limit} автосохранений в час")
        return None

    def _rejected(self, rejection: Rejection, candidate: Candidate, trigger: str | None) -> SaveResult:
        self._rejections[rejection.reason.value] += 1
        result = SaveResult(
            SaveStatus.REJECTED,
            f"Не сохранён: {rejection.message}",
            region_id=candidate.region_id,
            label=candidate.label,
            reason=rejection.reason,
            trigger=trigger,
            similar_to=rejection.similar_to,
            distance=rejection.distance,
        )
        self._notify(result)
        return result

    def _store(
        self,
        pending: _Pending,
        mode: SaveMode,
        trigger: str,
        status: SampleStatus,
        *,
        label: str | None,
        model_label: str | None,
        proposed: str | None = None,
        note: str | None = None,
    ) -> SaveResult:
        """ Регистрирует вырез в каталоге и ставит запись файла. """
        config = self._config
        candidate, frame = pending.candidate, pending.frame
        name = self._planner.filename(source=frame.source, region=candidate.region_id, frame=frame.index,
                                      label=label, conf=candidate.confidence, mode=mode.value)
        directory = self._planner.image_dir(label, proposed, suspect=status == SampleStatus.SUSPECT)
        path = directory / name
        label_path: Path | None = None
        label_text: str | None = None
        if config.task != TaskType.CLASSIFY and candidate.predictions:
            label_path = self._planner.label_path(path)
            label_text = yolo_lines(candidate.predictions, config.classes, pending.crop.width,
                                    pending.crop.height, config.task)
        record = SampleRecord(
            collection=config.catalog.collection,
            task=config.task.value,
            path=self._catalog.relative(path),
            status=status,
            mode=mode,
            source=frame.source,
            frame_index=frame.index,
            hash=candidate.hash,
            width=pending.crop.width,
            height=pending.crop.height,
            label=label,
            model_label=model_label,
            proposed_label=proposed,
            confidence=candidate.confidence,
            trigger=trigger,
            timestamp=frame.timestamp,
            region_id=candidate.region_id,
            region=[list(p) for p in pending.region.polygon] if pending.region else None,
            predictions=[p.to_dict() for p in candidate.predictions],
            attrs={**frame.attrs, **(dict(pending.region.attrs) if pending.region else {})},
            session_id=self._session_id,
        )
        sample_id = self._catalog.add(record)
        job = WriteJob(path, candidate.image, config.storage.image_format, config.storage.jpeg_quality,
                       label_path, label_text, on_done=lambda err: self._on_written(sample_id, err))
        try:
            self._writer.submit(job)
        except QueueFullError:
            self._catalog.update(sample_id, origin="queue_full", status=SampleStatus.FAILED)
            self._planner.release(name)
            return self._rejected(Rejection(RejectReason.QUEUE_FULL, "Очередь записи переполнена"), candidate,
                                  trigger)
        if config.storage.keep_source_frame:
            frame_job = WriteJob(self._planner.frame_path(frame.source, frame.index), frame.image,
                                 config.storage.image_format, config.storage.jpeg_quality, skip_if_exists=True)
            self._submit(frame_job)
        pending.sample_id = sample_id
        if mode == SaveMode.AUTO:
            self._auto_save_times.append(time.monotonic())
        self._session_counts[label or ""] += 1
        where = "в подозрительные" if status == SampleStatus.SUSPECT else f"в «{label or config.unsorted_label}»" \
            if config.task == TaskType.CLASSIFY else ""
        message = f"Сохранён {where}: {record.path}".replace("  ", " ")
        if note:
            message += f" ({note})"
        result = SaveResult(SaveStatus.SAVED, message, region_id=candidate.region_id, label=label,
                            sample_id=sample_id, path=path, sample_status=status, trigger=trigger)
        self._notify(result)
        return result

    def _submit(self, job: WriteJob | MoveJob) -> None:
        """ Ставит служебное задание; при переполнении очереди пишет в лог. """
        try:
            self._writer.submit(job)
        except QueueFullError:
            _log.warning("Writer queue is full, job skipped: %s", job)

    def _on_written(self, sample_id: int, error: Exception | None) -> None:
        """ Ошибка записи помечает запись как сбойную. """
        if error is not None:
            self._catalog.update(sample_id, origin="write_error", status=SampleStatus.FAILED)

    def _notify(self, result: SaveResult) -> None:
        for listener in self._listeners:
            listener(result)

    def stats(self) -> CollectorStats:
        """ Счётчики сеанса и коллекции. """
        collection = self._config.catalog.collection
        return CollectorStats(
            session_by_label=dict(self._session_counts),
            total_by_label=self._catalog.label_counts(collection),
            by_status=self._catalog.status_counts(collection),
            rejected_by_reason=dict(self._rejections),
        )

    def flush(self) -> None:
        """ Ждёт, пока все файлы будут записаны. """
        self._writer.flush()

    def close(self) -> None:
        """ Дописывает файлы и закрывает каталог (если он создан сборщиком). """
        self._writer.close()
        if self._owns_catalog:
            self._catalog.close()

    def __enter__(self) -> Collector:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
