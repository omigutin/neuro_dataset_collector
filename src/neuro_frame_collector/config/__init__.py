""" Настройки сборщика кадров. """

from .loader import dump_config, load_config, resolve_paths, save_config
from .models import *  # noqa: F403
from .models import __all__ as _models_all

__all__ = ["dump_config", "load_config", "resolve_paths", "save_config", *_models_all]
