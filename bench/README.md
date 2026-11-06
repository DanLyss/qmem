# bench: 100 профилей для сравнения replay на Linux и на телефоне

Одни и те же 100 профилей прогоняются `qmem-replay` на обеих машинах, итоги потом сравниваются. Результаты Linux лежат не в репозитории, а у того, кто пишет сравнение.

| Файл | Что |
| --- | --- |
| `profiles/NNN_<что>_<параметр>.txt` | 100 профилей (не менять) |
| `run.sh` | прогон всех профилей; POSIX `sh`, без python |
| `results/phone/` | **сюда** положить результаты телефона |

Группы профилей: `size` (1–256 МБ), `rate` (0.01–100000 касаний на страницу в секунду), `ranges` (те же касания на 1–256 участков), `overload` (перегрузка на 1–4096 участков), `pattern` (roundrobin/random/poisson × write), `churn` (map/unmap каждые 10–500 мс), `populate`, `epoch` (эпохи 1–1000 мс), `regions` (2–200 регионов), `memtest` (5 конфигов damoLoad). Почти все идут 2 с, `overload` — 0.5 с.

## Задача для агента: прогнать на телефоне

Делай по шагам. Код replay, профили и `run.sh` не меняй.

### 1. Обнови репозиторий и собери replay для телефона

```sh
git pull
make -f Makefile.phone          # как в прошлый раз → build/qmem-replay
```

### 2. Перенеси на телефон

```sh
tar -czf bench.tgz bench/run.sh bench/profiles
hdc file send bench.tgz /data/local/tmp/bench.tgz
hdc file send build/qmem-replay /data/local/tmp/qmem-replay-ohos
hdc shell "cd /data/local/tmp && rm -rf bench && tar -xzf bench.tgz && chmod +x qmem-replay-ohos"
```

### 3. Прогони

```sh
hdc shell "cd /data/local/tmp && sh bench/run.sh ./qmem-replay-ohos bench/results/phone 2> bench/progress.log"
```

- Идёт 5–15 минут. Прогресс: `hdc shell tail -1 /data/local/tmp/bench/progress.log` (строка `[N/100] имя`).
- **`044_overload_4096` и `041_overload_256` идут долго** (на Linux 17 с и 1 с вместо 0.5 с, на телефоне может быть несколько минут). Это известная ошибка replay, которую мы и меряем. Не прерывай.
- `099_memtest_5500reg_realistic_app` занимает ~450 МБ RAM. Если телефон убьёт процесс — это тоже результат, ничего не перезапускай.
- Если `hdc shell` отваливается на долгом прогоне — запусти в фоне: `hdc shell "cd /data/local/tmp && nohup sh bench/run.sh ./qmem-replay-ohos bench/results/phone > /dev/null 2> bench/progress.log &"` и жди, пока в `progress.log` не появится `done:`.

### 4. Забери результаты

```sh
hdc shell "cd /data/local/tmp/bench && tar -czf results-phone.tgz results/phone"
hdc file recv /data/local/tmp/bench/results-phone.tgz .
tar -xzf results-phone.tgz -C bench/          # → bench/results/phone/
```

### 5. Проверь и запушь

```sh
ls bench/results/phone/*.out | wc -l           # должно быть 100
grep -L 'exit=0' bench/results/phone/*.out      # профили не с exit=0 (лучше пусто)
cat bench/results/phone/env.txt
git add bench/results/phone
git commit -m "bench: results on the phone"
git push
```

Если какие-то профили не с `exit=0` — не перезапускай, просто напиши в сообщении коммита, какие и с каким кодом.

## Что лежит в результатах

Для каждого профиля `<имя>`:

| Файл | Что |
| --- | --- |
| `<имя>.out` | stderr replay: итоговая строка `replay: …` (у `poisson` перед ней предупреждение) и последней строкой `exit=N` |
| `<имя>.csv` | `--stats`: `t_ms,planned,done,lag_ms` по эпохам |
| `<имя>.rss` | `VmRSS` процесса раз в ~100 мс: `<номер отсчёта> <кБ>` |

`env.txt` — `uname -a`, размер страницы, дата.
