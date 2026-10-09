судья: grok-4.7-high
модель: grok-4.7 (effort high)
начало: 2026-09-29T12:12:18Z
конец: 2026-09-29T12:20:59Z
секунд: 521
---
Приёмка порции: сначала читаю чек-лист, файл порции и пакет диффа, затем прогоню каждый пункт по факту.В диффе `listik_release` берёт `as_owner` из аргументов. Сверяю это с кодом и прогоняю проверки.Тесты порции зелёные. Дальше прогоняю отказ чужому и подмену владельца в аргументах `listik_release`.красный

1. `listik/mcp.py:665`: `listik_release` передаёт `as_owner=args.get("owner") or owner`. При `LISTIK_OWNER=bob` и аргументе `{"owner":"ann"}` stdio-вызов вернул `holder=""`, `status=in_progress`, событий 4, `Forbidden` нет. `POST /mcp` `tools/call` `listik_release` с заголовком `bob` и тем же аргументом: http 200, `isError` null, `holder=""`, событие `release` с заметкой `освободил`. Передавать только транспортный `owner`, как у `listik_done` в `listik/mcp.py:662`. В `tests/test_done_release.py:333` аргумент `owner` проверяется только у `listik_done`. Добавь тот же отказ для `listik_release` по `/mcp` и stdio: `isError`/`Forbidden`, держатель и снимок задачи те же.

Чек-лист store закрыт: close/release/no-op от `bob` — `Forbidden`, текст «принадлежит ann», снимок тот же; `carol` — `BadArgument`; `ann` — `close_reason` `R` / `X` / null, заметка release «освободил»; локальный `bob` закрывает (`status=done`) и снимает держателя.
HTTP закрыт: `/done`, `/release`, `/stage` `{"to":"done"}` и тела `owner`/`as_owner` от `bob` — 403 `forbidden`; `s1-spec` от `bob` — 200, этап `s2-review`.
MCP: `listik_done` и `listik_release` без `owner` в аргументах — `isError` true, «принадлежит ann»; `listik_done` с `owner=ann` — тоже. `listik_release` с `owner` в аргументах — пункт 1.
CLI `--local`: `done`/`release`/`stage --to done` от `bob` — код 1, `error.code=forbidden`, в hint есть `owner=`, держатель `agent:dsh`. Через живой сервер: `release` от `bob` — код 1, `forbidden`, stderr пустой, `fallback` false, держатель `agent:dsh`; `release` от `ann` — код 0, держатель пуст.
Инварианты: чужой отказ есть на всех прогнанных путях, кроме `listik_release` с `owner` в аргументах. Локальный режим не отказывает. Переход не в `done` владельца не смотрит.
Код и доки: `close_task`/`release_task` и вызовы с `as_owner` есть; `grep -rn "освободил" listik bin` — только `listik/store.py:953`. `git diff --name-status` — только файлы границ; `tests/test_done_release.py` неотслеживаемый и в границах. Обработчики `stage`, `_guard_op`, `FENCED_TOOLS`, `fence.py`, `web/`, `swarm/`, `stage_launch.py` в диффе нет. `docs/usage.md:50` — `done`, `release`, `stage --to done`.
Проверки: `python3 -m unittest` модулей из чек-листа — Ran 222 tests in 57.956s, OK. Полный discover не запускал: дифф не трогает сборку, зависимости и общий код вне путей порции.
Отложено: нет. Коммита нет.
