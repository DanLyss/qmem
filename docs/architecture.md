# Архитектура: модули и датафлоу

Три программы, между ними только файлы двух форматов: запись ([record v2](formats/record.md)) и [profile v2](formats/profile.md). Подробности каждой части — в [skeleton.md](skeleton.md) и README модулей.

## Общая схема

```mermaid
flowchart TB
    APP(["приложение"]) -.->|обращения к памяти| KERNEL

    subgraph KERNEL["ядро Linux"]
        direction LR
        DAMON["DAMON / kdamond"]
        PROC["/proc/pid<br/>smaps, stat, pagemap"]
        SYS["/proc<br/>meminfo, vmstat, PSI"]
        TRACE["tracefs"]
    end

    KERNEL --> RECORD

    subgraph RECORD["record — record/"]
        direction LR
        DRV["driver qmem-record.sh<br/>+ lib.sh: часы, метки, паузы<br/>→ record.json, clock.csv, pauses.csv"]
        subgraph SRC["sources/"]
            direction TB
            S_DAMON["damon.sh (damo)<br/>→ damon/access.csv"]
            S_SMAPS["smaps.sh<br/>→ smaps/*.txt"]
            S_SD["softdirty.sh (qmem-softdirty)<br/>→ softdirty/writes.csv"]
            S_REST["proc.sh, system.sh, syscalls.sh<br/>→ proc/, system/, syscalls/"]
            S_PMU["pmu_count.sh, pmu_sample.sh<br/><i>заглушки</i>"]
        end
        DRV -->|check, start, tick, stop, finish| SRC
    end

    RECORD ==> RDIR[/"каталог записи out/ — record v2"/] ==> BUILDER

    subgraph BUILDER["builder — builder/"]
        direction LR
        LAYOUT["readers/layout.py<br/>record.json: где файлы"]
        subgraph READERS["readers/"]
            direction TB
            R_TIME["timebase.py ← pauses.csv"]
            R_SMAPS["smaps.py ← smaps/"]
            R_DAMON["damon.py ← damon/"]
            R_SD["softdirty.py ← softdirty/"]
        end
        MODEL["model.py<br/>регионы, эпохи,<br/>наблюдения DAMON"]
        subgraph EST["estimators/"]
            direction TB
            E_LC["lifecycle"] --> E_RES["resident"] --> E_INT["intensity_passthrough <i>заглушка</i>"] --> E_WR["write_softdirty <i>заглушка</i>"] --> E_TM["timing_roundrobin <i>заглушка</i>"]
        end
        WRITER["profile_v2.py<br/>writer"]
        LAYOUT --> READERS --> MODEL --> EST --> WRITER
    end

    BUILDER ==> PROFILE[/"profile.txt — profile v2"/] ==> REPLAY

    subgraph REPLAY["replay — replay/"]
        direction LR
        LOADER["loader.c<br/>текст → таблицы"]
        SCHED["replay.c<br/>scheduler, тик 1 мс"]
        EXEC["exec.c<br/>касания тика<br/><i>threads — заглушка</i>"]
        GEN["generators.c<br/>gen_roundrobin.c<br/>gen_random.c <i>заглушки</i>"]
        MEM["memory.c<br/>резерв, map/unmap/populate,<br/>касание страницы"]
        LOADER --> SCHED
        SCHED -->|эпоха| EXEC
        SCHED -->|события| MEM
        EXEC -->|какая страница| GEN
        EXEC -->|касание| MEM
    end

    REPLAY --> OUT[/"сводка в stderr, stats.csv"/]
    REPLAY -.->|нагрузка на память| KERNEL2(["ядро Linux"])
```

## Откуда каждое поле profile

```mermaid
flowchart LR
    SM[/"smaps/*.txt"/] --> LC["lifecycle"] --> P1["region, map, unmap"]
    SM --> RS["resident"] --> P2["populate"]
    DM[/"damon/access.csv"/] --> IN["intensity"] --> P3["epoch, access: region off len rate"]
    SM -.->|обрезка по выделенному| IN
    SD[/"softdirty/writes.csv"/] --> WR["write"] --> P4["access: write"]
    DM -.->|что затронуто| WR
    TM["timing"] --> P5["access: pattern"]
```

## Внутри replay: один тик

```mermaid
flowchart TB
    T["тик t: спать до start + t"] --> EV{"есть события<br/>с time ≤ t?"}
    EV -->|да| AP["memory_apply:<br/>map / unmap / populate"] --> EP
    EV -->|нет| EP{"началась<br/>новая эпоха?"}
    EP -->|да| SW["carry = 0<br/>gen.epoch(prev)"] --> RUN
    EP -->|нет| RUN{"эпоха ещё идёт?"}
    RUN -->|нет| NX
    RUN -->|да| EX["exec_tick: n = rate × 1 мс + carry<br/>n раз: page = gen.next(), memory_touch(page)"]
    EX --> DL{"успели до<br/>дедлайна?"}
    DL -->|да| NX["t += 1 мс"]
    DL -->|нет| SK["пропустить опоздавшие тики"] --> NX
    NX --> T
```

## Точки расширения

| Где | Что добавить | Как |
| --- | --- | --- |
| record | Источник | `record/sources/<имя>.sh` с `check/start/tick/stop/finish/manifest` и имя в `ALL_SOURCES` |
| builder | Reader | `builder/readers/<источник>.py` и поле в `model.py` |
| builder | Оценщик | `builder/estimators/<вид>_<имя>.py`, выбор `--intensity/--write/--timing <имя>` |
| profile | Поле или тип строки | Новый ключ; старый replay пропустит его (или откажется, если ключ с `!`) |
| replay | Генератор | `replay/gen_<имя>.c` и строка в `generators.c`, выбор `pattern=<имя>` |
