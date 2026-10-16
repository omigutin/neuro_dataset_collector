""" Сборщик кадров и офлайн-нарезка. """

from .collector import FULL_FRAME_ID, Collector, CollectorStats, PredictionsInput
from .offline import OfflineReport, collect_videos

__all__ = ["FULL_FRAME_ID", "Collector", "CollectorStats", "OfflineReport", "PredictionsInput", "collect_videos"]
