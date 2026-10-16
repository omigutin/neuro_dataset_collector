""" Модели настроек сборщика кадров. """

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "AlwaysTrigger",
    "AuditTrigger",
    "AutoConfig",
    "CatalogConfig",
    "CollectorConfig",
    "ConfidenceTrigger",
    "ConsistencyConfig",
    "DedupLimit",
    "DedupScope",
    "DuplicatePolicy",
    "ImageFormat",
    "IntervalLimit",
    "LabelOverride",
    "Limit",
    "ManualConfig",
    "ModelConfig",
    "PredictionsSpace",
    "QuotaLimit",
    "RegionSource",
    "RegionsConfig",
    "RuntimeConfig",
    "RuntimeProfile",
    "SceneChangeLimit",
    "SourceConfig",
    "StorageConfig",
    "TaskType",
    "Trigger",
]


class _Model(BaseModel):
    """ Базовая модель настроек: лишние ключи запрещены, чтобы опечатки в YAML не терялись молча. """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TaskType(str, Enum):
    """ Для какой задачи собираются кадры. """

    CLASSIFY = "classify"
    DETECT = "detect"
    SEGMENT = "segment"


class RegionSource(str, Enum):
    """ Откуда берутся зоны для вырезки. """

    NONE = "none"
    FILE = "file"
    API = "api"


class DedupScope(str, Enum):
    """ Где искать похожие кадры. """

    COLLECTION = "collection"
    CATALOG = "catalog"


class DuplicatePolicy(str, Enum):
    """ Что делать при ручном сохранении похожего кадра. """

    REJECT = "reject"
    WARN = "warn"
    ALLOW = "allow"


class ImageFormat(str, Enum):
    """ Формат сохраняемых картинок. """

    JPG = "jpg"
    PNG = "png"


class PredictionsSpace(str, Enum):
    """ В каких координатах переданы предсказания. """

    REGION = "region"
    FRAME = "frame"


class RuntimeProfile(str, Enum):
    """ Профиль работы: разработка или боевой сервер. """

    DEV = "dev"
    PRODUCTION = "production"


class CatalogConfig(_Model):
    """ Где хранятся кадры и история. """

    root: Path = Path("datasets")
    collection: str = "default"
    dedup_scope: DedupScope = DedupScope.COLLECTION

    @field_validator("collection")
    @classmethod
    def _check_collection(cls, value: str) -> str:
        """ Проверяет ключ коллекции: части через '/', без пустых и служебных частей. """
        parts = [part.strip() for part in value.replace("\\", "/").split("/")]
        if not parts or any(not part or part in {".", ".."} for part in parts):
            raise ValueError(f"Invalid collection key: {value!r}")
        return "/".join(parts)


class RegionsConfig(_Model):
    """ Настройки зон для вырезки. """

    source: RegionSource = RegionSource.NONE
    file: Path | None = None
    labels: list[str] = Field(default_factory=list)
    where: dict[str, str | int | float | bool] = Field(default_factory=dict)
    id_field: str | None = None
    mask_outside: bool = False
    padding: int = Field(default=0, ge=0)
    fallback_to_full_frame: bool = True

    @model_validator(mode="after")
    def _check_file(self) -> RegionsConfig:
        """ Для источника 'file' путь к файлу обязателен. """
        if self.source == RegionSource.FILE and self.file is None:
            raise ValueError("regions.file is required when regions.source is 'file'")
        return self


class ManualConfig(_Model):
    """ Ручное сохранение по клавишам. """

    keys: dict[str, str] = Field(default_factory=dict)
    save_all_key: str = "s"
    on_duplicate: DuplicatePolicy = DuplicatePolicy.REJECT


class AlwaysTrigger(_Model):
    """ Повод «всегда»: каждый поданный кадр — кандидат. Для первого сбора без модели. """

    type: Literal["always"] = "always"


class ConfidenceTrigger(_Model):
    """ Повод «уверенность модели в диапазоне». """

    type: Literal["confidence"] = "confidence"
    min: float = Field(default=0.0, ge=0.0, le=1.0)
    max: float = Field(default=1.0, ge=0.0, le=1.0)
    include_empty: bool = True

    @model_validator(mode="after")
    def _check_range(self) -> ConfidenceTrigger:
        """ Нижняя граница не больше верхней. """
        if self.min > self.max:
            raise ValueError("confidence trigger: min must be <= max")
        return self


class AuditTrigger(_Model):
    """ Повод «случайная проверка»: доля кадров сохраняется независимо от уверенности. """

    type: Literal["audit"] = "audit"
    rate: float = Field(default=0.02, gt=0.0, le=1.0)
    seed: int | None = None


Trigger = Annotated[AlwaysTrigger | ConfidenceTrigger | AuditTrigger, Field(discriminator="type")]


IntervalKey = Literal["source", "region", "label"]
_ALL_INTERVAL_KEYS: tuple[IntervalKey, ...] = ("source", "region", "label")


class IntervalLimit(_Model):
    """ Ограничение «не чаще, чем раз в N секунд или N кадров». """

    type: Literal["interval"] = "interval"
    seconds: float | None = Field(default=1.0, gt=0.0)
    frames: int | None = Field(default=None, gt=0)
    per: list[IntervalKey] = Field(default_factory=lambda: list(_ALL_INTERVAL_KEYS))

    @model_validator(mode="after")
    def _check_unit(self) -> IntervalLimit:
        """ Должна быть задана ровно одна единица интервала. """
        if (self.seconds is None) == (self.frames is None):
            raise ValueError("interval limit: set exactly one of 'seconds' or 'frames'")
        return self


class DedupLimit(_Model):
    """ Ограничение «не сохранять почти одинаковые картинки». """

    type: Literal["dedup"] = "dedup"
    threshold: int = Field(default=5, ge=0, le=64)


class QuotaLimit(_Model):
    """ Ограничение по количеству кадров. """

    type: Literal["quota"] = "quota"
    per_label: int | None = Field(default=None, gt=0)
    total: int | None = Field(default=None, gt=0)


class SceneChangeLimit(_Model):
    """ Ограничение «картинка должна заметно измениться с прошлого сохранения». """

    type: Literal["scene_change"] = "scene_change"
    min_diff: float = Field(default=6.0, ge=0.0, le=255.0)


Limit = Annotated[IntervalLimit | DedupLimit | QuotaLimit | SceneChangeLimit, Field(discriminator="type")]


def _default_triggers() -> list[Trigger]:
    """ Поводы по умолчанию: сомнительные ответы модели. """
    return [ConfidenceTrigger(max=0.8)]


def _default_limits() -> list[Limit]:
    """ Ограничения по умолчанию: раз в секунду и без дублей. """
    return [IntervalLimit(seconds=1.0), DedupLimit()]


class LabelOverride(_Model):
    """
        Особые правила автоотбора для одного класса.
        enabled=false — не сохранять этот класс автоматически;
        min_conf / max_conf — свой диапазон уверенности для повода confidence;
        interval_seconds — свой интервал для ограничения interval (в секундах).
    """

    enabled: bool = True
    min_conf: float | None = Field(default=None, ge=0.0, le=1.0)
    max_conf: float | None = Field(default=None, ge=0.0, le=1.0)
    interval_seconds: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def _check_range(self) -> LabelOverride:
        """ Нижняя граница не больше верхней. """
        if self.min_conf is not None and self.max_conf is not None and self.min_conf > self.max_conf:
            raise ValueError("per_label: min_conf must be <= max_conf")
        return self


class AutoConfig(_Model):
    """ Автоматический отбор: поводы сохранить (любой) и ограничения (все), плюс особые правила по классам. """

    enabled: bool = True
    triggers: list[Trigger] = Field(default_factory=_default_triggers)
    limits: list[Limit] = Field(default_factory=_default_limits)
    per_label: dict[str, LabelOverride] = Field(default_factory=dict)

    def is_label_enabled(self, label: str | None) -> bool:
        """ Разрешён ли автосбор для класса (None — без класса). """
        override = self.per_label.get(label) if label is not None else None
        return override is None or override.enabled


class ConsistencyConfig(_Model):
    """ Проверка последовательности ответов модели (только для классификации). """

    enabled: bool = False
    context: int = Field(default=5, ge=1)
    max_run: int = Field(default=2, ge=1)
    review_always: list[str] = Field(default_factory=list)
    trusted_flips: dict[str, list[str]] = Field(default_factory=dict)


class StorageConfig(_Model):
    """ Как называть и сохранять файлы. """

    filename: str = "{source}_r{region}_f{frame:06d}"
    image_format: ImageFormat = ImageFormat.JPG
    jpeg_quality: int = Field(default=95, ge=10, le=100)
    keep_source_frame: bool = False
    min_free_gb: float = Field(default=1.0, ge=0.0)

    @field_validator("filename")
    @classmethod
    def _check_filename(cls, value: str) -> str:
        """ Шаблон имени должен собираться из известных полей. """
        try:
            rendered = value.format(**FILENAME_SAMPLE_FIELDS)
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError(f"Invalid filename template {value!r}: {error}") from error
        if not rendered or "/" in rendered or "\\" in rendered:
            raise ValueError(f"Filename template must not be empty or contain path separators: {value!r}")
        return value


# Поля, доступные в шаблоне имени файла, с примерами значений для проверки шаблона.
FILENAME_SAMPLE_FIELDS: dict[str, str | int | float] = {
    "source": "video",
    "region": "1",
    "frame": 0,
    "label": "label",
    "conf": 0.5,
    "mode": "auto",
    "date": "20260101",
    "time": "120000",
    "collection": "default",
}


class RuntimeConfig(_Model):
    """ Режим работы и защита основного процесса. """

    profile: RuntimeProfile = RuntimeProfile.DEV
    async_write: bool = True
    queue_size: int = Field(default=64, ge=1)
    max_saves_per_hour: int | None = Field(default=None, gt=0)
    disable_on_error: bool = False

    @model_validator(mode="after")
    def _apply_profile(self) -> RuntimeConfig:
        """ Профиль production включает фоновую запись, защиту от ошибок и лимит сохранений. """
        if self.profile == RuntimeProfile.PRODUCTION:
            object.__setattr__(self, "async_write", True)
            object.__setattr__(self, "disable_on_error", True)
            if self.max_saves_per_hour is None:
                object.__setattr__(self, "max_saves_per_hour", 300)
        return self


class SourceConfig(_Model):
    """ Видео для офлайн-нарезки и веб-инструмента. """

    path: Path | None = None
    extensions: list[str] = Field(default_factory=lambda: [".mp4", ".avi", ".mov", ".mkv"])
    recursive: bool = False
    step: int = Field(default=1, ge=1)


class ModelConfig(_Model):
    """ Необязательная модель для офлайн-сбора (ultralytics YOLO). """

    path: Path | None = None
    device: str | None = None
    conf: float = Field(default=0.05, ge=0.0, le=1.0)


class CollectorConfig(_Model):
    """ Все настройки сборщика кадров. """

    task: TaskType = TaskType.CLASSIFY
    classes: list[str] = Field(default_factory=list)
    unsorted_label: str = "_unsorted"
    predictions_space: PredictionsSpace = PredictionsSpace.REGION
    catalog: CatalogConfig = Field(default_factory=CatalogConfig)
    regions: RegionsConfig = Field(default_factory=RegionsConfig)
    manual: ManualConfig = Field(default_factory=ManualConfig)
    auto: AutoConfig = Field(default_factory=AutoConfig)
    consistency: ConsistencyConfig = Field(default_factory=ConsistencyConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)

    @field_validator("classes")
    @classmethod
    def _check_classes(cls, value: list[str]) -> list[str]:
        """ Имена классов уникальны, непусты и годятся для имени папки. """
        if len(set(value)) != len(value):
            raise ValueError("classes must be unique")
        for name in value:
            if not name or any(ch in name for ch in '/\\:*?"<>|') or name.startswith("_"):
                raise ValueError(f"Invalid class name: {name!r}")
        return value

    @model_validator(mode="after")
    def _check_references(self) -> CollectorConfig:
        """ Клавиши и правила ссылаются только на объявленные классы. """
        known = set(self.classes)
        if not known:
            return self
        referenced: list[str] = list(self.manual.keys.values()) + list(self.consistency.review_always)
        referenced.extend(name for name in self.auto.per_label if name != self.unsorted_label)
        for source, targets in self.consistency.trusted_flips.items():
            referenced.append(source)
            referenced.extend(targets)
        unknown = sorted({name for name in referenced if name not in known})
        if unknown:
            raise ValueError(f"Unknown classes referenced in config: {unknown}")
        return self

    @property
    def collection_dir(self) -> Path:
        """ Папка текущей коллекции. """
        return self.catalog.root.joinpath(*self.catalog.collection.split("/"))
