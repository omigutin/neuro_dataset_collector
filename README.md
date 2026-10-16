# neuro_frame_collector

Сбор и раскладка кадров из видео для обучения нейросетей: вручную по клавишам, автоматически по правилам,
с историей каждого кадра.

- Классификация, детекция, сегментация (YOLO-разметка из ответов модели).
- Вырезка зон по файлу LabelMe или зонам от программы; без зон — весь кадр.
- Автоотбор: «модель сомневается», «случайная проверка», «каждый кадр» + ограничения по интервалу,
  изменению картинки, количеству и дублям.
- Поиск подозрительных ответов модели по последовательности кадров.
- Отказ от дублей с понятным сообщением и ссылкой на похожий кадр.
- История в SQLite: что откуда, что сказала модель, что исправил человек.
- Сверка после ручной проверки и выгрузка для Roboflow.
- Необязательный веб-интерфейс: настройки, нарезка клавишами, автонарезка, сверка и выгрузка.

## Установка

```bash
pip install "neuro-frame-collector[web,opencv]"
```

`opencv` / `headless` — если OpenCV ещё не установлен; `web` — веб-интерфейс.

## Быстро

```bash
nfc init nfc.yaml      # файл настроек с пояснениями
nfc web -c nfc.yaml    # веб-интерфейс
nfc collect -c nfc.yaml
nfc sync -c nfc.yaml
nfc export -c nfc.yaml -o export/
```

```python
from neuro_frame_collector import Collector, Frame, Prediction, load_config

with Collector(load_config("nfc.yaml")) as collector:
    results = collector.offer(Frame(image, "cam1", index, timestamp), [Prediction("FULL", 0.6)])
```

Документация: [docs/index.md](docs/index.md).

## Разработка

```bash
poetry install --all-extras
poetry run pytest
poetry run ruff check src tests
poetry run mypy
```

Лицензия: MIT.
