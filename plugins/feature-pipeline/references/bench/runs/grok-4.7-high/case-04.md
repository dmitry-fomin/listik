судья: grok-4.7-high
модель: grok-4.7 (effort high)
начало: 2026-09-29T12:12:18Z
конец: 2026-09-29T12:19:38Z
секунд: 440
---
Читаю порцию, чек-лист и пакет диффа, затем прогоню каждый пункт по факту.Дифф уже показывает два места, которые надо проверить вживую: онлайн-досыпку в alembic и правку `local_call` вне ветки `meta`. Читаю код и прогоняю проверки.красный

1. ГРАНИЦЫ: `listik/client.py:257` (hunk `+255,3`) — в `local_call` ветка `update` вызывает `store.check_update_fields(kwargs, service=store.UPDATE_SERVICE_KEYS | {"as_owner"})`. В этом файле порция разрешает только ветку `meta`. Убрать эти три строки из `update` либо расширить границу: иначе ключ `transition` из kwargs попадает в служебный параметр `update_task`.
2. `alembic/versions/0012_event_transition.py:41` — цикл `BACKFILL_TRANSITION_SQL` стоит под `if op.get_context().as_sql`. Онлайн `alembic upgrade head` после `0011_task_orchestrator` колонку добавляет и оставляет старые события `NULL`: `1|NULL` при заметке `этап -> s2-review (handoff)`, `5|NULL` при `этап -> s4-judge (sticky)`. После `ALTER` выполнять те же три UPDATE и в онлайн-режиме; ранний return, если колонка уже есть, оставить.

Пункты чек-листа:
1–3, 5–11, 13–16 — зелёные (`Ran 501 tests in 87.357s`, `OK`). Проба: `HANDOFF holder='' to='s2-review' transition='handoff'`; `STICKY holder='agent:x' transition='sticky'`; `POST_BODY holder='' to='s2-review' transition='handoff'`.
4 — пустая база и `--sql` зелёные: `10|transition|TEXT|0||0`; `ALTER TABLE events ADD COLUMN transition TEXT` и три `UPDATE` с `to_value <> 'done'`; `SQL_EQUAL True`. Онлайн-досыпка — дефект 2.
12 — зелёный: `PATCH_REFUSAL status=400 code=bad_argument`; `before=('s1-spec', 'agent:x') n=4 after=('s1-spec', 'agent:x') n=4`.
17 — зелёный: `docs/API.md:112`, `docs/API.md:119`, `docs/API.md:1404`, `docs/API.md:1574`.
18 — зелёный: diff `server.py` — только ключ `routing` в `/api/meta`; `listik/config.py`, `UPDATABLE`, обработчик `POST /stage` и `task_timeline` в diff отсутствуют; строка `этап -> {nxt} ({transition})` сохранена.
19 — зелёный: в семи тестовых файлах diff только `"13"` → `"14"`.
20 — зелёный: `git diff --name-only` — 12 путей порции; `??` только `alembic/versions/0012_event_transition.py` и `tests/test_stage_transition_event.py`.

Проверки:
- `which alembic` → `/opt/homebrew/bin/alembic`.
- Узкий unittest чек-листа: `Ran 501 tests in 87.357s`, `OK`.
- `alembic upgrade head` на пустой базе и `--sql` `0011:0012` — см. пункт 4.
- Онлайн-досыпка: `SELECT id, quote(transition)` → `1|NULL` … `5|NULL`.
- Полный `unittest discover` не запускал: чек-лист запрещает, дифф не трогает сборку и зависимости.

Отложено: нет. Коммита нет.
