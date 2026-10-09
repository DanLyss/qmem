# Qmem

Запись нагрузки приложения на память и её воспроизведение без приложения — с теми же побочными эффектами в ядре (page faults, LRU, swap, файловый кэш).

- [docs/spec.md](docs/spec.md) — что такое Qmem и зачем
- [docs/architecture.md](docs/architecture.md) — архитектура и общий план
- [docs/modules/record.md](docs/modules/record.md) — qmem-record, итерация 1
- [docs/modules/replay.md](docs/modules/replay.md) — qmem-replay, итерация 1

## Итерация 1: qmem-record

```sh
make                      # собрать тестовые нагрузки
sudo record/qmem-record.sh -o out -- ./workloads/hot_region 256 16 10
record/qmem-record.sh --normalize out   # пересобрать access.csv из out/damon.data
```

Нужны Linux с DAMON (`CONFIG_DAMON_VADDR`, `CONFIG_DAMON_SYSFS`), [damo](https://github.com/damonitor/damo) и root.

## Тесты

```sh
make test                 # без root: интеграционный тест пропускается
sudo make test            # с root и DAMON: все тесты
```
