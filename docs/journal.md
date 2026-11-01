# Журнал ответов модели и участки на проверку

Общий формат нарезчика и Production Counter (PC). Код — модуль `neuro_dataset_collector.journal`.

## Журнал ответов модели

Что модель ответила в каждой зоне каждого проверенного кадра. Кадры не сохраняются: картинку любого кадра
и выреза можно достать из видео по номеру кадра и контуру зоны. Два файла с общей частью имени:

| Файл | Что внутри |
|---|---|
| `<имя>.predictions.csv` | строки `frame,time,zone,label,conf,state`: кадр, секунда, зона, класс (пусто — ничего), уверенность, состояние машины состояний (PC) |
| `<имя>.predictions.json` | видео (полный путь), FPS, размер кадра, зоны с контурами в координатах кадра, классы модели, модель, кто записал (`source`: `regression` или `collector`) |

Кто пишет:

- регрессионный прогон PC — в папку видео прогона `runs/<run_id>/<имя видео>/`;
- нарезчик («Прогнать моделью») — в папку датасета видео: `<датасет>/_journal/<имя видео>.predictions.*`.

```python
from neuro_dataset_collector.journal import JournalMeta, JournalWriter, JournalZone, read_journal

meta = JournalMeta(video=str(video), fps=25.0, width=1920, height=1080,
                   zones=[JournalZone(id="1", polygon=[(0, 0), (100, 0), (100, 80), (0, 80)])],
                   classes=["EMPTY", "FULL"], model="ml.pt", source="regression")
with JournalWriter(folder / video.stem, meta) as journal:
    journal.write(frame=120, time=4.8, zone="1", label="FULL", conf=0.93, state="LOADED")

for row in read_journal(folder / video.stem):
    ...
```

### Прогон моделью в нарезчике

```python
from neuro_dataset_collector.pipeline import Collector, run_model
from neuro_dataset_collector.predictors import create_predictor

predictor = create_predictor(config.model, config.task)
with Collector(config.for_video(video)) as collector:
    report = run_model(collector, video, predictor, model_name="ml.pt")
print(report.summary(), report.journal)
```

Если в настройках `journal.save: true`, вырезы, где модель «не прошла условия» (низкая уверенность,
смена класса, отмеченные классы — см. [настройки](settings.md#journal--прогон-моделью)), сохраняются
на проверку в «_suspect»: разложите их по папкам классов и нажмите «Сверить».

## Участки на проверку

`<имя видео>.review.json` — участки видео, где прогон ошибся (по сравнению с эталоном) или где человек
отметил ошибку: зона, кадры с–по, кадр события, вид (`missing`, `extra`, `changed`, `marked`), что ожидалось
и что получилось, отметка «разобрано». Окно вокруг события — `before_seconds` до и `after_seconds` после;
пересекающиеся окна одной зоны объединяются.

```python
from neuro_dataset_collector.journal import ReviewFile, ReviewKind, build_segments, review_path, write_review

segments = build_segments([("1", 4500, ReviewKind.MISSING, "+1", "нет события", "")], fps=25.0,
                          before_seconds=5, after_seconds=2, frame_count=90000)
write_review(review_path(folder, video), ReviewFile(video=str(video), journal=str(folder / video.stem),
                                                    source="regression", segments=segments))
```

## Видео «….annotated.*»

Видео с `annotated` перед расширением (`cam25.annotated.mp4`) — записи PC с нарисованными счётчиками.
`list_videos` их не возвращает; проверить имя — `sources.video.is_annotated_video(path)`.
