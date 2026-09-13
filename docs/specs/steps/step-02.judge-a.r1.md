# Приёмка порции 02.a, заход r1 — вердикт: зелёный

Чек-лист: `docs/specs/steps/step-02.check-a.md`. Дамп: `.git/feature-pipeline/step-02.diff-a.r1.txt`.
Все проверки прогнаны судьёй заново, на временных базах в скретчпаде.

## Тесты

1. **Зелёный.** `tests/test_needs_owner.py` наследует `TempDbTestCase`,
   `python3 -m unittest tests.test_needs_owner -v` — 9 тестов, OK. Все восемь требуемых сценариев
   на месте: первый вопрос (`test_first_question_writes_comment_and_event`), повторный вопрос
   (`test_second_question_while_flag_already_set_is_not_lost`), ответ
   (`test_answer_clears_flag_and_writes_answer_comment`), вызов без текста
   (`test_without_text_no_comment_but_event_written`), поиск
   (`test_question_text_is_findable_by_search`), равенство ключей с `get_task`
   (`test_response_shape_matches_get_task`), CLI `--local --json`
   (`test_cli_local_two_questions_json_output_is_clean`), неизвестная задача — два теста
   (store `KeyError` + CLI с ненулевым кодом). Тесты обращаются к `store.set_needs_owner`,
   которой до порции не было, и проверяют второй вопрос и чистый JSON — красные до правки.
2. **Зелёный.** `python3 -m unittest discover tests` — 73 теста, OK (в т. ч. `test_context.py`).

## store

3. **Зелёный.** Проверено тестом и живым вызовом: комментарий `question` один, событие `question`
   одно с `note == "Q1"`, `unchanged` в ответе нет (`listik/store.py:318-350`).
4. **Зелёный.** Второй вопрос при поднятом флаге: два комментария, два события, `unchanged` нет.
   Живой прогон: `['question', 'question'] False`.
5. **Зелёный.** `--clear "Ответ"` → флаг снят, один комментарий `answer`, одно событие `answer`
   с `note`.
6. **Зелёный.** Без текста комментарий не создаётся: `clean_text = (text or "").strip()`
   (`listik/store.py:339-343`), событие пишется безусловно.
7. **Зелёный.** `search --mode text "квазипериодический"` находит задачу (комментарий проиндексирован
   через `add_comment` → `_index_comment`); живой прогон CLI подтверждает.
8. **Зелёный.** Набор ключей ответа равен набору ключей `get_task` — функция возвращает
   `get_task(conn, task_id)` (`listik/store.py:350`).
9. **Зелёный.** `KeyError(f"задача не найдена: {task_id}")` до любых записей (`listik/store.py:328-329`).

## CLI

10. **Зелёный.** `needs-owner <id> "Q" --json | json.load` проходит: человеческие строки печатаются
    только при `not getattr(args, "json", False)` (`bin/listik:487-491`). CLI-тест запускает
    `bin/listik` по контракту ТЗ (абсолютный путь от `__file__`, `sys.executable`, `--local`,
    `env` с `LISTIK_DB`, `capture_output`, результат читается через `store.get_task`).
11. **Зелёный.** Живой прогон: `флаг «нужен человек» поднят: demo-yeq3` / `  вопрос: Первый вопрос`;
    `… снят: demo-yeq3` / `  ответ: Ответ`.
12. **Зелёный.** `show` после двух вопросов и ответа показывает историю из трёх строк
    `(question)`, `(question)`, `(answer)`; `show --json` → `['question', 'question', 'answer']`
    в порядке создания.
13. **Зелёный.** Сервер на временной базе: `POST /api/tasks/demo-lbjr/needs-owner` с
    `{"value": true, "note": "Q-http", "actor": "agent:test"}` → `{"ok": true, "data": {…}}`,
    `data.comments` = `[('question','Q-http')]`; повтор с `Q-http-2` → два `question`.
    `git diff HEAD -- web/` пуст, `web/src/api/client.ts:177` шлёт `{value, note, actor}` как раньше.
14. **Зелёный.** MCP stdio: `tools/list` у `listik_needs_owner` — схема `['actor','id','text','value']`,
    `required: ['id']` (прежняя), описание дополнено фразой про `kind=question`/`answer` и поиск;
    `tools/call` с `{"id": …, "text": "Q-mcp"}` вернул карточку с комментарием `question` `Q-mcp`.

## Не сломано

15. **Зелёный.** `./bin/listik --local set <id> needs_owner=1` поднял флаг, число комментариев
    не изменилось (3 до и 3 после). Ветка `needs_owner` в `update_task` (`listik/store.py:293-295`)
    в диффе не тронута.
16. **Зелёный.** `git diff --stat HEAD` по коду: `bin/listik`, `listik/client.py`, `listik/mcp.py`,
    `listik/server.py`, `listik/store.py`, новый `tests/test_needs_owner.py`. Помимо них изменён
    только `docs/specs/listik-product.journal.md` — это бумага конвейера, не код порции; в коммит
    порции не попадает. `documents.py`, `db.py`, `alembic/`, `web/`, `API.md`, `README.md`,
    `AGENTS.md`, `docs/harness-protocol.md`, `migrate.py`, существующие тесты — без изменений.
17. **Зелёный.** В диффе `listik/store.py` — только новая функция `set_needs_owner`; `add_comment`
    и `update_task` не изменены ни одной строкой. Флаг ставится прямым
    `UPDATE tasks SET needs_owner = ?, updated_at = ? WHERE id = ?`, после него —
    `deps_mod.refresh_task` и `_index_task` (`listik/store.py:345-348`). В остальных файлах —
    только вызовы новой функции из трёх транспортов (`client.py` `local_call` op `needs-owner`,
    `server.py` действие `needs-owner`, `mcp.py` `listik_needs_owner`) и вывод CLI.
18. **Зелёный.** Токенов, содержимого `config.toml` и личных данных в диффе и тестах нет.

## Срезанные углы — не найдено

- Хардкода под тест нет: тесты проверяют состояние базы через `store.get_task`, а не вывод.
- CLI-тест ходит только в `--local` с временной `LISTIK_DB` — рабочая база не под риском.
- Реализация не подменена заглушкой: событие и комментарий пишутся честно, флаг обновляется в
  таблице, индексация и пересчёт блокировок вызваны как в `update_task`.
- Требование «второй вопрос не теряется» решено в нужном слое (store), а не в CLI.

Мелочь, не влияющая на вердикт и не входящая в чек-лист: в событии `note=text` (нестриппленный),
тогда как в комментарий идёт `text.strip()`; `add_comment` делает свой `conn.commit()` до записи
события, так что вызов не атомарен целиком. Ни то, ни другое не нарушает требований порции.

## Перенесено на приёмку шага

Нет.
