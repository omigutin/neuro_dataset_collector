""" Веб-интерфейс (необязательная часть: pip install neuro-dataset-collector[web]). """

from __future__ import annotations

import asyncio
import atexit
import json
import os
import socket
import string
import threading
import urllib.request
import webbrowser
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from neuro_markup_lab.web import mount_app as mount_markup_lab
from pydantic import BaseModel, ValidationError

from .. import __version__
from ..config.models import NAME_PATTERN, MarkupChoice, dataset_dir_for_video
from ..journal.table import JournalFilter
from . import DEFAULT_PORT
from .markup_host import CollectorMarkupHost
from .picker import pick_path
from .session import AutoMode, AutoScope, WebSession

__all__ = ["APP_ID", "MARKUP_PREFIX", "create_app", "find_port", "mount_app", "run"]

# Сколько следующих портов пробовать, если порт занят другой программой.
_PORT_ATTEMPTS = 20
# Ответ /api/ping: по нему видно, что на порту уже работает этот инструмент.
APP_ID = "neuro_dataset_collector"
# Редактор разметки neuro_markup_lab внутри сборщика (страница — MARKUP_PREFIX + "/")
MARKUP_PREFIX = "/markup"

_STATIC = Path(__file__).parent / "static"
_PICK_EXT = {
    "video": {".mp4", ".avi", ".mov", ".mkv"},
    "json": {".json"},
    "yaml": {".yaml", ".yml"},
    "model": {".pt", ".onnx"},
    "journal": {".csv", ".json"},
    "review": {".json"},
    "dir": set(),
}


class SaveRequest(BaseModel):
    """ Запрос ручного сохранения. """

    frame: int
    label: str | None = None
    regions: list[str] | None = None
    force: bool = False
    model: bool = False


class PickRequest(BaseModel):
    """ Запрос системного окна выбора. """

    kind: str = "file"
    filter: str = "any"
    initial: str | None = None
    title: str = ""


class MarkupRequest(BaseModel):
    """ Выбор разметки открытого видео. """

    variant: MarkupChoice = MarkupChoice.AUTO
    version: int | None = None


class AutoRequest(BaseModel):
    """ Запуск автонарезки или прогона моделью: открытое видео или весь плейлист. """

    scope: AutoScope = "video"
    mode: AutoMode = "collect"


class JournalQuery(BaseModel):
    """ Выборка из журнала ответов модели. """

    path: str
    filter: JournalFilter = JournalFilter()
    offset: int = 0
    limit: int = 60


class ZoneSaveRequest(BaseModel):
    """ Сохранение выреза одной зоны кадра (из журнала или участка на проверку). """

    video: str
    frame: int
    zone: str
    label: str | None = None
    journal: str | None = None
    force: bool = False


class CheckRequest(BaseModel):
    """ Проверка датасета моделью. """

    scope: AutoScope = "video"
    min_conf: float = 0.0
    threshold: int = 3


class ExportRequest(BaseModel):
    """ Запрос выгрузки. """

    out_dir: str
    only_new: bool = True
    include_pending: bool = False
    full_frames: bool = False
    scope: AutoScope = "video"


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


def _static_version() -> str:
    """ Метка версии файлов интерфейса: меняется при обновлении программы и при правке файлов. """
    stamp = max(int(path.stat().st_mtime) for path in _STATIC.rglob("*") if path.is_file())
    return f"{__version__}-{stamp}"


def create_app(session: WebSession) -> FastAPI:
    """ Создаёт приложение для одной рабочей сессии. """
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        session.close()

    app = FastAPI(title="neuro_dataset_collector", docs_url="/api/docs", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    mount_markup_lab(app, MARKUP_PREFIX, host=CollectorMarkupHost(lambda: session.config.regions.search_dir))

    @app.middleware("http")
    async def no_stale_files(request: Request, call_next: Callable[[Request], Any]) -> Any:
        # Браузер должен сверять файлы интерфейса с сервером, иначе после обновления программы
        # остаются старые стили и скрипт.
        # Путь — до вызова: маршрутизация дальше меняет root_path в том же scope.
        path, root = request.url.path, request.scope.get("root_path", "")
        if root and path.startswith(root):
            path = path[len(root):]
        response = await call_next(request)
        if path in ("", "/") or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/ping")
    def ping() -> dict[str, str]:
        return {"app": APP_ID, "version": __version__}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        # Адреса на странице относительные (её можно встроить в другое приложение под своим префиксом),
        # поэтому адрес страницы должен заканчиваться на «/».
        if not request.url.path.endswith("/"):
            return RedirectResponse(request.url.replace(path=request.url.path + "/"), status_code=307)
        page = (_STATIC / "index.html").read_text(encoding="utf-8").replace("{{version}}", _static_version())
        return HTMLResponse(page)

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

    @app.post("/api/video/markup")
    def set_markup(request: MarkupRequest) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.set_markup(request.variant, request.version))
        return result

    @app.post("/api/video/markup/saved")
    def markup_saved(variant: str = Body(..., embed=True)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.markup_saved(variant))
        return result

    # ---- плейлист ----

    @app.get("/api/playlist")
    def playlist() -> dict[str, Any]:
        result: dict[str, Any] = _call(session.playlist)
        return result

    @app.post("/api/playlist")
    def set_playlist(path: str | None = Body(None), recursive: bool | None = Body(None)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.set_playlist(Path(path) if path else None, recursive))
        return result

    @app.post("/api/video/zones")
    def set_zones(ids: list[str] = Body(..., embed=True)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.set_zones(ids))
        return result

    @app.post("/api/video/position")
    def remember_frame(frame: int = Body(..., embed=True)) -> dict[str, int]:
        session.remember_frame(frame)
        return {"frame": session.frame}

    @app.get("/api/video/frame/{index}")
    def video_frame(index: int, zones: bool = Query(True), model: bool = Query(False)) -> Response:
        frame = _call(lambda: session.frame_at(index))
        if frame is None:
            raise HTTPException(404, {"errors": ["End of video"]})
        image = _call(lambda: session.render(frame, zones=zones, model=model))
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/model/answers/{index}")
    def model_answers(index: int) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.model_answers(index))
        return result

    @app.post("/api/save")
    def save(request: SaveRequest) -> dict[str, Any]:
        results = _call(lambda: session.save(request.frame, request.label, request.regions, request.force,
                                             request.model))
        return {"results": [session.result_to_dict(r) for r in results]}

    @app.get("/api/history")
    def history(video: str = Query(...), offset: int = Query(0, ge=0),
                limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.history(Path(video), offset, limit))
        return result

    @app.get("/api/rejected/{rejection_id}.jpg")
    def rejected_image(rejection_id: int) -> Response:
        return Response(_call(lambda: session.rejected_image(rejection_id)), media_type="image/jpeg")

    @app.post("/api/sample/{sample_id}/relabel")
    def relabel(sample_id: int, video: str = Body(...), label: str = Body(...)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.relabel(Path(video), sample_id, label))
        return result

    @app.post("/api/sample/{sample_id}/delete")
    def delete_sample(sample_id: int, video: str = Body(..., embed=True)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.delete_sample(Path(video), sample_id))
        return result

    @app.post("/api/open-folder")
    def open_folder(video: str = Body(..., embed=True)) -> dict[str, str]:
        return {"path": str(_call(lambda: session.open_dataset_folder(Path(video))))}

    @app.get("/api/file")
    def catalog_file(path: str = Query(...), video: str | None = Query(None)) -> FileResponse:
        return FileResponse(_call(lambda: session.catalog_file(path, Path(video) if video else None)))

    # ---- журнал ответов модели и участки на проверку ----

    def journals_payload() -> dict[str, Any]:
        return {"items": session.journals.journals(session.journal_folders())}

    @app.get("/api/resolve-video")
    def resolve_video(path: str = Query(...)) -> dict[str, str]:
        return {"path": str(_call(lambda: session.resolve_video(path)))}

    @app.get("/api/journals")
    def journals() -> dict[str, Any]:
        result: dict[str, Any] = _call(journals_payload)
        return result

    @app.post("/api/journals/open")
    def open_journal(path: str = Body(..., embed=True)) -> dict[str, Any]:
        base = _call(lambda: session.journals.remember(Path(path)))
        return {**journals_payload(), "path": str(base)}

    @app.post("/api/journal/query")
    def journal_query(request: JournalQuery) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.journals.query(Path(request.path), request.filter,
                                                                      max(0, request.offset),
                                                                      min(500, max(1, request.limit))))
        return result

    @app.get("/api/video/timeline")
    def video_timeline(journal: str | None = Query(None), zone: str | None = Query(None)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.timeline(journal, zone))
        return result

    @app.get("/api/journal/frame")
    def journal_frame(video: str = Query(...), frame: int = Query(..., ge=0), zone: str = Query(...),
                      journal: str | None = Query(None)) -> Response:
        image = _call(lambda: session.journal_frame(video, frame, zone, journal))
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/journal/crop")
    def journal_crop(video: str = Query(...), frame: int = Query(..., ge=0), zone: str = Query(...),
                     journal: str | None = Query(None)) -> Response:
        image = _call(lambda: session.journal_crop(video, frame, zone, journal))
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/journal/save")
    def journal_save(request: ZoneSaveRequest) -> dict[str, Any]:
        results = _call(lambda: session.save_zone(request.video, request.frame, request.zone, request.label,
                                                  request.journal, request.force))
        return {"results": [session.result_to_dict(r) for r in results]}

    @app.get("/api/review")
    def review(path: str = Query(...)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.journals.review(Path(path)))
        return result

    @app.post("/api/review/done")
    def review_done(path: str = Body(...), index: int = Body(...), done: bool = Body(True)) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.journals.set_done(Path(path), index, done))
        return result

    @app.get("/api/review/items")
    def review_items(path: str = Query(...), index: int = Query(..., ge=0)) -> dict[str, Any]:
        return {"items": _call(lambda: session.journals.segment_items(Path(path), index))}

    # ---- автонарезка, сверка, выгрузка ----

    @app.post("/api/auto/start")
    def auto_start(request: AutoRequest | None = Body(None)) -> dict[str, Any]:
        scope: AutoScope = request.scope if request is not None else "video"
        mode: AutoMode = request.mode if request is not None else "collect"
        _call(lambda: session.start_auto(scope, mode))
        return asdict(session.auto_state())

    @app.post("/api/auto/stop")
    def auto_stop() -> dict[str, Any]:
        session.stop_auto()
        return asdict(session.auto_state())

    @app.get("/api/auto/frame")
    def auto_frame() -> Response:
        image = session.auto_frame()
        if image is None:
            raise HTTPException(404, {"errors": ["Automatic collection has not started"]})
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/auto/status")
    def auto_status() -> dict[str, Any]:
        return asdict(session.auto_state())

    @app.post("/api/check/start")
    def check_start(request: CheckRequest) -> dict[str, Any]:
        _call(lambda: session.start_check(request.scope, request.min_conf, request.threshold))
        return asdict(session.check_state())

    @app.post("/api/check/stop")
    def check_stop() -> dict[str, Any]:
        session.stop_check()
        return asdict(session.check_state())

    @app.get("/api/check/status")
    def check_status() -> dict[str, Any]:
        return asdict(session.check_state())

    @app.get("/api/stats")
    def stats(scope: AutoScope = Query("video")) -> dict[str, Any]:
        result: dict[str, Any] = _call(lambda: session.stats(scope))
        return result

    @app.post("/api/sync")
    def sync(scope: AutoScope = Body("video", embed=True)) -> dict[str, Any]:
        report = _call(lambda: session.sync(scope))
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
        report = _call(lambda: session.export(Path(request.out_dir), request.only_new, request.include_pending,
                                              request.full_frames, request.scope))
        return {"summary": report.summary(), "exported": report.exported, "by_label": report.by_label,
                "frames": report.frames}

    return app


def mount_app(parent: FastAPI, prefix: str = "/dataset", config_path: Path | None = None) -> WebSession:
    """
        Встраивает сборщик в другое веб-приложение (например, NeuroFramework) по адресу prefix
        (страница — prefix + "/"). Возвращает сессию: приложение-хозяин закрывает её при остановке
        (session.close()); на случай, если не закроет, — закрытие при выходе из программы.
        Ссылки на страницу: prefix/?video=…&frame=…, prefix/?journal=…, prefix/?review=…, &back=адрес_возврата.
    """
    prefix = "/" + prefix.strip("/")
    session = WebSession.open(config_path.resolve() if config_path else None)
    parent.mount(prefix, create_app(session), name="neuro_dataset_collector")
    atexit.register(session.close)
    return session


def _port_free(host: str, port: int) -> bool:
    """ Можно ли занять порт. """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _is_collector(host: str, port: int) -> bool:
    """ Работает ли на порту уже запущенный сборщик датасета. """
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/ping", timeout=1.5) as response:
            return bool(json.loads(response.read().decode("utf-8")).get("app") == APP_ID)
    except (OSError, ValueError):
        return False


def find_port(host: str, port: int) -> tuple[int, bool]:
    """
        Порт для запуска: (порт, уже_запущен). Если на порту уже работает сборщик — его порт и True;
        если порт занят другой программой — следующий свободный.
    """
    for candidate in range(port, port + _PORT_ATTEMPTS):
        if _port_free(host, candidate):
            return candidate, False
        if _is_collector(host, candidate):
            return candidate, True
    raise OSError(f"No free port in range {port}-{port + _PORT_ATTEMPTS - 1}")


def run(config_path: Path | None = None, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
        open_browser: bool = True) -> None:
    """
        Запускает веб-интерфейс. Без файла настроек открывается прошлый сеанс.
        Если сборщик уже запущен — открывает его в браузере; если порт занят другой программой — берёт следующий.
    """
    import uvicorn

    free_port, running = find_port(host, port)
    if running:
        url = f"http://{host}:{free_port}/"
        print(f"Сборщик датасета уже запущен: {url} — открываю его в браузере.")
        if open_browser:
            webbrowser.open(url)
        return
    if free_port != port:
        print(f"Порт {port} занят другой программой, использую {free_port}.")
    port = free_port
    session = WebSession.open(config_path.resolve() if config_path else None)
    app = create_app(session)
    url = f"http://{host}:{port}/"
    print(f"Настройки: {session.config_path}")
    print(f"Веб-интерфейс: {url} (остановить — Ctrl+C)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
