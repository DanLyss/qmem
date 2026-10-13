# qmem-record: что сделано (каркас, record v2)

2026-10-13 · устройство: [docs/skeleton.md](../docs/skeleton.md), раздел 2 · формат записи: [docs/formats/record.md](../docs/formats/record.md) · итерация 1: [docs/modules/record.md](../docs/modules/record.md)

`qmem-record` записывает, как приложение нагружает память. Код итерации 1 разложен по каркасу: **driver** запускает приложение или подключается к нему, ведёт время записи (ts_ms, метки часов, паузы VM) и вызывает **источники**. Каждый источник пишет в свою подпапку и описывает себя в `record.json`.

Проверено под root с настоящими DAMON и tracefs на WSL2 (ядро 6.18, damo 3.3.0).

## Что лежит в репозитории

| Файл | Что это |
| --- | --- |
| `record/qmem-record.sh` | Driver: опции, запуск/подключение, цикл тиков, остановка, `record.json` |
| `record/lib.sh` | Общее для driver и источников: сообщения, JSON, часы (метки `clock.csv`, паузы `pauses.csv`) |
| `record/sources/<имя>.sh` | Источники, по файлу на источник |
| `record/softdirty.c` → `qmem-softdirty` | Помощник источника softdirty |

## Источники

| Источник | Файлы | Что пишет | Статус |
| --- | --- | --- | --- |
| `damon` | `damon/access.csv`, `damon/damon.data` | Псевдочастоты регионов DAMON (через `damo`) | есть |
| `smaps` | `smaps/<ts_ms>.txt` | Копии `/proc/<pid>/smaps` | есть |
| `proc` | `proc/proc.csv` | minflt, majflt, RSS процесса | есть |
| `system` | `system/system.csv` | meminfo, PSI, vmstat | есть |
| `softdirty` | `softdirty/writes.csv` | Записанные страницы по отображениям (только с `-w`) | есть |
| `syscalls` | `syscalls/syscalls.txt` | mmap/munmap/brk/mremap/madvise (tracefs) | есть |
| `pmu_count` | — | Общее число обращений (perf) | **заглушка** |
| `pmu_sample` | — | Выборка адресов (PEBS/SPE) | **заглушка** |

Статус каждого источника пишется в `record.json`: `ok`, `off` (выключен), `unavailable` (здесь не работает, с причиной) или `stub`. Заглушки PMU проверяют, есть ли аппаратные счётчики. Без них (WSL) статус — `unavailable`, с ними — `stub`: пока ничего не записывается.

### Интерфейс источника

Источник `X` — файл `sources/X.sh` с функциями:

| Функция | Когда | Что делает |
| --- | --- | --- |
| `src_X_check` | до старта | 0 — может работать; 3 — заглушка; иначе недоступен, причина в `SRC_REASON` |
| `src_X_start <pid>` | после запуска приложения | Начать запись; ошибка → источник `unavailable` |
| `src_X_tick <ts_ms>` | каждые `-i` мс | Снимок (только smaps, proc, system); 1 от smaps — приложение завершилось |
| `src_X_stop` | при остановке | Остановить фоновые процессы |
| `src_X_finish` | после закрытия часов | Довести файлы (damon: `access.csv` по меткам часов) |
| `src_X_manifest` | в конце | Поля источника для `record.json`: `version`, `files`, параметры |

Новый источник — новый файл и его имя в `ALL_SOURCES` в driver.

## Как пользоваться

```sh
sudo record/qmem-record.sh -o out -- ./my_app --flag           # все источники
sudo record/qmem-record.sh -o out -p 1234 -d 30 -w 5000        # подключиться на 30 с, с softdirty
sudo record/qmem-record.sh -o out --sources smaps,proc -- ./my_app   # только часть источников
record/qmem-record.sh --normalize out                          # пересобрать damon/access.csv
```

| Опция | Значение |
| --- | --- |
| `-o <dir>` | Каталог записи (пустой или несуществующий) |
| `-i <ms>` | Период снимков smaps, proc, system (по умолчанию 1000) |
| `-w <ms>` | Включить softdirty с этим периодом |
| `-p <pid>` | Подключиться к процессу вместо запуска |
| `-d <s>` | Остановить через `<s>` секунд |
| `-D "<опции>"` | Дополнительные опции `damo record` |
| `--no-syscalls` | Выключить источник syscalls |
| `--sources <список>` | Включить только эти источники; неизвестное имя — ошибка (код 2) |

Без источника `damon` приложение запускает сам driver.

## Тесты

`tests/test_record_*.py`, `tests/test_softdirty.py`: раскладка v2 и `record.json`, `--sources`, заглушки PMU, режимы запуска и подключения, остановка (выход, `-d`, Ctrl+C), метки часов и паузы, tracefs. Тесты с настоящими DAMON и tracefs идут под root (`sudo make test`).
