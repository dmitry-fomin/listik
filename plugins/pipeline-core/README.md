# pipeline-core

Общее ядро пресетов конвейера ТЗ → критика ТЗ → код → приёмка: протокол этапов
(`references/pipeline-core.md`), агенты конвейера, хук автоодобрения их команд и скил `core`, который называет
путь ядра. Сам задачу не ведёт — от него зависят пресетные плагины (`pipeline-full`, `pipeline-cc`, `pipeline-claude`), их
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
| `pipeline-spec-writer` | opus / high | ТЗ шага, порции, чек-листы; только каталог шагов, неясное — вопросом автору | `pipeline-full:high`, `pipeline-cc:high` — **`model: opus`** |
| `pipeline-spec-writer-xhigh` | opus / xhigh | то же | `pipeline-full:xhigh`, `pipeline-cc:xhigh` — **`model: opus`** |
| `pipeline-spec-writer-medium` | opus / medium | то же | `pipeline-full:medium`, `pipeline-cc:medium`, `pipeline-cc:sol` — **`model: opus`** |
| `pipeline-spec-writer-low` | opus / low | то же | `pipeline-full:low`, `pipeline-cc:low` — **`model: opus`** |
| `pipeline-spec-writer-inherit` | opus / наследует | то же, усилие — от сессии | `pipeline-claude:high` — **`model: opus`** |
| `pipeline-critic` | sonnet / high | штатный критик ТЗ и чек-листа (`model: sonnet`), критик `sonnet` в составе | этап 2 `pipeline-full:xhigh`, `pipeline-full:high`, `pipeline-full:medium`, `pipeline-full:cross`, `pipeline-cc:high`, `pipeline-cc:sol`; `pipeline-cc:xhigh` — критик `opus`, **`model: opus`** |
| `pipeline-critic-xhigh` | sonnet / xhigh | критик ТЗ и чек-листа, тело — как у `pipeline-critic` | `pipeline-cc:xhigh` — критик `sonnet`, **`model: sonnet`** |
| `pipeline-critic-medium` | sonnet / medium | то же | `pipeline-cc:medium` — критик `sonnet`, **`model: sonnet`**; `pipeline-cc:high` — критик `opus`, **`model: opus`** |
| `pipeline-critic-inherit` | sonnet / наследует | то же, усилие — от сессии | `pipeline-claude:high` — Sonnet и Opus в кворуме |
| `pipeline-implementer` | sonnet / medium | реализует одну порцию, не коммитит | `pipeline-full:medium`, `pipeline-cc:medium`, `pipeline-cc:xlow`, `pipeline-cc:nano`, `pipeline-cc:sol` — **`model: opus`** |
| `pipeline-implementer-high` | sonnet / high | то же для неочевидных порций | `pipeline-full:high`, `pipeline-cc:high` — **`model: opus`** |
| `pipeline-implementer-xhigh` | opus / xhigh | то же на максимальном усилии | `pipeline-full:xhigh`, `pipeline-cc:xhigh` |
| `pipeline-implementer-low` | opus / low | то же на низком усилии | `pipeline-cc:low` (`model` в вызове не передаётся) |
| `pipeline-implementer-inherit` | opus / наследует | то же, усилие — от сессии | `pipeline-claude:high` — **`model: opus`** |
| `pipeline-implementer-solo` | sonnet / medium | задача в один проход и сам коммитит | `pipeline-claude:opus` — **`model: opus`** |
| `pipeline-judge` | opus / high | приёмка: чек-лист, срезанные углы в диффе, вердикт, при зелёном — коммит; код не правит | не зовётся ни одним скилом |
| `pipeline-judge-inherit` | sonnet / наследует | то же, усилие — от сессии | `pipeline-claude:high` — при находке линз |
| `pipeline-lens` | sonnet / наследует | линза приёмки | `pipeline-claude:high` — три линзы |

Исполнители и судья преднагружают скил `listik:listik` (поле `skills:`) и при названном в задаче id (`Listik, карточка <id>`) ведут карточку сами — раздел «Карточка Listik» в теле агента.

## Хуки

`hooks/hooks.json` → `approve-pipeline-agents.py` на `PermissionRequest` (Bash/Edit/Write/MultiEdit/
NotebookEdit): автоодобряет запросы исполнителей и судьи, только если в настройках плагина включён
`auto_approve_agents`, правка лежит в проекте/его worktree/`.git/pipeline-core` и не секрет,
команда не из списка опасных. Иначе и при ошибке хук молчит — обычный запрос; deny-правила сильнее.

Настройка `auto_approve_agents` теперь у плагина `pipeline-core` (раньше была у прежнего плагина пресетов) — включается
заново: `/plugin` → pipeline-core → configure.

## references

- `pipeline-core.md` — общий протокол пресетов: правила, цикл, вопросы (в т.ч. headless под роем),
  шаг 0, имена бумаг и деревьев, треки, пакет диффа, коммит приёмкой, приёмка линзами, пределы, журнал, Listik.
- `ROLES.md` — почему на роли поставлены эти модели; таблицы генерирует `presets.py`.
- `README.md` + `fetch_aa.py`, `fetch_openrouter.py`, `models.*`, `openrouter.*` — снимки рейтингов
  моделей и скрипты их обновления.
- `bench/` — стенд судей (`bench/README.md`).
