# Qmem

Запись нагрузки приложения на память и её воспроизведение без приложения — с теми же побочными эффектами в ядре (page faults, LRU, swap, файловый кэш).

Сейчас готов **каркас** ([docs/skeleton.md](docs/skeleton.md)): три модуля, форматы между ними и заглушки там, где будут другие алгоритмы. Replay перенесён на телефон (HarmonyOS, aarch64) и сравнён с Linux. Точность воспроизведения пока не оценивается.

```
программа → record → папка записи → builder → profile → replay
```

| Модуль | Что делает | Описание | Формат на выходе |
| --- | --- | --- | --- |
| record | следит за работающим процессом: DAMON, smaps, `/proc` | [record/README.md](record/README.md) | [record v2](docs/formats/record.md) |
| builder | собирает profile из записи | [builder/README.md](builder/README.md) | [profile v2](docs/formats/profile.md) |
| replay | исполняет profile: память и касания страниц по расписанию | [replay/README.md](replay/README.md) | итоговая строка, `--stats`, [лог](docs/formats/replay_log.md) |

```sh
make
./workloads/hot_region 256 16 10 & sudo record/qmem-record.sh -o out -p $!   # записать
builder/qmem-build.py out -o profile.txt                                # собрать profile
replay/qmem-replay profile.txt --stats stats.csv                        # воспроизвести
```

Для записи нужны Linux с DAMON (`CONFIG_DAMON_VADDR`, `CONFIG_DAMON_SYSFS`), [damo](https://github.com/damonitor/damo) и root. Для replay — только Linux-совместимое ядро и libc; root не нужен.

## Что в репозитории

| Папка | Что |
| --- | --- |
| `record/`, `builder/`, `replay/` | три модуля |
| `workloads/` | тестовые программы на C (`hot_region`, `rw_split`, `random_access`, `moving_window`, `fixed_work`) |
| `tools/` | `memtest2profile.py` (конфиг damoLoad → profile), `damoload_check.py` (replay против damoLoad), `measure-overhead.sh` (накладные расходы record) |
| `tests/` | тесты всех модулей (`make test`) |
| `bench/` | 100 профилей, прогон `run.sh`, результаты Linux и телефона |
| `third_party/damoLoad` | подмодуль: генератор нагрузки для сравнения |
| `Makefile.phone` | сборка replay под телефон (OHOS clang) |

## Документы

- [docs/spec.md](docs/spec.md) — что такое Qmem и зачем
- [docs/architecture.md](docs/architecture.md) — схема: датафлоу и устройство модулей
- [docs/skeleton.md](docs/skeleton.md) — каркас: модули, интерфейсы, заглушки
- [docs/formats/](docs/formats/) — форматы: запись, profile, лог replay
- [docs/replay_testing_report.md](docs/replay_testing_report.md) — проверка replay: лог, тесты алгоритма, сравнение с damoLoad
- [docs/replay_guide.md](docs/replay_guide.md) — сборка и проверка replay на новой машине
- [docs/phone_port_report.md](docs/phone_port_report.md) — сборка replay на телефоне
- [docs/phone_vs_linux.md](docs/phone_vs_linux.md) — replay на Linux и на телефоне: 100 профилей
- [docs/phone_record_task.md](docs/phone_record_task.md) — задача: перенести record под DAMON телефона

Итерация 1 (verify, mkprofile, record v1, profile v1) удалена; она есть в git до тега `skeleton-v0.1`.

## Известные ограничения

- **Дедлайн тика в replay.** `exec_tick` смотрит на часы раз в 1024 касания одного участка. Если в эпохе много мелких участков (меньше 1024 касаний за тик у каждого), часы не проверяются, лишняя работа не выбрасывается и replay отстаёт от профиля: в `bench/` профиль на 0.5 с шёл 17 с на Linux и 20 с на телефоне. Исправление — общий счётчик касаний за тик.
- **`populate` и обращения DAMON (builder, `estimators/resident.py`).** `populate` — разовая запись в каждую страницу отображения, чтобы у replay в RAM было столько же, сколько Rss у оригинала. Частоту обращений она почти не меняет, но какие страницы заполнять, `resident` выбирает без оглядки на DAMON — всегда самые младшие адреса. Если DAMON трогает другие страницы того же отображения, в RAM окажется больше, чем Rss оригинала. Исправление: засчитывать участки, которые затронут эпохи (`model.observations`).
- **Только анонимная память.** Файловые регионы (`kind=file`) replay отвергает.
- **`rate` — псевдочастота DAMON** (доля проверок с обращением / интервал выборки), а не настоящая частота обращений программы.
- **Прогоны не повторяются**: стартовые страницы генераторов случайные в каждом прогоне, `checksum` разный.
- **Page faults на разных ядрах не сравнимы**: на телефоне `minflt` на больших регионах на 30–45% меньше, чем на Linux, при той же RAM ([docs/phone_vs_linux.md](docs/phone_vs_linux.md)). Сравнивать replay надо с оригиналом, записанным на той же машине.

## Тесты

```sh
make test                 # без root: тесты с DAMON и tracefs пропускаются
sudo make test            # с root и DAMON: все тесты
```
