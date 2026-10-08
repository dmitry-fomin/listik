судья: sonnet-5.5-high
модель: sonnet (feature-pipeline:pipeline-judge, model: sonnet в вызове, effort high из frontmatter)
начало: 2026-09-29T12:12:48Z
конец: 2026-09-29T12:15:06Z
секунд: 106
---
красный

1. listik/mcp.py:665 — `listik_release` берёт идентичность из аргументов инструмента: `as_owner=args.get("owner") or owner`. Нарушены критичный инвариант «Идентичность не берётся из тела или аргументов» и требование 4 порции (`as_owner=owner`). Сценарий: stdio, `LISTIK_OWNER=bob`, `mcp.call_tool("listik_release", {"id": <задача ann с держателем agent:dsh>, "owner": "ann"})` — отказа нет, держатель снят (`holder: ''`), состояние задачи изменилось. Для `listik_done` с тем же `owner` отказ есть (mcp.py:664 передаёт `as_owner=owner`). Тот же обход работает по `POST /mcp` от `bob`.
2. tests/test_done_release.py:805-808 — тест `test_foreign_done_and_release` проверяет `owner` в аргументах только у `listik_done`; для `listik_release` такого случая нет, поэтому дефект пункта 1 тестами не пойман. Добавить случай `listik_release` от `bob` с `{"owner": "ann"}` (`/mcp` и stdio).

Проверки:
- Минимум из «Проверок порции» (11 модулей): Ran 222 tests, OK.
- Негативный контроль: временный тест в дереве (удалён, в git status его нет) — вывод «RELEASE BY BOB WITH owner=ann ARG -> holder: ''», «state changed: True», «done refused».
- Полный discover не запускал: дифф не задевает общий код вне done/release, а по правилам проекта полный набор не гоняется.
- Остальные пункты чек-листа (store, HTTP, CLI, ограждение, границы правки, docs, grep «освободил» — только listik/store.py:953) — по коду и тестам закрыты, кроме пункта «идентичность не из аргументов» для release.
Перенесено: нет.
Карточка: карточки нет. Коммита нет.
