""" neuro_dataset_collector — сбор и раскладка кадров из видео для обучения нейросетей. """

from .catalog import Catalog, SampleRecord
from .config import CollectorConfig, TaskType, dump_config, load_config, save_config
from .core import (
    BBox,
    ExportReport,
    Frame,
    Prediction,
    Region,
    RejectReason,
    SampleStatus,
    SaveMode,
    SaveResult,
    SaveStatus,
    SyncReport,
)
from .export import export_dataset
from .pipeline import Collector, CollectorStats, OfflineReport, collect_videos
from .review import sync_collection

__version__ = "0.2.0"

__all__ = [
    "BBox",
    "Catalog",
    "Collector",
    "CollectorConfig",
    "CollectorStats",
    "ExportReport",
    "Frame",
    "OfflineReport",
    "Prediction",
    "Region",
    "RejectReason",
    "SampleRecord",
    "SampleStatus",
    "SaveMode",
    "SaveResult",
    "SaveStatus",
    "SyncReport",
    "TaskType",
    "__version__",
    "collect_videos",
    "dump_config",
    "export_dataset",
    "load_config",
    "save_config",
    "sync_collection",
]
