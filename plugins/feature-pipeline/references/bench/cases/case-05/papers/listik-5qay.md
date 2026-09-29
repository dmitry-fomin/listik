Рабочее дерево: {{TREE}}

# listik-5qay — memory, dep suggested, MCP listik_list: итоги по total и одинаковый limit=0 на всех путях

## Цель

Хвосты listik-n1aw (там это сделано для `list`/`ready`/`blocked`):

1. `listik memory` (список без запроса): итог «всего: N» — это размер страницы, а не число
   заметок; `-n 0` через `--local` печатает «памяти пока нет», хотя сервер на 0 отдаёт 20.
2. `listik dep suggested`: «всего предложений: N» — размер страницы; сервер на `limit=0`
   отдаёт 100, локальный путь — все предложения без ограничения.
3. MCP `listik_list`: `limit: 0` даёт пустую страницу, а сервер `GET /api/tasks` на 0 отдаёт 200.

Правило, взятое из listik-n1aw: `limit=0` на любом пути (HTTP, `--local`, MCP) значит
«страница по умолчанию, как у сервера», а текстовый итог считает полное число.

## Порции

- `a` — всё сразу: memory (CLI, local, MCP), dep suggested (`total` в ответе, CLI, local, MCP),
  MCP `listik_list` с `limit: 0`; тесты, API.md.

## Вне шага

- Смена формы ответа `GET /api/memory` (голый список) — не трогаем.
- Отрицательные `limit`, `offset` у memory/dep suggested — не вводим.
- Доска (`web/`) — не трогаем.
