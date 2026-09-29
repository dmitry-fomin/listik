Рабочее дерево: {{TREE}}

# listik-jaid — /done и /release: проверка владельца и note одинаково в HTTP, MCP и local_call

Карточка: listik-jaid (bug, P1), эпик-ревью listik-hil0.

## Цель

1. Закрытие (`done`) и освобождение (`release`) задачи идут через одну функцию store на каждое
   действие — из HTTP, MCP и локального фолбэка CLI. В серверном режиме чужая задача
   (представился не её владелец) не закрывается и не освобождается ни одним путём: HTTP 403
   `forbidden`, MCP `isError`, CLI — ошибка с кодом `forbidden`.
2. `listik done --note X` без сервера сохраняет заметку; `harness` у `done`/`release` доходит до
   событий по всем путям.
3. Закрытие через `stage` (переход в `done`) проверяет владельца так же, как `/done`.
4. Имя операции в карантине ограждения (`note.op` события `rejected`) одно и то же для одной
   операции во всех транспортах и берётся из одного словаря в `listik/fence.py`.

## Решения, принятые при написании ТЗ

- **Семантика проверки — как у `PATCH`** (`store.update_task` с `as_owner`): чужая задача при
  представившемся — 403; имя не из `server.users` — 400 `bad_argument`; **без представления —
  проходит** (как `PATCH`/`set`); задача без владельца — любой; локальный режим — без проверок.
- **`stage --to done` тоже проверяет владельца** при переходе в `done` (явный `to=done` или
  неявный после `s4-judge`): docs/API.md сам описывает этот переход как «закрывает задачу так же,
  как `/done`», и без проверки 403 у `/done` обходился бы одной командой. Прочие переходы этапа
  без `holder` владельца по-прежнему не смотрят (решение listik-xt69 для них не меняется).

## Порции (по порядку)

| Порция | Суть | Файлы |
| --- | --- | --- |
| `a` | `store.close_task`/`store.release_task` с `as_owner`; server, MCP и новые op `done`/`release` в `client.local_call` зовут их; CLI без эмуляции через `update`; закрытие через `next_stage` с проверкой владельца; docs/API.md и docs/usage.md; тесты всех трёх путей. | `listik-jaid.a.md`, `listik-jaid.check-a.md` |
| `b` | Имена операций ограждения — один словарь `fence.OPS`; MCP пишет в карантин те же имена, что HTTP и local (`needs-owner`, `dep_add`/`dep_remove`, `document`); абзац в docs/API.md; тесты. | `listik-jaid.b.md`, `listik-jaid.check-b.md` |

Порция `b` опирается на закоммиченную `a` (локальные op `done`/`release` уже есть).

## Вне шага

- Доска (`web/`), мок `web/scripts/mock-api.mjs`, `verify-owner.mjs`: доска и так шлёт
  `X-Listik-Owner` и показывает ошибку запроса.
- Схемы и описания MCP-инструментов, новые поля в них.
- Внутренние закрытия без представления (`stage_launch`, автозакрытие эпика в `store`) — не
  через `close_task`.
- Смежные карточки эпика listik-hil0: удаление `OWNER_OPS` (listik-pf6i), `harness` в `new`/`set`
  (listik-3urk), автор `listik_comment` (listik-ou3i), ветка `ListikError` в `error_response`
  (listik-l56q), одна ветка `listik_stage` (listik-p2ot), строка docs/API.md про ограждение
  `listik_deps` «только ветка добавления» и autostart (listik-9s7e).
- Миграции схемы, alembic.
