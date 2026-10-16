""" Результаты операций сборщика: сохранение, сверка, выгрузка. """

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .types import SampleStatus

__all__ = ["ExportReport", "RejectReason", "SaveResult", "SaveStatus", "SyncReport"]


class SaveStatus(str, Enum):
    """ Итог попытки сохранить кадр. """

    SAVED = "saved"
    REJECTED = "rejected"


class RejectReason(str, Enum):
    """ Почему кадр не сохранён. """

    DUPLICATE = "duplicate"
    INTERVAL = "interval"
    QUOTA = "quota"
    SCENE_UNCHANGED = "scene_unchanged"
    RATE_LIMIT = "rate_limit"
    DISK_FULL = "disk_full"
    QUEUE_FULL = "queue_full"
    DISABLED = "disabled"
    EMPTY_REGION = "empty_region"


@dataclass(frozen=True)
class SaveResult:
    """ Итог сохранения одного выреза; message — понятное человеку пояснение. """

    status: SaveStatus
    message: str
    region_id: str | None = None
    label: str | None = None
    reason: RejectReason | None = None
    sample_id: int | None = None
    path: Path | None = None
    sample_status: SampleStatus | None = None
    trigger: str | None = None
    similar_to: Path | None = None
    distance: int | None = None

    @property
    def ok(self) -> bool:
        """ Сохранён ли кадр. """
        return self.status == SaveStatus.SAVED


@dataclass
class SyncReport:
    """ Итог сверки папок с каталогом после ручной проверки. """

    verified: int = 0
    relabeled: int = 0
    rejected: int = 0
    still_suspect: int = 0
    unsorted: int = 0
    unknown_files: list[Path] = field(default_factory=list)
    unknown_labels: set[str] = field(default_factory=set)

    def summary(self) -> str:
        """ Короткое описание итога для человека. """
        lines = [
            f"Подтверждено: {self.verified}",
            f"Из них с исправленным классом: {self.relabeled}",
            f"Отклонено (удалены): {self.rejected}",
            f"Осталось в «подозрительных»: {self.still_suspect}",
            f"Осталось без класса: {self.unsorted}",
        ]
        if self.unknown_labels:
            lines.append(f"Папки с неизвестными классами: {', '.join(sorted(self.unknown_labels))}")
        if self.unknown_files:
            lines.append(f"Файлы, которых нет в истории: {len(self.unknown_files)}")
        return "\n".join(lines)


@dataclass
class ExportReport:
    """ Итог выгрузки набора. """

    out_dir: Path
    exported: int = 0
    by_label: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        """ Короткое описание итога для человека. """
        parts = ", ".join(f"{label}: {count}" for label, count in sorted(self.by_label.items()))
        return f"Выгружено {self.exported} файлов в {self.out_dir}" + (f" ({parts})" if parts else "")
