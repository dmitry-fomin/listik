# Шаг 03. Импорт старых задач WriterLLM — план порций

Спека шага: `docs/specs/steps/step-03-writerllm-import.md`. Продукт: `docs/specs/listik-product.md`
(раздел 6.3 «Импорт WriterLLM», раздел 9 «повторный импорт не создаёт дубликаты»). Дух и объём —
`docs/listik-vision.md`: ничего сверх спеки. Самокритика (`docs/specs/self-critique.md`, раздел
«Импорт»): старые `in_progress` не превращать в активную работу с держателем.

## Цель

Одна команда `listik import-writerllm --source <path> [--project <slug>] [--dry-run] [--update]`,
которая переносит выгрузку beads-трекера WriterLLM в Listik без дубликатов при повторном запуске,
с сохранением старых ID, текстов, статусов, приоритета, дат, исполнителя, связей, результатов и
комментариев; отчёт пригоден для CI; и — последней порцией — фактический перенос 777 задач в
живую базу.

## Что уже есть в коде (после шага 02, коммит c0715ef)

- `listik/import_writerllm.py` — черновик из коммита 3452012: читает JSON (массив или объект с
  ключом `tasks|issues|records|items`), JSONL и каталог Markdown; ключ идемпотентности
  `(source='writerllm', project, external_ref)`; старый `id` становится `tasks.id`, если свободен;
  хеш `writerllm:<sha256[:24]>` пути+заголовка для записей без ID; `--update` с PATCH-семантикой
  и журналом diff. CLI `bin/listik import-writerllm` (`cmd_import_writerllm`) уже вызывает
  `import_file(conn, source, project=, dry_run=, update=)` и печатает счётчики; код возврата 1
  при ошибках. Тестов на модуль нет.
- Прогон черновика на реальной выгрузке (временная база) показал: идемпотентность и поиск по
  старому ID работают; **связи не импортируются вовсе** (`_dep_parts` не читает `depends_on_id`
  — формат `bd export`; 0 из 722); **`created_at` подменяется временем импорта**
  (`create_task` вызывается без `created_at`); **`--update` падает** (`_task_fields(partial=True)`
  — ошибка области видимости в генераторе); исполнитель пишется сырой строкой, а не ключом
  актора (в `import_beads` — через `actors.resolve`); строка в `projects` не создаётся (доска и
  `list_projects` читают только таблицу `projects`); комментарии импортируются через
  `store.add_comment` (пишет событие `comment`, трогает `updated_at`).
- `listik/import_beads.py` — образец: `_actor` нормализует исполнителя, комментарии вставляются
  прямым `INSERT` + `store._index_comment`, проект регистрируется в `projects`, в конце
  `store.recompute_blocked`.
- `store.create_task(task_id=, source=, external_ref=, created_at=, created_by=)` принимает старый
  ID и дату создания; `started_at/closed_at/close_reason/updated_at/holder*` в неё не входят —
  только прямым `UPDATE`. `store.add_dep(..., confirm=True)` пишет ребро любого типа, для
  жёстких проверяет цикл (`ValueError`). `deps.HARD_BLOCKERS`/`SOFT_LINKS` — списки типов.
- `store._index_task` кладёт `external_ref` в тело FTS, поэтому `search` находит старый ID.
- `bin/listik show` печатает `было в beads: <external_ref>` для любого `source`.
- Тестовый каркас: `tests/helpers.py` (`TempDbTestCase`, `tests/fixtures/`),
  `python3 -m unittest discover tests`.

## Источник данных (разведка 12.09.2026)

Старые задачи WriterLLM — beads-трекер в `~/Agents/WriterLLM/.beads` на dolt-бэкенде
(`bd` 1.1.0, `issues.jsonl` в дереве нет; каталог вне `~/Projects`, поэтому `import-beads` его не
видит). `bd export` из этого каталога даёт JSONL: 777 записей `_type=issue` (776 `closed`,
1 `open`), ID вида `WriterLLM-xxxx` и `WriterLLM-xxxx.N`, приоритет строкой `"0"…"4"`,
`acceptance_criteria`, `design`, `notes`, `labels` (список), `assignee`/`created_by` — имя
человека, `owner` — e-mail, даты `created_at/updated_at/started_at/closed_at`, `close_reason`;
`dependencies[]` = `{issue_id, depends_on_id, type, created_at, created_by, metadata}` — 722 рёбер
(`parent-child` 366, `blocks` 338, `discovered-from` 8, `relates-to` 6, `related` 3, `supersedes`
1), все цели внутри выгрузки; `comments[]` = `{id, issue_id, author, text, created_at}` — 90 штук.
`bd list --json` даёт тот же набор массивом, но без `comments`. Выгрузка содержит имя и e-mail
владельца — в репозиторий, фикстуры и ТЗ она не попадает.

## Решения автора (12.09.2026)

1. Входной формат — только JSON/JSONL из `bd export` (и массив `bd list --json`). Markdown-адаптер
   черновика удаляется как непроверяемый; слой «единый внутренний record» остаётся.
2. Slug проекта по умолчанию — `writerllm` (как в черновике), не `WriterLLM`.
3. Прогон импорта на живой базе (реальный `bd export` из `~/Agents/WriterLLM`) — последняя порция
   шага; исполнитель её выполняет, судья принимает по счётчикам `listik status`/`show`.

## Допущения планировщика (автор не спрашивался; менять при несогласии)

- `tasks.source = 'writerllm'` (как в черновике и спеке), даже если технически это beads.
- Один запуск — один проект: slug берётся из `--project`, иначе `writerllm`; поле `project`
  внутри записи игнорируется.
- Исполнитель, автор записи и автор комментария нормализуются через `actors.resolve` (+
  `actors.remember`), как в `import_beads`. Событие `created` получает `ts=created_at` записи и
  актора из `created_by`; событие `import` — `actor` из `created_by`, `note` с путём выгрузки.
- Комментарии — прямым `INSERT` + `store._index_comment`, без события `comment` и без
  красного-вердикта-логики; `kind='journal'`, если текст содержит `[listik]`, иначе `comment`.
- Строка `projects`: `kind='writerllm'`, `imported_at`, `import_note`; на конфликте slug
  обновляются только `imported_at`/`import_note`. Побочный эффект `import_beads`
  (`UPDATE projects SET kind='beads' WHERE imported_at IS NOT NULL`) не чинится — вне шага.
- Записи `_type` ≠ `issue` (память, инфраструктура при `bd export --all`) считаются `ignored`,
  а не ошибкой.
- `--update` меняет только поля содержания (`title, description, acceptance, design, notes,
  result, status, priority, issue_type, assignee, labels, close_reason`) и только те, чьи ключи есть
  в записи; даты создания/старта/закрытия, держатель, `external_ref`, `project`, `source` не
  трогаются; комментарии никогда не удаляются и не переписываются.
- Код возврата CLI — `1`, если в отчёте есть ошибки (и при `--dry-run` тоже); иначе `0`.
- Подпись в `show` меняется с `было в beads:` на `старый ID (<source>):` — маленькая правка
  CLI, чтобы не врать про источник.
- Фикстуры для тестов — синтетические (`tests/fixtures/writerllm/`), без имён и e-mail; для
  проверки нормализации актора используется алиас `dfomin` из `actors.ALIASES`.

## Порции (по порядку, каждая опирается на закоммиченные предыдущие)

| Порция | Суть | Файлы |
| --- | --- | --- |
| a | Модуль импорта: адаптеры JSON/JSONL → внутренний record (Markdown удалён), создание задач со старыми ID, датами, актором, результатом; комментарии; связи после задач по новым ID; строка `projects`; dry-run без записи; идемпотентный skip; ошибка записи не останавливает импорт; тесты и синтетические фикстуры | `step-03.a.md`, `step-03.check-a.md` |
| b | `--update` (diff + журнал, ручные комментарии целы), контракт отчёта с примерами и ненормализованными записями, CLI-вывод/`--json`/код возврата, подпись `старый ID (<source>)` в `show`; тесты | `step-03.b.md`, `step-03.check-b.md` |
| c | Документация: `README.md` («Импорт WriterLLM»: как выгрузить `bd export`, dry-run, повтор, `--update`), `API.md` (строка CLI), `CLAUDE.md` (описание импортёра) | `step-03.c.md`, `step-03.check-c.md` |
| d | Прогон на живой базе: `bd export` из `~/Agents/WriterLLM` в файл вне репозитория, dry-run, импорт, повторный запуск, сверка счётчиков и `show`/`search`; отчёт с числами | `step-03.d.md`, `step-03.check-d.md` |

## Вне шага

- Импорт Markdown-задач лендинга (`Projects/Writer/writer-frontend-nuxt/docs/tz/task-NN.md`) —
  решение 1 автора.
- HTTP-эндпоинт и MCP-инструмент для импорта: команда, как и `import-beads`, работает только
  локально с базой.
- Правки `import_beads.py` (в том числе `kind='beads'` для всех импортированных проектов).
- Доска `web/` (шаги 05–06): подпись источника в карточке UI не меняется.
- Перевод старых `in_progress` в работу, назначение держателя, снятие `needs_owner` — задачи
  импортируются с тем статусом, что в выгрузке.
- Удаление задач WriterLLM из Listik и обратная синхронизация в beads.

## Как проверять любую порцию

```sh
export LISTIK_DB=/tmp/listik-step03.db   # любой путь вне репозитория
./bin/listik --local init
python3 -m unittest discover tests        # без сервера и Ollama
```

Реальную выгрузку (`bd export` из `~/Agents/WriterLLM`) порции a–c не используют: она содержит
личные данные и в репозиторий не кладётся. Судья порций a–c работает только на фикстурах.
