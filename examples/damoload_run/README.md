# Один прогон сравнения replay с damoLoad

```sh
python3 tools/damoload_check.py third_party/damoLoad/memtest/configs/08_hotspot_two_pages.json \
    --duration 3 --keep examples/damoload_run --json examples/damoload_run/report.json
```

Конфиг: 1 регион из 10 страниц, 150 обращений в секунду, 90% — на страницы 0 и 1. Первые 3 секунды.

| Файл | Откуда | Что в нём |
| --- | --- | --- |
| `config.json` | вход | Конфиг memtest, обрезанный до 3 с |
| `gt.log` | memtest | Каждое обращение memtest: `время_нс регион страница` (это эталон) |
| `profile.txt` | memtest2profile | Тот же конфиг как profile: страницы 0–1 по 67.5 касания/с на страницу, страницы 2–9 по 1.875 |
| `replay.log` | qmem-replay `--log-level touches` | Шаги replay: `event`, `epoch`, `tick`, и каждое касание `touch` |
| `comparison.txt` | damoload_check | Что сравнивалось: касания по страницам и по окнам 100 мс, memtest и replay рядом |
| `report.json` | damoload_check | Итог сравнения с порогами |
| `summary.txt` | damoload_check | Одна строка итога |
