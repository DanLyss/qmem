# Задача: перенести record (и при необходимости builder) на телефон

Для агента, который работает с телефоном (HarmonyOS, HongMeng, aarch64, root через `hdc shell`). Replay на телефоне уже собран и проверен ([replay_guide.md](replay_guide.md), [phone_port_report.md](phone_port_report.md)).

## Цель

На телефоне записать работу программы так, чтобы дальше всё шло как на Linux:

```
телефон: программа + record  →  папка записи  →  hdc file recv  →
компьютер: builder/qmem-build.py <запись> -o profile.txt  →  hdc file send  →
телефон: qmem-replay profile.txt
```

DAMON на телефоне есть, но его интерфейс и формат вывода отличаются от Linux. Твоя задача — переделать **record** под DAMON телефона. **builder** переносить не нужно: он работает на компьютере и читает только файлы записи. Трогай его, только если иначе никак — и опиши почему.

## Правила

- Работай **локально**: в своей копии репозитория, в отдельной ветке (например, `phone-record`). **Не пушь** в `DanLyss/qmem`, не меняй `main`.
- `replay/` не меняй.
- Главное — **формат записи** ([formats/record.md](formats/record.md)): папку записи потом читает наш builder. Скрипты можно переписать как угодно, формат на выходе должен остаться тем же.
- Отчёт («выжимку») пиши, только когда тебя об этом попросят (раздел «Выжимка» ниже).

## Что делает record сейчас (Linux)

`record/qmem-record.sh -o <папка> -p <pid> [-i <интервал_мс>]` — bash-скрипт. Следит за уже запущенным процессом, пока тот не завершится или не придёт Ctrl+C. Источники лежат в `record/sources/`, у каждого функции `src_X_check/start/tick/stop/finish/manifest`:

| Источник | Файл | Как на Linux | Что пишет |
| --- | --- | --- | --- |
| `damon` | `damon.sh` | утилита `damo record <pid>` (python3), часы трассировки через tracefs, потом `damo report` | `damon/access.csv` |
| `smaps` | `smaps.sh` | копия `/proc/<pid>/smaps` раз в `-i` мс | `smaps/<ts_ms>.txt` |
| `proc` | `proc.sh` | `/proc/<pid>/stat` | `proc/proc.csv` |
| `system` | `system.sh` | `/proc/meminfo`, `/proc/vmstat`, `/proc/pressure/memory` | `system/system.csv` |

Все времена — по часам `boot` (`/proc/uptime`), в миллисекундах от старта записи (`time.start_ms` в `record.json`).

## Что builder берёт из записи (это и есть контракт)

1. `record.json` — манифест: `time.start_ms`, `sources.<имя>.status` и `files` ([formats/record.md](formats/record.md)).
2. `smaps/<ts_ms>.txt` — копии `/proc/<pid>/smaps`. Из них берутся анонимные отображения (адреса) и их `Rss`. **Без smaps профиля не будет.**
3. `damon/access.csv` — CSV с заголовком:

   ```
   snapshot_start_ns,snapshot_end_ns,start,end,access_rate,access_hz,age
   ```

   - по строке на регион DAMON в каждом снимке;
   - `snapshot_start_ns`, `snapshot_end_ns` — начало и конец снимка (интервала агрегации), **наносекунды от старта записи** по часам `boot`;
   - `start`, `end` — виртуальные адреса региона в процессе (десятичные числа);
   - `access_rate` — доля проверок, в которых DAMON видел обращение, **в процентах** (0–100) за этот снимок;
   - `access_hz` и `age` builder сейчас не читает — можно писать 0.

   Builder читает только `snapshot_start_ns`, `snapshot_end_ns`, `start`, `end`, `access_rate` (`builder/readers/damon.py`).
4. Интервал выборки DAMON: builder ищет ключ `sample_us` в `damon/damon.data.kdamonds` (любой JSON, ключ на любой глубине). Нет файла — считает 5 мс. **Если на телефоне интервал другой — положи JSON с `sample_us`**, иначе частоты в профиле будут неверными.

`proc` и `system` builder для профиля не использует — их можно оставить как есть или отключить, если не работают.

## Порядок работы

### 1. Выясни окружение и запиши результат

- есть ли `bash` (скрипты record — bash), `python3`, `awk`, `date`;
- работают ли `/proc/<pid>/smaps`, `/proc/<pid>/stat`, `/proc/uptime`, `/proc/meminfo`, `/proc/vmstat`;
- как устроен DAMON телефона: где его интерфейс (sysfs? другое?), как задать целевой pid, интервалы (sampling, aggregation), как получить регионы и счётчики обращений, по каким часам метки времени;
- есть ли `damo` или его аналог.

### 2. Сделай источник damon для телефона

Лучше новым файлом, не трогая Linux-версию: например, `record/sources/damon_ohos.sh` с теми же функциями `src_damon_*`, и выбор нужной версии в `qmem-record.sh`. Он должен:

- запустить DAMON на целевой pid;
- собирать снимки: регионы `[start, end)` и долю обращений за интервал агрегации;
- перевести время на часы `boot` от старта записи (как это делает Linux-версия через поправку `boot − mono` — посмотри `src_damon_start` и `src_damon_normalize`);
- записать `damon/access.csv` ровно в формате выше;
- записать `sample_us` (раздел «Контракт», п. 4);
- корректно остановить DAMON и вернуть систему в исходное состояние (как `src_damon_restore_clock`).

### 3. Остальное — минимально

- Если нет `bash` — либо найди способ его поставить, либо перепиши `qmem-record.sh`, `lib.sh` и источники на `sh` (toybox). Опиши, что выбрал.
- `smaps` обязателен. `proc`, `system` — по возможности.

### 4. Проверь целиком

1. Собери тестовую программу для телефона: `workloads/hot_region.c` тем же тулчейном, что replay (`Makefile.phone`, только другой исходник). Запуск: `hot_region <MB> <hot_MB> <секунд>`, например `hot_region 64 8 10`.
2. На телефоне: запусти её в фоне и запиши: `record … -o rec -p $!`.
3. Забери `rec/` на компьютер. Там:

   ```sh
   builder/qmem-build.py rec -o profile.txt
   ```

   Builder не должен падать. В stderr — сводка: регионы, события, эпохи.
4. Проверь профиль глазами: есть строки `region`, `map`, `populate`, `epoch`, `access`. У горячих 8 МБ `rate` должен быть заметно выше, чем у остальных 56.
5. Отправь `profile.txt` на телефон и запусти `qmem-replay profile.txt --stats stats.csv`. Ожидаемо: `exit=0`, `done` близко к `planned`, `wall_s` ≈ длине записи, `maxrss_kb` близко к RSS исходной программы.

Для сравнения можно записать ту же программу на Linux и сравнить профили: число регионов, эпох, порядок `rate`.

## Выжимка (когда попросят)

Один файл `docs/phone_record_report.md`:

1. Окружение телефона: что есть, чего нет (bash, python3, damo, части `/proc`).
2. DAMON телефона: интерфейс, параметры, формат вывода, часы — чем отличается от Linux.
3. Что изменено в record: файлы, дифф или его суть; что выбрано для bash/sh.
4. Пример записи: `record.json`, первые строки `access.csv`, список файлов `smaps/`.
5. Проверка из раздела 4: команды, вывод builder, начало профиля, итоговая строка replay.
6. Что не получилось или работает не так, как на Linux.

Дифф кода — отдельным файлом рядом (`docs/phone_record.diff`) или веткой, как скажут.
