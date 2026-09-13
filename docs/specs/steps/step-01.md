# Шаг 01. Документы, чанки и контекст — план порций

Спека шага: `docs/specs/steps/step-01-documents-and-context.md`. Продукт: `docs/specs/listik-product.md`.

## Цель

Markdown-ТЗ становится первоклассным индексируемым источником: карточка хранит только пути и
ревизии, текст лежит в `documents`/`document_chunks`, поиск и embeddings работают по чанкам,
а `listik context <id> --stage …` собирает для harness компактный воспроизводимый контекст.

## Что уже есть в коде (коммит 3452012)

Значительная часть шага уже реализована черновиком, и порции этого шага **закрывают разрывы
между кодом и контрактом**, а не пишут с нуля:

- схема `documents` / `document_chunks` / `document_chunk_fts` есть и в `listik/db.py`, и в
  `alembic/versions/0001_initial.py`;
- `listik/documents.py`: резка по заголовкам с breadcrumb, overlap, `content_hash`/`revision`,
  индексация `spec_path`/`checklist_path`/`review_path`/`decision_path`/`journal_path`,
  функция `context()`;
- `listik/search.py` ищет по `document_chunk_fts`, `listik/embed.py` умеет kind `chunk`;
- CLI `listik context`, API `GET /api/tasks/{id}/context`, локальный фолбэк в `client.py`.

Проверка на временной базе показала разрывы, которые и режутся на порции:
лимит 24 000 символов режет «полный spec» на s1/s2; `search` не отдаёт heading/breadcrumb;
s3 без порции отдаёт все чанки подряд под именем «релевантных»; s4 не содержит
worktree/diff-метаданных; `document_error` пишется при каждом вызове, а `show` не показывает
документы; `/api/embed` по умолчанию не считает чанки; при переиндексации остаются сиротские
embeddings; в документации (`API.md`, `README.md`, MCP) контекста и документов нет;
автоматических тестов нет вовсе.

## Решения автора (зафиксированы 12.09.2026)

1. Релевантные чанки для s3/s4 выбираются слоями: чек-лист целиком → чанки spec, чей
   heading/breadcrumb совпадает с `--portion` → при отсутствии порции или совпадений — чанки,
   ранжированные лексическим поиском по `document_chunk_fts` запросом из заголовка задачи и
   acceptance, до лимита. У каждого чанка `reason` говорит, какой слой его включил.
2. Заводится `tests/` на stdlib `unittest` (`python3 -m unittest discover tests`) с фикстурами
   длинных Markdown-ТЗ. Чек-листы приёмки опираются на эти тесты плюс сценарии CLI на временной
   базе (`LISTIK_DB=…`).
3. На s4 сервер вызывает git в `tasks.worktree` (паттерн `_git_value` в `store.py`): HEAD, ветка,
   список изменённых файлов из `git status --porcelain`, `git diff --stat HEAD`. Если worktree не
   задан или каталога нет — блок с `exists: false` и причиной, без ошибки.

## Порции (по порядку, каждая опирается на закоммиченные предыдущие)

| Порция | Суть | Файлы |
| --- | --- | --- |
| a | Тестовый каркас `tests/` с фикстурами; правки чанкера: списки, заголовки внутри code-fence, публичный `split_markdown`; тесты идемпотентности индексации | `step-01.a.md`, `step-01.check-a.md` |
| b | Статус документа (`status`/`error`/`checked_at`) с миграцией в `db.py` и alembic; событие только на переходе доступен↔недоступен; `show` показывает документы; удаление задачи чистит FTS и embeddings чанков | `step-01.b.md`, `step-01.check-b.md` |
| c | Поиск и embeddings на уровне чанков: heading/breadcrumb в результатах и текстовом выводе, чанки в `/api/embed` по умолчанию, уборка сиротских векторов при переиндексации, фоновая переиндексация изменённых файлов | `step-01.c.md`, `step-01.check-c.md` |
| d | Контракт `context` по этапам: лимиты по этапу, слоистый выбор чанков, worktree/diff-блок на s4, `reasons` для каждого блока, текстовый формат | `step-01.d.md`, `step-01.check-d.md` |
| e | Документация и MCP: `API.md`, `README.md`, инструмент `listik_context`, `bin/listik-codex` | `step-01.e.md`, `step-01.check-e.md` |

## Вне шага

- Изменение алгоритма RRF, автоматический пересказ ТЗ моделью, отдельная векторная база.
- Маршрутизация `stage → harnesses` и `ready --harness` (шаг 04).
- Показ документов и контекста на доске `web/` (шаги 05–06).
- Протокол harness в `AGENTS.md` (шаг 02); `bin/listik-codex` трогается только в объёме,
  описанном в порции e.

## Как проверять любую порцию

Все сценарии выполняются на временной базе, чтобы не трогать рабочую `listik.db`:

```sh
export LISTIK_DB=/tmp/listik-step01.db   # любой путь вне репозитория
./bin/listik --local init
```

Флаг `--local` заставляет CLI ходить в SQLite напрямую (сервер не нужен). Тесты:
`python3 -m unittest discover tests`. Ollama для тестов не требуется: векторная ветка
должна деградировать молча.
