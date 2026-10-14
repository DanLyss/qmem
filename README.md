# Qmem

Запись нагрузки приложения на память и её воспроизведение без приложения — с теми же побочными эффектами в ядре (page faults, LRU, swap, файловый кэш).

Сейчас идёт работа над **каркасом**: архитектурные шаблоны модулей, форматы и заглушки ([docs/skeleton.md](docs/skeleton.md)). Метрики и точность в этой работе не рассматриваются.

| Модуль | Что сделано | Формат |
| --- | --- | --- |
| record — запись | [record/README.md](record/README.md) | [record v2](docs/formats/record.md) |
| builder — сборка profile | [builder/README.md](builder/README.md) | — |
| replay — воспроизведение | [replay/README.md](replay/README.md) | [profile v2](docs/formats/profile.md) |

```sh
make
sudo record/qmem-record.sh -o out -- ./workloads/hot_region 256 16 10   # записать
builder/qmem-build.py out -o profile.txt                                # собрать profile
replay/qmem-replay profile.txt --stats stats.csv                        # воспроизвести
```

Для записи нужны Linux с DAMON (`CONFIG_DAMON_VADDR`, `CONFIG_DAMON_SYSFS`), [damo](https://github.com/damonitor/damo) и root.

### Документы

- [docs/spec.md](docs/spec.md) — что такое Qmem и зачем
- [docs/architecture.md](docs/architecture.md) — схема: датафлоу и устройство модулей
- [docs/skeleton.md](docs/skeleton.md) — каркас: модули, интерфейсы, заглушки, план и что дальше

### Итерация 1 (история)

- verify, mkprofile, инструменты оценки, примеры и старые форматы (record v1, profile v1) удалены; они есть в git до тега `skeleton-v0.1`

## Тесты

```sh
make test                 # без root: тесты с DAMON и tracefs пропускаются
sudo make test            # с root и DAMON: все тесты
```
