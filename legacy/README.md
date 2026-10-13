# legacy

Инструменты, которые сейчас не входят в работу (см. [docs/skeleton.md](../docs/skeleton.md)): они читают **формат записи v1** (плоский каталог с `meta.json`) и **profile v1**.

| Что | Было |
| --- | --- |
| `verify/qmem-verify.py`, `verify/gt.py`, `verify/cycle.sh` | сравнение оригинала и replay, ground truth, цикл одной командой |
| `tools/mkprofile.py` | сборка profile v1 (заменена `builder/qmem-build.py`) |
| `tools/intensity_eval.py`, `tools/summarize-record.py` | оценка интенсивности, сводка записи |

Тесты — `tests/legacy/`, запускаются отдельно: `make test-legacy`. Те, что сами вызывают `qmem-record`, ждут записи v1 и с нынешним record не работают; тесты на синтетических записях v1 работают.
