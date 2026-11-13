# Qmem

Запись нагрузки приложения на память и её воспроизведение без приложения — с теми же побочными эффектами в ядре (page faults, LRU, swap, файловый кэш).

```
программа → record → папка записи → builder → profile → replay
```

| Модуль | Что делает | Описание | Формат на выходе |
| --- | --- | --- | --- |
| record | следит за работающим процессом: DAMON, smaps, `/proc` | [record/README.md](record/README.md) | [record v2](docs/formats/record.md) |
| builder | собирает profile из записи | [builder/README.md](builder/README.md) | [profile v2](docs/formats/profile.md) |
| replay | исполняет profile без приложения | [replay/README.md](replay/README.md) | итоговая строка, `--stats`, [лог](docs/formats/replay_log.md) |

Сейчас готов **каркас** ([docs/skeleton.md](docs/skeleton.md)): модули, форматы между ними и заглушки там, где будут другие алгоритмы. Точность воспроизведения пока не оценивается.

```sh
make
./workloads/hot_region 256 16 10 & sudo record/qmem-record.sh -o out -p $!   # записать
builder/qmem-build.py out -o profile.txt                                # собрать profile
replay/qmem-replay profile.txt --stats stats.csv                        # воспроизвести
```

Для записи нужны Linux с DAMON (`CONFIG_DAMON_VADDR`, `CONFIG_DAMON_SYSFS`), [damo](https://github.com/damonitor/damo) и root.

## Replay

`replay/qmem-replay` — программа на C без зависимостей, root не нужен. Читает profile и 1 мс за 1 мс исполняет его: выделяет и освобождает память (`mmap`), заполняет резидентные страницы, касается страниц с частотой из profile. Не успевает за тик — выбрасывает лишнее, но не отстаёт по времени. На выходе — сводка (план и сделанные касания, page faults, RSS, время), `--stats` по эпохам и пошаговый лог. Подробно — [replay/README.md](replay/README.md).

Что в репозитории связано с replay:

| Что | Где |
| --- | --- |
| формат входа и лога | [docs/formats/profile.md](docs/formats/profile.md), [docs/formats/replay_log.md](docs/formats/replay_log.md) |
| тесты replay (`make test`) | `tests/test_replay*.py`, `tests/test_skeleton_e2e.py` |
| profile без записи: из конфига damoLoad | `tools/memtest2profile.py <config.json> -o profile.txt` |
| сравнение с damoLoad касание в касание | `tools/damoload_check.py`, `make damoload-check`; [docs/replay_testing.md](docs/replay_testing.md), [отчёт](docs/replay_testing_report.md) |
| генератор нагрузки damoLoad (эталон) | подмодуль `third_party/damoLoad` (`git submodule update --init`) |
| сборка и проверка на новой машине | [docs/replay_guide.md](docs/replay_guide.md) |
| телефон (HarmonyOS, aarch64) | `make -f Makefile.phone`; [docs/phone_port_report.md](docs/phone_port_report.md) |
| 100 profile на Linux и телефоне | [bench/](bench/README.md) (`run.sh`, результаты); сравнение — [docs/phone_vs_linux.md](docs/phone_vs_linux.md) |
| record на телефоне (задача) | [docs/phone_record_task.md](docs/phone_record_task.md) |

## Что ещё в репозитории

| Папка | Что |
| --- | --- |
| `workloads/` | тестовые программы на C (`hot_region`, `rw_split`, `random_access`, `moving_window`, `fixed_work`) — их записывают и воспроизводят |
| `tools/measure-overhead.sh` | накладные расходы record |
| `tests/` | тесты всех модулей |
| `docs/` | [spec.md](docs/spec.md) — что такое Qmem и зачем; [architecture.md](docs/architecture.md) — схема модулей; [skeleton.md](docs/skeleton.md) — каркас; [formats/](docs/formats/) — форматы |

Итерация 1 (verify, mkprofile, record v1, profile v1) удалена; она есть в git до тега `skeleton-v0.1`.

## Известные ограничения

- **Дедлайн тика в replay** проверяется по одному участку. Если в эпохе много мелких участков, replay отстаёт от profile: в `bench/` 0.5 с шли 17 с на Linux и 20 с на телефоне. Исправление — общий счётчик касаний за тик.
- **`populate` выбирает страницы без оглядки на DAMON** (builder, `estimators/resident.py`): всегда младшие адреса. Если DAMON трогает другие страницы того же отображения, в RAM у replay окажется больше, чем Rss оригинала. Исправление: засчитывать участки, которые затронут эпохи (`model.observations`).
- **Только анонимная память.** Файловые регионы (`kind=file`) replay отвергает.
- **`rate` — псевдочастота DAMON** (доля проверок с обращением / интервал выборки), а не настоящая частота обращений программы.
- **Прогоны replay не повторяются**: старт генераторов случайный, `checksum` свой в каждом прогоне.
- **Page faults на разных ядрах не сравнимы**: на телефоне `minflt` на больших регионах на 30–45% меньше, чем на Linux, при той же RAM ([docs/phone_vs_linux.md](docs/phone_vs_linux.md)). Сравнивать replay надо с оригиналом, записанным на той же машине.

## Тесты

```sh
make test                 # без root: тесты с DAMON и tracefs пропускаются
sudo make test            # с root и DAMON: все тесты
```
