# Приёмка порции 01.e. Документация и MCP

## Предусловие

0. Порция d закоммичена до начала проверки: `git log --oneline` содержит её коммит, и живой
   вывод `listik --local context <id> --stage s4-judge --format json` на временной базе содержит
   ключи `verdict`, `worktree`, `limits.default_for_stage`, `dependencies.hard`,
   `dependencies.suggested` и **не содержит** ключа `verdicts`. Если `verdicts` присутствует —
   приёмка не проводится, порция возвращается как преждевременная (пункты 2, 3, 12 на старой
   форме ответа не засчитываются).

## `API.md` (проверяется чтением файла и сверкой с живым выводом на временной базе)

1. В таблице «Чтение» есть строка `GET /api/tasks/{id}/context` с параметрами `stage`,
   `portion`, `max_chars` и дефолтами лимита по этапу (150 000 для s1/s2, 24 000 для s3/s4).
   Про `stage` написано фактическое поведение HTTP: **необязателен**, при отсутствии — `s1-spec`,
   значение сервером не валидируется; обязателен и ограничен четырьмя значениями только в CLI
   и MCP. Судья сверяет с `listik/server.py` (`q1("stage") or "s1-spec"`) и, при поднятом
   сервере на временной базе, запросом `GET /api/tasks/{id}/context` без `stage` — ответ 200 со
   `stage == "s1-spec"`. Формулировка «обязателен» для HTTP — красный пункт.
2. Перечисленные в `API.md` ключи ответа `context` совпадают с реальным набором ключей вывода
   `listik --local context <id> --stage s4-judge --format json` (сверка судьёй: `python3 -c`
   печать `sorted(d.keys())`); в обоих местах есть `verdict`, `worktree`, `limits.default_for_stage`,
   `dependencies.hard`, `dependencies.suggested` и нет `verdicts`.
3. Есть описание контракта по этапам: s1, s2, s3 (слои: чек-лист целиком → порция →
   лексика → начало), s4 (`worktree`, `verdict`, `journal` с переходами).
4. В «Модели задачи» описаны `spec_path`, `checklist_path`, `review_path`, `decision_path`,
   `journal_path` и `documents[]` в ответе `GET /api/tasks/{id}`. Список полей `documents[]`
   в `API.md` **полностью** совпадает с `sorted(data["documents"][0].keys())` из живого
   `listik --local show <id> --json` на задаче с документом (ожидаемо: `chunk_count,
   content_hash, error, id, kind, path, revision, status, title, updated_at`); поля `checked_at`
   в списке нет, а в тексте есть оговорка, что оно хранится только в таблице. Упоминание
   `checked_at` как поля ответа — красный пункт.
5. Есть подраздел о документах и чанках: виды документов, `content_hash`/`revision`,
   `status=missing`, события `document_error`/`document_restored`, фоновая перепроверка.
6. `GET /api/search`: описаны `best_hit` и поля хита чанка (`heading`, `breadcrumb`, `path`,
   `start_line`); `POST /api/tasks`/`PATCH` содержат три новых пути; `POST /api/embed`
   упомянут один раз с `kinds=task,comment,chunk`.
7. В разделе CLI есть `listik context` с флагами.

## `README.md`

8. Есть пример `listik context <id> --stage s2-review` и пример с `--portion`; описано, как
   задать пути документов (`new --spec/--checklist/--review/--decision`, `set …_path=`).
9. В списке MCP-инструментов есть `listik_context`; в «Структура базы» — `documents`,
   `document_chunks`, `document_chunk_fts`; есть раздел «Тесты» с
   `python3 -m unittest discover tests`.
10. Примеры команд из README выполняются на временной базе без ошибок (судья прогоняет
    минимум два: `context` и `search`).

## MCP

11. `tools/list` через stdio содержит `listik_context` с обязательными `id` и `stage`
    (enum из четырёх значений) и необязательными `portion`, `max_chars`.
12. `tools/call` `listik_context` для задачи с документом на временной базе возвращает
    JSON с теми же ключами, что CLI `context --format json`; без `max_chars` лимит —
    дефолт этапа (`limits.default_for_stage`).
13. Описание `listik_show` упоминает `documents[]`; описания `listik_create`/`listik_update`
    упоминают `checklist_path`, `review_path`, `decision_path`.

## `bin/listik-codex`

14. Вызов `context` в скрипте использует `--stage <stage> --format json` и совпадает с
    контрактом CLI (чтение файла; если правок не потребовалось — файл не в диффе).

## Границы

15. `git diff --stat HEAD` содержит только `API.md`, `README.md`, `listik/mcp.py` и, возможно,
    `bin/listik-codex`. `AGENTS.md`, `CLAUDE.md`, `docs/harness-protocol.md`, код в
    `documents.py`/`search.py`/`server.py`/`store.py`/`bin/listik`, `tests/` не изменены.
16. В диффе документации нет токенов и содержимого `config.toml` (`git diff | grep -i token`
    показывает только упоминания флага/команды `token`, если они и были раньше).
17. `python3 -m unittest discover tests` зелёный.
