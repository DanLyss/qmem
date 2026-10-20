# Формат лога replay

`qmem-replay profile.txt --log run.log [--log-level events|ticks|touches]` пишет, что replay делал по шагам. Лог нужен тестам алгоритма и сравнению с другими нагрузчиками. Без `--log` код лога не выполняется.

Записи копятся в памяти и пишутся в файл после прогона, чтобы запись не мешала таймингу.

## Строки

Одна запись на строку: `тип ключ=значение ...`, как в profile. Порядок строк — порядок, в котором всё происходило.

Время:
- `t` — миллисекунды profile, начиная с 0;
- `tick` — номер тика (тик — 1 мс), в котором что-то сделано;
- `t_ns` — наносекунды от старта replay (`CLOCK_MONOTONIC`).

| Тип | Уровень | Когда | Ключи |
| --- | --- | --- | --- |
| `start` | все | Перед первым тиком | `seed`, `regions`, `events`, `epochs`, `end_ms`, `level` |
| `region` | все | После резерва адресов | `id`, `pages`, `base` (адрес в этом запуске, hex) |
| `event` | все | Событие применено | `kind=map\|unmap\|populate`, `t` (из profile), `tick`, `region`, `off`, `len`; у `populate` ещё `pages` — сколько страниц записано |
| `epoch` | все | Эпоха началась (`what=start`) или закончилась (`what=end`) | `idx`, `t`, `dur`, `tick`; у `end` ещё `planned`, `done` |
| `drop` | все | Тик не уложился в дедлайн, работа брошена | `tick`, `skipped` — сколько следующих тиков пропущено |
| `tick` | `ticks`, `touches` | Тик с касаниями одного `access` | `tick`, `epoch`, `access` (номер в эпохе), `n` (положено в этом тике), `done` (сделано), `carry` (дробный остаток) |
| `touch` | `touches` | Каждое касание `access` | `t_ns`, `region`, `page` (номер страницы в регионе), `op=w\|r\|skip` (`skip` — страница не выделена) |
| `end` | все | После прогона | `planned`, `done`, `skipped`, `populated`, `lagged_ticks` |

Запись страниц при `populate` в строки `touch` не попадает: она отражена в `event kind=populate pages=…`.

## Пример (уровень `ticks`)

```
start seed=42 regions=1 events=2 epochs=1 end_ms=200 level=ticks
region id=0 pages=1024 base=0x7f3a2c000000
event kind=map t=0 tick=0 region=0 off=0 len=4194304
event kind=populate t=0 tick=0 region=0 off=0 len=4194304 pages=1024
epoch what=start idx=0 t=0 dur=100 tick=0
tick tick=0 epoch=0 access=0 n=10 done=10 carry=0.24
...
epoch what=end idx=0 t=0 dur=100 tick=100 planned=1024 done=1024
end planned=1024 done=1024 skipped=0 populated=1024 lagged_ticks=0
```
