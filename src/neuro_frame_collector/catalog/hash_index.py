""" Индекс отпечатков в памяти для быстрого поиска похожих кадров. """

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from ..imaging.hashing import hamming_many

__all__ = ["HashIndex"]


class HashIndex:
    """ Растущий массив отпечатков с идентификаторами записей. """

    def __init__(self, capacity: int = 1024) -> None:
        self._hashes: npt.NDArray[np.uint64] = np.zeros(capacity, dtype=np.uint64)
        self._ids: npt.NDArray[np.int64] = np.zeros(capacity, dtype=np.int64)
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def add(self, sample_id: int, value: int) -> None:
        """ Добавляет отпечаток. """
        if self._size == self._hashes.size:
            self._hashes = np.concatenate([self._hashes, np.zeros(self._hashes.size, dtype=np.uint64)])
            self._ids = np.concatenate([self._ids, np.zeros(self._ids.size, dtype=np.int64)])
        self._hashes[self._size] = np.uint64(value)
        self._ids[self._size] = sample_id
        self._size += 1

    def remove(self, sample_id: int) -> None:
        """ Удаляет отпечаток записи (если есть). """
        positions = np.flatnonzero(self._ids[: self._size] == sample_id)
        for position in positions[::-1]:
            last = self._size - 1
            self._hashes[position] = self._hashes[last]
            self._ids[position] = self._ids[last]
            self._size -= 1

    def nearest(self, value: int) -> tuple[int, int] | None:
        """ Ближайшая запись: (id, расстояние) или None, если индекс пуст. """
        if self._size == 0:
            return None
        distances = hamming_many(value, self._hashes[: self._size])
        position = int(np.argmin(distances))
        return int(self._ids[position]), int(distances[position])
