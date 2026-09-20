# Разработка Listik

Устройство кода — [CLAUDE.md](../CLAUDE.md), доска — [web/README.md](../web/README.md),
работа с задачами — [docs/usage.md](usage.md).

## Что где лежит

```
bin/listik     CLI и клиент API (одна программа)
listik/        сервер, база, поиск, эмбеддинги, MCP
web/           доска (Vue 3)
plugins/       плагины Claude Code: listik, feature-pipeline, dsh, codex, second-opinion
docs/API.md    контракт данных и эндпоинтов — источник истины по поведению
docs/usage.md  работа с задачами: протокол, конвейер, доска, поиск
AGENTS.md      правила работы агента с задачами
```

## Команды

```sh
python3 -m unittest discover tests      # тесты на временной базе, без сервера и Ollama
node --test swarm/test/                 # чистые функции роя (config/decide/run/listik)
cd web && npm run dev                   # доска с hot reload
cd web && npm run typecheck && npm run build
```

`tests/test_swarm_e2e.py` входит в `discover tests`: гоняет настоящий `bin/listik-swarm` против
живого сервера Listik и настоящего git на временном проекте. Требует `node` и `git` в `PATH` —
без них модуль пропускается (`skip`) с понятной причиной; при их наличии занимает до 3 минут.

## Миграции Alembic

`alembic/` — отдельный путь миграции схемы. Для безопасности команда требует явно указать
файл базы через `LISTIK_DB`; без переменной Alembic завершается с подсказкой и не открывает
`listik.db` репозитория. При разработке используйте временный файл, например:

```sh
LISTIK_DB=/tmp/listik-alembic.db alembic upgrade head
```

Новые изменения схемы должны включать и мягкую миграцию в `listik/db.py`, и ревизию Alembic.
