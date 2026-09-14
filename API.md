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
| `holder_note` | str? | что именно держащий делает сейчас; сбрасывается при смене держателя (release, claim другим, handoff, истечение окна возврата), повторный claim тем же держателем её сохраняет |
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
| `autostart` | bool | галочка «запустить сразу»: процесс задачи поднимает сам сервер Listik |
| `launch_route` | str? | ключ маршрута из `routes.json`, по которому запускать |
| `launched_by` | str? | `listik`, если процесс запустил сервер; иначе `null` |
| `launch_pid` | int? | PID запущенного процесса |
| `launched_at` | str? | когда запустили, ISO-8601 UTC |
| `launch_log` | str? | абсолютный путь к логу процесса |
| `launch_exit_code` | int? | код выхода; `null` — процесс идёт или код неизвестен (слежение потеряно) |
| `launch_finished_at` | str? | когда процесс завершился или когда слежение потеряно |
| `launch_error` | str? | почему не запустили; `null` — запуск был или его не пытались |
| `source` | str | `native` \| `beads` \| `writerllm` (импортированные задачи) |
| `external_ref` | str? | старое ID в beads/WriterLLM |
| `created_at`/`updated_at`/`started_at`/`closed_at` | str | ISO-8601 UTC |
| `stage_title`/`status_title`/`holder_title`/`assignee_title`/`holder_age`/`stage_age`/`updated_age` | str | готовые к показу подписи |

`GET /api/tasks/{id}` при `details=1` (по умолчанию включено) добавляет: `comments[]`
(`id, author, kind, text, created_at`), `dependencies[]`, `dependents[]`, `events[]`
(`ts, kind, from_value, to_value, actor, harness, note, duration_s`) и `documents[]` — по одной
записи на каждый индексируемый документ задачи (`spec_path`/`checklist_path`/`review_path`/
`decision_path`/`journal_path`), с полями `id, kind, path, source, revision, content_hash, title,
updated_at, status, error, chunk_count`. Колонка `checked_at` (время последней фоновой
перепроверки файла) есть только в таблице `documents` и в ответы API не попадает — это
решение ради побайтной стабильности `context` (см. ниже).

Поля запуска (девять колонок `autostart`…`launch_error`) пишут только:

| Писатель | Поля |
|---|---|
| `store.create_task` (POST `/api/tasks`) | `autostart`, `launch_route` |
| `listik/launcher.py: start` (сервер) | `launched_by`, `launched_at`, `launch_pid`, `launch_log`, `launch_error`, плюс `needs_owner` через `set_needs_owner` |
| поток слежения за процессом и `recover` при старте сервера | `launch_exit_code`, `launch_finished_at` |
| локальный фолбэк CLI (`client.local_call`, ветка `create`) | `launch_error`, плюс `needs_owner` |

Ни одно из девяти полей не входит в белый список `PATCH /api/tasks/{id}`
(`store.UPDATABLE`): правкой карточки их изменить нельзя.

## Маршруты запуска (routes.json) и автостарт

Маршруты — это таблица пресетов конвейера и отдельных исполнителей; она же даёт команду
для автостарта. Источник — `routes.json` в корне Listik (образец без `command`), при старте
сервера он один раз копируется в `~/.config/listik/routes.json` (или в `$LISTIK_ROUTES`,
если переменная задана). Уже существующая копия не перезаписывается: команды автор
вписывает в неё. Сервер читает файл только при старте (`routes.init_at_startup`), поэтому
правка `routes.json` на ходу ничего не меняет до перезапуска.

Формат (версия 1): `{"version": 1, "routes": [ {...}, ... ]}`. Лишние поля — ошибка.
Запись: `key` (`^[a-z0-9][a-z0-9-]*$`, уникален), `kind` (`pipeline` или `direct`),
`title`, `hint`, `visible` (именно JSON `true`/`false`); у `pipeline` обязателен `roles`
(`spec`/`critic`/`impl`/`judge`), у `direct` — `harness`; необязательный `strip` и
необязательный `command` — непустой массив непустых строк, argv процесса.

В `command` допустимы только подстановки `{task_id}`, `{project}` (пусто, если проекта
нет), `{route}`, `{cwd}`, `{title}`; любая другая фигурная скобка — ошибка проверки.
Подстановка однопроходная: значение занимает место целиком в элементе массива, а
фигурные скобки внутри значения (`{task_id}` в `title`) остаются как есть. Shell не
используется (`shell=True` нигде нет), команда берётся только из `routes.json` — поле
`command` в теле POST `/api/tasks` игнорируется.

Если файл не прошёл проверку (нет файла, битый JSON, ошибка формата), автостарт выключен
целиком: `GET /api/routes` и `GET /api/health` отдают `ok=false` и текст ошибки, а задача
с `autostart` создаётся, но не запускается — `launch_error` начинается с
`routes.json с ошибкой`, поднимается `needs_owner`. Отказы запуска (проверяются по порядку,
состояние задачи меняется одинаково — `launch_error`, `needs_owner`, строка в stderr):

| Отказ | `launch_error` |
|---|---|
| `routes.json` не загружен или с ошибкой | `routes.json с ошибкой: <текст>` |
| ключа `launch_route` нет среди записей (`visible:false` запуску не мешает) | `маршрута <key> нет в routes.json` |
| у записи нет `command` | `у маршрута <key> нет command в routes.json` |
| нет рабочего каталога: `worktree` задачи пуст и `path` проекта пуст, либо каталога нет на диске | `нет рабочего каталога (worktree или path проекта <slug>)` |
| `Popen` бросил `OSError` (нет бинарника и т. п.) | `не удалось запустить: <ошибка>` |

Рабочий каталог (`cwd`) — `worktree` задачи, если он непустой, иначе `path` её проекта.
Ошибка запуска не отменяет создание задачи: POST отвечает `201`, а не `500`.

Отдельный случай — повторный запуск: задача захватывается условным
`UPDATE … SET launched_by='listik' WHERE id=? AND launched_by IS NULL`, поэтому `start`
для уже запущенной задачи возвращает `уже запущена Listik` и **ничего** не меняет —
ни `launch_error`, ни `needs_owner`, ни комментариев; в stderr только строка
`autostart <id>: уже запущена Listik`. Так же ведёт себя гонка двух одновременных
запусков: процесс и лог-файл ровно одни.

Успешный запуск пишет `launched_by=listik`, `launch_pid`, `launched_at`, `launch_log`
(файл `logs/launch-<id>-<ГГГГММДДТЧЧММССZ>.log` в корне Listik, каталог создаётся),
`launch_error=NULL` и комментарий `journal` от `agent:listik`: `автостарт: маршрут <key>,
pid <N>, лог <path>`. Процесс не блокирует запрос: POST возвращается сразу после `Popen`.
Поток-демон дожидается процесса и пишет `launch_exit_code`, `launch_finished_at` и
комментарий `автостарт: процесс <pid> завершился с кодом <code>`; этап, держатель и статус
не меняются — запуск не делает claim за агента. После перезапуска сервера `launcher.recover`
проверяет задачи с `launched_by=listik`, PID которых ещё не завершён: мёртвый процесс
(`ProcessLookupError`) получает `launch_finished_at` и журнал «отслеживание потеряно при
перезапуске сервера» (`launch_exit_code` остаётся `NULL`), живой (в том числе
`PermissionError`) не трогается. Переиспользованный PID считается живым — принятый риск.

`command` наружу не отдаётся: его нет ни в `GET /api/routes`, ни в `/api/health`.

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

Документ может быть не только файловым. `PUT /api/tasks/{id}/documents/{kind}` (и MCP
`listik_put_document`) принимает текст телом запроса и хранит его в базе: у такого документа
`source=upload`, а сам текст лежит в колонке `documents.content`. Загруженный документ читается
из базы и на диск не ходит вообще — даже если путь в карточке указывает на несуществующий файл:
переиндексация для `source=upload` берёт текст из базы, поэтому в `status=missing` такой документ
не переходит и события `document_error` по нему не пишутся. Файловый документ, как и раньше,
имеет `source=file` и читается с диска. Ключ `source` (`file` или `upload`) есть в каждой записи
`documents[]` карточки и в `documents[]` ответа `context`.

Если у задачи нет пути к документу этого вида, `PUT` (и MCP `listik_put_document` без `path`)
присваивает документу виртуальный путь `listik://<id>/<kind>.md` — он записывается в
соответствующее поле карточки (`spec_path`, `checklist_path`, `review_path`, `decision_path`) и
адресует документ в таблице `documents`. На диске такого файла нет и быть не должно: для
`source=upload` путь — только идентификатор записи, а не файл. Повторная загрузка того же текста
по тому же пути ревизию не меняет (см. «Запись»).

## Эндпоинты

### Чтение

| Метод | Путь | Параметры | Ответ |
|---|---|---|---|
| GET | `/api/health` | — | `status, version, embed{model}, now, authed`; авторизованному — ещё `db`, `counts`, `embed{ok,models}`, `routes{ok,error,path,count}` и `db_error{where,error,at}` — только если последний фоновый проход упал с `sqlite3.DatabaseError` (см. ниже) |
| GET | `/api/routes` | — | `ok, error, path, routes[]` — записи `routes.json`, загруженные при старте, без `command` (см. «Маршруты запуска»); ошибка файла — `ok=false` и текст, а не HTTP-ошибка |
| GET | `/api/meta` | `archived` | `projects[], actors[], facets{}, statuses{}, stages{}, priorities{}` |
| GET | `/api/projects` | — | `projects[]` — все репозитории доски, включая скрытые: `slug, title, kind, path, path_exists, git_remote, git_branch, archived, n_tasks, n_open, n_wip`, плюс `routing` (переопределение проекта — объект или `null`), `routing_effective` (действующая слитая таблица, которой реально пользуются `allowed_harnesses`/`transition_kind`), `routing_source` (`default`\|`config`\|`db`\|`config+db`), плюс `root` (корень поиска проектов) |
| GET | `/api/stats` | `project` | `by_status{}, by_stage{}, by_project[], by_holder[], by_actor[], stale, needs_owner, closed_7d, closed_prev_7d, closed_delta, closed_by_day[{date,count}] (14 дней), long_stage, running[], generated_at` |
| GET | `/api/board` | `group_by=status\|stage\|project\|holder`, `project`, `include_closed`, `limit` | `group_by, columns[], total, needs_you[], generated_at` |
| GET | `/api/tasks` | `project,status,stage,assignee,holder,needs_owner,type,label,text,include_closed,include_archived,limit,offset,order=updated\|created\|priority\|stage` | `total, limit, offset, tasks[]` |
| GET | `/api/tasks/{id}` | `details=0/1` | задача + `comments/dependencies/dependents/events/documents` |
| GET | `/api/tasks/{id}/context` | `stage`, `portion`, `max_chars` | компактный, побайтно стабильный контекст этапа для harness — см. ниже |
| GET | `/api/tasks/{id}/documents/{kind}` | — (вид документа задан в пути: `spec`, `checklist`, `review`, `decision`) | документ задачи содержимым: `task_id, kind, path, source, revision, content_hash, status, error, content`. `source=upload` — текст из базы (`status=ok`); `source=file` — с диска: `status=ok` и текст, а если файл не читается — `status=missing`, `content=null` и текст ошибки (`revision`/`content_hash` = `null`, если документ ещё не индексировался). 404 — нет такой задачи или у задачи не задан путь к документу этого вида; 400 — неизвестный `kind`; 405 — любой метод по этому пути, кроме `GET` и `PUT` |
| GET | `/api/search` | `q` (обязателен), `limit`, `project`, `status`, `stage`, `actor`, `needs_owner`, `mode=hybrid\|text\|vector` | `query, mode, took_ms, lexical_docs, vector_docs, count, results[]` |
| GET | `/api/ready` | `project`, `stage`, `harness` (только задачи, чей этап разрешён этому harness в routing проекта; задача без этапа — всем), `include_occupied`, `limit` | `tasks[]` (можно брать: нет незакрытых блокеров и держателя), `cycles[]` |
| GET | `/api/blocked` | `project`, `limit` | `tasks[]` с разбором `blockers[]`, `blocked_by_stale`, `blocked_by_holder` |
| GET | `/api/deps/suggested` | `project`, `limit` | `items[]` (предложения агентов, ждущие подтверждения человеком: `issue_id, issue_title, issue_stage, project, depends_on, depends_on_title, depends_on_status, created_by, created_at`), `generated_at` |
| GET | `/api/timeline` | `limit` | `items[]`: `ts, kind, from_value, to_value, actor, actor_title, harness, note, duration_s, task_id, title, project, stage, status, age` |
| GET | `/api/events` | `limit` | сырые события |
| GET | `/api/stream` | `token` (обязателен) | SSE: `data: {"kind":"task","at":...,"payload":{"id":...,"action":"updated"}}`, плюс `: ping` каждые 15 с |

`GET /api/health` без токена отдаёт только пробу живости (`status`, `version`, `embed.model`,
`now`, `authed`) — по ней CLI понимает, поднят ли сервер; подробности (`db`, `counts`,
`embed.ok`, `routes`) только авторизованному запросу.

`db_error` — здоровье базы в фоновом потоке сервера, а не история. Поле появляется в ответе
авторизованному запросу, когда проход `documents` или `embed` (раз в 45 с) поймал
`sqlite3.DatabaseError` — например, «database disk image is malformed», если базу или её WAL
подменили под работающим сервером. Формат: `{where, error, at}` — шаг (`documents` или `embed`),
текст `<Тип>: <сообщение>` и время события, ISO-8601 UTC. Первый же проход, в котором база ни
разу не упала, снимает поле: после восстановления базы health снова отвечает без `db_error`, а
не показывает старую ошибку (listik-9csm). Ошибки, не связанные с базой (недоступный ollama,
нечитаемый файл документа), поля не создают и снять его не мешают — важно только, падала ли в
проходе сама база. `listik status` печатает `db_error` отдельной строкой
`база: ОШИБКА в фоне …`, если поле есть в ответе.

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
| POST | `/api/tasks` | `title`(обязателен), `project, description, acceptance, design, notes, type, status, priority, assignee, stage, labels[], spec_path, checklist_path, review_path, decision_path, journal_path, external_ref, actor, harness, needs_owner, id, autostart, route` | создать. `autostart: true` сразу запускает процесс по маршруту `route` (см. «Маршруты запуска»): ответ — `201` с перечитанной задачей, отказ запуска не отменяет создание и не даёт `500`. `autostart: true` без непустого `route` — `400`, задача не создаётся; `route` без `autostart` просто сохраняется в `launch_route` |
| PATCH | `/api/tasks/{id}` | любые из `title, description, acceptance, design, notes, result, status, stage, priority, issue_type, assignee, holder, holder_note, project, labels[], spec_path, checklist_path, review_path, decision_path, journal_path, worktree, branch, close_reason, needs_owner, external_ref, archived` + `actor`, `harness`, `note` | изменить (каждое изменение пишется в events) |
| DELETE | `/api/tasks/{id}` | — | удалить |
| PUT | `/api/tasks/{id}/documents/{kind}` | `content` (обязателен, строка не длиннее 1 000 000 символов), `path`, `actor` | принять текст документа и хранить его в базе (`source=upload`) — для сервера, где файлов проектов нет. Путь выбирается по шагам, ровно в этом порядке: 1) непустой `path` из тела; 2) иначе — уже записанный в карточке путь этого вида (`spec_path`/`checklist_path`/`review_path`/`decision_path`); 3) иначе, для `decision`, — `journal_path`; 4) иначе — виртуальный `listik://<id>/<kind>.md`. В случаях 1 и 4 выбранный путь дописывается в карточку. `revision` растёт только при смене текста (новая запись — сразу `revision=1`); при новой записи и при смене текста пишется событие `document_uploaded` с пометкой `r<revision>`; повтор с тем же текстом ревизию не меняет и события не создаёт. 400 — неизвестный `kind`, не передан или не строка `content`, текст длиннее 1 000 000 символов, не строка `path`; 404 — нет такой задачи; 405 — любой метод по этому пути, кроме `GET` и `PUT` |
| POST | `/api/tasks/{id}/claim` | `holder`(обязателен), `harness`, `note`, `force=false` | взять в работу. 400 по трём причинам: незакрытые жёсткие блокеры (обходится `force`, пишет предупреждение в историю), чужой держатель, занятое рабочее дерево — держатель и рабочее дерево `force` не обходят. `harness` проверяется, только если передан (сверяется с routing проекта на этапе задачи) |
| POST | `/api/tasks/{id}/heartbeat` | `holder`(обязателен), `note` | отметка «жив, работаю» (событие не чаще 10 мин) |
| POST | `/api/tasks/{id}/stage` | `holder`, `note`, `harness` | следующий этап конвейера s1→s2→s3→s4→done, считает длительность прошлого этапа |
| POST | `/api/tasks/{id}/comment` | `text`(обязателен), `author`/`actor`, `kind=comment\|journal\|question\|answer\|review\|verdict`, `harness` | комментарий в журнал задачи; `kind=question`/`answer` — те же виды, что пишет `needs-owner` (см. ниже), их можно оставить и вручную, но сам флаг `needs_owner` они не меняют; `kind=verdict` — первая строка ровно `VERDICT: PASS` или `VERDICT: FAIL` (после `FAIL` — список правок), иначе 400 |
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

Загрузка документа (`PUT .../documents/{kind}`) пишет строку в `documents` до того, как путь
допишется в карточку, — поэтому переиндексация внутри `PATCH /api/tasks/{id}` уже видит
`source=upload` и не идёт на диск. Если документ с этим путём раньше индексировался из файла и
присланный текст совпадает с ним по `content_hash`, ревизия не меняется и `document_uploaded` не
пишется, но `source` становится `upload` и текст сохраняется в базе.

`GET` и `PUT` — единственные методы у `/api/tasks/{id}/documents/{kind}`: `POST`, `PATCH` и
`DELETE` по этому пути отвечают `405` с текстом «метод не поддерживается» (заголовок `Allow` в
этом ответе не выставляется).

Статус задачи `claim`/`add_dep`/`remove_dep` сами не меняют: «заблокирована» не статус, а
вычисляемое состояние — `blocked_by` (список незакрытых жёстких блокеров) и `deps_state`
пересчитываются при каждой правке связей и статусов (см. «Зависимости: можно ли брать задачу»).


## MCP по HTTP (/mcp)

Тот же MCP-сервер Listik, что и локальный stdio (`bin/listik mcp`), но доступный по сети: агент
подключается к серверу и работает с задачами, не имея на своей машине ни базы, ни репозиториев
проектов.

**Транспорт.** `POST /mcp`, Streamable HTTP в минимальном виде: один POST — ровно одно
JSON-RPC-сообщение, ответ — обычный JSON (`Content-Type: application/json`), без SSE-потока и без
сессий (`Mcp-Session-Id` не выдаётся и не проверяется). Запрос:

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "listik_ready", "arguments": {}}}
```

Поддержаны методы `initialize`, `notifications/initialized`, `tools/list`, `tools/call`, `ping`;
на неизвестный метод с `id` приходит JSON-RPC-ошибка `-32601`. Сообщение разбирает тот же код,
что и в stdio (`mcp.handle`), только соединение с базой берётся на время запроса.

| Код | Когда |
|---|---|
| 200 | обычный JSON-RPC-ответ, в том числе `result` с `isError: true` (ошибка инструмента — HTTP-код при этом 200) |
| 202 | уведомление без `id` (`notifications/initialized`) — тело пустое |
| 400 | сообщение не разбирается: не JSON (`-32700`), пакет (`-32600`), не объект или без `method` (`-32600`) |
| 401 | нет или неверный токен: `WWW-Authenticate: Bearer realm="listik"`, соединение закрывается |
| 403 | запрос с заголовком `Origin` (защита от DNS rebinding), соединение закрывается |
| 405 | `GET`, `PUT`, `PATCH`, `DELETE /mcp`; в ответе `Allow: POST` |
| 413 | `Content-Length` больше 5 МБ: тело не читается, соединение закрывается |
| 500 | исключение при разборе сообщения — JSON-RPC-ошибка `-32603` |

`GET /.well-known/*` (OAuth-метаданные, которые ищет MCP-клиент) отдаёт `404` JSON-ом: Listik
не притворяется OAuth-сервером.

**Авторизация.** Тот же токен, что у доски и API (`[auth] token` в `config.toml`): заголовок
`Authorization: Bearer <token>` или `X-Listik-Token: <token>`. `?token=` в строке запроса здесь
не принимается (в отличие от `/api/*`). Если токен в конфиге пуст, проверка не применяется — так
сервер работает только на `127.0.0.1`, наружу его отдаёт обратный прокси (см. README).

**Согласование версий.** В `initialize` клиент присылает `protocolVersion`; сервер отвечает той
же версией, если знает её: `2025-06-18`, `2025-03-26`, `2024-11-05`. Неизвестная или
отсутствующая версия — ответ самой свежей из поддерживаемых (`2025-06-18`). `capabilities` —
только `tools`, `serverInfo` — `{"name": "listik", "version": "0.1.0"}`.

**Инструменты.** Полный список отдаёт `tools/list`; ниже — фактические `TOOLS` из
`listik/mcp.py`, обязательные параметры — ровно `inputSchema.required`:

| Инструмент | Обязательные параметры | Эндпоинт или команда CLI |
|---|---|---|
| `listik_search` | `query` | `GET /api/search` (`listik search`) |
| `listik_list` | — | `GET /api/tasks` (`listik list`) |
| `listik_show` | `id` | `GET /api/tasks/{id}` (`listik show`) |
| `listik_create` | `title` | `POST /api/tasks` (`listik new`) |
| `listik_update` | `id`, `fields` | `PATCH /api/tasks/{id}` (`listik set`) |
| `listik_context` | `id`, `stage` | `GET /api/tasks/{id}/context` (`listik context`) |
| `listik_put_document` | `id`, `kind`, `content` | `PUT /api/tasks/{id}/documents/{kind}` (CLI нет) |
| `listik_get_document` | `id`, `kind` | `GET /api/tasks/{id}/documents/{kind}` (CLI нет) |
| `listik_claim` | `id`, `holder` | `POST /api/tasks/{id}/claim` (`listik claim`) |
| `listik_heartbeat` | `id`, `holder` | `POST /api/tasks/{id}/heartbeat` (`listik heartbeat`) |
| `listik_stage` | `id` | `POST /api/tasks/{id}/stage` (`listik stage`) |
| `listik_comment` | `id`, `text` | `POST /api/tasks/{id}/comment` (`listik comment`) |
| `listik_needs_owner` | `id` | `POST /api/tasks/{id}/needs-owner` (`listik needs-owner`) |
| `listik_done` | `id` | `POST /api/tasks/{id}/done` (`listik done`) |
| `listik_ready` | — | `GET /api/ready` (`listik ready`) |
| `listik_blocked` | — | `GET /api/blocked` (`listik blocked`) |
| `listik_can_take` | `id` | `POST /api/tasks/{id}/ready` (`listik show` → `deps_state`) |
| `listik_dep_tree` | `id` | `POST /api/tasks/{id}/deps` без `depends_on` (`listik tree`) |
| `listik_board` | — | `GET /api/board` (`listik board`) |
| `listik_stats` | — | `GET /api/stats` (`listik stats`) |
| `listik_deps` | `id`, `depends_on` | `POST /api/tasks/{id}/deps`, `DELETE /api/tasks/{id}/deps/{depends_on}` (`listik dep add/rm`) |
| `listik_release` | `id` | `POST /api/tasks/{id}/release` (`listik release`) |
| `listik_inbox` | — | `GET /api/board` → `needs_you` (`listik inbox`) |
| `listik_memory` | — | `GET /api/memory` (`listik memory`) |
| `listik_remember` | `text` | HTTP-эндпоинта нет; `listik remember` пишет в базу на самой машине |
| `listik_projects` | — | `GET /api/projects` (`listik projects`) |
| `listik_actors` | — | `GET /api/meta` → `actors[]` (`listik actors`) |
| `listik_timeline` | — | `GET /api/timeline` (`listik timeline`) |
| `listik_deps_suggested` | — | `GET /api/deps/suggested` (`listik dep suggested`) |
| `listik_cycles` | — | — (`listik cycles` — только локально; `cycles[]` есть и в `GET /api/ready`) |

**Событие доске.** После успешного `tools/call` пишущего инструмента (`listik_create`,
`listik_update`, `listik_claim`, `listik_heartbeat`, `listik_stage`, `listik_comment`,
`listik_needs_owner`, `listik_done`, `listik_release`, `listik_deps`, `listik_put_document`)
сервер публикует событие для доски. Событие уходит уже после ответа и на сам ответ не влияет;
ответ с `isError: true` события не создаёт.

**Чего через MCP нет.** Администрирование намеренно оставлено только локальному CLI на самой
машине: проекты — добавление на доску (`POST /api/projects`), правка и скрытие
(`PATCH /api/projects/{slug}`, `archived=1`) и удаление (`DELETE /api/projects/{slug}`) — вместе с
их routing; удаление задач (`DELETE /api/tasks/{id}`); импорты (`listik import-beads`,
`listik import-writerllm`); пересчёт векторов (`POST /api/embed`, `listik embed`); серверные
команды `listik serve`, `listik stop`, `listik status`, `listik init` и `listik token`; раскладка
блока протокола по чужим `AGENTS.md`/`CLAUDE.md` (`listik init-projects`). Из проектов через MCP
доступно только чтение — `listik_projects`.

**`confirm` у `listik_deps`.** Инструмент принимает `confirm` и технически может поставить
жёсткую связь сразу, но по протоколу harness `confirm` ставится только по указанию человека:
без него жёсткая связь от агента записывается предложением `suggested-blocks` и ждёт
`listik dep confirm` (см. «Зависимости: можно ли брать задачу»).

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
listik new "Заголовок" -p project --autostart --route low-pipeline   # сразу запустить по маршруту (--autostart без --route — ошибка)
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
(`s1→s2`, `s3→s4`) — нет; вердикт `VERDICT: FAIL` на `s4-judge` сервер сам возвращает на `s3-impl`.
