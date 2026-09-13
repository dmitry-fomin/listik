# Порция 03.a. Модуль импорта WriterLLM: адаптеры, создание задач, связи, комментарии, тесты

## Контекст

Listik — трекер задач на stdlib-Python (без фреймворков и пакетов), одна база SQLite. Логика
задач — `listik/store.py`; связи — `listik/deps.py`; нормализация исполнителей —
`listik/actors.py`; схема — `listik/db.py`. Образец разового импорта — `listik/import_beads.py`.
Тесты — `python3 -m unittest discover tests`, каркас `tests/helpers.py` (`TempDbTestCase` даёт
изолированную базу `self.conn`, `self.tmp_path`, `FIXTURES_DIR`).

Старые задачи WriterLLM живут в beads-трекере на dolt-бэкенде; в Listik их переносят из выгрузки
`bd export` (JSONL, одна запись на строку) или `bd list --json` (JSON-массив). Форма записи
`bd export` (все ключи необязательны, кроме `id` и `title`):

```text
_type="issue"; id; title; description; acceptance_criteria; design; notes;
status: open|in_progress|blocked|closed|deferred|…; priority: "0".."4" (строка) или число;
issue_type: task|bug|feature|epic|decision|chore; assignee, created_by — имя человека;
owner — e-mail (игнорировать); labels — список строк;
created_at, updated_at, started_at, closed_at — ISO UTC "…Z"; close_reason;
dependencies: [{issue_id, depends_on_id, type, created_at, created_by, metadata}];
comments:     [{id, issue_id, author, text, created_at}];
comment_count, dependency_count, dependent_count, defer_until, spec_id — игнорировать.
```

Записи с `_type` ≠ `issue` (память, инфраструктура при `bd export --all`) в задачи не
превращаются. `bd list --json` даёт те же записи массивом, без `_type` и без `comments`.
Реальная выгрузка содержит имя и e-mail владельца: **в репозиторий, фикстуры, ТЗ и отчёты она не
попадает**; порция работает только на синтетических фикстурах.

Сейчас в `listik/import_writerllm.py` лежит черновик (`import_file(conn, path, *, project, dry_run,
update)`), и `bin/listik import-writerllm` его уже вызывает. Черновик: не читает `depends_on_id`
(связи не импортируются), теряет `created_at` (подставляет время импорта), не нормализует
исполнителя, не регистрирует проект в `projects`, содержит Markdown-адаптер, который решением
автора удаляется, и падает на `--update`. Эта порция переписывает модуль по контракту ниже;
`--update` в ней **не реализуется** (порция b) — флаг принимается и ведёт себя как обычный skip.

Перед началом прочитай: `listik/import_writerllm.py` (что есть), `listik/import_beads.py`
(`_actor`, вставка комментариев, строка `projects`, `recompute_blocked`), `store.create_task`,
`store.gen_id`, `store.event`, `store.add_dep`, `store._index_task`, `store._index_comment`,
`store.recompute_blocked`, `store.STATUS_TITLES`, `deps.HARD_BLOCKERS`/`SOFT_LINKS`,
`actors.resolve`/`actors.remember`, схему `tasks/deps/comments/events/projects` в `listik/db.py`,
`tests/helpers.py`, любой существующий тест (например `tests/test_needs_owner.py`).

## Что сделать

### 1. Входной формат и внутренний record

`import_file(conn, path, *, project=None, dry_run=False, update=False) -> dict` — сигнатура и имя
сохраняются (CLI уже вызывает так).

Чтение `path`:
- суффикс `.jsonl` или `.ndjson` — построчно, пустые строки пропускаются; строка, которая не
  разбирается как JSON-объект, даёт ошибку с номером строки и сырым текстом, остальные строки
  читаются;
- иначе — файл целиком как JSON: массив объектов, либо объект с ключом `tasks`, `issues`,
  `records` или `items` (первый найденный), либо одиночный объект-запись;
- каталог, отсутствующий файл, не-JSON — одна ошибка в отчёте (`where="source"`), `total=0`,
  ничего не пишется. Markdown-каталоги не поддерживаются; функции `_markdown_record`,
  `_scalar` и обход `rglob("*.md")` удалить.

Каждая сырая запись приводится к одному внутреннему record с фиксированным набором полей
(dict или dataclass — на выбор исполнителя; набор полей — контракт):

| Поле record | Откуда (первый непустой ключ) | Правило |
| --- | --- | --- |
| `external_ref` | `external_ref`, `external_id`, `id`, `task_id`, `uuid` | строка без пробелов по краям; если ни одного нет — `writerllm:` + `sha256(норм_путь + "\0" + норм_заголовок)[:24]`, где норм_путь — абсолютный путь файла выгрузки с `/`-разделителями в нижнем регистре, норм_заголовок — заголовок со схлопнутыми пробелами в нижнем регистре |
| `title` | `title`, `name` | пустой заголовок — ошибка записи (`where="record"`), не «Untitled» |
| `description` | `description`, `body`, `content` | строка, по умолчанию `""` |
| `acceptance` | `acceptance_criteria`, `acceptance`, `criteria` | `""` |
| `design` | `design`, `design_notes` | `""` |
| `notes` | `notes`, `note` | `""` |
| `result` | `result`, `outcome`; если пусто и статус после маппинга `done`/`cancelled` — `close_reason` | `""` |
| `status` | `status`, `state` | маппинг: `open→open`; `in_progress`, `inprogress`, `doing`, `in-progress` → `in_progress`; `blocked→blocked`; `review→review`; `closed`, `done`, `complete`, `completed` → `done`; `cancelled`, `canceled` → `cancelled`; `deferred`, `todo`, `pending` → `open`; пусто → `open`; любое другое → `open` + warning (`field="status"`) |
| `priority` | `priority`, `importance` | целое 0..4 (строку с цифрой разобрать); имена `urgent/critical→0`, `high→1`, `normal/medium→2`, `low→3`; иное → `2` + warning |
| `issue_type` | `issue_type`, `type` | как есть, по умолчанию `task` |
| `assignee` | `assignee`, `assigned_to` | ключ актора через `actors.resolve`; `actors.remember` (пишет в `actor_aliases`) — **только не в dry-run**, как `import_beads._actor`; пусто → `None` |
| `created_by` | `created_by`, `author` | так же через `actors.resolve` |
| `labels` | `labels`, `tags` | список строк; строка с запятыми/точками с запятой режется; иначе `[]` |
| `created_at`, `updated_at`, `started_at`, `closed_at` | одноимённые (`modified_at` → `updated_at`; `completed_at`, `finished_at` → `closed_at`) | строки как есть; отсутствуют → `None` |
| `close_reason` | `close_reason` | `None` |
| `holder`, `holder_at`, `holder_note` | одноимённые (`current_holder` → `holder`) | `holder` через `actors.resolve`; отсутствуют → `None` |
| `spec_path`, `journal_path`, `checklist_path` | `spec_path`/`spec`, `journal_path`/`journal`/`log_path`, `checklist_path`/`checklist` | `None` |
| `dependencies` | `dependencies`, `depends_on`, `deps`, `blocked_by` | список пар `(target_ref, dep_type)`: элемент-dict — цель из `depends_on_id`, `depends_on`, `external_ref`, `external_id`, `dependency`, `id`, `task_id`; тип из `type`, `dep_type`, `relation`, по умолчанию `blocks`; элемент-строка — `(строка, "blocks")`; пустая цель — ошибка `where="dependency"` для этой записи, остальные пары остаются |
| `comments` | `comments`, `journal_entries`, `history` | список `{id, author, text, created_at, kind}`: dict — `text`/`body`, `author`/`created_by`, `created_at`/`timestamp`, `id`; строка — `{text: строка}`; элемент без текста пропускается; `kind` = значение ключа `kind`, иначе `journal`, если в тексте есть `[listik]`, иначе `comment` |
| `raw` | сама сырая запись | для отчёта об ошибках |
| `line` | номер строки JSONL (с 1) или индекс в массиве (с 0) | для отчёта |

Ключ `project` внутри записи игнорируется; slug проекта = аргумент `project`, иначе `writerllm`.
Записи с `_type`, отличным от `issue`, считаются в `ignored` и дальше не обрабатываются;
отсутствие ключа `_type` (как в `bd list --json`) означает `issue`.

### 2. Запись задач

Ключ идемпотентности — `(tasks.source='writerllm', tasks.project=<slug>, tasks.external_ref)`.

Для каждой записи по порядку:
- существует задача с таким ключом → `skipped` (и с `update=True` в этой порции — тоже
  `skipped`); её `id` кладётся в карту `external_ref → id` для связей; сама задача не меняется,
  но недостающие комментарии (п. 3, с дедупликацией) дописываются — это дозаполнение, а не
  перезапись;
- иначе → `created`: `id` = `external_ref` как есть, если `SELECT 1 FROM tasks WHERE id=?` пуст;
  иначе `store.gen_id(conn, slug, prefix=slug)` (старый ID остаётся в `external_ref`; в примере
  отчёта помечается `"remapped": true`). Создание через `store.create_task(conn, task_id=id,
  project=slug, source="writerllm", external_ref=..., created_at=record.created_at,
  created_by=record.created_by, title, description, acceptance, design, notes, result, status,
  priority, issue_type, assignee, labels, spec_path, journal_path, checklist_path)` — так событие
  `created` получает дату и актора записи. Затем прямым `UPDATE tasks SET …` — `started_at`,
  `closed_at`, `close_reason`, `holder`, `holder_at`, `holder_note` (только непустые), затем
  комментарии (п. 3), затем **последним** `updated_at = record.updated_at` (если есть) и
  `store._index_task(conn, id)`. Событие `store.event(conn, id, "import",
  actor=record.created_by, note="writerllm <external_ref> из <путь выгрузки>",
  ts=record.updated_at or record.created_at)`.
- Пустой `title`, исключение при создании — ошибка `where="record"` с `external_ref`, `line`,
  `error`, `raw`; следующая запись обрабатывается. Если исключение случилось после
  `create_task` (например, на комментарии), задача остаётся, ошибка фиксируется с `where="comment"`.

Статус пишется как в выгрузке: старый `in_progress` без держателя остаётся `in_progress` без
держателя (самокритика: не превращать в активную работу; доска покажет как брошенную).

### 3. Комментарии

Прямым `INSERT INTO comments(id, task_id, author, kind, text, created_at)` (как в
`import_beads`), `author` — ключ актора через `actors.resolve`. Всё, что определяет
идентичность комментария, берётся **из данных записи, а не из времени прогона** — иначе
повторный импорт продублирует комментарии, у которых в выгрузке нет даты:

- `created_at` — из комментария; если его нет — `created_at` задачи (из записи), и только если
  нет и его — `store.now_iso()`;
- `id` — `id` из выгрузки, если есть; иначе детерминированный
  `f"{task_id}:wl:{sha256(text + '\0' + (created_at_комментария_из_записи or ''))[:16]}"` —
  то есть от текста и исходной (возможно пустой) даты, без `now_iso()` и без порядкового номера;
- дубликат — если уже есть комментарий с таким `id`, **или** с теми же `(task_id, text)` и при
  этом либо `created_at` в записи пуст, либо совпадает с `created_at` найденного комментария.
  Дубликат не вставляется.

После вставки `store._index_comment(conn, cid)`. Событий `comment` и `store.add_comment` не
использовать: они трогают `updated_at` и запускают логику вердикта. Считать только новые вставки
(`report["comments"]`). Гарантия, которую проверяет тест 6: второй импорт того же файла не
добавляет ни одной строки в `comments`, включая комментарии без `created_at`.

### 4. Связи — после всех задач

Второй проход по записям со статусом `created` **и** `skipped` (чтобы повторный запуск
дописывал связи, которых не хватало): для каждой пары `(target_ref, dep_type)` цель ищется в
карте текущего запуска, затем `SELECT id FROM tasks WHERE source='writerllm' AND project=? AND
external_ref=?`; не найдена — ошибка `where="dependency"` (`external_ref` записи, `target_ref`,
`error="target task not found"`), задача остаётся. Найдена — если ребра `(issue_id, depends_on,
dep_type)` ещё нет: `store.add_dep(conn, task_id, target_id, dep_type, created_by=<ключ актора
из dep.created_by>, confirm=True)`; `ValueError` (цикл, self-reference) — ошибка
`where="dependency"`, импорт продолжается. Считать только новые рёбра (`report["dependencies"]`).
Типы вне `deps.HARD_BLOCKERS ∪ deps.SOFT_LINKS` (например `supersedes` уже в `SOFT_LINKS`, а
незнакомый тип — нет) пишутся как есть: для `ready`/`blocked` они мягкие по построению.
Направление как в beads: `issue_id` = задача записи, `depends_on` = цель (`parent-child`:
`issue_id` — ребёнок, `depends_on` — родитель; это совпадает с `deps.children/parent`).

После прохода — `store.recompute_blocked(conn)` (**не в dry-run**: функция делает `UPDATE
tasks` и `conn.commit()`).

### 5. Проект

Не в dry-run: `INSERT INTO projects(slug, title, kind, imported_at, import_note)
VALUES(?, ?, 'writerllm', now, '<created> задач из WriterLLM') ON CONFLICT(slug) DO UPDATE SET
imported_at=excluded.imported_at, import_note=excluded.import_note` — `title`, `kind`, `path`
существующей строки не трогать.

### 6. Dry-run

`dry_run=True`: ни одного `INSERT/UPDATE/DELETE` и ни одного `conn.commit()` — ни задач, ни
комментариев, ни связей, ни строки `projects`, ни событий, **ни побочных записей через чужие
функции**. Соединение открыто с неявной транзакцией, поэтому любой `commit()` внутри вызванной
функции зафиксирует и всё накопленное; в dry-run-ветке запрещено вызывать: `store.create_task`,
`store.update_task`, `store.add_dep`, `store.add_comment`, `store.event`, `store._index_task`,
`store._index_comment`, `store.recompute_blocked`, `db.seed_actors`, `actors.remember` (пишет в
`actor_aliases`) и любой прямой SQL кроме `SELECT`. `actors.resolve(raw, conn)` только читает — его
можно. Практически: нормализация записи (п. 1) и ключ идемпотентности (п. 2) считаются одинаково
в обоих режимах, а всё, что пишет, стоит за одной проверкой `if not dry_run` — включая
`remember` и `recompute_blocked`, а не только `create_task`. Отчёт считается «как если бы»:
`created`/`skipped`/`ignored` по ключу идемпотентности, `dependencies` — число пар, чьи цели
нашлись в карте или в базе (без проверки цикла), `comments` — число комментариев у создаваемых
записей плюс новых у пропускаемых, `errors` — ошибки чтения и нормализации. Ошибки, которые
проявляются только при записи (цикл), в dry-run не ловятся — это допустимо.

### 7. Отчёт

`import_file` возвращает dict со всеми ключами (порция b добавит поведение `updated` и
CLI-вывод, структура задаётся здесь):

```text
source: str (абсолютный путь), project: str, dry_run: bool, update: bool,
total: int (сырых записей, включая нечитаемые строки и ignored),
created, updated, skipped, ignored: int,        # updated в этой порции всегда 0
dependencies: int (новых рёбер), comments: int (новых комментариев),
errors: [ {where: "source"|"record"|"dependency"|"comment", line: int|null,
           external_ref: str|null, target_ref: str|null, error: str, raw: <запись|строка>} ],
warnings: [ {external_ref, field, value, used} ],
examples: { "create": [...], "update": [...], "skip": [...], "error": [...] }
          # ≤5 элементов на действие; create/update/skip: {id, external_ref, title, remapped?};
          # error: {external_ref, where, error}
```

Логировать (`logging.getLogger("listik.import_writerllm")`) по одной строке на ошибку —
достаточно; печатать из модуля ничего не нужно.

### 8. Тесты — `tests/test_import_writerllm.py` и фикстуры `tests/fixtures/writerllm/`

Фикстуры синтетические: ID вида `WL-a1`, `WL-a1.1`, заголовки/тексты выдуманные, ни имён, ни
e-mail; для актора — алиас `dfomin` (есть в `actors.ALIASES`, даёт `me`). Минимум:

- `export.jsonl` — 7–9 записей `_type=issue`: закрытая с `close_reason`, `started_at`, `closed_at`,
  приоритет `"1"` строкой, `assignee`/`created_by` = `dfomin`, `labels`; открытая; `in_progress`
  без держателя; пара с `blocks` (открытая ждёт открытую); ребёнок с `parent-child`; связь
  `relates-to`; запись с двумя комментариями (`id`, `author`, `text`, `created_at`, один с
  `[listik]` в тексте); запись с комментарием **без `id` и без `created_at`** (только `author`
  и `text`); запись с неизвестным статусом `weird` и приоритетом `"x"`; одна запись
  `_type=memory`; одна запись со связью на несуществующую цель.
- `export.json` — **только `issue`-записи** `export.jsonl` (запись-память в него не входит),
  массивом, без ключа `_type` и без `comments`, как даёт `bd list --json`. Из-за отсутствия
  `_type` любая запись массива считается `issue` (§1), поэтому память там быть не должна.
- `broken.jsonl` — валидная запись, строка `{not json`, запись без `id` (с `title`), запись без
  `title`, валидная запись.

Тесты через `import_file` на `TempDbTestCase.conn` (для поиска — `listik.search.search(conn,
"<old id>", mode="text")`, для карточки — `store.get_task`):

1. JSONL создаёт все `issue`-записи: `created` = их число, `ignored` = 1, `tasks.id` равен старому
   ID, `source='writerllm'`, `external_ref` = старый ID, `project='writerllm'`; заголовок, описание,
   acceptance, design, notes, метки совпадают с фикстурой.
2. Даты и результат: `created_at/updated_at/started_at/closed_at/close_reason` равны фикстуре
   (не времени импорта), `status='done'` для `closed`, `result` = `close_reason`, приоритет `1`;
   событие `created` у задачи имеет `ts` = `created_at` фикстуры; есть событие `import`.
3. Акторы: `assignee`, `created_by`, `author` комментария = `me`.
4. Связи после задач: рёбра `blocks`, `parent-child`, `relates-to` есть в `deps` с ID Listik;
   `report["dependencies"]` = число вставленных; у открытой задачи, ждущей открытую,
   `deps.blockers(conn, id)` непуст (после `recompute_blocked` — `blocked_by` заполнен); связь на
   несуществующую цель — запись в `errors` с `where="dependency"`, задача при этом создана.
5. Комментарии: два комментария с `created_at` и `kind` (`journal` для `[listik]`, `comment` для
   другого) и `author=me`; события `comment` у задачи нет; у записи с комментарием без `id` и
   `created_at` комментарий один, его `created_at` равен `created_at` задачи из фикстуры, а `id`
   начинается с `"<task_id>:wl:"`; `report["comments"]` = 3.
6. Повторный запуск: `created=0`, `skipped` = число issue-записей; `select count(*) from
   comments` снят **до** и **после** второго запуска и равен (это ловит дубль комментария без
   `created_at`); число строк в `tasks`, `deps`, `events` тоже не изменилось;
   `report["comments"]` второго запуска = 0.
7. Dry-run: число строк в `tasks`, `comments`, `deps`, `events`, `projects`, `actors`,
   `actor_aliases` до и после равно (последние две ловят `actors.remember`/`seed_actors`), и
   `PRAGMA data_version` соединения не изменился — если это проверяется через второе соединение
   к той же базе, иначе достаточно счётчиков; отчёт даёт те же `created`/`dependencies`/`comments`,
   что даст реальный запуск следом.
8. `broken.jsonl`: две валидные записи созданы; запись без `id` создана с `external_ref`,
   начинающимся на `writerllm:`, и второй запуск её пропускает (хеш стабилен); в `errors` —
   элемент с `line`, `error` и `raw` для битой строки и элемент `where="record"` для записи без
   заголовка; `total` = 5.
9. Коллизия ID: заранее `store.create_task(task_id="WL-a1", source="native", …)`; импорт создаёт
   задачу с другим `id`, `external_ref="WL-a1"`, в `examples["create"]` у неё `remapped=True`;
   нативная задача не изменена.
10. `export.json` (массив) даёт тот же набор задач и связей, что `export.jsonl`: сравнение ведётся
    на **двух разных базах** — `export.jsonl` в `self.conn`, `export.json` во второй
    (`db_mod.init(self.tmp_path / "second.db")`), иначе совпадающие `external_ref` дадут
    `skipped`; во второй базе `created` равен `created` первой, `ignored=0`, множества
    `tasks.id` и рёбер `(issue_id, depends_on, dep_type)` совпадают, комментариев 0.
11. Строка `projects`: после импорта есть `slug='writerllm'`, `kind='writerllm'`,
    `import_note` содержит число созданных; dry-run строку не создаёт.
12. Неверный источник: каталог (`self.tmp_path`) → `total=0`, одна ошибка `where="source"`,
    в `tasks` ничего; Markdown-файл (`.md`) — то же (не JSON).
13. Старый ID находится: `store.get_task(conn, "WL-a1")` возвращает карточку с
    `external_ref="WL-a1"`; `search(conn, "WL-a1", mode="text")["results"]` содержит её.
14. Неизвестный статус/приоритет: задача создана с `open`/`2`, в `warnings` два элемента.

## Границы правки

- Правятся: `listik/import_writerllm.py`, `tests/test_import_writerllm.py` (новый),
  `tests/fixtures/writerllm/*` (новые).
- `bin/listik` не трогать: сигнатура `import_file` и ключи `created/updated/skipped/dependencies/
  errors/dry_run` сохраняются, `cmd_import_writerllm` работает как есть. Вывод CLI, `--json`,
  `show` — порция b. Документация — порция c.
- Не менять `listik/store.py`, `listik/deps.py`, `listik/actors.py`, `listik/db.py`,
  `listik/import_beads.py`, `listik/server.py`, `listik/mcp.py`, `listik/client.py`, схему и
  `alembic/`. Если для порции не хватает функции в `store` — использовать прямой SQL в модуле
  импорта, как делает `import_beads`, а не расширять `store`.
- `--update` не реализовывать: флаг принимается, существующие записи пропускаются.
- Существующие тесты не править; новые тесты не читают и не пишут файлы вне `self.tmp_path` и
  `tests/fixtures/writerllm/`; реальную выгрузку `bd export`, `~/Agents/WriterLLM` и
  `listik.db` репозитория не трогать и не читать.
- В фикстурах, тестах, коде и отчёте — никаких имён людей, e-mail, токенов, содержимого
  `config.toml`.
- **Каждый** вызов `bin/listik` — в тестах (если где-то используется `subprocess`), в отчёте
  исполнителя и в разделе «Как проверить» — идёт с явным префиксом `LISTIK_DB=<временная база>`
  в той же команде (или `env={"LISTIK_DB": …}` в `subprocess`), без исключений и без расчёта на
  `export` из предыдущей строки: `paths.DB_PATH` читается из окружения при импорте модуля, и
  команда без переменной пишет в `listik.db` репозитория. Тесты, идущие через `import_file` на
  `self.conn`, этой переменной не касаются и `db.init()` без явного пути не вызывают.
- Не добавлять зависимостей, не заводить Markdown/YAML-парсеров, не оставлять «на будущее»
  неиспользуемых адаптеров.

## Как проверить

Каждая команда `./bin/listik` — с явным `LISTIK_DB=…` в той же строке; `export` не использовать.

```sh
python3 -m unittest discover tests                          # все зелёные, включая новые
python3 -m unittest tests.test_import_writerllm -v          # ≥14 тестов из списка выше
grep -n "_markdown_record\|rglob\|def _scalar" listik/import_writerllm.py   # пусто
DB=/tmp/listik-step03a.db; rm -f "$DB" "$DB-wal" "$DB-shm"
LISTIK_DB=$DB ./bin/listik --local init
LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/export.jsonl --dry-run
sqlite3 $DB "select count(*) from tasks; select count(*) from actor_aliases"   # 0 и столько же, сколько сразу после init
LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/export.jsonl
sqlite3 $DB "select count(*) from comments"                                   # запомнить
LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/export.jsonl   # create=0
sqlite3 $DB "select count(*) from comments"                                   # то же число
LISTIK_DB=$DB ./bin/listik --local show WL-a1 ; LISTIK_DB=$DB ./bin/listik --local search WL-a1 --mode text
sqlite3 $DB "select count(*) from deps; select slug,kind from projects"
git status --porcelain -- bin listik/store.py listik/deps.py listik/db.py README.md API.md   # пусто
stat -f %m listik.db   # снять до и после всей проверки: живая база (в .gitignore) не должна измениться
```
