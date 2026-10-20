""" Системное окно выбора файла или папки (tkinter в отдельном процессе). """

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

__all__ = ["FILE_FILTERS", "pick_path"]

_log = logging.getLogger("neuro_dataset_collector")

# Фильтры окна выбора файла: название и маски.
FILE_FILTERS: dict[str, list[tuple[str, str]]] = {
    "video": [("Видео", "*.mp4 *.avi *.mov *.mkv"), ("Все файлы", "*.*")],
    "json": [("Разметка LabelMe", "*.json"), ("Все файлы", "*.*")],
    "yaml": [("Настройки", "*.yaml *.yml"), ("Все файлы", "*.*")],
    "model": [("Модель", "*.pt *.onnx"), ("Все файлы", "*.*")],
    "any": [("Все файлы", "*.*")],
}

# Расширение по умолчанию для окна «Сохранить как».
_DEFAULT_EXT: dict[str, str] = {"json": ".json", "yaml": ".yaml"}

# Окно открывается в отдельном процессе: tkinter должен работать в главном потоке своего процесса,
# а веб-сервер занят своими потоками. Путь отдаётся в UTF-8: в Windows консольная кодировка другая,
# и путь с русскими буквами иначе не читается.
_SCRIPT = r"""
import json, sys, tkinter as tk
from tkinter import filedialog
args = json.loads(sys.argv[1])
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
common = {"initialdir": args["initial"] or None, "title": args["title"]}
if args["kind"] == "dir":
    path = filedialog.askdirectory(mustexist=True, **common)
elif args["kind"] == "save":
    path = filedialog.asksaveasfilename(filetypes=args["filters"], defaultextension=args["ext"],
                                        initialfile=args["file"] or None, **common)
else:
    path = filedialog.askopenfilename(filetypes=args["filters"], initialfile=args["file"] or None, **common)
root.destroy()
sys.stdout.buffer.write((path or "").encode("utf-8"))
"""


def pick_path(kind: str, file_filter: str = "any", initial: str | None = None, title: str = "") -> str | None:
    """
        Открывает системное окно выбора. kind: 'file', 'dir' или 'save'.
        Возвращает путь, '' если пользователь отменил выбор, None если окно открыть не удалось.
    """
    start = Path(initial) if initial else None
    file_name = ""
    if start is not None and not start.is_dir():
        # Указан файл (существующий или будущий): открываем его папку и подставляем имя.
        file_name = start.name if start.suffix else ""
        start = start.parent if start.parent.is_dir() else None
    args = {
        "kind": kind,
        "filters": FILE_FILTERS.get(file_filter, FILE_FILTERS["any"]),
        "ext": _DEFAULT_EXT.get(file_filter, ""),
        "initial": str(start) if start else "",
        "file": file_name if kind != "dir" else "",
        "title": title or ("Выберите папку" if kind == "dir" else "Выберите файл"),
    }
    try:
        result = subprocess.run(
            [sys.executable, "-c", _SCRIPT, json.dumps(args)],
            capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=600, check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as error:
        _log.warning("Native file dialog failed: %s", error)
        return None
    if result.returncode != 0:
        _log.warning("Native file dialog is unavailable: %s", result.stderr.strip()[-300:])
        return None
    return result.stdout.strip()
