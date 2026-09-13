# Приёмка порции 02.a. `needs-owner` пишет вопрос и ответ в историю

Все сценарии — на временной базе (`LISTIK_DB=/tmp/…`, `./bin/listik --local init`), тесты —
`python3 -m unittest`. Пункты с пометкой «красный до правки» судья при необходимости проверяет
на `git stash`-нутом состоянии только читая тест: тест должен обращаться к
`store.set_needs_owner` (которой до порции нет) или проверять второй вопрос/чистый JSON.

## Тесты

1. Файл `tests/test_needs_owner.py` существует, наследует `TempDbTestCase`, и
   `python3 -m unittest tests.test_needs_owner -v` зелёный; в нём есть тесты на: первый вопрос,
   повторный вопрос при поднятом флаге, ответ (`--clear` с текстом), вызов без текста (нет
   комментария), поиск по слову из вопроса, равенство набора ключей ответа и `get_task`,
   CLI `--local --json` (чистый JSON и два `question`), неизвестная задача. Отсутствие любого
   из восьми — красный пункт.
2. `python3 -m unittest discover tests` зелёный целиком (в т. ч. `test_context.py` — форма и
   стабильность `context` не менялись).

## store

3. `store.set_needs_owner(conn, id, value=True, text="Q1")` на свежей задаче: в ответе
   `needs_owner` истинен, `comments == [ {kind: "question", text: "Q1", …} ]`, среди `events`
   ровно одно с `kind == "question"` и `note == "Q1"`. Ключа `unchanged` в ответе нет.
4. Повтор `set_needs_owner(value=True, text="Q2")` при поднятом флаге: два комментария
   `question` (`Q1`, `Q2`), два события `question`, ключа `unchanged` нет. **Красный до правки**
   (сегодня возвращается `unchanged` и не пишется ничего).
5. `set_needs_owner(value=False, text="Ответ")`: `needs_owner` ложен, добавлен ровно один
   комментарий `kind == "answer"`, одно событие `answer` с `note == "Ответ"`.
6. `set_needs_owner(value=True)` без текста: флаг поднят, событие `question` есть, число
   комментариев не изменилось (пустой комментарий не создан). То же для `value=False`.
7. `search.search(conn, "<уникальное слово из вопроса>", mode="text")` находит задачу —
   комментарий-вопрос проиндексирован FTS. **Красный до правки.**
8. `sorted(set_needs_owner(...).keys()) == sorted(store.get_task(conn, id).keys())` — форма
   ответа `show` не изменилась.
9. Неизвестный `task_id` → `KeyError`.

## CLI (оба пути)

10. `./bin/listik --local needs-owner <id> "Q" --json | python3 -c 'import json,sys;json.load(sys.stdin)'`
    проходит без ошибки: после JSON нет строки `вопрос:`. **Красный до правки** (сейчас строка
    печатается после JSON и ломает парсинг). В `tests/test_needs_owner.py` CLI-тест запускает
    `bin/listik` по контракту ТЗ: абсолютный путь от `__file__`, `sys.executable`, флаг
    `--local`, `env` с `LISTIK_DB` временной базы, `capture_output=True`; результат в базе
    проверяется через `store.get_task(self.conn, …)`, из stdout читается только JSON. Тест,
    ходящий в CLI без `--local` или без `LISTIK_DB`, — красный пункт (рискует рабочей базой).
11. Без `--json`: `needs-owner <id> "Q"` печатает `флаг «нужен человек» поднят: <id>` и
    `  вопрос: Q`; `needs-owner <id> --clear "A"` печатает `… снят: <id>` и `  ответ: A`.
12. `./bin/listik --local show <id>` после двух вопросов и ответа показывает в «истории» три
    строки с `(question)`, `(question)`, `(answer)` и текстами; `show --json` содержит их в
    `comments` в порядке создания.
13. HTTP-путь: при поднятом на временной базе сервере `POST /api/tasks/{id}/needs-owner` с телом
    `{"value": true, "note": "Q-http", "actor": "agent:test"}` возвращает `{"ok": true, "data": {…}}`,
    где `data.comments` содержит `question` с текстом `Q-http`; повтор с `Q-http-2` — два
    `question`. Тело запроса и путь не изменились (`git diff web/` пуст, `web/src/api/client.ts`
    шлёт `{value, note, actor}` как раньше).
14. MCP: stdio-обмен `initialize` → `tools/call` `listik_needs_owner` с `{"id": …, "text": "Q-mcp"}`
    возвращает карточку с комментарием `question` `Q-mcp`; `tools/list` показывает у
    `listik_needs_owner` прежнюю схему (`id`, `text`, `value`, `actor`) и описание, где
    упомянуты комментарий `question`/`answer`.

## Не сломано

15. `./bin/listik --local set <id> needs_owner=1` (низкоуровневый `PATCH`) по-прежнему только
    меняет флаг и пишет событие `question`, комментария не создаёт (проверяется чтением
    `update_task` — ветка не менялась — и живым вызовом: число `comments` не выросло).
16. `git diff --stat HEAD` содержит только `listik/store.py`, `listik/client.py`, `bin/listik`,
    `listik/server.py`, `listik/mcp.py`, `tests/test_needs_owner.py`. `documents.py`, `db.py`,
    `alembic/`, `web/`, `API.md`, `README.md`, `AGENTS.md`, `docs/harness-protocol.md`,
    `migrate.py`, существующие тесты — без изменений.
17. В диффе `listik/store.py` тела `add_comment` и `update_task` не изменены ни одной строкой
    (проверяется чтением диффа); флаг в `set_needs_owner` ставится прямым `UPDATE`, после
    которого вызваны `deps_mod.refresh_task` и `_index_task`. Допустимы только новая функция и
    вызовы её из трёх транспортов.
18. В тестах и диффе нет токенов и содержимого `config.toml`.
