# Работа с Listik

Полное руководство: протокол работы с задачами, конвейер, зависимости, серверный режим,
доска, поиск и подключение агентов. Установка и общее описание — в [README.md](../README.md),
контракт данных и эндпоинтов — в [docs/API.md](API.md), протокол агента — в
[AGENTS.md](../AGENTS.md).

## Как устроена работа

У задачи есть проект, тип, приоритет P0–P4, статус (`open`, `in_progress`, `review`, `done`,
`cancelled`), **держатель** с heartbeat, **этап** и флаг «нужен человек».

```sh
# агент добавляет к каждой команде: --actor agent:<harness> --harness <harness>
listik ready                                        # что можно брать
listik show <id>                                    # вся карточка
listik worktree <id>                                # дерево .worktrees/<id>, ветка task/<id>, от HEAD
listik claim <id> --holder claude --note "беру"
listik heartbeat <id> --holder claude --note "…"    # каждые 10–15 минут
listik comment <id> "решение и почему" -k journal
listik stage <id>                                   # следующий этап
listik needs-owner <id> "вопрос человеку"
listik done <id> -r "что сделано"
```

Конвейер: `s1-spec` (ТЗ) → `s2-review` (критика) → `s3-impl` (реализация) → `s4-judge`
(приёмка, `VERDICT: PASS|FAIL`) → `done`. Переход `s1-spec` → `s2-review` освобождает держателя,
чтобы следующий harness взял карточку через `ready` → `claim`; держателя можно выдать явно
(`stage <id> --holder <кому>`). Задача, созданная с `--route <маршрут> --autostart`, запускается сервером сама
по `routes.json`.

**Зависимости.** «Заблокирована» — не статус, а вычисление: жёсткие связи (`dep add <id>
<блокер>`) убирают задачу из `ready`, мягкие (`relates-to`, `parent-child`, `discovered-from`)
только информируют. Жёсткую связь от агента без `--confirm` сервер пишет предложением, его
подтверждает человек (`dep confirm`). Найденное по ходу — `new "…" --discovered-from <id>`.

**Серверный режим: у задачи есть хозяин.** По умолчанию Listik локальный — владельца нет и
спрашивать его никто не будет. Один Listik на несколько человек включается в `config.toml`:

```toml
[server]
mode = "server"          # "local" (по умолчанию) или "server"
users = ["ann", "bob"]   # кто работает с этим Listik
```

Тогда у новой задачи обязательно есть владелец, `claim`/`heartbeat` чужой задачи отказывают,
а `list`/`board`/`ready` показывают свои задачи и ничьи. Представиться можно тремя способами,
в порядке приоритета:

```sh
listik --owner ann new "Заголовок"     # флаг у любой команды
export LISTIK_OWNER=ann                # переменная окружения
```

```toml
[auth]
owner = "ann"                          # у каждого в своём config.toml — удобнее всего для команды
```

Владелец виден в `listik show` («владелец: ann») и меняется как поле: `listik set <id> owner=bob`,
`listik set <id> owner=` снимает его. В локальном режиме всё это не действует.

Подробные правила (`claim`, окно возврата, блокировка дерева, маршруты) — в [docs/API.md](API.md),
протокол агента — в [AGENTS.md](../AGENTS.md).

## Доска

`listik token` даёт ссылку. Вкладки: **Доска** (колонки этапов, «Ты нужен»), **Список**
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
(Caddy/nginx, `proxy_read_timeout` ≥ 120 с). Отдельного «удалённого режима» у CLI нет: он ходит
на `server.host`/`server.port` из своего `config.toml` — укажите там адрес нужного сервера.

**Правила в проектах**: `listik projects --add <путь>`, затем `listik init-projects` вписывает
протокол в `AGENTS.md`/`CLAUDE.md` проектов и `.worktrees/` в их `.gitignore`.

## Проект по рабочему каталогу

Команды `ready`, `list`, `board`, `stats`, `search`, `blocked`, `new`, `inbox` и `timeline`,
если проект не задан явно, определяют его по текущему каталогу. Проект подставляется как
значение `--project`, поэтому вызов из каталога проекта показывает очередь этого проекта,
а не всех сразу.

```
# проект определён по каталогу: listik (все проекты: --project all)
```

Порядок источников: явный `--project`/`-p` → переменная `LISTIK_PROJECT` → каталог → без
фильтра. `LISTIK_PROJECT` трактуется как явный флаг: несуществующий slug не ошибка, просто
пустая выдача. Строка «проект определён по каталогу» печатается только когда сработал
каталог, и только в человеческом режиме — при `--json` её нет, stdout остаётся валидным JSON.

Как ищется проект: сравнивается `projects.path` (колонка «Путь» у `listik projects`) — путь
проекта должен совпасть с каталогом или быть его предком, побеждает самое длинное совпадение
(вложенный проект перебивает объемлющий). Каталог внутри `<path>/.worktrees/<id>` — тот же
проект. Если по путям ничего не совпало, первый сегмент каталога внутри
`[import].projects_root` из `config.toml` берётся как slug проекта. Каталог вне всех проектов —
как раньше: без фильтра, без ошибки.

Значение `all` (`--project all`, `LISTIK_PROJECT=ALL`, регистр не важен) зарезервировано под
«все проекты»: фильтр не подставляется, автоопределение не работает. Поэтому проект со
slug `all` выбрать этим CLI нельзя. Автоопределение есть только у перечисленных команд:
у `memory`, `remember`, `dep suggested`, `import-from-bd` и `projects` тот же `--project`
значит другое.

## Справочник команд

| Группа | Команды |
|---|---|
| Сервер | `serve`, `stop`, `status`, `init`, `token`, `mcp`, `service` |
| Копии | `backup`, `restore` |
| Задачи | `new`, `list`, `show`, `set`, `context`, `board`, `stats`, `timeline` |
| Работа | `ready`, `worktree`, `claim`, `heartbeat`, `stage`, `release`, `done`, `needs-owner`, `inbox`, `launch`, `revoke` |
| Журнал | `comment -k comment\|journal\|review\|verdict\|question\|answer` |
| Связи | `dep add\|confirm\|rm\|suggest\|link\|suggested`, `blocked`, `tree`, `cycles` |
| Поиск | `search`, `memory`, `remember`, `embed` |
| Проекты | `projects`, `actors`, `init-projects`, `import-from-bd` |

`listik <команда> --help` — все флаги.

