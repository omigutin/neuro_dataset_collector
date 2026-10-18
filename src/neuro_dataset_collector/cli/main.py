""" Командная строка: ndc init | collect | sync | export | stats | web. """

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from ..catalog.catalog import Catalog
from ..config.loader import load_config
from ..config.models import CollectorConfig
from ..export.roboflow import export_dataset
from ..pipeline.collector import Collector
from ..pipeline.offline import collect_videos
from ..predictors.base import create_predictor
from ..review.sync import sync_collection
from ..sources.video import list_videos

__all__ = ["main"]

EXAMPLE_CONFIG = "config.example.yaml"


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if target.exists() and not args.force:
        print(f"Файл уже есть: {target}. Добавьте --force, чтобы перезаписать.")
        return 1
    text = resources.files("neuro_dataset_collector").joinpath(EXAMPLE_CONFIG).read_text(encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    print(f"Создан файл настроек: {target}")
    return 0


def _load(args: argparse.Namespace) -> CollectorConfig:
    """ Настройки из файла; с --video — для этого видео (папка <видео>_dataset при next_to_video). """
    config = load_config(args.config)
    video = getattr(args, "video", None)
    return config.for_video(Path(video).resolve()) if video else config


def _print_video(path: Path, number: int, total: int) -> None:
    print(f"[{number}/{total}] {path.name}")


def _cmd_collect(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.source:
        config.source.path = Path(args.source).resolve()
    if config.source.path is None:
        print("Не задано видео: укажите --source или source.path в настройках.")
        return 1
    videos = list_videos(config.source.path, config.source.extensions, config.source.recursive)
    predictor = create_predictor(config.model, config.task)
    # Без next_to_video — один сборщик на все видео; иначе у каждого видео своя папка и своя история.
    groups = [(config, videos)] if not config.catalog.next_to_video else [(config.for_video(v), [v]) for v in videos]
    try:
        for video_config, group in groups:
            with Collector(video_config) as collector:
                report = collect_videos(collector, group, predictor=predictor, on_video=_print_video)
            print(report.summary())
            print(f"Кадры лежат в: {video_config.collection_dir}")
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
        return 130
    print("Дальше: проверьте папки глазами, исправьте ошибки и запустите «ndc sync».")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    config = _load(args)
    report = sync_collection(config)
    print(report.summary())
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    config = _load(args)
    report = export_dataset(config, Path(args.out), only_new=not args.all, include_pending=args.include_pending)
    print(report.summary())
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    config = _load(args)
    with Catalog(config.catalog.root) as catalog:
        collection = config.catalog.collection
        print(f"Коллекция: {collection}")
        print("По состояниям:", catalog.status_counts(collection))
        print("По классам:", catalog.label_counts(collection))
    return 0


def _cmd_web(args: argparse.Namespace) -> int:
    try:
        from ..web.app import run
    except ImportError as error:
        print(f"Веб-интерфейс не установлен: pip install 'neuro-dataset-collector[web]' ({error})")
        return 1
    run(Path(args.config) if args.config else None, host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """ Описание команд. """
    parser = argparse.ArgumentParser(prog="ndc", description="Сбор кадров из видео для обучения нейросетей.")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный журнал")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="создать файл настроек с примером")
    p.add_argument("path", nargs="?", default="ndc.yaml")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cmd_init)

    p = sub.add_parser("collect", help="нарезать кадры из видео по правилам из настроек")
    p.add_argument("-c", "--config", required=True)
    p.add_argument("--source", help="видео или папка с видео (вместо source.path)")
    p.set_defaults(func=_cmd_collect)

    p = sub.add_parser("sync", help="сверить папки после ручной проверки")
    p.add_argument("-c", "--config", required=True)
    p.add_argument("--video", help="видео, если папка датасета лежит рядом с ним (next_to_video)")
    p.set_defaults(func=_cmd_sync)

    p = sub.add_parser("export", help="выгрузить проверенные кадры (для Roboflow)")
    p.add_argument("-c", "--config", required=True)
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--video", help="видео, если папка датасета лежит рядом с ним (next_to_video)")
    p.add_argument("--all", action="store_true", help="включая уже выгруженные ранее")
    p.add_argument("--include-pending", action="store_true", help="включая непроверенные")
    p.set_defaults(func=_cmd_export)

    p = sub.add_parser("stats", help="счётчики коллекции")
    p.add_argument("-c", "--config", required=True)
    p.add_argument("--video", help="видео, если папка датасета лежит рядом с ним (next_to_video)")
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("web", help="запустить веб-интерфейс")
    p.add_argument("-c", "--config")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=_cmd_web)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """ Точка входа команды ndc. """
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
