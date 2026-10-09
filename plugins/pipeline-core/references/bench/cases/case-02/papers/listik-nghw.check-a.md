Рабочее дерево: {{TREE}}

# listik-nghw.check-a — приёмка порции a

## Пункты

0. **Предпосылки подтверждены.** В отчёте исполнителя есть вывод команд из раздела
   «Предпосылки» ТЗ:
   - `OPEN_STATUSES` в `store.py` и `deps.py` — один и тот же кортеж из четырёх статусов;
   - в `row_to_task` есть `"blocked_by": blockers`;
   - внутри `board` строки `server_cfg()`, `check_owner`, `(owner = ? OR owner IS NULL)`
     стоят **выше** строки `tasks = [row_to_task…`;
   - `server.py` передаёт в `board` `as_owner=owner`;
   - все три тестовых символа на месте;
   - `node` печатал `12`.

   Судья может повторить команды сам: они читают неизменённый код.
1. **Основной сценарий доски.** Есть тест `BoardBlockedCountTests` (файл
   `tests/test_board_blocked_count.py`): A1 — открытый блокер `blocks`, A2 — закрытый блокер
   того же проекта, A3 — закрытый блокер из `other`, A4 — только `resource-blocks`,
   A5 — открытый блокер из `other`, A6 — только `suggested-blocks`. Отдельными
   утверждениями проверяется:
   - `store.board(conn, project="demo")["blocked_count"] == 3`;
   - `len(deps.blocked_tasks(conn, project="demo")) == 3`;
   - множество id из `blocked_tasks` равно `{A1, A4, A5}`.

   Тест проходит.
2. **Сценарий `include_closed`.** Есть тест: `X` ждёт открытую `B`, потом `X` отменена.
   - Предусловие `get_task(X)["blocked_by"] == [B]` тест проверяет.
   - Отдельными утверждениями: `store.board(conn, project="demo",
     include_closed=True)["blocked_count"] == 0` и
     `len(deps.blocked_tasks(conn, project="demo")) == 0`.

   Тест проходит.
3. **Серверный режим.** Есть тест `BoardBlockedCountOwnerTests(OwnerStoreCase)` с
   `server_mode()`. `B` и `Aa` принадлежат `ann`, `Ab` — `bob`, обе ждут `B`. Проверяется:
   `blocked_count` при `as_owner="ann"` — `1`, при `as_owner="bob"` — `1`, а
   `len(blocked_tasks) == 2`. Тест проходит.
4. **Словарь доски.** Есть тест `LinkTypesDictionaryTests`: читает `LINK_TYPES` из
   `web/src/lib/dictionaries.ts` и проверяет, что `{value: label}` == `deps.DEP_TITLES`,
   что значения `value` не повторяются и что каждая `icon` есть в `board_icon_names()`.
   Тест проходит.
5. **Тесты действительно ловят старую ошибку.** В отчёте исполнителя есть дословный вывод
   прогона `tests.test_board_blocked_count` до правки кода:
   - основной сценарий — `AssertionError: 4 != 3`;
   - `include_closed` — `AssertionError: 1 != 0`;
   - тест словаря падает на отсутствующих `conditional-blocks`/`resource-blocks`;
   - серверный тест — ok.

   Судья воспроизводит это сам на снимке кода до порции, без `git stash` (стек общий для
   всех деревьев):
   ```sh
   cd {{TREE}}
   BASE_TREE=$(mktemp -d) && git archive "$(git merge-base HEAD main)" | tar -x -C "$BASE_TREE" \
     && cp tests/test_board_blocked_count.py "$BASE_TREE/tests/" \
     && (cd "$BASE_TREE" && python3 -m unittest tests.test_board_blocked_count); echo "$BASE_TREE"
   ```
   Ожидание: ровно три `FAIL` с числами выше, серверный тест ok. Временный каталог
   удалять не обязательно.
6. **Правка словаря.** В `dictionaries.ts` добавлены ровно две строки и ни одной не удалено
   (`git diff` файла):
   - `conditional-blocks` / `блокирует условно` / `lock` — сразу после `waits-for`;
   - `resource-blocks` / `ресурсный блокер` / `lock` — последней.

   Команда
   `node --input-type=module -e "const m = await import('./web/src/lib/dictionaries.ts'); console.log(m.LINK_TYPES.length)"`
   печатает `14`: файл разбирается целиком, синтаксис не сломан.
7. **Дифф `board`.** В `git diff listik/store.py` удалены ровно строки старого подсчёта:
   - `blocked_count = 0` перед `if ready_limit:`;
   - `try:` / `statuses = {…}` / `blocked_ids = set()` / цикл по запросу
     `SELECT issue_id, depends_on FROM deps WHERE dep_type IN (…)`;
   - `blocked_count = len(blocked_ids & set(statuses))`;
   - `except Exception: … blocked_count = 0`.

   Добавлены одно присваивание `blocked_count = sum(…)` (условия `t["blocked_by"]` и
   `t["status"] in OPEN_STATUSES`) и комментарий над ним. Присваивание стоит там же, где был
   старый блок, то есть ниже `tasks = [row_to_task…]` и фильтра владельца. Других изменений
   в `store.py` нет.
8. **Нет своего запроса и страховочного `try`.** Результаты команд над телом `board`
   (`sed -n '/^def board(/,/^def stats(/p' listik/store.py`):
   - `| grep -n "blocked_ids\|statuses = \|FROM deps"` — пусто;
   - `| grep -c "try:"` — `2` (было `3`: остались `lint` и `ready_list`);
   - `| grep -c "blocked_count ="` — `1` (было `3`).

   По диффу видно, что новое выражение не обёрнуто в `try/except`.
9. **Остальная доска не изменилась.** Блок `ready_list` с его `try/except` на месте. Ключи
   ответа `board` прежние: `group_by, columns, total, needs_you, ready, blocked_count,
   cycles, lint, generated_at` (по диффу). Тесты `tests.test_board_lint`,
   `tests.test_board_stage_columns`, `tests.test_assigned_not_taken`,
   `tests.test_owner_store` проходят без правок.
10. **`docs/API.md`.** В строке `GET /api/board` появились `blocked_count` в перечне полей
    и одно предложение с определением: незакрытые карточки с непустым `blocked_by`, правило
    как у `/api/blocked`, в серверном режиме — после фильтра `X-Listik-Owner`. Другие
    строки файла не менялись (`git diff docs/API.md`).
11. **Состав правки.** `git status --short` показывает ровно четыре строки:
    ` M docs/API.md`, ` M listik/store.py`, ` M web/src/lib/dictionaries.ts`,
    `?? tests/test_board_blocked_count.py`. `listik/deps.py` не тронут, существующие тесты
    не изменены.

## Проверки порции

```sh
cd {{TREE}}
python3 -m unittest tests.test_board_blocked_count tests.test_board_lint tests.test_board_stage_columns tests.test_assigned_not_taken tests.test_routes_config tests.test_owner_store
sed -n '/^def board(/,/^def stats(/p' listik/store.py | grep -n "blocked_ids\|statuses = \|FROM deps"
sed -n '/^def board(/,/^def stats(/p' listik/store.py | grep -c "try:"
sed -n '/^def board(/,/^def stats(/p' listik/store.py | grep -c "blocked_count ="
node --input-type=module -e "const m = await import('./web/src/lib/dictionaries.ts'); console.log(m.LINK_TYPES.length)"
git status --short
```

Ожидаемые результаты — в пунктах 6, 8 и 11. Линтера для Python в проекте нет.
`cd web && npm run typecheck` — только если в дереве есть `web/node_modules` (сейчас их
нет, ставить не нужно).
