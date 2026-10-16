""" Каталог: история всех сохранённых кадров в SQLite. """

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ..core.types import AttrValue, SampleStatus, SaveMode
from .hash_index import HashIndex

__all__ = ["CATALOG_FILENAME", "Catalog", "SampleRecord"]

CATALOG_FILENAME = "catalog.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection TEXT NOT NULL,
    started_at TEXT NOT NULL,
    config_json TEXT
);
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection TEXT NOT NULL,
    task TEXT NOT NULL,
    path TEXT NOT NULL,
    label TEXT,
    model_label TEXT,
    proposed_label TEXT,
    confidence REAL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    trigger TEXT,
    source TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    timestamp REAL,
    region_id TEXT,
    region_json TEXT,
    hash INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    predictions_json TEXT,
    attrs_json TEXT,
    session_id INTEGER,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    exported_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_samples_collection ON samples(collection, status);
CREATE TABLE IF NOT EXISTS label_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL,
    old_label TEXT,
    new_label TEXT,
    old_status TEXT,
    new_status TEXT,
    origin TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection TEXT NOT NULL,
    target TEXT NOT NULL,
    out_dir TEXT NOT NULL,
    count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""

_COLUMNS = (
    "id, collection, task, path, label, model_label, proposed_label, confidence, status, mode, trigger, source, "
    "frame_index, timestamp, region_id, region_json, hash, width, height, predictions_json, attrs_json, session_id, "
    "created_at, reviewed_at, exported_at"
)

# Отклонённые человеком кадры остаются в индексе, чтобы тот же мусор не собирался снова.
_INDEXED_STATUSES = (
    SampleStatus.PENDING.value,
    SampleStatus.SUSPECT.value,
    SampleStatus.VERIFIED.value,
    SampleStatus.REJECTED.value,
)


def _now() -> str:
    """ Текущее время в ISO-формате. """
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _to_signed(value: int) -> int:
    """ SQLite хранит только знаковые 64-битные целые. """
    return value - (1 << 64) if value >= (1 << 63) else value


def _to_unsigned(value: int) -> int:
    """ Обратное преобразование к беззнаковому отпечатку. """
    return value + (1 << 64) if value < 0 else value


@dataclass
class SampleRecord:
    """ Запись о сохранённом вырезе. path — относительный путь от корня каталога. """

    collection: str
    task: str
    path: str
    status: SampleStatus
    mode: SaveMode
    source: str
    frame_index: int
    hash: int
    width: int
    height: int
    label: str | None = None
    model_label: str | None = None
    proposed_label: str | None = None
    confidence: float | None = None
    trigger: str | None = None
    timestamp: float | None = None
    region_id: str | None = None
    region: list[list[float]] | None = None
    predictions: list[dict[str, object]] = field(default_factory=list)
    attrs: dict[str, AttrValue] = field(default_factory=dict)
    session_id: int | None = None
    created_at: str = field(default_factory=_now)
    reviewed_at: str | None = None
    exported_at: str | None = None
    id: int | None = None

    @property
    def filename(self) -> str:
        """ Имя файла без папок. """
        return PurePosixPath(self.path).name


class Catalog:
    """ История сохранённых кадров одного корня каталога; потокобезопасна. """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._root / CATALOG_FILENAME), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._indexes: dict[str, HashIndex] = {}
        self._load_indexes()

    @property
    def root(self) -> Path:
        """ Корень каталога. """
        return self._root

    def close(self) -> None:
        """ Закрывает базу. """
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Catalog:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _load_indexes(self) -> None:
        """ Загружает отпечатки всех действующих записей в память. """
        placeholders = ",".join("?" for _ in _INDEXED_STATUSES)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, collection, hash FROM samples WHERE status IN ({placeholders})", _INDEXED_STATUSES
            ).fetchall()
        for row in rows:
            self._index(row["collection"]).add(int(row["id"]), _to_unsigned(int(row["hash"])))

    def _index(self, collection: str) -> HashIndex:
        """ Индекс коллекции (создаётся при первом обращении). """
        index = self._indexes.get(collection)
        if index is None:
            index = self._indexes[collection] = HashIndex()
        return index

    def absolute(self, record: SampleRecord) -> Path:
        """ Абсолютный путь к файлу записи. """
        return self._root.joinpath(*PurePosixPath(record.path).parts)

    def relative(self, path: Path) -> str:
        """ Путь относительно корня в виде с прямыми слэшами. """
        return Path(path).resolve().relative_to(self._root.resolve()).as_posix()

    def start_session(self, collection: str, config_json: str | None = None) -> int:
        """ Регистрирует сеанс сбора. """
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO sessions (collection, started_at, config_json) VALUES (?, ?, ?)",
                (collection, _now(), config_json),
            )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def add(self, record: SampleRecord) -> int:
        """ Добавляет запись и её отпечаток; возвращает id. """
        with self._lock:
            cursor = self._conn.execute(
                f"INSERT INTO samples ({_COLUMNS}) VALUES ({','.join('?' for _ in range(25))})",
                (
                    None, record.collection, record.task, record.path, record.label, record.model_label,
                    record.proposed_label, record.confidence, record.status.value, record.mode.value,
                    record.trigger, record.source, record.frame_index, record.timestamp, record.region_id,
                    json.dumps(record.region) if record.region is not None else None,
                    _to_signed(record.hash), record.width, record.height,
                    json.dumps(record.predictions, ensure_ascii=False),
                    json.dumps(record.attrs, ensure_ascii=False),
                    record.session_id, record.created_at, record.reviewed_at, record.exported_at,
                ),
            )
            self._conn.commit()
            record.id = int(cursor.lastrowid or 0)
            self._index(record.collection).add(record.id, record.hash)
            return record.id

    @staticmethod
    def _record(row: sqlite3.Row) -> SampleRecord:
        """ Строка базы → запись. """
        return SampleRecord(
            id=int(row["id"]),
            collection=row["collection"],
            task=row["task"],
            path=row["path"],
            label=row["label"],
            model_label=row["model_label"],
            proposed_label=row["proposed_label"],
            confidence=row["confidence"],
            status=SampleStatus(row["status"]),
            mode=SaveMode(row["mode"]),
            trigger=row["trigger"],
            source=row["source"],
            frame_index=int(row["frame_index"]),
            timestamp=row["timestamp"],
            region_id=row["region_id"],
            region=json.loads(row["region_json"]) if row["region_json"] else None,
            hash=_to_unsigned(int(row["hash"])),
            width=int(row["width"]),
            height=int(row["height"]),
            predictions=json.loads(row["predictions_json"] or "[]"),
            attrs=json.loads(row["attrs_json"] or "{}"),
            session_id=row["session_id"],
            created_at=row["created_at"],
            reviewed_at=row["reviewed_at"],
            exported_at=row["exported_at"],
        )

    def get(self, sample_id: int) -> SampleRecord | None:
        """ Запись по id. """
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM samples WHERE id = ?", (sample_id,)).fetchone()
        return self._record(row) if row else None

    def records(self, collection: str, statuses: Iterable[SampleStatus] | None = None) -> list[SampleRecord]:
        """ Записи коллекции (с фильтром по состоянию). """
        query = f"SELECT {_COLUMNS} FROM samples WHERE collection = ?"
        params: list[object] = [collection]
        if statuses is not None:
            values = [status.value for status in statuses]
            query += f" AND status IN ({','.join('?' for _ in values)})"
            params.extend(values)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY id", params).fetchall()
        return [self._record(row) for row in rows]

    def collections(self) -> list[str]:
        """ Все коллекции каталога. """
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT collection FROM samples ORDER BY collection").fetchall()
        return [row["collection"] for row in rows]

    def find_similar(self, value: int, threshold: int, collection: str | None) -> tuple[SampleRecord, int] | None:
        """ Ближайшая запись с расстоянием не больше threshold (в коллекции или во всём каталоге). """
        indexes = [self._index(collection)] if collection is not None else list(self._indexes.values())
        best: tuple[int, int] | None = None
        for index in indexes:
            found = index.nearest(value)
            if found and (best is None or found[1] < best[1]):
                best = found
        if best is None or best[1] > threshold:
            return None
        record = self.get(best[0])
        return (record, best[1]) if record else None

    def update(
        self,
        sample_id: int,
        *,
        origin: str,
        path: str | None = None,
        label: str | None = None,
        status: SampleStatus | None = None,
        proposed_label: str | None = None,
        reviewed: bool = False,
    ) -> None:
        """ Меняет путь, класс или состояние записи; смена класса или состояния пишется в историю. """
        record = self.get(sample_id)
        if record is None:
            raise KeyError(f"Sample {sample_id} not found")
        new_label = label if label is not None else record.label
        new_status = status if status is not None else record.status
        with self._lock:
            self._conn.execute(
                "UPDATE samples SET path = ?, label = ?, status = ?, proposed_label = ?, reviewed_at = ? WHERE id = ?",
                (
                    path if path is not None else record.path,
                    new_label,
                    new_status.value,
                    proposed_label if proposed_label is not None else record.proposed_label,
                    _now() if reviewed else record.reviewed_at,
                    sample_id,
                ),
            )
            if new_label != record.label or new_status != record.status:
                self._conn.execute(
                    "INSERT INTO label_history (sample_id, old_label, new_label, old_status, new_status, origin, "
                    "changed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sample_id, record.label, new_label, record.status.value, new_status.value, origin, _now()),
                )
            self._conn.commit()
        if new_status == SampleStatus.FAILED:
            self._index(record.collection).remove(sample_id)

    def history(self, sample_id: int) -> list[dict[str, str | None]]:
        """ История изменений записи. """
        with self._lock:
            rows = self._conn.execute(
                "SELECT old_label, new_label, old_status, new_status, origin, changed_at FROM label_history "
                "WHERE sample_id = ? ORDER BY id",
                (sample_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def label_counts(self, collection: str, statuses: Sequence[SampleStatus] | None = None) -> dict[str, int]:
        """ Количество записей по классам (по умолчанию — без отклонённых и сбойных). """
        values = [s.value for s in statuses] if statuses else [
            SampleStatus.PENDING.value, SampleStatus.SUSPECT.value, SampleStatus.VERIFIED.value,
        ]
        with self._lock:
            rows = self._conn.execute(
                f"SELECT COALESCE(label, ''), COUNT(*) FROM samples WHERE collection = ? "
                f"AND status IN ({','.join('?' for _ in values)}) GROUP BY label",
                [collection, *values],
            ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def status_counts(self, collection: str) -> dict[str, int]:
        """ Количество записей по состояниям. """
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM samples WHERE collection = ? GROUP BY status", (collection,)
            ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def filenames(self, collection: str) -> set[str]:
        """ Имена файлов коллекции — для уникальности новых имён. """
        with self._lock:
            rows = self._conn.execute("SELECT path FROM samples WHERE collection = ?", (collection,)).fetchall()
        return {PurePosixPath(row["path"]).name for row in rows}

    def mark_exported(self, sample_ids: Sequence[int]) -> None:
        """ Отмечает записи как выгруженные. """
        stamp = _now()
        with self._lock:
            self._conn.executemany(
                "UPDATE samples SET exported_at = ? WHERE id = ?", [(stamp, sample_id) for sample_id in sample_ids]
            )
            self._conn.commit()

    def add_export(self, collection: str, target: str, out_dir: Path, count: int) -> None:
        """ Записывает факт выгрузки. """
        with self._lock:
            self._conn.execute(
                "INSERT INTO exports (collection, target, out_dir, count, created_at) VALUES (?, ?, ?, ?, ?)",
                (collection, target, str(out_dir), count, _now()),
            )
            self._conn.commit()
