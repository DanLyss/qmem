# qmem-replay: что сделано (каркас)

2026-10-13 · устройство: [docs/skeleton.md](../docs/skeleton.md), раздел 5 · формат profile: [docs/formats/profile.md](../docs/formats/profile.md)

`qmem-replay` исполняет profile без приложения: выделяет и освобождает регионы по времени, заполняет резидентную память, касается страниц с заданной частотой тиками по 1 мс с абсолютными дедлайнами. Работа, которая не влезла в тик, отбрасывается, поэтому replay не уплывает по времени. Код итерации 1 разложен по частям каркаса.

## Что лежит в репозитории

| Файл | Часть | Что делает |
| --- | --- | --- |
| `profile.h` | — | Структуры profile и интерфейсы между частями |
| `loader.c` | loader | Разбор profile v2 и v1; ошибки — код 2 и `profile:<строка>:` |
| `memory.c` | memory manager | Резерв регионов, map/unmap/populate, битовые карты, касание страницы |
| `generators.c`, `gen_roundrobin.c`, `gen_random.c` | generators | Какая страница участка следующая |
| `exec.c` | executors | Касания одного тика; один поток |
| `replay.c` | scheduler | Тики, события, смена эпох, статистика, `--stats`, `--layout`, сводка |

Сборка: `make`.

## Генераторы

```c
struct generator_ops {
	const char *name;                                   /* значение pattern= */
	const char *stub;                                   /* предупреждение, если заглушка */
	void (*init)(struct access *a, uint64_t seed);      /* при загрузке */
	void (*epoch)(struct access *a, const struct access *prev);  /* при смене эпохи */
	uint64_t (*next)(struct access *a);                 /* номер следующей страницы участка */
};
```

| `pattern=` | Сейчас |
| --- | --- |
| `roundrobin` (по умолчанию) | Обход по кругу с продолжением между эпохами |
| `random` | **заглушка**: равновероятная случайная страница |
| `poisson` | **заглушка**: как `random`, одно предупреждение |

Новый генератор — файл `gen_<имя>.c` и строка в таблице `generators.c`.

## Заглушки и совместимость

| Что | Поведение |
| --- | --- |
| Неизвестный ключ | Пропускается |
| Неизвестный тип строки | Пропускается, одно предупреждение на тип |
| `!ключ`, которого replay не знает | Ошибка, код 2 |
| `threads=N`, N > 1 | Один поток, одно предупреждение |
| `kind=file` | Ошибка: `file regions are not supported yet` |
| `counter` | Принимается, не используется |
| profile v1 | Читается; `rate` в нём — на весь участок |

## Как пользоваться

```sh
replay/qmem-replay profile.txt [--seed N] [--stats stats.csv] [--layout layout.txt]
```

Root не нужен. В конце в stderr печатается сводка: эпохи, план и сделанные касания, пропущенные, заполненные страницы, отставание, page faults, пиковый RSS, CPU, контрольная сумма.

## Тесты

| Файл | Что проверяет |
| --- | --- |
| `tests/test_replay.py` | profile v2: память, `rate` на страницу, `dur` эпохи, seed, unmap и частичный map, ошибки, тайминг и `--stats`, перегрузка, доля записи (softdirty), populate, `--layout`, отдельные VMA |
| `tests/test_replay_v2.py` | Правила совместимости и заглушки |
| `tests/test_replay_v1.py` | Чтение profile v1 |
| `tests/test_skeleton_e2e.py` | record → build → replay целиком (с настоящим DAMON — под root) |
