""" Запуск веб-интерфейса neuro_dataset_collector.

    python run.py                 — открыть прошлый сеанс (настройки и видео);
    python run.py настройки.yaml  — открыть указанный файл настроек.
"""

import sys
from pathlib import Path

# Запуск прямо из папки проекта, без установки пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from neuro_dataset_collector.web.app import run

if __name__ == "__main__":
    run(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
