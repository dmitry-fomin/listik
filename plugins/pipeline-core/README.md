# pipeline-core

Общее ядро пресетов конвейера ТЗ → критика ТЗ → код → приёмка: протокол этапов
(`references/pipeline-core.md`), агенты конвейера, хук автоодобрения их команд и скил `core`, который называет
путь ядра. Сам задачу не ведёт — от него зависят пресетные плагины (`feature-pipeline`, `claude-codex`), их
`plugin.json` перечисляет `pipeline-core` в `dependencies`. Агенты зовутся как `pipeline-core:<имя>`.

## Скил core

`pipeline-core:core` — указатель на ядро: пресет вызывает его до первого действия, скил называет путь
`${CLAUDE_PLUGIN_ROOT}/references/pipeline-core.md`, и пресет читает файл целиком. Нет файла по пути — пресет стоит
по разделу «Стоп-фактор» ядра. Скил скрыт из меню `/` (`user-invocable: false`) и сам задачу не решает.

Почему не относительная ссылка `../../../pipeline-core/references/pipeline-core.md` из скила пресета: Claude Code
копирует каждый плагин в кэш отдельно (`~/.claude/plugins/cache/<marketplace>/<плагин>/<версия>/`), файлы выше
корня плагина в копию не попадают, и путь в соседний плагин в кэше не разрешается. `${CLAUDE_PLUGIN_ROOT}` в теле
скила Claude Code подставляет сам — это корень установленной версии `pipeline-core`.

## Агенты

Модель и effort — из frontmatter `agents/*.md`. Effort задаётся только во frontmatter; `model` в
вызове перебивает frontmatter.

| Агент | Модель / effort | Роль | Где используется (перебивка model) |
| --- | --- | --- | --- |
| `pipeline-spec-writer` | opus / high | ТЗ шага, порции, чек-листы; только каталог шагов, неясное — вопросом автору | `high-pipeline` — **`model: opus`** |
| `pipeline-spec-writer-xhigh` | opus / xhigh | то же | `xhigh-pipeline` — **`model: opus`** |
| `pipeline-spec-writer-medium` | opus / medium | то же | `medium-pipeline`, `sol-pipeline` — **`model: opus`** |
| `pipeline-spec-writer-low` | opus / low | то же | `low-pipeline` — **`model: opus`** |
| `pipeline-spec-writer-inherit` | opus / наследует | то же, усилие — от сессии | `claude-pipeline` — **`model: opus`** |
| `pipeline-critic` | sonnet / high | штатный критик ТЗ и чек-листа (`model: sonnet`), критик `sonnet` в составе | этап 2 xhigh/high/medium/cross/sol-pipeline |
| `pipeline-critic-xhigh` | sonnet / xhigh | критик ТЗ и чек-листа, тело — как у `pipeline-critic` | `claude-codex:xhigh-pipeline` — критик `sonnet`, **`model: sonnet`** |
| `pipeline-critic-medium` | sonnet / medium | то же | `claude-codex:medium-pipeline` — критик `sonnet`, **`model: sonnet`**; `claude-codex:high-pipeline` — критик `opus`, **`model: opus`** |
| `pipeline-critic-inherit` | sonnet / наследует | то же, усилие — от сессии | `claude-pipeline` — Sonnet и Opus в кворуме |
| `pipeline-implementer` | sonnet / medium | реализует одну порцию, не коммитит | `medium-pipeline`, `sol-pipeline` — **`model: opus`** |
| `pipeline-implementer-high` | sonnet / high | то же для неочевидных порций | `high-pipeline` — **`model: opus`** |
| `pipeline-implementer-xhigh` | opus / xhigh | то же на максимальном усилии | `xhigh-pipeline` |
| `pipeline-implementer-low` | opus / low | то же на низком усилии | `claude-codex:low-pipeline` (`model` в вызове не передаётся) |
| `pipeline-implementer-inherit` | opus / наследует | то же, усилие — от сессии | `claude-pipeline` — **`model: opus`** |
| `pipeline-implementer-solo` | sonnet / medium | задача в один проход и сам коммитит | `opus-pipeline` — **`model: opus`** |
| `pipeline-judge` | opus / high | приёмка: чек-лист, срезанные углы в диффе, вердикт, при зелёном — коммит; код не правит | не зовётся ни одним скилом |
| `pipeline-judge-inherit` | sonnet / наследует | то же, усилие — от сессии | `claude-pipeline` — при находке линз |
| `pipeline-lens` | sonnet / наследует | линза приёмки | `claude-pipeline` — три линзы |

Исполнители и судья преднагружают скил `listik:listik` (поле `skills:`) и при названном в задаче id (`Listik, карточка <id>`) ведут карточку сами — раздел «Карточка Listik» в теле агента.

## Хуки

`hooks/hooks.json` → `approve-pipeline-agents.py` на `PermissionRequest` (Bash/Edit/Write/MultiEdit/
NotebookEdit): автоодобряет запросы исполнителей и судьи, только если в настройках плагина включён
`auto_approve_agents`, правка лежит в проекте/его worktree/`.git/pipeline-core` и не секрет,
команда не из списка опасных. Иначе и при ошибке хук молчит — обычный запрос; deny-правила сильнее.

Настройка `auto_approve_agents` теперь у плагина `pipeline-core` (раньше была у `feature-pipeline`) — включается
заново: `/plugin` → pipeline-core → configure.

## references

- `pipeline-core.md` — общий протокол пресетов: правила, цикл, вопросы (в т.ч. headless под роем),
  шаг 0, имена бумаг и деревьев, треки, пакет диффа, коммит приёмкой, приёмка линзами, пределы, журнал, Listik.
- `ROLES.md` — почему на роли поставлены эти модели; таблицы генерирует `presets.py`.
- `README.md` + `fetch_aa.py`, `fetch_openrouter.py`, `models.*`, `openrouter.*` — снимки рейтингов
  моделей и скрипты их обновления.
- `bench/` — стенд судей (`bench/README.md`).
