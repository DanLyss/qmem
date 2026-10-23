# Формат записи (qmem-record, версия 2)

Каталог записи создаёт `record/qmem-record.sh`. Каждый источник данных пишет только в свой подкаталог. Что записано, перечислено в манифесте `record.json`.

```
<dir>/
  record.json        # манифест
  damon/             # источник damon
  smaps/             # источник smaps
  proc/              # источник proc
  system/            # источник system
```

Часы у записи одни — `mono` (`CLOCK_MONOTONIC`: время с загрузки системы без сна). Время во всех файлах — `ts_ms`, миллисекунды по ним от старта записи (`time.start_ms`).

## record.json

```json
{
  "format": "qmem-record",
  "version": 2,
  "target": {"command": "./app --x", "pid": 1234},
  "time": {"clock": "mono", "start_ms": 7603190,
           "start_time": 1791554595.73, "end_time": 1791554604.85},
  "stop": {"reason": "exited", "interrupted": false},
  "kernel": "6.18.35.2-microsoft-standard-WSL2+",
  "interval_ms": 1000,
  "sources": {
    "damon":     {"status": "ok", "version": 1, "files": ["damon/access.csv"], ...}
  }
}
```

| Поле | Значение |
| --- | --- |
| `time.start_ms` | старт записи по часам `mono`, мс; от него отсчитываются все `ts_ms`. `start_time`, `end_time` — настенные часы, для человека |
| `stop.reason` | `exited` (приложение завершилось) или `interrupted` (Ctrl+C) |
| `sources.<имя>.status` | `ok`, `unavailable` (проверка не прошла, источник пропущен) или `off` (выключен опцией) |
| `sources.<имя>.version` | версия формата файлов источника |
| `sources.<имя>.files` | файлы источника, относительно каталога записи |

Остальные поля источника — его параметры (ниже).

## Источники

### damon (версия 1)

| Файл | Содержимое |
| --- | --- |
| `damon/access.csv` | `snapshot_start_ns,snapshot_end_ns,start,end,access_rate,access_hz,age` — регионы DAMON по снимкам. Время — нс от старта записи, адреса — виртуальные адреса процесса, `access_rate` — % проверок с «да» |
| `damon/damon.data`, `damon/damon.data.kdamonds` | файлы damo, напрямую не читаются |

Параметры: `damo_version`.

damo ставит снимкам метки по часам трассировки. На время записи источник переводит их на `mono` — часы записи — и потом возвращает прежние.

### smaps (версия 1)

`smaps/<ts_ms>.txt` — копии `/proc/<pid>/smaps`. Параметры: `interval_ms`.

### proc (версия 1)

`proc/proc.csv`: `ts_ms,minflt,majflt,rss_kb` — счётчики процесса с его старта.

### system (версия 1)

`system/system.csv`: `ts_ms,MemFree,MemAvailable,Cached,SwapFree,psi_some_avg10,psi_full_avg10,pgfault,pgmajfault,pswpin,pswpout` (память в кБ).
