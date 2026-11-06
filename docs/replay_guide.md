# qmem-replay: руководство по сборке и запуску

Для того, кто собирает и запускает replay на новой машине (например, на телефоне с Android). Делай шаги по порядку. Код replay не меняй, кроме случаев из раздела «Если не собирается»; любую правку опиши в отчёте.

## 1. Что это

`qmem-replay` — одна программа на C без зависимостей, кроме libc. Она читает текстовый файл-профиль и по его расписанию выделяет и освобождает память и касается страниц с заданной частотой. Root не нужен.

Исходники — только папка `replay/`:

| Файл | Что делает |
| --- | --- |
| `profile.h` | общие структуры и объявления |
| `loader.c` | читает профиль; ошибка профиля — выход с кодом 2 и `profile:<строка>: …` |
| `replay.c` | `main`, планировщик: тики по 1 мс, события, эпохи, касания, итоговая строка |
| `memory.c` | резерв памяти, map / unmap / populate, касание страницы |
| `generators.c`, `gen_roundrobin.c`, `gen_random.c` | какую страницу участка тронуть следующей |
| `log.c` | пошаговый лог (`--log`) |

## 2. Что нужно от системы

| Что | Где используется | Замечание для Android |
| --- | --- | --- |
| `mmap`, `munmap`, `MAP_FIXED`, `MAP_NORESERVE` | `memory.c` | есть |
| `MAP_FIXED_NOREPLACE` | `memory.c` | макрос должен быть в заголовках (NDK r19+, Termux — есть). Ядро старше 4.17 его не знает — код это обрабатывает сам |
| `clock_gettime`, `clock_nanosleep` (`CLOCK_MONOTONIC`) | `replay.c` | есть (bionic, API 21+) |
| `getline`, `strtok_r` | `loader.c` | есть (bionic, API 18+) |
| `sysconf(_SC_PAGESIZE)` | `replay.c` | размер страницы берётся у системы: 4096 или 16384 (см. раздел 6) |
| `getrusage` | `replay.c` | есть |
| `/proc/self/status` (строка `VmHWM`) | `replay.c` | есть; если нет — в итоге будет `maxrss_kb=-1`, это не ошибка |

## 3. Сборка

### Обычный Linux

```sh
make                       # собирает replay/qmem-replay (и workloads)
```

### Android, прямо на телефоне (Termux)

```sh
pkg install clang git
git clone <repo> qmem && cd qmem
clang -O2 -Wall -Wextra -o qmem-replay replay/*.c
```

### Android, сборка на компьютере (NDK) и запуск через adb

```sh
NDK=/path/to/android-ndk           # r19 или новее
$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang \
    -O2 -Wall -Wextra -o qmem-replay replay/*.c
adb push qmem-replay /data/local/tmp/
adb shell chmod +x /data/local/tmp/qmem-replay
```

Запускать из `/data/local/tmp` (через `adb shell`) или из домашней папки Termux. Из `/sdcard` запускать нельзя: там запрещено исполнение.

Сборка должна пройти **без предупреждений**. На x86-64 Linux с gcc и clang так и есть.

## 4. Проверка, что запускается

### 4.1. Профиль для проверки

Сохрани в файл `smoke.txt` (на телефоне — рядом с `qmem-replay`):

```
qmem-profile 2
meta epoch_ms=100
region id=0 size=1048576
region id=1 size=1048576
map t=0 region=0 off=0 len=1048576
populate t=0 region=0 off=0 len=262144
map t=0 region=1 off=0 len=1048576
epoch t=0
access region=0 off=0 len=1048576 rate=10 write=1
access region=1 off=0 len=1048576 rate=10 pattern=random
epoch t=500
access region=0 off=0 len=1048576 rate=10 write=0.3
unmap t=1000 region=1 off=0 len=1048576
```

Все размеры и смещения кратны 64 КБ, поэтому профиль подходит и для страниц 4 КБ, и для 16 КБ.

### 4.2. Запуск

```sh
./qmem-replay smoke.txt --stats stats.csv --log run.log
echo "exit=$?"
getconf PAGESIZE           # или: узнать из run.log, см. ниже
```

Прогон длится 1 секунду. В stderr — одна итоговая строка `replay: …`.

### 4.3. Что должно получиться

Числа зависят от размера страницы:

| Поле | Страница 4096 | Страница 16384 |
| --- | --- | --- |
| `exit` | 0 | 0 |
| `epochs` | 2 | 2 |
| `planned` | 768 | 192 |
| `done` | 768 | 192 |
| `skipped` | 0 | 0 |
| `populated` | 64 | 16 |
| `wall_s` | 1.00 (допустимо до ~1.05) | то же |
| `lagged_ticks` | 0 (на медленном телефоне допустимо немного) | то же |

`stats.csv`:

```
страница 4096:          страница 16384:
t_ms,planned,done,lag_ms    t_ms,planned,done,lag_ms
0,512,512,0                 0,128,128,0
500,256,256,0               500,64,64,0
```

`lag_ms` — 0 или 1, это нормально.

В `run.log` первые строки:

```
start regions=2 events=4 epochs=2 end_ms=1000 level=events
region id=0 pages=256 base=0x…      (pages=64 при странице 16384)
region id=1 pages=256 base=0x…
```

`minflt`, `majflt`, `maxrss_kb`, `cpu_s`, `checksum` — свои у каждого прогона, их не сравнивать.

**Проверка пройдена, если:** `exit=0`, `done == planned`, `skipped=0`, `populated` и `planned` совпадают с таблицей для твоего размера страницы, `wall_s` около 1.00.

### 4.4. Проверка ошибок

```sh
./qmem-replay; echo "exit=$?"
```

Должно быть: строка `usage: … <profile> [--stats <file>] [--log <file> [--log-level events|ticks|touches]]`, `exit=2`.

```sh
printf 'qmem-profile 2\nregion id=0 size=65536\nmap t=0 region=0 off=1 len=65536\n' > bad.txt
./qmem-replay bad.txt; echo "exit=$?"
```

Должно быть: `profile:3: range 1+65536 outside region 0`, `exit=2`.

### 4.5. Проверка нагрузки (по желанию)

Поставь в `smoke.txt` у первого `access` `rate=10000000` и запусти снова. Ожидаемо: `done` намного меньше `planned`, `lagged_ticks > 0`, но `wall_s` всё равно около 1.00 — replay выкидывает работу, которую не успевает, и не отстаёт по времени. Если `wall_s` заметно больше 1, запиши это в отчёт.

Для сравнения, x86-64 ноутбук: `planned=256000512 done=12561564 lagged_ticks=100 wall_s=1.00`.

## 5. Опции

```
qmem-replay <profile> [--stats <file>] [--log <file> [--log-level events|ticks|touches]]
```

| Опция | Что даёт |
| --- | --- |
| `--stats F` | CSV по эпохам: `t_ms,planned,done,lag_ms` |
| `--log F` | пошаговый лог, формат — [formats/replay_log.md](formats/replay_log.md) |
| `--log-level` | `events` (по умолчанию) — события и эпохи; `ticks` — плюс тики; `touches` — плюс каждое касание (большой файл) |

Коды выхода: `0` — успех, `1` — ошибка окружения (не открылся файл, не хватило памяти, `mmap` не сработал), `2` — неверные аргументы или ошибка в профиле.

Итоговая строка в stderr:

| Поле | Что значит |
| --- | --- |
| `planned` / `done` | касаний по плану / сделано |
| `skipped` | касание пришлось на неотображённую страницу |
| `populated` | страниц, записанных `populate` |
| `lagged_ticks`, `max_lag_ms` | сколько тиков опоздали, наибольшее опоздание |
| `minflt`, `majflt`, `maxrss_kb`, `cpu_s` | что увидело ядро: page faults, пик RAM, CPU |
| `wall_s` | сколько длился прогон |
| `checksum` | отпечаток последовательности страниц (свой в каждом прогоне) |

## 6. Профиль: минимум, который надо знать

Полный формат — [formats/profile.md](formats/profile.md).

- Первая строка `qmem-profile 2`. Дальше строки `тип ключ=значение …`, `#` — комментарий.
- `region id=N size=БАЙТ [base=0x…]` — регионы по порядку с 0.
- `map` / `unmap` / `populate t=МС region=N off=БАЙТ len=БАЙТ` — `off` и `len` **кратны размеру страницы системы**; время не убывает.
- `epoch t=МС [dur=МС]` — начало эпохи; следующие строки `access` относятся к ней.
- `access region=N off=… len=… rate=R [write=0..1] [pattern=roundrobin|random|poisson]` — `rate` — касаний **одной страницы** в секунду.

**Важно для телефонов со страницей 16 КБ.** Профили, собранные на компьютере со страницей 4 КБ, могут содержать `off`/`len`, не кратные 16384. Такой профиль не загрузится: `profile:N: range … is not page aligned`, код 2. Это не ошибка сборки. Для проверки используй `smoke.txt` из раздела 4; о таких профилях напиши в отчёте.

## 7. Если не собирается или не запускается

| Симптом | Причина | Что делать |
| --- | --- | --- |
| `'MAP_FIXED_NOREPLACE' undeclared` | старые заголовки | в начало `replay/memory.c` после `#include <sys/mman.h>` добавить: `#ifndef MAP_FIXED_NOREPLACE` / `#define MAP_FIXED_NOREPLACE 0x100000` / `#endif` (три строки). Записать в отчёт |
| `Permission denied` при запуске | файл в `/sdcard` или без `chmod +x` | положить в `/data/local/tmp` или в домашнюю папку Termux, `chmod +x` |
| `profile:N: range … is not page aligned` | страница 16 КБ, профиль под 4 КБ | см. раздел 6 |
| `qmem-replay: region N: 0x… is not free, reserved elsewhere` | адрес из `base=` занят или недоступен (у arm64 бывает меньше адресов, чем у x86-64) | это предупреждение, не ошибка; прогон продолжается |
| процесс убит (`Killed`), без итоговой строки | не хватило памяти (Android убивает процессы) | уменьшить `size` регионов в профиле |
| `prepare: Cannot allocate memory` / `mmap: …`, код 1 | мало памяти или адресов | уменьшить `size` |
| `lagged_ticks` растёт, `done < planned` на `smoke.txt` | телефон не успевает 1 мс тики | записать в отчёт числа; это не авария |

Другие ошибки сборки — не исправлять наугад. Записать в отчёт полный вывод компилятора.

## 8. Что прислать в отчёте

1. Устройство и система: модель, версия Android, `uname -a`, `getconf PAGESIZE`.
2. Как собирал: Termux или NDK (версия), точная команда, вывод компилятора (пусто — так и написать).
3. Были ли правки кода — какие (дифф).
4. Вывод раздела 4.2: итоговая строка `replay: …`, `exit`, содержимое `stats.csv`, первые 3 строки `run.log`.
5. Вывод обеих проверок из 4.4.
6. Если делал 4.5 — итоговая строка.
7. Всё, что отличалось от ожидаемого.
