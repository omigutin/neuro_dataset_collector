""" Запоминание прошлого сеанса веб-инструмента: файл настроек, видео, кадр. """

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = ["SessionState", "app_dir", "default_config_path", "load_state", "save_state"]

_log = logging.getLogger("neuro_dataset_collector")
_STATE_FILE = "session.yaml"
_CONFIG_FILE = "settings.yaml"


class SessionState(BaseModel):
    """ Что было открыто в прошлый раз. """

    model_config = ConfigDict(extra="ignore")

    config_path: str | None = None
    video: str | None = None
    frame: int = 0


def app_dir() -> Path:
    """ Папка программы: %APPDATA%\\NeuroDatasetCollector в Windows, ~/.config/neuro_dataset_collector в остальных. """
    override = os.environ.get("NDC_HOME")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if os.name == "nt" and appdata:
        return Path(appdata) / "NeuroDatasetCollector"
    return Path.home() / ".config" / "neuro_dataset_collector"


def default_config_path() -> Path:
    """ Файл настроек по умолчанию (если пользователь не указал свой). """
    return app_dir() / _CONFIG_FILE


def load_state() -> SessionState:
    """ Состояние прошлого сеанса; пустое, если файла нет или он испорчен. """
    path = app_dir() / _STATE_FILE
    if not path.exists():
        return SessionState()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return SessionState.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        _log.warning("Session state is ignored (%s): %s", path, error)
        return SessionState()


def save_state(state: SessionState) -> None:
    """ Сохраняет состояние сеанса. """
    path = app_dir() / _STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(state.model_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8")
