""" Загрузка и сохранение настроек в YAML. """

from __future__ import annotations

from pathlib import Path

import yaml

from .models import CollectorConfig

__all__ = ["dump_config", "load_config", "save_config"]


def load_config(path: str | Path) -> CollectorConfig:
    """ Читает настройки из YAML-файла. Относительные пути считаются от папки файла. """
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {config_path}")
    config = CollectorConfig.model_validate(data)
    return resolve_paths(config, config_path.parent)


def resolve_paths(config: CollectorConfig, base_dir: Path) -> CollectorConfig:
    """ Делает относительные пути настроек абсолютными относительно base_dir. """
    def _abs(value: Path | None) -> Path | None:
        if value is None or value.is_absolute():
            return value
        return (base_dir / value).resolve()

    root = _abs(config.catalog.root)
    if root is not None:
        config.catalog.root = root
    config.regions.file = _abs(config.regions.file)
    config.source.path = _abs(config.source.path)
    config.model.path = _abs(config.model.path)
    return config


def dump_config(config: CollectorConfig) -> str:
    """ Возвращает настройки в виде YAML-текста. """
    data = config.model_dump(mode="json")
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def save_config(config: CollectorConfig, path: str | Path) -> None:
    """ Сохраняет настройки в YAML-файл. """
    Path(path).write_text(dump_config(config), encoding="utf-8")
