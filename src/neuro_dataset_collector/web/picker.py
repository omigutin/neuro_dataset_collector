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

# Окно открывается в отдельном процессе: tkinter должен работать в главном потоке своего процесса,
# а веб-сервер занят своими потоками.
_SCRIPT = r"""
import json, sys, tkinter as tk
from tkinter import filedialog
args = json.loads(sys.argv[1])
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
if args["kind"] == "dir":
    path = filedialog.askdirectory(initialdir=args["initial"] or None, title=args["title"], mustexist=True)
elif args["kind"] == "save":
    path = filedialog.asksaveasfilename(initialdir=args["initial"] or None, title=args["title"],
                                        filetypes=args["filters"], defaultextension=".yaml")
else:
    path = filedialog.askopenfilename(initialdir=args["initial"] or None, title=args["title"],
                                      filetypes=args["filters"])
root.destroy()
sys.stdout.write(path or "")
"""


def pick_path(kind: str, file_filter: str = "any", initial: str | None = None, title: str = "") -> str | None:
    """
        Открывает системное окно выбора. kind: 'file', 'dir' или 'save'.
        Возвращает путь, '' если пользователь отменил выбор, None если окно открыть не удалось.
    """
    start = Path(initial) if initial else None
    if start is not None and not start.is_dir():
        start = start.parent if start.parent.is_dir() else None
    args = {
        "kind": kind,
        "filters": FILE_FILTERS.get(file_filter, FILE_FILTERS["any"]),
        "initial": str(start) if start else "",
        "title": title or ("Выберите папку" if kind == "dir" else "Выберите файл"),
    }
    try:
        result = subprocess.run(
            [sys.executable, "-c", _SCRIPT, json.dumps(args)],
            capture_output=True, text=True, encoding="utf-8", timeout=600, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _log.warning("Native file dialog failed: %s", error)
        return None
    if result.returncode != 0:
        _log.warning("Native file dialog is unavailable: %s", result.stderr.strip()[-300:])
        return None
    return result.stdout.strip()
