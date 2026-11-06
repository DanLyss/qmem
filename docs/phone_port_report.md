# Отчёт: сборка replay под телефон

2026-11-06 · гайд: [replay_guide.md](replay_guide.md)

## Что сделано

1. `Makefile.phone` (корень репо) — сборка OHOS-тулчейном с компа:

   ```sh
   make -f Makefile.phone        # build/qmem-replay
   make -f Makefile.phone clean
   ```

2. Сборка без предупреждений: `aarch64-unknown-linux-ohos-clang` (OHOS clang 15), `--sysroot=/opt/native/sysroot`, динамическая линковка musl, 37 КБ, ELF aarch64.

3. Загрузка и запуск на телефоне через `hdc file send` → `/data/local/tmp/qmem-replay-ohos`.

## Устройство

- HarmonyOS, HongMeng Kernel 1.12.0, aarch64, страница 4096 (`getconf PAGESIZE`), shell root.
- Сборка проходила на компе (x86-64, Ubuntu 24.04, gcc 13.3 — не используется для phone-цели).

## Результаты на телефоне

### smoke.txt (гайд, раздел 4)

```
replay: epochs=2 planned=768 done=768 skipped=0 populated=64 lagged_ticks=0 max_lag_ms=0.8 minflt=932 majflt=233 maxrss_kb=6960 cpu_s=0.06 wall_s=1.00 checksum=7f75043b8adb354b
exit=0
stats.csv: 0,512,512,1 | 500,256,256,0
run.log: start regions=2 events=4 epochs=2 end_ms=1000 level=events
region id=0 pages=256 base=0x5ae4607000
region id=1 pages=256 base=0x5ae4708000
```

Всё совпадает с таблицей гайда для страницы 4096: `planned=done=768`, `skipped=0`, `populated=64`, `wall_s≈1.00`.

### Проверки ошибок (гайд, раздел 4.4)

- без аргументов: `usage: …`, `exit=2`;
- bad.txt (`off=1` вне региона): `profile:3: range 1+65536 outside region 0`, `exit=2`.

Также прогнан статический musl-вариант (musl.cc, `-static`, 200 КБ) — идентичные ожидаемые числа (`planned=768 done=768 skipped=0 populated=64 checksum=ded1526df64da2c4`).

## Сравнение Linux ↔ телефон

Полное сравнение на 100 профилях — [phone_vs_linux.md](phone_vs_linux.md). Для smoke-проверки:

- **Сравнивать:** `epochs`, `planned`, `done`, `skipped`, `populated`, `wall_s`, поэпохные `planned`/`done` из `stats.csv` — они определяются профилем и размером страницы, не ядром.
- **Не сравнивать:** `checksum` (генераторы без seed — свой в каждом прогоне), `minflt`/`majflt`/`maxrss_kb`/`cpu_s` (свои у каждого ядра).
