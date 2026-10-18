""" Ленивый импорт OpenCV с понятной ошибкой. """

from __future__ import annotations

from types import ModuleType

__all__ = ["get_cv2"]

_CV2: ModuleType | None = None


def get_cv2() -> ModuleType:
    """ Возвращает модуль cv2; OpenCV не входит в обязательные зависимости, чтобы не конфликтовать с хозяином. """
    global _CV2
    if _CV2 is None:
        try:
            import cv2
        except ImportError as error:
            raise ImportError(
                "OpenCV is required. Install 'neuro-dataset-collector[opencv]' or "
                "'neuro-dataset-collector[headless]', or use the opencv already present in your project."
            ) from error
        _CV2 = cv2
    return _CV2
