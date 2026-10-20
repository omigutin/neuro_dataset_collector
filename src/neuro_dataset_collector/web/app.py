""" Веб-интерфейс (необязательная часть: pip install neuro-dataset-collector[web]). """

from __future__ import annotations

import asyncio
import os
import string
import threading
import webbrowser
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from ..config.models import NAME_PATTERN, dataset_dir_for_video
from .picker import pick_path
from .session import AutoScope, WebSession

__all__ = ["create_app", "run"]

_STATIC = Path(__file__).parent / "static"
_PICK_EXT = {
    "video": {".mp4", ".avi", ".mov", ".mkv"},
    "json": {".json"},
    "yaml": {".yaml", ".yml"},
    "model": {".pt", ".onnx"},
    "dir": set(),
}


class SaveRequest(BaseModel):
    """ Запрос ручного сохранения. """

    frame: int
    label: str | None = None
    regions: list[str] | None = None
    force: bool = False


class PickRequest(BaseModel):
    """ Запрос системного окна выбора. """

    kind: str = "file"
    filter: str = "any"
    initial: str | None = None
    title: str = ""


class RegionsEditRequest(BaseModel):
    """ Исправленные зоны: номер зоны → точки контура в координатах кадра. """

    regions: dict[str, list[tuple[float, float]]]


class AutoRequest(BaseModel):
    """ Запуск автонарезки: открытое видео или весь плейлист. """

    scope: AutoScope = "video"


class ExportRequest(BaseModel):
    """ Запрос выгрузки. """

    out_dir: str
    only_new: bool = True
    include_pending: bool = False


def _errors(error: ValidationError) -> list[str]:
    """ Ошибки проверки настроек в виде строк «поле: текст». """
    return [f"{'.'.join(str(p) for p in item['loc'])}: {item['msg']}" for item in error.errors()]


def _roots() -> list[str]:
    """ Корни файловой системы: диски в Windows, '/' в остальных. """
    if os.name == "nt":
        return [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
    return ["/"]


def _call(action: Callable[[], Any]) -> Any:
    """ Выполняет действие и превращает ошибки в понятные ответы с кодами 4xx. """
    try:
        return action()
    except ValidationError as error:
        raise HTTPException(422, {"errors": _errors(error)}) from error
    except (FileNotFoundError, IndexError) as error:
        raise HTTPException(404, {"errors": [str(error)]}) from error
    except (ValueError, RuntimeError, OSError) as error:
        raise HTTPException(400, {"errors": [str(error)]}) from error


def create_app(session: WebSession) -> FastAPI:
    """ Создаёт приложение для одной рабочей сессии. """
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        session.close()

    app = FastAPI(title="neuro_dataset_collector", docs_url="/api/docs", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    # ---- настройки ----

    def config_payload() -> dict[str, Any]:
        return {
            "config": session.config.model_dump(mode="json"),
            "yaml": session.config_yaml(),
            "path": str(session.config_path),
            "name_pattern": NAME_PATTERN.pattern,
        }

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return config_payload()

    @app.put("/api/config")
    def put_config(data: dict[str, Any] = Body(...)) -> dict[str, Any]:
        _call(lambda: session.update_config(data))
        return config_payload()

    @app.put("/api/config/yaml")
    def put_config_yaml(text: str = Body(..., embed=True)) -> dict[str, Any]:
        _call(lambda: session.update_config_yaml(text))
        return config_payload()

    @app.post("/api/config/open")
    def open_config(path: str = Body(..., embed=True)) -> dict[str, Any]:
        _call(lambda: session.load_config_file(Path(path)))
        return config_payload()

    @app.post("/api/config/save-as")
    def save_config_as(path: str = Body(..., embed=True)) -> dict[str, Any]:
        _call(lambda: session.save_config_as(Path(path)))
        return config_payload()

    @app.get("/api/dataset-dir")
    def dataset_dir(video: str = Query(...)) -> dict[str, str]:
        return {"path": str(dataset_dir_for_video(Path(video)))}

    # ---- выбор файлов на компьютере, где запущен сервер ----

    @app.post("/api/pick")
    async def pick(request: PickRequest) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        path = await loop.run_in_executor(None, pick_path, request.kind, request.filter, request.initial,
                                          request.title)
        return {"path": path or "", "available": path is not None}

    @app.get("/api/browse")
    def browse(path: str | None = Query(None), kind: str = Query("dir")) -> dict[str, Any]:
        if not path:
            return {"path": None, "parent": None, "dirs": _roots(), "files": []}
        folder = Path(path)
        if not folder.is_dir():
            raise HTTPException(404, {"errors": [f"Folder not found: {path}"]})
        allowed = _PICK_EXT.get(kind, set())
        dirs, files = [], []
        try:
            for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    dirs.append(entry.name)
                elif entry.suffix.lower() in allowed:
                    files.append(entry.name)
        except PermissionError as error:
            raise HTTPException(403, {"errors": [str(error)]}) from error
        parent = str(folder.parent) if folder.parent != folder else None
        return {"path": str(folder), "parent": parent, "dirs": dirs, "files": files}

    # ---- видео и нарезка ----

    @app.get("/api/video")
    def video_info() -> dict[str, Any]:
        result: dict[str, Any] = _call(session.video_info)
        return result

    @app.post("/api/video/open")
    def open_video(path: str = Body(..., embed=True)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.open_video(Path(path)))
        return result

    @app.post("/api/video/regions")
    def set_regions(path: str | None = Body(None, embed=True)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.set_regions_file(Path(path) if path else None))
        return result

    @app.post("/api/video/regions/edit")
    def edit_regions(request: RegionsEditRequest) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.edit_regions(
            {region_id: [(x, y) for x, y in points] for region_id, points in request.regions.items()}))
        return result

    @app.get("/api/video/regions/save-target")
    def regions_save_target() -> dict[str, str | None]:
        target = session.regions_save_target()
        return {"path": str(target) if target else None}

    @app.post("/api/video/regions/save-as")
    def save_regions_as(path: str = Body(..., embed=True)) -> dict[str, str]:
        return {"path": str(_call(lambda: session.save_regions_as(Path(path))))}

    # ---- плейлист ----

    @app.get("/api/playlist")
    def playlist() -> dict[str, Any]:
        result: dict[str, Any] = _call(session.playlist)
        return result

    @app.post("/api/playlist")
    def set_playlist(path: str | None = Body(None, embed=True)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.set_playlist(Path(path) if path else None))
        return result

    @app.post("/api/video/position")
    def remember_frame(frame: int = Body(..., embed=True)) -> dict[str, int]:
        session.remember_frame(frame)
        return {"frame": session.frame}

    @app.get("/api/video/frame/{index}")
    def video_frame(index: int, zones: bool = Query(True)) -> Response:
        frame = _call(lambda: session.frame_at(index))
        if frame is None:
            raise HTTPException(404, {"errors": ["End of video"]})
        return Response(session.render(frame, zones=zones), media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/save")
    def save(request: SaveRequest) -> dict[str, Any]:
        results = _call(lambda: session.save(request.frame, request.label, request.regions, request.force))
        return {"results": [session.result_to_dict(r) for r in results]}

    @app.get("/api/history")
    def history() -> dict[str, Any]:
        return {"items": session.history()}

    @app.get("/api/file")
    def catalog_file(path: str = Query(...)) -> FileResponse:
        return FileResponse(_call(lambda: session.catalog_file(path)))

    # ---- автонарезка, сверка, выгрузка ----

    @app.post("/api/auto/start")
    def auto_start(request: AutoRequest | None = Body(None)) -> dict[str, Any]:
        scope: AutoScope = request.scope if request is not None else "video"
        _call(lambda: session.start_auto(scope))
        return asdict(session.auto_state())

    @app.post("/api/auto/stop")
    def auto_stop() -> dict[str, Any]:
        session.stop_auto()
        return asdict(session.auto_state())

    @app.get("/api/auto/status")
    def auto_status() -> dict[str, Any]:
        return asdict(session.auto_state())

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        result: dict[str, Any] = _call(session.stats)
        return result

    @app.post("/api/sync")
    def sync() -> dict[str, Any]:
        report = _call(session.sync)
        return {
            "summary": report.summary(),
            "verified": report.verified,
            "relabeled": report.relabeled,
            "rejected": report.rejected,
            "still_suspect": report.still_suspect,
            "unsorted": report.unsorted,
            "unknown_files": [str(p) for p in report.unknown_files[:50]],
            "unknown_labels": sorted(report.unknown_labels),
        }

    @app.post("/api/export")
    def export(request: ExportRequest) -> dict[str, Any]:
        report = _call(lambda: session.export(Path(request.out_dir), request.only_new, request.include_pending))
        return {"summary": report.summary(), "exported": report.exported, "by_label": report.by_label}

    return app


def run(config_path: Path | None = None, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """ Запускает веб-интерфейс. Без файла настроек открывается прошлый сеанс. """
    import uvicorn

    session = WebSession.open(config_path.resolve() if config_path else None)
    app = create_app(session)
    url = f"http://{host}:{port}/"
    print(f"Настройки: {session.config_path}")
    print(f"Веб-интерфейс: {url} (остановить — Ctrl+C)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
