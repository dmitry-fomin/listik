# Порция 01.e. Документация и MCP для документов и контекста

## Контекст

Listik — трекер задач на stdlib-Python (`bin/listik` — CLI и API-клиент, `listik/server.py` —
HTTP API, `listik/mcp.py` — stdio MCP-сервер с инструментами `listik_*`, тот же store).
`API.md` — авторитетный контракт данных и эндпоинтов; `README.md` — справочник команд.
Порциями a–d в коде появились: индексируемые документы задачи (`documents`, `document_chunks`,
`document_chunk_fts`; поля `tasks.spec_path`, `checklist_path`, `review_path`, `decision_path`,
`journal_path`), поиск по чанкам с `heading`/`breadcrumb`/`best_hit` в результатах,
`GET /api/tasks/{id}/context`, CLI `listik context`, статус документа в `show`, тесты
`python3 -m unittest discover tests`. Ни `API.md`, ни `README.md`, ни MCP об этом не знают;
`bin/listik-codex` уже вызывает `listik context … --format json`.

**Предусловие: порция d закоммичена.** Порция e документирует форму ответа `context` после
порции d (ключи `verdict` в единственном числе, `worktree`, `limits.default_for_stage`,
`dependencies.hard`/`suggested`). До коммита d в дереве старая форма: `verdicts` (мн. ч.),
без `worktree` и `default_for_stage`, `dependencies` — сырой `deps_state`. Проверка перед
стартом: `git log --oneline` содержит коммит порции d, и в выводе
`listik --local context <id> --stage s4-judge --format json` на временной базе **есть** ключи
`verdict`, `worktree` и **нет** ключа `verdicts`. Если ключ `verdicts` есть — порцию не
начинать и вернуть задачу оркестратору.

Перед началом прочитай `API.md`, `README.md`, `listik/mcp.py` (список `TOOLS` и `call_tool`),
`listik/documents.py` (`context`, `document_json`), `listik/search.py` (`search`) и
`listik/server.py` (обработчик `context`, `POST /api/tasks`, `/api/embed`). Форму ответов не
придумывать — брать из кода и проверять живым вызовом на временной базе. Документируется
**фактическое** поведение кода: если оно расходится с ожиданием (например, параметр в API не
валидируется), в `API.md` пишется то, что есть, а расхождение — строкой в отчёте исполнителя.

## Что сделать

### 1. `API.md`

- Раздел «Модель задачи»: описать поля `spec_path`, `checklist_path`, `review_path`,
  `decision_path`, `journal_path` (журнал — алиас документа `decision`), и что в
  `GET /api/tasks/{id}` при `details=1` есть `documents[]` с полями `id, kind, path, revision,
  content_hash, title, updated_at, status, error, chunk_count` — ровно то, что выбирает запрос
  в `store.get_task` (`store.py`, блок `documents`). Колонка `checked_at` есть только в таблице
  `documents` и в ответы API **не попадает** (решение порции b ради побайтной стабильности
  `context`); в `API.md` это оговаривается одной фразой, а в список полей ответа `checked_at`
  не включается. Список полей в `API.md` сверяется с живым `listik --local show <id> --json`.
- Новый подраздел «Документы и чанки»: таблицы `documents`/`document_chunks`, виды `spec`,
  `checklist`, `review`, `decision`; правила: файл не копируется в карточку, индекс строится
  по заголовкам Markdown с breadcrumb и overlap, переиндексация по `content_hash`, `revision`
  растёт при изменении, недоступный файл — `status=missing` + событие `document_error` один
  раз, восстановление — `document_restored`; фоновая перепроверка воркером сервера.
- Таблица «Чтение»: строка `GET /api/tasks/{id}/context` с параметрами `stage`, `portion`,
  `max_chars` (дефолт по этапу: 150 000 для s1/s2, 24 000 для s3/s4) и перечнем ключей ответа.
  Про `stage` писать **фактическое поведение HTTP-эндпоинта**: параметр необязателен, при
  отсутствии берётся `s1-spec` (`server.py`: `q1("stage") or "s1-spec"`), значение сервером
  не валидируется — неизвестная строка обрабатывается как «не s1/s2» (то есть по правилам
  s3/s4-подобного этапа без worktree). Ожидаемые значения — `s1-spec|s2-review|s3-impl|s4-judge`.
  Обязательным и ограниченным списком `stage` является только в CLI (`--stage`, `required=True`,
  `choices`) и в схеме MCP-инструмента — это тоже написать, чтобы читатель не переносил
  гарантии CLI на HTTP. Валидацию на сервере в этой порции не добавлять (границы). Отдельно — контракт по этапам (что входит
  на s1/s2/s3/s4, слои выбора чанков для s3/s4, блок `worktree` на s4, `limits`, `reasons`) —
  кратко, по одному абзацу на этап, без копирования кода.
- Строка `GET /api/search`: в `results[]` описать `best_hit` и форму `hits[]` (в том числе
  `heading, breadcrumb, document_id, document_kind, path, start_line, end_line` для `kind=chunk`).
- Строки `POST /api/tasks` и `PATCH /api/tasks/{id}`: добавить `checklist_path`,
  `review_path`, `decision_path`. Строка `POST /api/embed`: `kinds=task,comment,chunk` по
  умолчанию; продублированную строку `/api/embed` в таблице (сейчас их две) свести к одной.
- Раздел «CLI»: добавить `listik context <id> --stage … [--portion] [--max-chars] [--format text|json]`.

### 2. `README.md`

- В «Работа агента с задачей» или отдельным разделом «Контекст этапа»: как задать пути
  документов при `new`/`set`, что делает `context`, пример вызова для s2 и s3 с `--portion`,
  что ответ побайтно стабилен и предназначен для harness.
- В «Поиск»: поиск находит фразы внутри разделов ТЗ и показывает `раздел: …`.
- В «Агентам: MCP и CLI»: добавить `listik_context` в список инструментов.
- В «Структура базы»: добавить `documents`, `document_chunks`, `document_chunk_fts`.
- Добавить раздел «Тесты» с командой `python3 -m unittest discover tests` и указанием, что
  тесты работают на временной базе без сервера и Ollama.

### 3. MCP: инструмент `listik_context`

В `listik/mcp.py` добавить в `TOOLS` инструмент `listik_context` со схемой
`{id: string (обязателен), stage: enum s1-spec|s2-review|s3-impl|s4-judge (обязателен),
portion: string, max_chars: integer}` и описанием на русском в стиле соседних инструментов;
в `call_tool` — вызов `documents.context(conn, id, stage, portion=…, max_chars=…)`
(`max_chars` отсутствует → `None`, чтобы сработал дефолт этапа). Описание `listik_show`
дополнить упоминанием `documents[]`; описание `listik_create`/`listik_update` — полями
`checklist_path`, `review_path`, `decision_path` (сами поля в схеме уже есть).

### 4. `bin/listik-codex`

Только проверить, что вызов `context … --format json` соответствует новому контракту
(имя параметра `--stage`, формат). Если менять нечего — файл не трогать.

### 5. Проверка

Все примеры в документации должны быть прогнаны на временной базе (`LISTIK_DB=/tmp/…`,
`--local`) и соответствовать реальному выводу. MCP проверить простейшим stdio-обменом:
запустить `./bin/listik mcp`, отправить `initialize`, затем `tools/list` и `tools/call` для
`listik_context` — так, как это уже делается для других инструментов (см. формат в `mcp.py`).

## Границы правки

- Правятся: `API.md`, `README.md`, `listik/mcp.py`, при необходимости `bin/listik-codex`.
- Не менять поведение кода в `documents.py`, `search.py`, `server.py`, `store.py`, `bin/listik`,
  схему базы, тесты. Документация описывает то, что есть; расхождение между кодом и спекой
  шага — не повод править код в этой порции, а строка в отчёте исполнителя.
- Не переписывать разделы `API.md`/`README.md`, не относящиеся к документам, контексту,
  поиску, MCP и тестам. Не трогать `AGENTS.md`, `docs/harness-protocol.md`, `CLAUDE.md`
  (протокол harness — шаг 02).
- Не класть в документацию токены, содержимое `config.toml` и пути к личным данным.

## Как проверить

```sh
python3 -m unittest discover tests -v                      # по-прежнему зелёные, код не менялся
git diff --stat HEAD                                       # только API.md, README.md, listik/mcp.py (+ listik-codex при необходимости)
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | LISTIK_DB=/tmp/listik-step01e.db ./bin/listik mcp | grep -o '"listik_context"'
```
