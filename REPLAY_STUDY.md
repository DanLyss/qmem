# План: освоить replay

Начато 2026-10-27, обновлено 2026-10-30. Цель — понимать код replay настолько, чтобы его поддерживать.

## Формат (договорились 2026-10-30)

1. Файлы по порядку (ниже), каждый от начала до конца.
2. Для каждого файла: **обзор** на один экран (что делает + пример на `ex.txt`, вход → выход), потом **построчно** частями по 40–60 строк: код, под ним пояснение — что делает, аргументы и что функция читает/пишет, пример на `ex.txt`.
3. В конце части — «нашёл, не трогал» и «дальше?». Переспрашиваю — объясняется именно это место.
4. Упражнения — после всех файлов.

## Карта файлов

| Файл | Строк | Что отвечает |
| --- | --- | --- |
| `profile.h` | 100 | общие структуры и объявления между файлами |
| `loader.c` | 340 | **что**: профиль → таблицы |
| `replay.c` | 348 | **когда и сколько**: тики, события, эпохи, касания тика, вывод |
| `generators.c`, `gen_roundrobin.c`, `gen_random.c` | 23 + 33 + 38 | **куда**: какая страница участка следующая |
| `memory.c` | 139 | **как**: резерв (по адресу оригинала, если свободен), map/unmap/populate, касание страницы |
| `log.c` | 189 | пошаговый лог (`--log`), только наблюдает |

```
main (replay.c)
 ├─ load()                      loader.c   профиль → regions, events, epochs, accesses
 ├─ prepare()                   replay.c   prev, семена (gen->init), memory_prepare
 ├─ replay()                    replay.c
 │    plan()                               end_ms, planned
 │    цикл по тикам t = 0 … end_ms−1:
 │      sleep_until → end_epoch → exec_events → start_epochs → note_lag →
 │      exec_tick (→ gen->next, memory_touch) → drop_late_ticks, если не успели
 └─ write_stats / write_layout / log_write / итоговая строка
```

## Пример `ex.txt`

```
qmem-profile 2
meta epoch_ms=100
region id=0 size=40960
region id=1 size=8192
map t=0 region=0 off=0 len=40960
populate t=0 region=0 off=0 len=16384
map t=0 region=1 off=0 len=8192
epoch t=0
access region=0 off=0 len=8192 rate=2.5 write=1
access region=1 off=0 len=8192 rate=10 pattern=random
epoch t=100 dur=200
access region=0 off=0 len=8192 rate=3 write=1
unmap t=150 region=1 off=0 len=8192
```

```sh
make
replay/qmem-replay ex.txt --stats s.csv --log log.txt --log-level touches
grep -v '^tick' log.txt
```

Касания на 49, 99 (регион 1) и 183 мс (регион 0), `planned=3 done=3 populated=4`.

## Порядок и прогресс

### Сделано

- `loader.c`, `profile.h` — построчно.
- `replay.c`:
  - шапка, глобальные счётчики;
  - `now_ns`, `exec_tick` (аргументы, `carry`, `+1e-9`, дедлайн раз в 1024 касания, сценарии: медленно, дробно, пропуск, перегрузка);
  - `sleep_until`, `prepare` (`prev` и зачем он: `carry` и курсор через границу эпох; семена; резерв);
  - новый `replay()`: `tick_ms`, переменные цикла, `plan`, `epoch_over`, `end_epoch`, `exec_events`, `start_epochs`, `note_lag`, `drop_late_ticks`, сам `replay()`;
  - `peak_rss_kb`, `write_stats`, `main`.
- `memory.c` — все четыре блока: битовые карты, `page_fraction`, `memory_prepare` (резерв адресов без RAM, `base=`), `memory_apply`, `memory_touch`.
- генераторы: интерфейс `generator_ops`, `generators.c`, `gen_roundrobin.c`, `gen_random.c`.

### Дальше

Пауза с 2026-11-06 (порт на телефон). Продолжить с `log.c`.

| # | Что | Строки |
| --- | --- | --- |
| 7 | `log.c` | 1–189 |
| 8 | тесты как спецификация: `test_replay_algorithm.py` (главный), `test_replay.py`, `test_replay_v2.py`, `test_replay_log.py` | |
| 9 | упражнения (ниже) | |

## Что поменялось в коде за время разбора

| Коммит | Что |
| --- | --- |
| `e52e41f` | убран `threads=` |
| `21391d7` | проверки loader помечены `/* ext */` |
| `0d38c20` | `exec.c` влит в `replay.c` |
| `9496577`, `dfd2484` | регион резервируется по `base=` оригинала, если адрес свободен; иначе предупреждение и любой адрес |
| `6eae5cb` | цикл `replay()` разбит на функции `plan`, `end_epoch`, `exec_events`, `start_epochs`, `note_lag`, `drop_late_ticks` |
| `7f24a3d`, `70812da`, `6b6e238` | опоздание тика, который вылез за свой конец, видно в `max_lag_ms` и `lag_ms` |
| `60eeee7` | убран `--layout` |
| `4a5d749` | `usage()` вместо `goto` |
| `14a11c7`, `704183a` | seed убран отовсюду: генераторы стартуют со случайного состояния в каждом прогоне |

## Упражнения (после всех файлов)

**Генератор.** Написать `gen_stride.c` (шаг 8 страниц), добавить в таблицу `generators.c`, `pattern=stride` в `ex.txt` на участок побольше, посмотреть `touch` в логе, удалить.

**Поломай и почини.** По одной правке: `python3 -m pytest -q tests/test_replay*.py` → какой тест упал и почему → `git checkout replay/`.

| Сломать | Какой тест должен упасть (догадка по именам, не проверено) |
| --- | --- |
| убрать `+ 1e-9` в `exec_tick` | `test_touch_count_and_carry` |
| в `start_epochs` `a->carry = 0` вместо `prev->carry` | `test_slow_access_over_short_epochs_is_not_lost` |
| roundrobin `epoch` не берёт курсор у `prev` | `test_roundrobin_walks_in_a_circle_and_continues` |
| `tick_ms = 10` | тесты распределения касаний в эпохе |
| убрать `+ 1` страницу в `memory_prepare` | `test_regions_stay_separate_mappings` |
| `drop_late_ticks` всегда возвращает `t` | `test_dropped_work_is_not_done_later`, `test_overload_does_not_drift` |

Не упал ни один или упал другой — тоже ответ: тест этого не проверяет.

## Что должен уметь в конце

- По строке лога сказать, какая строка кода её написала.
- Знать, куда добавить новый тип события, новый генератор, новую колонку в `stats`.
- По упавшему тесту из `test_replay_algorithm.py` найти нарушенное правило алгоритма.
