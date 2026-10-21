# Формат записи (qmem-record, версия 2)

Каталог записи создаёт `record/qmem-record.sh`. Каждый источник данных пишет только в свой подкаталог. Что записано, перечислено в манифесте `record.json`.

```
<dir>/
  record.json        # манифест
  clock.csv          # метки часов: local_s,ts_ms (если есть tracefs)
  pauses.csv         # паузы виртуальной машины: ts_ms,pause_ms (если есть clock.csv)
  damon/             # источник damon
  smaps/             # источник smaps
  proc/              # источник proc
  system/            # источник system
  softdirty/         # источник softdirty
  syscalls/          # источник syscalls
  pmu_count/         # заглушка
  pmu_sample/        # заглушка
```

Время во всех файлах — `ts_ms`, миллисекунды от старта записи по настенным часам. Паузы виртуальной машины из `pauses.csv` читатели вычитают сами.

## record.json

```json
{
  "format": "qmem-record",
  "version": 2,
  "target": {"mode": "launch", "command": "./app --x", "pid": 1234, "kernel_pid": 1234},
  "time": {"start_time": 1791554595.73, "end_time": 1791554604.85,
           "start_uptime": 16772.57, "paused_ms": 0},
  "stop": {"reason": "exited", "interrupted": false},
  "kernel": "6.18.35.2-microsoft-standard-WSL2+",
  "interval_ms": 1000,
  "sources": {
    "damon":     {"status": "ok", "version": 1, "files": ["damon/access.csv"], ...},
    "pmu_count": {"status": "unavailable", "reason": "no PMU (perf_event_open: hardware events)"}
  }
}
```

| Поле | Значение |
| --- | --- |
| `target.mode` | `launch` (запустили сами) или `attach` (`-p`) |
| `target.kernel_pid` | pid, под которым процесс известен ядру (отличается внутри pid namespace) |
| `time.start_uptime` | `/proc/uptime` в момент старта — для совмещения с часами `boot` |
| `stop.reason` | `exited` (приложение завершилось) или `interrupted` (Ctrl+C) |
| `sources.<имя>.status` | `ok`, `unavailable` (проверка не прошла, источник пропущен), `off` (выключен опцией) или `stub` (заглушка, данных нет) |
| `sources.<имя>.version` | версия формата файлов источника |
| `sources.<имя>.files` | файлы источника, относительно каталога записи |

Остальные поля источника — его параметры (ниже).

## Источники

### damon (версия 1)

| Файл | Содержимое |
| --- | --- |
| `damon/access.csv` | `snapshot_start_ns,snapshot_end_ns,start,end,access_rate,access_hz,age` — регионы DAMON по снимкам. Время в шкале записи (если есть `clock.csv`), адреса — виртуальные адреса процесса, `access_rate` — % проверок с «да» |
| `damon/damon.data`, `damon/damon.data.kdamonds` | файлы damo, напрямую не читаются |

Параметры: `damo_version`, `time_base` (`record` или `damo`).

### smaps (версия 1)

`smaps/<ts_ms>.txt` — копии `/proc/<pid>/smaps`. Параметры: `interval_ms`.

### proc (версия 1)

`proc/proc.csv`: `ts_ms,minflt,majflt,rss_kb` — счётчики процесса с его старта.

### system (версия 1)

`system/system.csv`: `ts_ms,MemFree,MemAvailable,Cached,SwapFree,psi_some_avg10,psi_full_avg10,pgfault,pgmajfault,pswpin,pswpout` (память в кБ).

### softdirty (версия 1)

`softdirty/writes.csv`: `ts_ms,start,end,present_pages,dirty_pages,rewritten_pages` по отображениям, выборка раз в `period_ms` (1000). Параметры: `period_ms`.

### syscalls (версия 1)

`syscalls/syscalls.txt` — сырой вывод tracefs (mmap, munmap, brk, mremap, madvise), часы `boot`. Параметры: `overrun` (потерянные события).

### pmu_count, pmu_sample (заглушки, версия 0)

Проверяют доступность аппаратных счётчиков. Без них — `status: unavailable` с причиной, с ними — `status: stub`: данных пока нет.
