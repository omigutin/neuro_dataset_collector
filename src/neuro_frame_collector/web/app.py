""" Веб-интерфейс (необязательная часть: pip install neuro-frame-collector[web]). """

from __future__ import annotations

import os
import string
import threading
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .session import WebSession

__all__ = ["create_app", "run"]

_STATIC = Path(__file__).parent / "static"
_VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv"}
_PICK_EXT = {
    "video": _VIDEO_EXT,
    "json": {".json"},
    "yaml": {".yaml", ".yml"},
    "model": {".pt", ".onnx"},
    "dir": set(),
}


class SaveRequest(BaseModel):
    """ Запрос ручного сохранения. """

    video: int
    frame: int
    label: str | None = None
    regions: list[str] | None = None
    force: bool = False


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


def create_app(session: WebSession) -> FastAPI:
    """ Создаёт приложение для одной рабочей сессии. """
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        session.close()

    app = FastAPI(title="neuro_frame_collector", docs_url="/api/docs", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    # ---- настройки ----

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return {
            "config": session.config.model_dump(mode="json"),
            "yaml": session.config_yaml(),
            "path": str(session.config_path) if session.config_path else None,
        }

    def _apply(action: Any) -> dict[str, Any]:
        try:
            action()
        except ValidationError as error:
            raise HTTPException(422, {"errors": _errors(error)}) from error
        except (ValueError, RuntimeError, OSError) as error:
            raise HTTPException(400, {"errors": [str(error)]}) from error
        return get_config()

    @app.put("/api/config")
    def put_config(data: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return _apply(lambda: session.update_config(data))

    @app.put("/api/config/yaml")
    def put_config_yaml(text: str = Body(..., embed=True)) -> dict[str, Any]:
        return _apply(lambda: session.update_config_yaml(text))

    @app.post("/api/config/open")
    def open_config(path: str = Body(..., embed=True)) -> dict[str, Any]:
        return _apply(lambda: session.load_config_file(Path(path)))

    @app.post("/api/config/save-as")
    def save_config_as(path: str = Body(..., embed=True)) -> dict[str, Any]:
        return _apply(lambda: session.save_config_as(Path(path)))

    # ---- выбор файлов на компьютере, где запущен сервер ----

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

    @app.post("/api/source")
    def open_source(path: str | None = Body(None, embed=True)) -> dict[str, Any]:
        try:
            names = session.open_source(Path(path) if path else None)
        except (ValueError, OSError) as error:
            raise HTTPException(400, {"errors": [str(error)]}) from error
        return {"videos": names}

    @app.get("/api/video/{number}")
    def video_info(number: int) -> dict[str, Any]:
        try:
            return session.video_info(number)
        except (IndexError, OSError) as error:
            raise HTTPException(404, {"errors": [str(error)]}) from error

    @app.get("/api/video/{number}/frame/{index}")
    def video_frame(number: int, index: int) -> Response:
        try:
            frame = session.frame(number, index)
        except (IndexError, OSError) as error:
            raise HTTPException(404, {"errors": [str(error)]}) from error
        if frame is None:
            raise HTTPException(404, {"errors": ["End of video"]})
        return Response(session.render(frame), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/save")
    def save(request: SaveRequest) -> dict[str, Any]:
        try:
            results = session.save(request.video, request.frame, request.label, request.regions, request.force)
        except (IndexError, ValueError, OSError) as error:
            raise HTTPException(400, {"errors": [str(error)]}) from error
        return {"results": [session.result_to_dict(r) for r in results]}

    @app.get("/api/history")
    def history() -> dict[str, Any]:
        return {"items": session.history()}

    @app.get("/api/file")
    def catalog_file(path: str = Query(...)) -> FileResponse:
        try:
            return FileResponse(session.catalog_file(path))
        except FileNotFoundError as error:
            raise HTTPException(404, {"errors": [str(error)]}) from error

    # ---- автонарезка, сверка, выгрузка ----

    @app.post("/api/auto/start")
    def auto_start() -> dict[str, Any]:
        try:
            session.start_auto()
        except (ValueError, OSError) as error:
            raise HTTPException(400, {"errors": [str(error)]}) from error
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
        return session.stats()

    @app.post("/api/sync")
    def sync() -> dict[str, Any]:
        report = session.sync()
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
        try:
            report = session.export(Path(request.out_dir), request.only_new, request.include_pending)
        except OSError as error:
            raise HTTPException(400, {"errors": [str(error)]}) from error
        return {"summary": report.summary(), "exported": report.exported, "by_label": report.by_label}

    return app


def run(config_path: Path | None, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """ Запускает веб-интерфейс и (по желанию) открывает браузер. """
    import uvicorn

    session = WebSession.open(config_path.resolve() if config_path else None)
    app = create_app(session)
    url = f"http://{host}:{port}/"
    print(f"Веб-интерфейс: {url} (остановить — Ctrl+C)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
