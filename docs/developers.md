# Подключение к своей программе

Ядро библиотеки не зависит от веб-интерфейса: `import neuro_frame_collector` не тянет FastAPI.

## Установка в проект

```bash
poetry add neuro-frame-collector          # OpenCV уже есть в проекте
poetry add "neuro-frame-collector[headless]"  # если OpenCV нет
```

## Минимальный пример

```python
from neuro_frame_collector import Collector, Frame, Prediction, load_config

config = load_config("nfc.yaml")
with Collector(config) as collector:
    for index, image in enumerate(frames):            # image — numpy BGR, как у OpenCV
        frame = Frame(image=image, source="cam7_20260101", index=index, timestamp=index / fps)
        predictions = [Prediction(label="FULL", confidence=0.63)]
        for result in collector.offer(frame, predictions):
            print(result.message)
```

## Зоны от программы

```python
from neuro_frame_collector import BBox, Region

regions = [
    Region.from_bbox("1", BBox(100, 50, 400, 350), {"position": "left"}),
    Region("2", ((500, 50), (800, 60), (790, 360), (510, 350))),
]
config.regions.source = "api"
collector = Collector(config, regions=regions)

# Ответы модели для каждой зоны — словарём «id зоны → список»
collector.offer(frame, {"1": [Prediction("FULL", 0.9)], "2": [Prediction("EMPTY", 0.55)]})

# Или по одной зоне
collector.offer(frame, [Prediction("FULL", 0.9)], region=regions[0])
```

Координаты боксов и контуров по умолчанию считаются внутри выреза (`predictions_space: region`) —
так обычно и бывает, когда модель запускается на вырезанной зоне. Если модель работает на всём кадре,
поставьте `predictions_space: frame`: координаты будут пересчитаны.

Свою реализацию зон можно передать через `region_provider=` — объект с методом
`regions(width, height) -> list[Region]`.

## Ручное сохранение (клавиши)

```python
results = collector.save(frame, "FULL")                    # все зоны
results = collector.save(frame, "EMPTY", region_ids=["1"])  # только зона 1
results = collector.save(frame, "FULL", force=True)         # даже если похож
for r in results:
    if not r.ok:
        print(r.message, r.similar_to)   # причина и путь к похожему кадру
```

## Подсказка от программы-хозяина

Если у программы есть собственное мнение о верном классе (например, итог машины состояний), передайте его:

```python
collector.offer(frame, predictions, reference_label=fsm_state_name)
```

При расхождении с моделью вырез сохранится в `_suspect/<модель>__<программа>/`.

## Результат `SaveResult`

| Поле | Что содержит |
|---|---|
| `ok` / `status` | сохранён или нет |
| `message` | пояснение для человека |
| `reason` | причина отказа (`RejectReason`) |
| `path` | куда сохранён |
| `similar_to`, `distance` | похожий кадр и насколько отличается |
| `sample_id` | номер записи в истории |
| `trigger` | какой повод сработал |

Подписка на все результаты (для журнала или интерфейса): `collector.add_listener(callback)`.

## Боевой режим

`runtime.profile: production` — запись в фоне, ограниченная очередь (основной поток не ждёт диск),
лимит сохранений в час, а при любой ошибке сборщик выключается и пишет в журнал, не роняя программу.

## Сверка, выгрузка, история

```python
from neuro_frame_collector import Catalog, export_dataset, sync_collection

report = sync_collection(config)         # после ручной проверки
print(report.summary())
export_dataset(config, "D:/export/batch1")

with Catalog(config.catalog.root) as catalog:
    for record in catalog.records(config.catalog.collection):
        print(record.path, record.label, record.model_label, record.status)
```

## Офлайн-нарезка своей моделью

```python
from neuro_frame_collector import collect_videos

def predictor(image):                    # любая модель: картинка → список Prediction
    return [Prediction("FULL", 0.7)]

with Collector(config) as collector:
    report = collect_videos(collector, predictor=predictor)
print(report.summary())
```

## Логи

Библиотека пишет в журнал `logging.getLogger("neuro_frame_collector")`.
