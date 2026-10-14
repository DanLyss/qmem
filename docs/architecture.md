# Архитектура

Датафлоу через record, builder и replay и устройство каждого модуля. Прямоугольник — код, параллелограмм — файл на диске, цилиндр — данные в памяти.

![Архитектура Qmem](architecture.png)

Исходник: [architecture.mmd](architecture.mmd). Пересобрать картинку: `make diagrams` (нужен [mermaid-cli](https://github.com/mermaid-js/mermaid-cli)).

## Как добавить новый источник X

Та же схема с наложенным источником X. Зелёное — новое для X, оранжевое — существующие части, которые меняются, жёлтое пунктиром — только если X нужен новый ключ в profile (вариант B). Остальное не меняется.

![Добавление источника X](architecture_extension.png)

Исходник: [architecture_extension.mmd](architecture_extension.mmd).
