# Порция 01.d. Контракт `context` по этапам

## Контекст

`listik context <id> --stage s1-spec|s2-review|s3-impl|s4-judge [--portion X] [--max-chars N]
[--format text|json]` собирает для harness компактный контекст задачи. Реализация —
`documents.context(conn, task_id, stage, portion=None, max_chars=24000)` в
`listik/documents.py`; вызывается из `GET /api/tasks/{id}/context` (`listik/server.py`),
локального фолбэка `client.local_call("context", …)` (`listik/client.py`) и CLI `cmd_context`
(`bin/listik`). Ответ сейчас: `task, card, stage, portion, documents, chunks, acceptance,
reviews, verdicts, journal, dependencies, limits, reasons, generated_at`; повторный вызов
побайтно стабилен (`generated_at` — это `tasks.updated_at`, поля возраста вырезаны функцией
`stable`). Это свойство сохранить.

Что уже есть и на что опираться: `store.get_task` (карточка с `deps_state` из `deps.ready` —
там `blocked_by`, `waiting_for`, `children_open`, `soft_links`, `verdict`), комментарии видов
`review|verdict|journal`, события `kind='stage'` с `from_value/to_value` в таблице `events`,
поля `tasks.worktree`/`tasks.branch`, хелпер `store._git_value(path, *args)` (запуск git с
таймаутом, ошибки не пробрасываются), `document_chunk_fts` с колонками
`chunk_id, document_id, task_id, heading, breadcrumb, body`, `textutil.fts_query_or`. После
порции b у документа есть `status`/`error`; после порции c поиск по чанкам отдаёт heading.
Жёсткие связи — `deps.HARD_BLOCKERS`; непод­тверждённые предложения хранятся как
`dep_type='suggested-blocks'` (мягкая).

Проверено на временной базе (файл 29 000 символов): на s1 ответ обрезан по `max_chars=24000`,
хотя контракт требует полный spec; на s3 без `--portion` отдаются все чанки с reason
«релевантный чанк документа»; на s4 нет worktree/diff и `verdict` не выделен; на s1 отдаются
review, хотя они нужны только с s2; словарь ошибки документа не имел `kind`. Спека шага
(контракт по этапам) и решения автора ниже — источник требований.

## Контракт по этапам

Общая форма JSON **не зависит от этапа** (одни и те же ключи всегда; пустые — `[]`/`null`):

```
task, card, stage, portion, documents, chunks, acceptance, dependencies,
reviews, verdict, journal, worktree, limits, reasons, generated_at
```

- `task` — карточка **без деталей**: `store.get_task(conn, task_id, with_details=False)`,
  прогнанная через `stable`, и дополнительно без ключа `deps_state` (он уходит в
  `dependencies`). Сейчас `context` зовёт `get_task` с дефолтным `with_details=True`, и в
  `task` приезжают все `comments` (включая review и verdict), 100 `events`, `documents`,
  `dependencies`, `dependents` — это ломает обещания этапов («на s1 review нет», «на s3 только
  последний review») внутри самого ответа и дублирует `documents[]`/`reviews`/`journal`.
  В `task` **не должно быть** ключей `comments`, `events`, `documents`, `dependencies`,
  `dependents`, `deps_state`. Что harness читает из `task`: `description`, `acceptance`,
  `design`, `notes`, `result`, статусные поля и пути — этого достаточно, детали он берёт
  через `show`. `card` — краткая:
  `id, project, title, status, stage, priority, holder, worktree, branch, spec_path,
  checklist_path, review_path, decision_path, issue_type, labels`.
- `documents[]` — все документы задачи из `index_task_documents` (одинаковая форма, включая
  `status`, `error`, `revision`, `chunk_count`), **без** поля `chunks` (чанки только в `chunks`).
- `chunks[]` — выбранные чанки: `id, document_id, kind, path, ordinal, heading, breadcrumb,
  text, start_line, end_line, token_count, reason, truncated` (`truncated` только если текст
  обрезан лимитом). Порядок: по документу (`spec`, `checklist`, `review`, `decision`), затем по
  `ordinal` — для s1/s2; для s3/s4 — в порядке слоёв (см. ниже), внутри слоя по `ordinal`.
- `dependencies` — объект, собранный из `deps.ready(conn, task_id)` (`deps_state`) **через
  `stable`** и без блока `reasons`: `deps.ready`/`deps._info` кладут в `blocked_by`/`waiting_for`
  поля `holder_age`, `idle_age`, `stale`, `stale_holder` и текстовые `reasons` с возрастами
  вида «ждёт завершения: lst-123 (open, 3 ч)» — всё это меняется со временем и ломает
  побайтную стабильность. Правило: функцию `stable` расширить, чтобы она вырезала ещё и
  `stale_holder`; из `deps_state` удалить ключ `reasons` целиком (текст с возрастами в контекст
  не попадает; `verdict` из `deps_state` — короткая фраза без возраста — остаётся). Если
  `deps_state` равен `None` (get_task глушит исключение) — `dependencies` всё равно объект с
  полным набором ключей и `null`/`[]` значениями. Плюс `hard[]` (жёсткие связи из таблицы
  `deps` с `depends_on`, `dep_type`, `status` и `title` зависимой задачи) и `suggested[]`
  (`suggested-blocks`). «Подтверждённые зависимости» на s3/s4 — это `hard[]`; `suggested[]`
  там пустой список.
- `reviews[]`, `verdict` (один объект или `null`) — комментарии соответствующих видов
  (`id, author, kind, text, created_at`).
- `journal[]` — **единая форма элемента** для комментариев и переходов, чтобы список можно
  было отсортировать и различить: `{"ts", "kind", "actor", "text", "id", "from", "to"}`.
  Для journal-комментария: `ts = created_at`, `kind = "journal"`, `actor = author`,
  `text = text`, `id` = id комментария, `from = to = null`. Для перехода этапа (событие
  `events.kind = 'stage'`): `ts = events.ts`, `kind = "stage"`, `actor = events.actor`,
  `text = events.note` (или `""`), `id = "event:<events.id>"`, `from = from_value`,
  `to = to_value`. Сортировка — по кортежу `(ts, kind, id)`: `ts` первичный, `kind` и `id`
  дают детерминизм при равных `ts`. Переходы включаются только на s4; на s1–s3 `journal == []`.
- `worktree` — объект (см. s4) или `null`.
- `limits` — `{max_chars, default_for_stage, used_chars, truncated, applies_to: "chunks",
  truncated_chunks: [{chunk_id, path, start_line}], dropped_chunks: N, reason}`;
  `reason` — короткая фраза: почему обрезано или «все выбранные блоки помещены».
- `reasons[]` — по одному элементу на **каждый включённый блок**, не только на чанк:
  `{"block": "card"|"acceptance"|"dependencies"|"review"|"verdict"|"journal"|"worktree"|"chunk",
  "chunk_id": … (для chunk), "reason": "…"}`.
- `generated_at` — `tasks.updated_at` (как сейчас).

### Лимиты по этапу

- Дефолт `max_chars`: s1/s2 — **150 000**, s3/s4 — **24 000**. CLI `--max-chars` и параметр
  API `max_chars` переопределяют; в CLI дефолт аргумента `None`, чтобы сработал дефолт этапа.
  Значение попадает в `limits.default_for_stage`.
- Лимит применяется к суммарной длине `text` чанков. На s1/s2 обрезка — исключительный случай
  («очень большой файл»): в `limits.reason` явно назвать, какой документ и с какой строки не
  вошёл; `truncated_chunks`/`dropped_chunks` заполнить.

### s1-spec

`card`, `task`, все документы целиком (`spec`, `checklist`, а также `review`/`decision`, если
заданы), `dependencies`; `reviews=[]`, `verdict=null`, `journal=[]`, `worktree=null`.
Reason чанка: `полный документ <kind> для этапа s1-spec`.

### s2-review

Всё из s1 плюс `reviews` — все review-комментарии. Reason чанка аналогичный.

### s3-impl — слоистый выбор (решение автора)

1. Слой «чек-лист»: все чанки документа `checklist` целиком, reason `чек-лист целиком`.
2. Слой «порция»: если задан `--portion`, чанки документов `spec` (и `decision`, если есть),
   у которых `heading` или `breadcrumb` содержит порцию без учёта регистра;
   reason `совпадение с порцией «<portion>»`.
3. Слой «лексика»: если порция не задана **или** слой 2 пуст — запрос
   `textutil.fts_query_or(title + " " + acceptance)` к `document_chunk_fts` с фильтром
   `task_id`, отсортированный по `bm25`, чанки документов `spec`/`decision`, не выбранные ранее,
   пока не исчерпан лимит; reason `лексическое совпадение с заголовком и acceptance, ранг N`.
   Если acceptance пустой и заголовок не даёт токенов — слой пропускается.
4. Слой «начало»: если после слоёв 1–3 чанков `spec` нет вовсе — первые чанки `spec` по
   `ordinal` до лимита, reason `начало документа: совпадений по порции и лексике нет`.

Плюс: `reviews` — только последний review; `verdict=null`; `journal=[]`;
`dependencies.hard[]` — подтверждённые; `worktree=null`.

### s4-judge — то же, что s3, плюс

- `verdict` — последний verdict-комментарий (или `null`), `reviews` — последний review.
- `journal[]` — journal-комментарии и переходы этапов (см. выше).
- `worktree` — решение автора: если `tasks.worktree` задан и каталог существует, запускать git
  через `store._git_value` (не дублировать — при необходимости вынести хелпер в `store`, но
  сигнатуру не менять):
  `{"path", "exists": true, "branch": rev-parse --abbrev-ref HEAD, "head": rev-parse HEAD,
  "changed_files": [строки git status --porcelain], "diff_stat": вывод git diff --stat HEAD,
  "card_branch": tasks.branch}`. Если `worktree` не задан — `{"exists": false, "reason":
  "worktree не задан в карточке"}`; если каталога нет — `{"path", "exists": false, "reason":
  "каталог не найден"}`; если git в каталоге отсутствует — `{"path", "exists": true,
  "git": false, "reason": "не git-репозиторий"}`. Ошибки git никогда не поднимают исключение.
  **Репозиторий без единого коммита** — отдельный законный случай: `rev-parse HEAD` и
  `diff --stat HEAD` там падают, `_git_value` возвращает `None`. Тогда `head = null`,
  `diff_stat = ""`, `branch` — что вернул `rev-parse --abbrev-ref HEAD` (может быть `null`),
  `changed_files` — из `status --porcelain` (работает и без коммитов), `reason: "нет
  коммитов"`. Пустой вывод `status`/`diff` (чистое дерево) — это `[]`/`""`, а не ошибка.
  Вывод git ограничить: `changed_files` — до 200 строк, `diff_stat` — до 4000 символов с
  пометкой обрезки в `reason`.
- Исходный spec — только выбранными слоями разделами; путь к нему есть в `documents[]`.

### Текстовый формат (`--format text`)

Печатает по порядку: заголовок `контекст <id> · этап <stage>`; карточку (s1/s2 — основные
поля `task`: заголовок, проект, статус, этап, держатель, пути; s3/s4 — `card`); `acceptance`;
зависимости (`hard[]` и `dependencies.verdict`); документы (kind, путь, rev, статус); чанки
(`### breadcrumb (path:start-end) — reason` и текст); reviews/verdict/journal при наличии;
worktree при наличии; строку лимитов при обрезке. `--json` (глобальный) и `--format json`
равнозначны.

### Тесты — `tests/test_context.py` (временная база, без Ollama, git доступен локально)

Подготовка: задача с `spec_path` = копия `long-spec.md` (29 000+ символов), `checklist_path`
= `checklist.md`, acceptance с двумя словами из одного раздела spec, комментарии `review` ×2,
`journal` ×1, `verdict` ×1, событие перехода этапа (через `store.next_stage`), жёсткая
зависимость (`store.add_dep(..., confirm=True)`) и предложение (`confirm=False` от агента).

- Набор ключей ответа одинаков для всех четырёх этапов.
- `task` без деталей: на всех этапах в `task` нет ключей `comments`, `events`, `documents`,
  `dependencies`, `dependents`, `deps_state`; на s1 текст verdict-комментария и текст
  review-комментариев не встречаются нигде в `json.dumps(ответ, ensure_ascii=False)`; на s3
  текст первого (не последнего) review не встречается нигде в сериализованном ответе.
- Стабильность по построению: рекурсивный обход всего ответа не находит ключей,
  оканчивающихся на `_age` или `_hours`, и ключей `stale`, `stale_holder`, `abandoned`;
  в `dependencies` нет ключа `reasons`; ни одна строка ответа не содержит подстроки « ч)» /
  « мин)» из `deps_state.reasons`. Проверка делается на задаче с незакрытым жёстким
  блокером, чей держатель проставлен (`store.claim`), — иначе полей возраста в `deps_state`
  не будет и тест ничего не докажет.
- s1: все чанки spec и checklist присутствуют (число = сумма `chunk_count`), `limits.truncated`
  ложь, `reviews == []`, `verdict is None`, `worktree is None`, `reasons` содержит блоки
  `card`, `acceptance`, `dependencies` и по одному `chunk` на чанк.
- s1 с `max_chars=5000`: `truncated` истина, `limits.reason` упоминает путь spec,
  `dropped_chunks > 0`.
- s2: `reviews` — оба.
- s3 без порции: чанки `checklist` все (reason «чек-лист целиком»), чанки `spec` только с
  reason «лексическое совпадение…» и среди них — раздел, из которого взяты слова acceptance;
  всего чанков меньше, чем на s1; `reviews` — один (последний); `dependencies.hard` — одна
  запись, `suggested == []`.
- s3 с `--portion "<heading подраздела>"`: чанки spec — только с этим heading/breadcrumb,
  reason «совпадение с порцией».
- s3 с порцией, которой нет в файле: срабатывает слой «лексика» (reason соответствующий).
- s3 при пустом acceptance и заголовке без совпадений: слой «начало», reason соответствующий.
- s4: `verdict` — последний verdict; `journal` содержит и journal-комментарий
  (`kind == "journal"`, `id` — id комментария), и переход (`kind == "stage"`, `from`/`to`
  заполнены, `id` начинается с `event:`); у всех элементов есть `ts`, и список равен
  `sorted(journal, key=lambda j: (j["ts"], j["kind"], j["id"]))`.
- s4, worktree с коммитом. Подготовка во временном каталоге: `git init -q`, затем один коммит
  (`git -c user.email=t@example.com -c user.name=t commit -q -m init` после `git add` файла
  `a.txt`), затем **изменить `a.txt`** (не добавлять новый файл: untracked-файл не попадает в
  `git diff --stat HEAD`). Ожидание: `exists` истина, `git` истина, `head` — строка из
  40 hex-символов, `branch` непустой, `changed_files` содержит строку с `a.txt`, `diff_stat` —
  непустая строка с `a.txt`.
- s4, репозиторий без коммитов: `git init -q` во временном каталоге и один незакоммиченный
  файл. Ожидание: `exists` истина, `git` истина, `head is None`, `diff_stat == ""`,
  `changed_files` содержит имя файла, `reason == "нет коммитов"`, исключений нет.
- s4 без worktree — `{"exists": false, "reason": …}`; несуществующий каталог — `exists: false`;
  каталог без git — `exists: true, git: false`.
- Два вызова подряд для каждого этапа дают одинаковый `json.dumps(..., sort_keys=True)`.
- Задача без документов: все четыре этапа возвращают корректный ответ с `documents == []`,
  `chunks == []`, без исключений.

## Границы правки

- Правятся: `listik/documents.py` (`context` и вспомогательные функции выбора/лимитов/worktree),
  `bin/listik` (`cmd_context` и его argparse: дефолт `--max-chars` → `None`), `listik/server.py`
  (только парсинг `max_chars` в обработчике `context`: отсутствие параметра → `None`),
  `listik/client.py` (только передача `max_chars=None`), `tests/test_context.py`. При
  необходимости `listik/store.py` — только чтобы сделать `_git_value` доступным без дублирования.
- Не менять индексацию (`index_document`, `split_markdown`, `refresh_all`), поиск
  (`search.py`), embeddings, схему базы, alembic, MCP (`mcp.py`) и документацию — MCP и
  документация идут в порции e.
- Не менять `store.get_task`, `deps.ready` и семантику зависимостей; `hard[]`/`suggested[]`
  вычисляются чтением таблицы `deps` внутри `documents.py`. Зачистка `task` и `dependencies`
  от деталей и возрастов делается в `documents.context` (`with_details=False`, `stable`,
  удаление `reasons`), а не правкой `store`/`deps`.
- Не подключать Ollama и не делать векторный отбор чанков: «лексика» — только FTS.
- Не нарушать побайтную стабильность ответа между вызовами; не добавлять текущее время.
- Не запускать git вне `tasks.worktree`; не выполнять запись в репозитории.
- Тесты порций a–c зелёные.

## Как проверить

```sh
python3 -m unittest discover tests -v
export LISTIK_DB=/tmp/listik-step01d.db && ./bin/listik --local init
cp tests/fixtures/long-spec.md /tmp/spec-d.md; cp tests/fixtures/checklist.md /tmp/check-d.md
./bin/listik --local --json new "контекст" --project listik --spec /tmp/spec-d.md --checklist /tmp/check-d.md   # → $TID
./bin/listik --local context $TID --stage s1-spec --format json | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['chunks']), d['limits'])"
./bin/listik --local context $TID --stage s3-impl --format json | python3 -c "import json,sys; d=json.load(sys.stdin); print([(c['kind'],c['heading'],c['reason']) for c in d['chunks']])"
./bin/listik --local context $TID --stage s3-impl --portion "<heading подраздела>" --format text
./bin/listik --local set $TID worktree=/Users/dmitry.fomin/Projects/Listik   # любой git-каталог
./bin/listik --local context $TID --stage s4-judge --format json | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['worktree']['exists'], d['worktree']['head'][:8], len(d['worktree']['changed_files']))"
./bin/listik --local context $TID --stage s1-spec --format json > /tmp/c1.json; ./bin/listik --local context $TID --stage s1-spec --format json > /tmp/c2.json; cmp /tmp/c1.json /tmp/c2.json && echo стабильно
```
