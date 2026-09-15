# Listik — трекер задач и память для людей и агентов

Listik — одна очередь задач на все проекты, общая для человека и AI-агентов (Claude Code, Codex,
DeepSeek, Grok и т. п.). Один сервер — локально или на отдельной машине, одна база SQLite, один
API (HTTP и MCP). Кроме задач — журнал работы по каждой карточке, долговременная память и
гибридный поиск (полнотекстовый + векторный) по всей истории.

Зачем: агентская работа теряется между сессиями, агенты берут одно и то же, вопросы человеку
тонут в чате. В Listik у задачи всегда видно, **кто её держит**, **на каком она этапе**, **жив ли
держатель**, **чего она ждёт** и **что уже решено** — и любая новая сессия восстанавливает
контекст из карточки, а не из переписки.

```
bin/listik     CLI и клиент API (одна программа)
listik/        сервер, база, поиск, эмбеддинги, MCP
web/           доска (Vue 3)
plugins/       плагины Claude Code: listik, feature-pipeline, dsh, codex, second-opinion
docs/API.md         контракт данных и эндпоинтов — источник истины по поведению
AGENTS.md      правила работы агента с задачами
```

## Установка и запуск

Нужно: Python 3.11+ (только стандартная библиотека). Для доски — Node.js. Для векторного поиска —
[Ollama](https://ollama.com) с моделью `bge-m3` (необязательно: без неё поиск лексический).

### Одной строкой

```sh
curl -fsSL https://github.com/dmitry-fomin/listik/releases/latest/download/install.sh | sh
```

Работает после публикации релиза (`scripts/release.sh --publish`). Код ставится в
`~/.listik/app/<версия>`, обёртка `listik` — в `~/.local/bin`, данные — в `~/.listik`.
Повторный запуск обновляет версию, данные не трогает. Установщик по ходу предлагает автозапуск,
MCP и плагины Claude.

Если установлен Codex, установщик проверяет его `config.toml` (`$CODEX_HOME/config.toml`,
по умолчанию `~/.codex/config.toml`): нет секции `[sandbox_workspace_write]` с
`network_access = true` — предложит дописать, сохранив рядом копию конфига
(`config.toml.bak-<время>`). При отказе — предупреждение: без этой настройки Codex в режиме
записи не достучится до сервера Listik (127.0.0.1) и не сможет брать задачи, слать heartbeat
и писать журнал. Ответ задаёт флаг `--codex-network yes|no|ask` (по умолчанию `ask` — вопрос
в `/dev/tty`); `--yes` этот вопрос не закрывает — конфиг Codex правится только явным
`--codex-network yes`.

Все флаги (`--version`, `--archive`, `--home`, `--service`, `--mcp`, `--plugins`,
`--codex-network`, `--yes`, …) — `install.sh --help`.

### Из исходников

```sh
git clone https://github.com/dmitry-fomin/listik.git && cd listik
(cd web && npm install && npm run build)   # собрать доску

listik serve --daemon   # сервер и доска на http://127.0.0.1:8787
listik token            # ссылка на доску с токеном
listik status           # сервер, база, поиск (--json)
listik stop
```

- База и `config.toml` (с токеном) создаются сами при первом `serve`/`init`.
- Каталог данных задаёт `LISTIK_HOME` (по умолчанию — корень репозитория); `LISTIK_DB`,
  `LISTIK_CONFIG`, `LISTIK_LOG` переопределяют отдельные пути.
- Векторы: `ollama serve` + `ollama pull bge-m3`; сервер досчитывает их раз в 45 с.
  Отключить — `serve --no-embed`.
- CLI работает и без сервера — идёт в базу напрямую. `--json` есть у всех команд; ошибка
  с `--json` приходит в stdout объектом `{"error": {"code", "message", "hint"}}`.

### Автозапуск

```sh
listik service install     # launchd (macOS) или systemd --user (Linux)
listik service status
listik service uninstall   # данные не трогает
```

На macOS launchd-плист хранится в `~/Library/LaunchAgents/listik.server.plist`, на Linux
systemd-юнит — в `~/.config/systemd/user/listik.service`. Лог сервиса — `logs/service.log`;
юнит сохраняет PATH установки и добавляет стандартные каталоги пользовательских CLI
(`~/.local/bin`, `~/bin`, Homebrew/Linuxbrew), чтобы автостарт находил харнессы без shell-профиля;
`uninstall --no-load` удаляет файл, но оставляет уже загруженный сервис работать до ручной выгрузки.

### Резервные копии

Сервер держит базу в WAL — копировать её через `cp`/`mv`/`rm` **нельзя**. Только так:

```sh
listik backup                  # согласованная копия рядом с базой (сервер не останавливать)
listik restore --stop <копия>  # остановить сервер и вернуть базу; текущая сохранится в .bak-pre-restore-*
```

## Как устроена работа

У задачи есть проект, тип, приоритет P0–P4, статус (`open`, `in_progress`, `review`, `done`,
`cancelled`), **держатель** с heartbeat, **этап** и флаг «нужен человек».

```sh
# агент добавляет к каждой команде: --actor agent:<harness> --harness <harness>
listik ready                                        # что можно брать
listik show <id>                                    # вся карточка
listik claim <id> --holder claude --note "беру"
listik heartbeat <id> --holder claude --note "…"    # каждые 10–15 минут
listik comment <id> "решение и почему" -k journal
listik stage <id>                                   # следующий этап
listik needs-owner <id> "вопрос человеку"
listik done <id> -r "что сделано"
```

Конвейер: `s1-spec` (ТЗ) → `s2-review` (критика) → `s3-impl` (реализация) → `s4-judge`
(приёмка, `VERDICT: PASS|FAIL`) → `done`. Держателя можно выдать (`stage <id> --holder <кому>`)
и взять (`claim`). Задача, созданная с `--route <маршрут> --autostart`, запускается сервером сама
по `routes.json`.

**Зависимости.** «Заблокирована» — не статус, а вычисление: жёсткие связи (`dep add <id>
<блокер>`) убирают задачу из `ready`, мягкие (`relates-to`, `parent-child`, `discovered-from`)
только информируют. Жёсткую связь от агента без `--confirm` сервер пишет предложением, его
подтверждает человек (`dep confirm`). Найденное по ходу — `new "…" --discovered-from <id>`.

Подробные правила (`claim`, окно возврата, блокировка дерева, маршруты) — в [docs/API.md](docs/API.md),
протокол агента — в [AGENTS.md](AGENTS.md).

## Доска

`listik token` даёт ссылку. Вкладки: **Доска** (колонки этапов, «Нужен ты»), **Список**
(фильтры и массовая правка), **Метрики**. Этап меняют команды, перетаскивания нет. Помощник
DeepSeek в окне «Новая задача» включается ключом в `[assistant]` конфига (см. docs/API.md).
Голосовой ввод — запись, расшифровка и черновик задачи — включается ключом в `[deepgram]`
(нужны оба ключа).

## Поиск и память

```sh
listik search "как решали X"          # задачи, комментарии, документы; --mode text|vector
listik remember "факт" -p <проект>
listik memory "про что-то"
```

## Подключение агентов

**Плагины Claude Code** — репозиторий является маркетплейсом:

```
/plugin marketplace add dmitry-fomin/listik
/plugin install listik@listik             # скил listik:listik — протокол задач
/plugin install feature-pipeline@listik   # пресеты конвейера *-pipeline и агенты pipeline-*
/plugin install dsh@listik                # DeepSeek Harness: dsh:dsh-delegate
/plugin install codex@listik              # OpenAI Codex CLI: codex:codex-delegate
/plugin install second-opinion@listik     # критика ТЗ: second-opinion:ask
```

Пресеты `feature-pipeline` зовут внешние харнессы скилами этих плагинов; Grok остаётся отдельным
(`grok@grok-build`). `inherit-pipeline`, `opus-single-pipeline` и `opus-sonnet-pipeline` обходятся
субагентами Claude. При изменении плагина поднимайте `version` в его `plugin.json` и в `marketplace.json`.

**MCP** (инструменты `listik_*`, список — в docs/API.md):

```sh
claude mcp add listik -- listik mcp                                          # локально
claude mcp add --transport http listik https://<домен>/mcp \
  --header "Authorization: Bearer <токен>"                                   # удалённый сервер
```

Удалённо сервер оставляют на `127.0.0.1` и выставляют наружу только через HTTPS-прокси
(Caddy/nginx, `proxy_read_timeout` ≥ 120 с). CLI удалённого режима не имеет.

**Правила в проектах**: `listik projects --add <путь>`, затем `listik init-projects` вписывает
протокол в `AGENTS.md`/`CLAUDE.md` проектов и `.worktrees/` в их `.gitignore`.

## Справочник команд

| Группа | Команды |
|---|---|
| Сервер | `serve`, `stop`, `status`, `init`, `token`, `mcp`, `service` |
| Копии | `backup`, `restore` |
| Задачи | `new`, `list`, `show`, `set`, `context`, `board`, `stats`, `timeline` |
| Работа | `ready`, `claim`, `heartbeat`, `stage`, `release`, `done`, `needs-owner`, `inbox` |
| Журнал | `comment -k comment\|journal\|review\|verdict\|question\|answer` |
| Связи | `dep add\|confirm\|rm\|suggest\|link\|suggested`, `blocked`, `tree`, `cycles` |
| Поиск | `search`, `memory`, `remember`, `embed` |
| Проекты | `projects`, `actors`, `init-projects`, `import-from-bd` |

`listik <команда> --help` — все флаги.

## Разработка

```sh
python3 -m unittest discover tests      # тесты на временной базе, без сервера и Ollama
cd web && npm run dev                   # доска с hot reload
cd web && npm run typecheck && npm run build
```

Устройство кода — [CLAUDE.md](CLAUDE.md), доска — [web/README.md](web/README.md).
