# Архитектура

Датафлоу через record, builder и replay и устройство каждого модуля. Прямоугольник — код, параллелограмм — файл на диске, цилиндр — данные в памяти.

![Архитектура Qmem](architecture.png)

Исходник: [architecture.mmd](architecture.mmd). Пересобрать картинку: `make diagrams` (нужен [mermaid-cli](https://github.com/mermaid-js/mermaid-cli)).

## Как добавить новый источник X

Что нужно добавить в каждом модуле, чтобы новый источник записи прошёл до replay. Зелёное — добавить для X, серое — уже есть и не меняется, жёлтое пунктиром — только если X требует нового поля в profile (вариант B).

![Добавление источника X](architecture_extension.png)

Исходник: [architecture_extension.mmd](architecture_extension.mmd).
