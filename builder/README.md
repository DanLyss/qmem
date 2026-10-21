# qmem-build: что сделано (каркас)

2026-10-13 · устройство: [docs/skeleton.md](../docs/skeleton.md), раздел 3 · формат profile: [docs/formats/profile.md](../docs/formats/profile.md)

`qmem-build.py` строит profile v2 для replay из каталога записи. Он заменил `tools/mkprofile.py`. Логика mkprofile стала первыми реализациями частей; там, где будут другие оценщики, стоят заглушки.

```
readers  →  model  →  estimators  →  writer
```

## Что лежит в репозитории

| Файл | Что это |
| --- | --- |
| `qmem-build.py` | Командная строка: записи → модель → estimators → profile |
| `readers/layout.py` | Где лежат файлы каждого источника: `record.json`, подпапка на источник |
| `readers/smaps.py`, `damon.py`, `softdirty.py` | По reader'у на источник |
| `model.py` | Модель: регионы, снимки smaps, наблюдения DAMON по эпохам, записи softdirty |
| `estimators/` | Оценщики (ниже) |
| `profile_v2.py` | Строки profile v2, writer и reader |
| `intervals.py`, `util.py` | Интервалы адресов, сообщения |

Нет источника — нет и его данных: без `damon` profile получается без эпох (с предупреждением), без `softdirty` — `write=1`.

## Estimators

Интерфейс: `estimate(model, options, profile) -> None`. Оценщик добавляет строки в `profile` или заполняет поля уже добавленных. Порядок вызова: lifecycle → resident → intensity → write → timing.

| Оценщик | Что даёт | Сейчас |
| --- | --- | --- |
| `lifecycle` | `region`, `map`, `unmap` | Из разниц снимков smaps; изменение ставится посередине между снимками |
| `resident` | `populate` | Рост Rss отображения |
| `intensity_passthrough` | `epoch`, `access` с `rate` | **заглушка**: `rate` = access_rate / 100 / интервал выборки DAMON — псевдочастота как касаний на страницу в секунду, без перевода |
| `write_softdirty` | поле `write` | **заглушка**: доля записанных байт отображения за период softdirty |
| `timing_roundrobin` | поле `pattern` | **заглушка**: всегда `roundrobin` |

Новый оценщик — файл `estimators/<вид>_<имя>.py`, выбирается опцией `--intensity <имя>` (`--write`, `--timing`). Список вариантов опция берёт из файлов сама.

## Как пользоваться

```sh
builder/qmem-build.py out -o profile.txt
builder/qmem-build.py out -o profile.txt --epoch-ms 200 --seed 9 --intensity passthrough
```

В stderr — сводка: регионы, события, эпохи, сколько файловых отображений пропущено. Какими оценщиками собран profile, видно в его строке `meta`.

## Тесты

`tests/test_build.py`: формат и `meta`, каждый оценщик на синтетической записи, дыры DAMON, выбор оценщиков, запись без damon. Точность не проверяется.
