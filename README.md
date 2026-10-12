# Qmem

Запись нагрузки приложения на память и её воспроизведение без приложения — с теми же побочными эффектами в ядре (page faults, LRU, swap, файловый кэш).

- [docs/spec.md](docs/spec.md) — что такое Qmem и зачем
- [docs/architecture.md](docs/architecture.md) — архитектура и общий план
- [docs/modules/record.md](docs/modules/record.md) — qmem-record, итерация 1 ([что сделано](record/README.md))
- [docs/modules/replay.md](docs/modules/replay.md) — qmem-replay, итерация 1 ([что сделано](replay/README.md))
- [examples/python_phases](examples/python_phases/README.md) — пример настоящей записи

## Итерация 1: qmem-record

```sh
make                                                    # нагрузки и qmem-softdirty
sudo record/qmem-record.sh -o out -- ./workloads/hot_region 256 16 10
sudo record/qmem-record.sh -o out2 -p <pid> -d 30 -w 5000  # подключиться на 30 с, с долей записи
record/qmem-record.sh --normalize out                   # пересобрать access.csv из damon.data
tools/measure-overhead.sh -n 5 -- ./workloads/fixed_work 512 600
```

Опции и формат результата — в [docs/modules/record.md](docs/modules/record.md).

Нужны Linux с DAMON (`CONFIG_DAMON_VADDR`, `CONFIG_DAMON_SYSFS`), [damo](https://github.com/damonitor/damo) и root.

## Итерация 1: qmem-replay

```sh
tools/mkprofile.py out -o profile.txt                   # profile из записи
sudo record/qmem-record.sh -o again -- replay/qmem-replay profile.txt
verify/qmem-verify.py out again --check                 # насколько replay похож на оригинал (см. docs/modules/verify.md)
```

## Тесты

```sh
make test                 # без root: интеграционный тест пропускается
sudo make test            # с root и DAMON: все тесты
```
