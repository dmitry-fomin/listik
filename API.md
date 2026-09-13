# Listik — API и модель задачи

## Что это

Listik — самостоятельный трекер задач вместо `bd` (beads). Один сервер, одна база SQLite,
один API. Все агенты (dsh, grok, claude, codex) и доска работают с ним же.

- База: `listik.db` в корне Listik
- Сервер: `./bin/listik serve` → `http://127.0.0.1:8787`
- Токен: `config.toml` в корне Listik ([auth] token), заголовок `Authorization: Bearer <token>`
  (или `X-Listik-Token`, или `?token=` — для прямых ссылок из браузера и SSE)

Формат ответа любого `/api/*` запроса:

```json
{ "ok": true, "data": { ... } }
```

Ошибка: `{ "ok": false, "error": "текст" }` + HTTP-код (401 без токена, 404 нет задачи, 400 плохой ввод).

## Модель задачи

| Поле | Тип | Смысл |
|---|---|---|
| `id` | str | человекочитаемый ID (`zoloto585-search-a1b2`), стабильный |
| `project` | str | slug проекта (`zoloto585-search`, `personal`, ...) |
| `title` | str | заголовок |
| `description` | str | что надо сделать |
| `acceptance` | str | критерии приёмки |
| `design` | str | проектные заметки |
| `notes` | str | рабочие заметки |
| `result` | str | чем кончилось |
| `status` | str | `open` \| `in_progress` \| `blocked` \| `review` \| `done` \| `cancelled` |
| `stage` | str? | этап конвейера: `s1-spec` \| `s2-review` \| `s3-impl` \| `s4-judge` \| `done` |
| `priority` | int | 0 (срочно) … 4 (потом) |
| `issue_type` | str | `task` \| `bug` \| `feature` \| `epic` \| `chore` \| `decision` \| `question` |
| `assignee` | str? | кому поручена: `me`, `agent:claude`, `agent:dsh`, `agent:grok`, `agent:codex` |
| `holder` | str? | кто **держит прямо сейчас** (может отличаться от assignee) |
| `holder_at` | str? | heartbeat держащего |
| `holder_note` | str? | что именно держащий делает сейчас |
| `needs_owner` | bool | ждёт решения человека |
| `stage_at` | str? | когда вошёл в текущий этап |
| `stage_hours` | float? | сколько часов на этапе (считает сервер) |
| `holder_hours` | float? | сколько часов держит (считает сервер) |
| `stale` | bool | держатель молчит дольше `board.stale_hours` (24 ч) |
| `abandoned` | bool | задача в работе, но держателя нет |
| `stage_warn` | bool | на этапе дольше `board.wip_warn_hours` (8 ч) |
| `blocked_by` | str[] | незакрытые блокеры |
| `labels` | str[] | метки |
| `spec_path` | str? | путь к ТЗ (индексируется по разделам, см. «Документы и чанки») |
| `checklist_path` | str? | путь к чек-листу приёмки |
| `review_path` | str? | путь к файлу ревью |
| `decision_path` | str? | путь к файлу решения; `journal_path` — старое имя, алиас того же вида документа `decision` |
| `journal_path` | str? | алиас `decision_path` (совместимость со старыми задачами) |
| `worktree` / `branch` | str? | где идёт работа |
| `source` | str | `native` \| `beads` \| `writerllm` (импортированные задачи) |
| `external_ref` | str? | старое ID в beads/WriterLLM |
| `created_at`/`updated_at`/`started_at`/`closed_at` | str | ISO-8601 UTC |
| `stage_title`/`status_title`/`holder_title`/`assignee_title`/`holder_age`/`stage_age`/`updated_age` | str | готовые к показу подписи |

`GET /api/tasks/{id}` при `details=1` (по умолчанию включено) добавляет: `comments[]`
(`id, author, kind, text, created_at`), `dependencies[]`, `dependents[]`, `events[]`
(`ts, kind, from_value, to_value, actor, harness, note, duration_s`) и `documents[]` — по одной
записи на каждый индексируемый документ задачи (`spec_path`/`checklist_path`/`review_path`/
`decision_path`/`journal_path`), с полями `id, kind, path, revision, content_hash, title,
updated_at, status, error, chunk_count`. Колонка `checked_at` (время последней фоновой
перепроверки файла) есть только в таблице `documents` и в ответы API не попадает — это
решение ради побайтной стабильности `context` (см. ниже).

## Документы и чанки

Задача может ссылаться на markdown-файлы четырёх видов: `spec` (ТЗ, поле `spec_path`),
`checklist` (чек-лист приёмки, `checklist_path`), `review` (ревью, `review_path`), `decision`
(решение/журнал, `decision_path`; `journal_path` — тот же вид документа под старым именем).
Файл не копируется в карточку — в базе (`documents`) хранится только путь, хэш содержимого и
метаданные; собственно текст режется на секции (`document_chunks`) по заголовкам Markdown:
у каждого чанка есть `heading` (заголовок своего раздела) и `breadcrumb` (путь заголовков от
корня, через ` / `), которые вшиты в начало текста чанка и используются при полнотекстовом и
векторном поиске. Длинные разделы режутся на несколько чанков с оверлапом, чтобы соседние
куски не теряли контекст на границе.

Переиндексация — по `content_hash`: если файл не менялся, ничего не пересчитывается;
`revision` документа растёт на единицу при каждом реальном изменении содержимого. Файл стал
недоступен (не читается/не существует) → `status=missing` и одно событие `document_error`
(повторные проверки того же отсутствующего файла новых событий не создают); файл снова
появился → `status=ok` и событие `document_restored`. Переиндексация происходит при вызове
`context`/`create`/`update` для задачи (`index_task_documents`), а также фоновым воркером
сервера, который периодически перепроверяет файлы всех незакрытых задач с хотя бы одним путём
документа. `show` файлы не читает и переиндексацию не запускает — он только отдаёт то, что уже
лежит в таблице `documents`.

## Эндпоинты

### Чтение

| Метод | Путь | Параметры | Ответ |
|---|---|---|---|
| GET | `/api/health` | — | `status, version, db, counts, embed{ok,models}, now` |
| GET | `/api/meta` | `archived` | `projects[], actors[], facets{}, statuses{}, stages{}, priorities{}` |
| GET | `/api/projects` | — | `projects[]` — все репозитории доски, включая скрытые: `slug, title, kind, path, path_exists, git_remote, git_branch, archived, n_tasks, n_open, n_wip`, плюс `routing` (переопределение проекта — объект или `null`), `routing_effective` (действующая слитая таблица, которой реально пользуются `allowed_harnesses`/`transition_kind`), `routing_source` (`default`\|`config`\|`db`\|`config+db`), плюс `root` (корень поиска проектов) |
| GET | `/api/stats` | `project` | `by_status{}, by_stage{}, by_project[], by_holder[], by_actor[], stale, needs_owner, closed_7d, closed_prev_7d, closed_delta, closed_by_day[{date,count}] (14 дней), long_stage, running[], generated_at` |
| GET | `/api/board` | `group_by=status\|stage\|project\|holder`, `project`, `include_closed`, `limit` | `group_by, columns[], total, needs_you[], generated_at` |
| GET | `/api/tasks` | `project,status,stage,assignee,holder,needs_owner,type,label,text,include_closed,include_archived,limit,offset,order=updated\|created\|priority\|stage` | `total, limit, offset, tasks[]` |
| GET | `/api/tasks/{id}` | `details=0/1` | задача + `comments/dependencies/dependents/events/documents` |
| GET | `/api/tasks/{id}/context` | `stage`, `portion`, `max_chars` | компактный, побайтно стабильный контекст этапа для harness — см. ниже |
| GET | `/api/search` | `q` (обязателен), `limit`, `project`, `status`, `stage`, `actor`, `needs_owner`, `mode=hybrid\|text\|vector` | `query, mode, took_ms, lexical_docs, vector_docs, count, results[]` |
| GET | `/api/ready` | `project`, `stage`, `harness` (только задачи, чей этап разрешён этому harness в routing проекта; задача без этапа — всем), `include_occupied`, `limit` | `tasks[]` (можно брать: нет незакрытых блокеров и держателя), `cycles[]` |
| GET | `/api/blocked` | `project`, `limit` | `tasks[]` с разбором `blockers[]`, `blocked_by_stale`, `blocked_by_holder` |
| GET | `/api/deps/suggested` | `project`, `limit` | `items[]` (предложения агентов, ждущие подтверждения человеком: `issue_id, issue_title, issue_stage, project, depends_on, depends_on_title, depends_on_status, created_by, created_at`), `generated_at` |
| GET | `/api/timeline` | `limit` | `items[]`: `ts, kind, from_value, to_value, actor, actor_title, harness, note, duration_s, task_id, title, project, stage, status, age` |
| GET | `/api/events` | `limit` | сырые события |
| GET | `/api/stream` | `token` (обязателен) | SSE: `data: {"kind":"task","at":...,"payload":{"id":...,"action":"updated"}}`, плюс `: ping` каждые 15 с |

`/api/board` — форма колонки:

```json
{ "key": "in_progress", "title": "в работе", "count": 7, "wip": 3, "needs_owner": 2, "stale": 1,
  "tasks": [ { ...задача... } ] }
```

`needs_you` — задачи, требующие человека: `needs_owner`, `stale` или `abandoned`.

`/api/search` — форма `results[]`: карточка задачи (обычные поля) плюс `snippet`, `score`,
`hits[]` и `best_hit` (лучший из `hits[]`). Каждый элемент `hits[]` — либо попадание в саму
задачу/комментарий (`kind=task`/`kind=comment`), либо в чанк документа (`kind=chunk`); для
`kind=chunk` в хите есть `heading, breadcrumb, document_id, document_kind, path, start_line,
end_line` — заголовок и путь заголовков найденного раздела, какому документу он принадлежит
и на каких строках файла лежит.

### `GET /api/tasks/{id}/context`

Компактный контекст задачи под конкретный этап конвейера — то, что скармливается harness вместо
всей карточки и файлов целиком; ответ побайтно стабилен при одинаковых входных данных (не зависит
от времени вызова).

Параметры: `stage`, `portion`, `max_chars`. **Фактическое поведение HTTP-эндпоинта**: `stage`
необязателен — при отсутствии сервер берёт `s1-spec` (`server.py`: `q1("stage") or "s1-spec"`),
и значение параметра не валидируется: неизвестная строка обрабатывается как «не s1/s2», то есть
по правилам, близким к s3/s4 (без блока `worktree`, с лимитом 24 000 символов). Ожидаемые
значения — `s1-spec`, `s2-review`, `s3-impl`, `s4-judge`. Обязательным и ограниченным этим
списком (`choices`) `stage` является только в CLI (`--stage`, обязателен) и в схеме MCP-инструмента
`listik_context` — гарантии CLI/MCP не переносятся на голый HTTP-вызов, валидация на сервере в
этой версии не добавлена. `portion` — свободный текст (совпадение по заголовку/breadcrumb раздела
ТЗ на s3/s4). `max_chars` — переопределить лимит на суммарный текст выбранных чанков; без
параметра лимит берётся по этапу (`limits.default_for_stage`): 150 000 для `s1-spec`/`s2-review`,
24 000 для `s3-impl`/`s4-judge` (и для любого неизвестного значения `stage`).

Ключи ответа: `task` (карточка без производных «возрастных» полей — то, что меняется со
временем без изменения самих данных, вырезано ради стабильности), `card` (сжатый набор ключевых
полей: id, project, title, status, stage, priority, holder, worktree, branch, четыре пути к
документам, issue_type, labels), `stage`, `portion`, `documents[]` (метаданные документов без
чанков), `chunks[]` (отобранные чанки с `reason` — почему каждый попал в контекст), `acceptance`,
`dependencies` (`hard[]` — жёсткие блокеры, `suggested[]` — предложенные связи, плюс вычисленное
состояние `ready/claimable/can_finish/verdict` и т. д.), `reviews[]`, `verdict`, `journal[]`,
`worktree`, `limits` (`max_chars, default_for_stage, used_chars, truncated, truncated_chunks[],
dropped_chunks, reason`), `reasons[]` (по одному пункту на каждый включённый блок и чанк),
`generated_at` (не время вызова, а `updated_at` задачи — тоже ради стабильности).

По этапам:

- **s1-spec / s2-review** — в контекст целиком входят все документы задачи (`spec`, `checklist`,
  `review`, `decision`), по порядку spec→checklist→review→decision; `reviews[]` пуст на s1-spec
  и содержит все ревью-комментарии на s2-review; `verdict`, `journal`, `worktree` не заполняются.
- **s3-impl** — чек-лист входит целиком первым слоем; дальше — либо совпадение `portion` по
  заголовку/breadcrumb в spec/decision, либо (если `portion` не задан или ничего не нашёл)
  лексический поиск по названию и acceptance задачи; если ни один чанк spec не попал ни одним из
  способов — в контекст добавляется начало spec-документа. `reviews[]` — последний ревью-комментарий;
  `verdict`, `journal`, `worktree` не заполняются.
- **s4-judge** — тот же слоёный отбор чанков, что на s3; дополнительно заполняются `verdict`
  (последний комментарий `kind=verdict`), `journal[]` (комментарии `kind=journal` вперемешку с
  событиями перехода этапов, по времени) и `worktree` (путь рабочего дерева, ветка, HEAD, `git
  status --porcelain`/`git diff --stat` с обрезкой при превышении лимита — если `worktree` в
  карточке не задан или каталог/репозиторий недоступны, в ответе только `exists: false` и `reason`).

### Запись

| Метод | Путь | Тело | Смысл |
|---|---|---|---|
| POST | `/api/tasks` | `title`(обязателен), `project, description, acceptance, design, notes, type, status, priority, assignee, stage, labels[], spec_path, checklist_path, review_path, decision_path, journal_path, external_ref, actor, harness, needs_owner, id` | создать |
| PATCH | `/api/tasks/{id}` | любые из `title, description, acceptance, design, notes, result, status, stage, priority, issue_type, assignee, holder, holder_note, project, labels[], spec_path, checklist_path, review_path, decision_path, journal_path, worktree, branch, close_reason, needs_owner, external_ref, archived` + `actor`, `harness`, `note` | изменить (каждое изменение пишется в events) |
| DELETE | `/api/tasks/{id}` | — | удалить |
| POST | `/api/tasks/{id}/claim` | `holder`(обязателен), `harness`, `note`, `force=false` | взять в работу. 400 по трём причинам: незакрытые жёсткие блокеры (обходится `force`, пишет предупреждение в историю), чужой держатель, занятое рабочее дерево — держатель и рабочее дерево `force` не обходят. `harness` проверяется, только если передан (сверяется с routing проекта на этапе задачи) |
| POST | `/api/tasks/{id}/heartbeat` | `holder`(обязателен), `note` | отметка «жив, работаю» (событие не чаще 10 мин) |
| POST | `/api/tasks/{id}/stage` | `holder`, `note`, `harness` | следующий этап конвейера s1→s2→s3→s4→done, считает длительность прошлого этапа |
| POST | `/api/tasks/{id}/comment` | `text`(обязателен), `author`/`actor`, `kind=comment\|journal\|question\|answer\|review\|verdict`, `harness` | комментарий в журнал задачи; `kind=question`/`answer` — те же виды, что пишет `needs-owner` (см. ниже), их можно оставить и вручную, но сам флаг `needs_owner` они не меняют |
| POST | `/api/tasks/{id}/needs-owner` | `value=true\|false`, `note`, `actor`, `harness` | поднять/снять флаг «нужен человек»: при непустом `note` создаётся комментарий `kind=question` (`value=true`) или `kind=answer` (`value=false`); событие `question`/`answer` пишется при каждом вызове, даже если флаг уже стоит в нужном значении; ответ — полная карточка, как у `PATCH`. `PATCH /api/tasks/{id}` с `needs_owner` меняет только флаг и комментария не пишет |
| POST | `/api/tasks/{id}/release` | `note`, `actor` | освободить задачу |
| POST | `/api/tasks/{id}/done` | `result`, `reason`, `actor`, `note` | закрыть: `status=done`, `stage=done` |
| POST | `/api/tasks/{id}/deps` | `depends_on`, `dep_type=blocks`, `confirm=false`, `actor` | с `depends_on` — добавить связь; без него — дерево зависимостей (`waits_for`/`waited_by`). Жёсткий `dep_type` (`blocks`/`blocked-by`/`waits-for`/`conditional-blocks`) от агентского `actor` без `confirm=true` не ставится сразу жёстким — пишется как `suggested-blocks` (мягкая, ждёт подтверждения человеком); `confirm=true` (или неагентский `actor`) ставит жёсткую связь сразу. Ответ: `dep_type` (фактически записанный тип), `requested_dep_type` (что просили), `suggested`, `confirmed`, `promoted` (предложение заменено на жёсткую связь этим вызовом), `created`, `created_by`. 400 на самосвязь и на цикл жёстких связей |
| DELETE | `/api/tasks/{id}/deps/{depends_on}` | `dep_type` строкой запроса | снять связь; без `dep_type` снимает разом `blocks` и `suggested-blocks` между той же парой задач. Ответ: `removed` (число снятых строк), `dep_types[]` |
| POST | `/api/tasks/{id}/ready` | — | вердикт по задаче (`deps_state`, см. ниже) |
| POST | `/api/tasks/{id}/mentions` | `limit` | задачи, упомянутые в тексте этой задачи, но не связанные с ней |
| POST | `/api/projects` | `path` (каталог репозитория) или `slug`, `title`, `kind=native` | добавить репозиторий на доску; slug по умолчанию — имя каталога, git remote/ветка подтягиваются сами. Существующий slug не падает: проект возвращается на доску и обновляется |
| PATCH | `/api/projects/{slug}` | `title`, `path`, `color`, `kind`, `archived=0/1`, `routing` | правка проекта; `archived=1` — убрать с доски, не теряя задачи; `routing` — объект-переопределение маршрутизации проекта (`{}` сбрасывает его), проверяется `config.validate_routing`: допустимые ключи — `harnesses` (словарь этап → список имён, этапы и имена без дублей), `default_process` (список этапов без дублей), `transitions` (словарь `"<этап>:<этап-или-done>"` → `sticky`\|`handoff`\|`sticky-return`), `return_window_hours` (число > 0); неизвестный ключ или неверная форма — 400 с текстом на русском |
| DELETE | `/api/projects/{slug}` | `force=1` (или в теле) | убрать проект из Listik. Проект с задачами отвечает 409 — их сначала скрывают; `force` удаляет задачи вместе с проектом |
| POST | `/api/embed` | `limit`, `kinds=task,comment,chunk` (по умолчанию все три) | досчитать векторы (ollama bge-m3) |

Статус задачи `claim`/`add_dep`/`remove_dep` сами не меняют: «заблокирована» не статус, а
вычисляемое состояние — `blocked_by` (список незакрытых жёстких блокеров) и `deps_state`
пересчитываются при каждой правке связей и статусов (см. «Зависимости: можно ли брать задачу»).


## Зависимости: можно ли брать задачу

Ответ на вопрос «можно ли начинать» даётся не флагом в базе, а вычисляется по графу связей.
Поэтому «заблокирована» — не статус, который кто-то проставил руками и забыл снять, а состояние,
которое исчезает само, как только закрыт блокер.

**Жёсткие блокеры** (`dep_type`: `blocks`, `blocked-by`, `waits-for`, `conditional-blocks`) —
пока блокер не закрыт (`done`/`cancelled`), задачу брать нельзя.
**Мягкие связи** (`parent-child`, `relates-to`, `related`, `discovered-from`, `duplicates`,
`supersedes`, `suggested-blocks`) — не запрет, но сигнал «сначала прочитай»; отдаются в
`soft_links`. `suggested-blocks` — предложение агентом жёсткой связи, ещё не подтверждённое
человеком (`dep_title`: «предложенный блокер»); на `ready`/`claimable` не влияет.

`parent-child` — отдельный смысл: родитель-эпик закрывается, когда закрыты его дети.
Поэтому `can_finish` (можно ли закрывать) и `ready` (можно ли брать) — разные вопросы.

`deps_state` (в `GET /api/tasks/{id}`, `POST /api/tasks/{id}/ready`, MCP `listik_can_take`):

| Поле | Смысл |
|---|---|
| `ready` | можно брать прямо сейчас: не завершена, нет незакрытых жёстких блокеров, нет держателя |
| `claimable` | то же, но занятость другим держателем не мешает (для «взять, если освободят») |
| `can_finish` | можно ли закрывать: все дети эпика закрыты |
| `verdict` | готовая строка: «можно брать» / «занята другим» / «нельзя: ждёт другие задачи» / «уже завершена» |
| `reasons[]` | почему нельзя, человеческими словами |
| `blocked_by[]` | незакрытые жёсткие блокеры: `id, title, status, holder_title, holder_age, idle_age, stale, missing, dep_title` |
| `waiting_for[]` | кто ждёт завершения этой задачи (за ней стоят другие) |
| `children_open[]` | незакрытые дети (для эпика) |
| `parent` | родитель, если есть |
| `soft_links[]` | мягкие связи с типом |
| `worktree_busy` | `null`, или (если рабочее дерево этой задачи занято другой пишущей задачей) `{id, title, holder, holder_title, holder_age, stale}`; на `ready`/`claimable` не влияет, но добавляет строку в `reasons` |

Правила для агента:

1. Перед работой — `GET /api/ready` (или `listik ready`): там только то, что не ждёт других.
2. Сомневаешься по конкретной задаче — `GET /api/tasks/{id}` → `deps_state.verdict` и `reasons`.
3. `POST .../claim` на заблокированной вернёт 400 с перечнем блокеров. Осознанный обход — `force: true`
   (в историю пишется строка «ЗАПУСК БЕЗ РАЗРЕШЕНИЯ БЛОКЕРОВ»).
4. Блокер стоит без движения (все `stale` или без держателя) — это повод не ждать молча,
   а поставить ему `needs-owner` или взять его самому.
5. Ставя жёсткую зависимость агентским `actor` без `confirm=true` — это только предложение
   (`suggested-blocks`), оно ничего не блокирует, пока человек не подтвердит `dep confirm`
   (или `dep add --confirm`).

## CLI (то же самое, но для агента)

```
listik serve                       # поднять сервер и доску
listik import-beads [--dry-run]    # разовый импорт из старых .beads
listik import-writerllm --source <path> [--project writerllm] [--dry-run] [--update]   # импорт выгрузки bd export WriterLLM, идемпотентно
listik new "Заголовок" -p project --type bug --priority 1 --actor agent:dsh
listik ready                        # что можно взять прямо сейчас
listik ready --harness dsh          # только то, что этому harness разрешено на его этапе
listik blocked                      # кто кого ждёт и почему
listik tree <id>                    # дерево зависимостей задачи
listik dep confirm <id> <блокер>    # подтвердить предложение агента → жёсткая связь
listik dep suggested [--project]    # предложения агентов, ждущие подтверждения человеком
listik projects <slug> [--routing '<json>']   # показать/задать маршрутизацию проекта
listik list --mine --json
listik show <id> [--json]          # полная карточка задачи
listik context <id> --stage s1-spec|s2-review|s3-impl|s4-judge [--portion "текст"] [--max-chars N] [--format text|json]
listik claim <id> --holder dsh/deepseek-flash   # заблокированную, чужую или в занятом дереве не возьмёт, скажет почему
listik claim <id> --holder dsh/deepseek-flash --force   # осознанный обход запрета по блокеру
listik heartbeat <id> --holder dsh/deepseek-flash --note "пишу порцию B"
listik stage <id> --holder dsh/deepseek-flash      # следующий этап
listik comment <id> "текст" --kind journal
listik needs-owner <id> "вопрос автору"
listik needs-owner <id> --clear "ответ"
listik done <id> --result "чем кончилось"
listik search "запрос" [--mode hybrid] [--json]
listik board [--group-by stage]
listik stats
listik timeline
listik embed
```

Глобальные флаги: `--json`, `--actor <кто>`, `--harness <dsh|grok|claude>`, `--server/--port`.
Если сервер не поднят, CLI работает с базой напрямую — команда агента не должна падать из-за
незапущенного сервера.

## Правила работы агента с задачами

Полный протокол harness — `docs/harness-protocol.md` (тот же текст ставится в блок
`<!-- BEGIN LISTIK -->` в `AGENTS.md` проектов командой `listik init-projects`). Здесь — только
то, что относится к самому API: заблокированную задачу `claim` не возьмёт (400 с перечнем
блокеров); переход этапа через `handoff` (`s2→s3`, `s4→done`) снимает держателя, `sticky`
(`s1→s2`, `s3→s4`) — нет; красный вердикт на `s4-judge` сервер сам возвращает на `s3-impl`.
