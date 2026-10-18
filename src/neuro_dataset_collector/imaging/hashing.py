""" Отпечатки картинок для поиска почти одинаковых кадров. """

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from .._cv import get_cv2
from ..core.types import Image

__all__ = ["dhash", "hamming_many", "mean_abs_diff", "small_gray"]

_BIT_WEIGHTS = (1 << np.arange(64, dtype=np.uint64)).astype(np.uint64)


def _gray(image: Image) -> Image:
    """ Переводит картинку в оттенки серого. """
    if image.ndim == 2:
        return image
    cv2 = get_cv2()
    return np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), dtype=np.uint8)


def dhash(image: Image) -> int:
    """ 64-битный разностный хэш: устойчив к небольшим изменениям яркости и сжатию. """
    cv2 = get_cv2()
    small = cv2.resize(_gray(image), (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int(np.sum(_BIT_WEIGHTS[bits]))


def hamming_many(value: int, others: npt.NDArray[np.uint64]) -> npt.NDArray[np.int64]:
    """ Число различающихся бит между value и каждым элементом others. """
    if others.size == 0:
        return np.zeros(0, dtype=np.int64)
    xor = np.bitwise_xor(others, np.uint64(value))
    bits = np.unpackbits(xor.view(np.uint8).reshape(-1, 8), axis=1)
    return bits.sum(axis=1).astype(np.int64)


def small_gray(image: Image, size: int = 32) -> npt.NDArray[np.float32]:
    """ Уменьшенная серая копия для быстрого сравнения кадров. """
    cv2 = get_cv2()
    return np.asarray(cv2.resize(_gray(image), (size, size), interpolation=cv2.INTER_AREA), dtype=np.float32)


def mean_abs_diff(a: npt.NDArray[np.float32], b: npt.NDArray[np.float32]) -> float:
    """ Средняя разница яркости между двумя уменьшенными картинками (0–255). """
    return float(np.mean(np.abs(a - b)))
