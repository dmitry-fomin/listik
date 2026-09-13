# Приёмка порции 01.e, заход r1 — красный

Дифф: `API.md`, `README.md`, `listik/mcp.py` (165+/14-). Проверка на временной базе
`/private/tmp/.../scratchpad/t.db`, задача `lk-hqsg` с четырьмя документами
(`spec`/`checklist`/`review`/`decision`), плюс живой сервер на порту 8899 с временной БД
(остановлен, `listik.pid` восстановлен).

## Предусловие 0 — выполнено

`git log --oneline` содержит `39787d7 Шаг 01, порция d: контракт context по этапам`.
`listik --local context lk-hqsg --stage s4-judge --format json`:

```
['acceptance', 'card', 'chunks', 'dependencies', 'documents', 'generated_at', 'journal',
 'limits', 'portion', 'reasons', 'reviews', 'stage', 'task', 'verdict', 'worktree']
limits: [..., 'default_for_stage', ...]  deps: [..., 'hard', ..., 'suggested', ...]
```

`verdict`, `worktree`, `limits.default_for_stage`, `dependencies.hard/suggested` есть,
`verdicts` нет. Форма новая, приёмка проводится.

## Красные пункты

### R1. `API.md:86-87` — ложное утверждение, что `show` переиндексирует документы

> «Переиндексация происходит при вызове `context`/`show`/`create`/`update` для задачи…»

`show` не переиндексирует: `store.get_task` (`listik/store.py:459-465`) только читает таблицу
`documents`, вызовов `index_task_documents` в пути `show` нет (grep: только `store.py:201`
create и `store.py:311` update). Живая проверка — дописал раздел в `spec.md`:

```
show после правки:    [('checklist',1,2), ('decision',1,1), ('review',1,1), ('spec',1,3)]
context после правки: [('spec',2), ('checklist',1), ('review',1), ('decision',1)]
show снова:           [('checklist',1,2), ('decision',1,1), ('review',1,1), ('spec',2,4)]
```

Ревизия изменилась только после `context`. Это не случайность, а зафиксированное решение
порции b (`docs/specs/steps/step-01.b.md:79-80`): «Файлы при `show` **не читаются** и
переиндексация не запускается: `show` только показывает состояние индекса». `API.md` —
авторитетный контракт, и здесь он расходится с кодом и с решением предыдущей порции.

### R2. `README.md:84` и `listik/mcp.py:124-125` — «на s3/s4» вместо «на s4»

`README.md:84`: «…а на s3/s4 ещё и последний вердикт, журнал и состояние рабочего дерева».
`listik/mcp.py:124-125` (описание `listik_context`): «…вместо файлов целиком; на s3/s4 ещё и
последний вердикт, журнал и состояние рабочего дерева».

Код (`listik/documents.py:640-643`) заполняет `verdict`/`journal`/`worktree` только при
`stage == "s4-judge"`. Живая проверка на задаче с комментариями `-k verdict` и `-k journal`:

```
s3-impl  verdict= None                journal= 0  worktree= None
s4-judge verdict= «вердикт: зелёный»  journal= 1  worktree= {'exists': False, ...}
```

Тот же дифф в `API.md` (раздел «По этапам») описывает это верно — то есть README и описание
MCP-инструмента расходятся и с кодом, и с собственным `API.md` порции.

## Зелёные пункты чек-листа

1. **зелёный.** Строка `GET /api/tasks/{id}/context` есть в таблице «Чтение» (`API.md:103`) с
   параметрами `stage`, `portion`, `max_chars`; дефолты лимита (150 000 s1/s2, 24 000 s3/s4) —
   в разделе ниже (`API.md:118-120`), на который строка ссылается «см. ниже». Про `stage`
   написано фактическое поведение HTTP (`API.md:110-117`): необязателен, при отсутствии
   `s1-spec`, не валидируется; обязателен и ограничен `choices` только в CLI и MCP. Сверено с
   `listik/server.py:328` (`q1("stage") or "s1-spec"`) и живыми запросами к серверу на временной
   базе: без `stage` → HTTP 200, `stage == "s1-spec"`, `default_for_stage == 150000`;
   `?stage=bogus` → HTTP 200, лимит 24 000, `worktree == None`. Формулировки «обязателен» для
   HTTP в тексте нет.
2. **зелёный.** Ключи ответа, перечисленные в `API.md:122-131`, — ровно 15 и совпадают с
   `sorted(d.keys())` живого `context --stage s4-judge --format json` (список выше). Есть
   `verdict`, `worktree`, `limits.default_for_stage`, `dependencies.hard`, `dependencies.suggested`;
   `verdicts` нет ни там, ни там.
3. **зелёный.** `API.md:133-147` — по абзацу на этап: s1/s2 (все документы целиком, порядок
   spec→checklist→review→decision — сверено с `_DOC_KIND_ORDER`, `documents.py:23`), s3 (чек-лист
   целиком → совпадение `portion` по heading/breadcrumb → лексический фолбэк по title+acceptance →
   начало spec; сверено с `_layered_chunks`, `documents.py:511-584`), s4 (`worktree`, `verdict`,
   `journal` с событиями переходов — `_journal_items`, `documents.py:484`).
4. **зелёный.** `API.md:61-68`: описаны `spec_path`/`checklist_path`/`review_path`/`decision_path`/
   `journal_path` (таблица модели, `API.md:63-67` диффа) и `documents[]` в `GET /api/tasks/{id}`.
   Список полей `id, kind, path, revision, content_hash, title, updated_at, status, error,
   chunk_count` побайтно совпадает с живым
   `sorted(show --json ["documents"][0].keys())` = `['chunk_count','content_hash','error','id',
   'kind','path','revision','status','title','updated_at']`. `checked_at` в списке нет, оговорка
   «есть только в таблице `documents` и в ответы API не попадает» — `API.md:66-68`.
5. **зелёный (кроме фразы из R1).** Подраздел «Документы и чанки» (`API.md:70-88`): четыре вида,
   `content_hash`/`revision`, `status=missing`, `document_error` один раз и `document_restored`
   при возврате файла (сверено с `index_document`, `documents.py:249-268` и `:328`), фоновая
   перепроверка воркером (`server.py:80` → `documents.refresh_all`, `documents.py:351-386`,
   незакрытые задачи с хотя бы одним путём — описано верно).
6. **зелёный.** `API.md:97-102` — `best_hit` и поля хита чанка; живой `search --json` даёт
   `hits[0].keys() = ['author','breadcrumb','doc_id','document_id','document_kind','end_line',
   'heading','kind','path','rrf','snippet','start_line']` — перечисленные в `API.md` поля
   присутствуют. `POST /api/tasks` и `PATCH` (`API.md:177-178` диффа) содержат `checklist_path`,
   `review_path`, `decision_path`. `POST /api/embed` — ровно одна строка в файле (`grep -n
   "api/embed" API.md` → `192`), `kinds=task,comment,chunk`, что совпадает с
   `server.py:411` (`body.get("kinds", "task,comment,chunk")`).
7. **зелёный.** Раздел CLI, `API.md` (строка `listik context <id> --stage s1-spec|s2-review|
   s3-impl|s4-judge [--portion "текст"] [--max-chars N] [--format text|json]`).
8. **зелёный.** `README.md:76-91`: примеры `context --stage s2-review` и
   `--stage s3-impl --portion "Раздел 2"`, `new --spec`, `set <id> checklist_path=…`,
   `review_path=… decision_path=…`.
9. **зелёный.** `listik_context` в списке инструментов (`README.md`, раздел «Агентам: MCP и CLI»);
   `documents`, `document_chunks`, `document_chunk_fts` в «Структуре базы»; раздел «Тесты» с
   `python3 -m unittest discover tests -v`.
10. **зелёный.** Прогнаны на временной базе: `new --spec/--checklist/--review/--decision`,
    `set … checklist_path=/review_path=/decision_path=`, `context --stage s2-review --format json`
    (8 чанков, четыре вида), `context --stage s3-impl --portion "Раздел 2" --format json`
    (чек-лист целиком + чанк «Раздел 2» с reason «совпадение с порцией»), `context --stage s1-spec`
    (текстовый формат), `search "лимиты" --mode text` (в выводе строка `раздел: ТЗ примера /
    Раздел 1 (spec …:5)` — ровно то, что обещает README). Ошибок нет.
11. **зелёный.** `tools/list` через stdio: `listik_context` присутствует, схема
    `required: ["id","stage"]`, `stage` — enum из четырёх значений, `portion` (string) и
    `max_chars` (integer) необязательны.
12. **зелёный.** `tools/call listik_context {id, stage:"s4-judge"}` вернул JSON с тем же набором
    из 15 ключей, что CLI `context --format json`; без `max_chars` —
    `limits.max_chars == limits.default_for_stage == 24000`.
13. **зелёный (кроме фразы из R2 в описании `listik_context`).** Описание `listik_show` упоминает
    `documents[]`; описания `listik_create` и `listik_update` содержат `checklist_path`,
    `review_path`, `decision_path` (проверено разбором живого `tools/list`).
14. **зелёный.** `bin/listik-codex:24` — `run("context", args.task_id, "--stage", args.stage,
    "--format", "json")`, совпадает с контрактом CLI; файл не менялся и в диффе его нет.
15. **зелёный.** `git diff --stat HEAD` = `API.md`, `README.md`, `listik/mcp.py`. `AGENTS.md`,
    `CLAUDE.md`, `docs/harness-protocol.md`, `documents.py`, `search.py`, `server.py`, `store.py`,
    `bin/listik`, `tests/` не тронуты.
16. **зелёный.** `git diff | grep -i token` — пусто; `password|secret|api_key|bearer` — пусто.
    Содержимого `config.toml` и личных путей в диффе нет.
17. **зелёный.** `python3 -m unittest discover tests` — `Ran 64 tests … OK`.

## Замечания (не красные, на усмотрение исполнителя)

- В перечне полей `limits` (`API.md:129-130`) не упомянут реально отдаваемый ключ `applies_to`
  (`documents.py:672`). Чек-лист сверки по `limits` не требует, но список неполон.
- «если `worktree` в карточке не задан или каталог/репозиторий недоступны, в ответе только
  `exists: false` и `reason`» (`API.md:146-147`): для «не git-репозиторий» код отдаёт
  `exists: true, git: false, reason` (`documents.py:451-452`), а для отсутствующего каталога —
  ещё и `path`.

## Чистота дерева после проверки

Временный сервер на 8899 остановлен, `listik.pid` восстановлен из бэкапа, рабочая база
`listik.db` не затрагивалась (все прогоны с `LISTIK_DB=` во временном каталоге).
`git status --porcelain` — те же три изменённых файла, что и до проверки.
