""" Подготовка ffmpeg/ffprobe для чтения видео: поиск и автоустановка через библиотеку true_fps. """

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from true_fps import InstallResult, ensure_video_tools

from .video import VideoReader

__all__ = ["VIDEO_TOOLS_DIR", "prepare_video_tools"]

# Папка видеоинструментов внутри папки программы.
VIDEO_TOOLS_DIR = "tools"


def prepare_video_tools(tools_dir: Path, on_status: Callable[[str], None] | None = None) -> InstallResult:
    """
        Находит ffmpeg и ffprobe (папка инструментов, затем PATH), а если их нет — ставит в tools_dir.
        При успехе VideoReader измеряет частоту кадров найденным ffprobe.
    """
    result = ensure_video_tools(tools_dir, on_status=on_status)
    if result.success and result.tools is not None:
        VideoReader.ffprobe_path = result.tools.ffprobe
    return result
