# Архитектура: модули и датафлоу

Три программы, между ними только файлы двух форматов: запись ([record v2](formats/record.md)) и [profile v2](formats/profile.md). Подробности каждой части — в [skeleton.md](skeleton.md) и README модулей.

## Общая схема

```mermaid
flowchart LR
    APP(["приложение"])

    subgraph KERNEL["ядро Linux"]
        DAMON["DAMON / kdamond"]
        PROC["/proc/pid: smaps, stat, pagemap"]
        SYS["/proc: meminfo, vmstat, PSI"]
        TRACE["tracefs"]
    end

    subgraph RECORD["record — record/"]
        direction TB
        DRV["driver<br/>qmem-record.sh"]
        LIB["lib.sh<br/>часы, метки, паузы"]
        subgraph SRC["sources/"]
            S_DAMON["damon.sh<br/>→ damo"]
            S_SMAPS["smaps.sh"]
            S_PROC["proc.sh"]
            S_SYS["system.sh"]
            S_SD["softdirty.sh<br/>→ qmem-softdirty"]
            S_SC["syscalls.sh"]
            S_PMU["pmu_count.sh<br/>pmu_sample.sh<br/><i>заглушки</i>"]
        end
        DRV --> LIB
        DRV --> SRC
    end

    subgraph RDIR["каталог записи out/"]
        direction TB
        F_JSON[/"record.json"/]
        F_TIME[/"clock.csv, pauses.csv"/]
        F_DAMON[/"damon/access.csv"/]
        F_SMAPS[/"smaps/*.txt"/]
        F_SD[/"softdirty/writes.csv"/]
        F_OTHER[/"proc/, system/, syscalls/<br/><i>пока не читаются</i>"/]
    end

    subgraph BUILDER["builder — builder/"]
        direction TB
        LAYOUT["readers/layout.py<br/>где файлы источников"]
        TBASE["readers/timebase.py<br/>вычет пауз"]
        subgraph READERS["readers/"]
            R_SMAPS["smaps.py"]
            R_DAMON["damon.py"]
            R_SD["softdirty.py"]
        end
        MODEL["model.py<br/>регионы, эпохи, наблюдения"]
        subgraph EST["estimators/"]
            E_LC["lifecycle"]
            E_RES["resident"]
            E_INT["intensity_passthrough<br/><i>заглушка</i>"]
            E_WR["write_softdirty<br/><i>заглушка</i>"]
            E_TM["timing_roundrobin<br/><i>заглушка</i>"]
            E_LC --> E_RES --> E_INT --> E_WR --> E_TM
        end
        WRITER["profile_v2.py<br/>writer"]
        LAYOUT --> READERS
        TBASE --> READERS
        READERS --> MODEL --> EST --> WRITER
    end

    PROFILE[/"profile.txt"/]

    subgraph REPLAY["replay — replay/"]
        direction TB
        LOADER["loader.c<br/>текст → таблицы"]
        SCHED["replay.c<br/>scheduler: тик 1 мс"]
        EXEC["exec.c<br/>касания тика<br/><i>threads — заглушка</i>"]
        subgraph GEN["generators"]
            G_RR["gen_roundrobin.c"]
            G_RND["gen_random.c<br/>random, poisson<br/><i>заглушки</i>"]
        end
        MEM["memory.c<br/>резерв, map/unmap/populate,<br/>касание страницы"]
        LOADER --> SCHED
        SCHED -->|события| MEM
        SCHED -->|эпоха| EXEC
        EXEC -->|какая страница| GEN
        EXEC -->|touch| MEM
    end

    OUT[/"сводка в stderr<br/>stats.csv, layout.txt"/]

    APP -.->|обращения к памяти| KERNEL
    DAMON --> S_DAMON
    PROC --> S_SMAPS & S_PROC & S_SD
    SYS --> S_SYS
    TRACE --> S_SC
    TRACE --> LIB

    S_DAMON --> F_DAMON
    S_SMAPS --> F_SMAPS
    S_SD --> F_SD
    S_PROC & S_SYS & S_SC --> F_OTHER
    LIB --> F_TIME
    DRV --> F_JSON

    F_JSON --> LAYOUT
    F_TIME --> TBASE
    F_SMAPS --> R_SMAPS
    F_DAMON --> R_DAMON
    F_SD --> R_SD

    WRITER --> PROFILE --> LOADER
    MEM -.->|нагрузка на память| KERNEL
    SCHED --> OUT
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
