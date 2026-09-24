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
python3 -m unittest discover -s tests -t .   # тесты на временной базе, без сервера и Ollama
node --test swarm/test/                 # чистые функции роя (config/decide/run/listik)
cd web && npm run dev                   # доска с hot reload
cd web && npm run typecheck && npm run build
```

`tests/test_swarm_e2e.py` и `tests/test_swarm_barrier_e2e.py` (тот же барьер волны, но сквозь
процесс роя) входят в `discover -s tests -t .`: гоняют настоящий `bin/listik-swarm` против живого
сервера Listik и настоящего git на временном проекте. Требуют `node` и `git` в `PATH` — без них
модуль пропускается (`skip`) с понятной причиной; при их наличии `test_swarm_e2e.py` занимает
до 3 минут, `test_swarm_barrier_e2e.py` — до 5.
`tests/test_swarm_rollback_e2e.py` — живой сервер, реальный `listik watch`, до 5 минут;
пропускается без `node`/`git`.
`tests/test_swarm_rescope_e2e.py` — rescope в тике роя после влитой волны, подставная модель через `[swarm].command`, до 2 минут; пропускается без `node`/`git`.
`swarm/test/git.test.mjs` и
`swarm/test/arbiter.test.mjs` (в `node --test swarm/test/`) заводят собственные временные git-
репозитории на каждый тест.

## Миграции Alembic

`alembic/` — отдельный путь миграции схемы. Для безопасности команда требует явно указать
файл базы через `LISTIK_DB`; без переменной Alembic завершается с подсказкой и не открывает
`listik.db` репозитория. При разработке используйте временный файл, например:

```sh
LISTIK_DB=/tmp/listik-alembic.db alembic upgrade head
```

Новые изменения схемы должны включать и мягкую миграцию в `listik/db.py`, и ревизию Alembic.
