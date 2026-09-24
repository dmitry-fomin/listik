# feature-pipeline

Пресеты конвейера реализации задачи: ТЗ → критика ТЗ → код → приёмка с коммитом. Основной контекст
только маршрутизирует, носит вопросы автору и ведёт журнал; код пишет исполнитель, коммитит судья.
Каждый скил запускается только по явному имени — маршрутом карточки Listik (`launch_route` или
метка `process:<ключ>`) или когда человек назвал пресет.

## Скилы

Все — `feature-pipeline:<имя>`. «—» — этапа нет.

| Скил | Когда брать | ТЗ | Критика ТЗ | Код | Приёмка + коммит |
| --- | --- | --- | --- | --- | --- |
| `xhigh-pipeline` | ошибка дороже прогона; ~$4 на задачу | Opus 5.5 xhigh (`pipeline-spec-writer-xhigh`, `model: opus`) | GLM 5.3 Flash по HTTP | Opus 5.5 xhigh (`pipeline-implementer-xhigh`) | Grok 4.7 xhigh |
| `high-pipeline` | расклад по умолчанию; ~$4 | Opus 5.5 high (`pipeline-spec-writer`, `model: opus`) | GLM 5.3 Flash по HTTP | Opus 5.5 high (`pipeline-implementer-high`, `model: opus`) | Grok 4.7 xhigh |
| `medium-pipeline` | работа понятная, хватит пониженного усилия; ~$3 | Opus 5.5 medium (`pipeline-spec-writer-medium`, `model: opus`) | GLM 5.3 Flash по HTTP | Opus 5.5 medium (`pipeline-implementer`, `model: opus`) | Grok 4.7 high |
| `low-pipeline` | поджимает лимит Max: код вне квоты; ~$3 | Opus 5.5 low (`pipeline-spec-writer-low`, `model: opus`) | GLM 5.3 Flash по HTTP | devin SWE-2 max (`devin:devin-delegate --thinking max`) | Grok 4.7 high |
| `xlow-pipeline` | задача в один прогон, нужна независимая приёмка | — | — | devin SWE-2 max (`devin:devin-delegate --thinking max`) | Grok 4.7 high |
| `nano-pipeline` | то же, дешевле; при пустом `write_scope` сперва ход на чтении за границами правки | — | — | devin SWE-2 high (`devin:devin-delegate --thinking high`) | GLM 5.3 Flash (`pi:pi-delegate --channel glm`) |
| `devin-pipeline` | то же, исполнитель devin | — | — | devin (SWE-2, max) | Grok 4.7 xhigh |
| `cross-pipeline` | автор ТЗ и исполнитель на разных вендорах: Devin пишет ТЗ, GLM в pi — код | Devin (SWE-2, max) | Grok 4.7 xhigh, без записи | GLM 5.3 Flash в pi | Grok 4.7 xhigh |
| `opus-single-pipeline` | понятная работа в один заход, приёмка не нужна; $0 внешних | — | — | Opus medium (`pipeline-implementer-solo`, `model: opus`), сам коммитит | — |
| `opus-sonnet-pipeline` | пачка простых задач трекера параллельно, по worktree | — | — | Opus medium (`pipeline-implementer`, `model: opus`) | Sonnet medium (`pipeline-judge`, `model: sonnet`) |
| `inherit-pipeline` | только локальные субагенты Claude, автор отвечает на вопросы по ходу | Fable (`pipeline-spec-writer`) | Opus (`pipeline-critic`) | Sonnet (`pipeline-implementer`) | Opus (`pipeline-judge`) |
| `feature-pipeline` | роли задаёт конфиг проекта `.claude/feature-pipeline.yaml` (пример — `skills/feature-pipeline/config.example.yaml`) | `pipeline-spec-writer` | провайдер из `second_opinion` | канал из `executor`: grok, dsh, codex или `pipeline-implementer` | `pipeline-judge` |
| `universal-pipeline` | роли читаются из `routes.roles` маршрута карточки; по каждой роли зовётся её скил-запускатор, отсутствующие роли — пропущенные этапы | из маршрута | из маршрута | из маршрута | из маршрута |

## Агенты

Модель и effort — из frontmatter `agents/*.md`. Effort задаётся только во frontmatter; `model` в
вызове перебивает frontmatter.

| Агент | Модель / effort | Роль | Где используется (перебивка model) |
| --- | --- | --- | --- |
| `pipeline-spec-writer` | fable / high | ТЗ шага, порции, чек-листы; только каталог шагов, неясное — вопросом автору | `inherit-pipeline`, `feature-pipeline`, `universal-pipeline`; `high-pipeline` — **`model: opus`** |
| `pipeline-spec-writer-xhigh` | fable / xhigh | то же | `xhigh-pipeline` — **`model: opus`** |
| `pipeline-spec-writer-medium` | fable / medium | то же | `medium-pipeline` — **`model: opus`** |
| `pipeline-spec-writer-low` | fable / low | то же | `low-pipeline` — **`model: opus`** |
| `pipeline-critic` | opus / high | критика ТЗ и чек-листа до реализации, репозиторий только на чтение, ничего не правит | `inherit-pipeline` |
| `pipeline-implementer` | sonnet / medium | реализует одну порцию, не коммитит | `inherit-pipeline`, `feature-pipeline`, `universal-pipeline`; `medium-`/`opus-sonnet-pipeline` — **`model: opus`** |
| `pipeline-implementer-high` | sonnet / high | то же для неочевидных порций | `high-pipeline` — **`model: opus`** |
| `pipeline-implementer-xhigh` | opus / xhigh | то же на максимальном усилии | `xhigh-pipeline` |
| `pipeline-implementer-solo` | sonnet / medium | задача в один проход и сам коммитит | `opus-single-pipeline` — **`model: opus`** |
| `pipeline-judge` | opus / high | приёмка: чек-лист, срезанные углы в диффе, вердикт, при зелёном — коммит; код не правит | `inherit-pipeline`, `feature-pipeline`, `universal-pipeline`; `opus-sonnet-pipeline` — **`model: sonnet`** |

Исполнители и судья преднагружают скил `listik:listik` (поле `skills:`) и при названном в задаче id (`Listik, карточка <id>`) ведут карточку сами — раздел «Карточка Listik» в теле агента.

## Хуки

`hooks/hooks.json` → `approve-pipeline-agents.py` на `PermissionRequest` (Bash/Edit/Write/MultiEdit/
NotebookEdit): автоодобряет запросы исполнителей и судьи, только если в настройках плагина включён
`auto_approve_agents`, правка лежит в проекте/его worktree/`.git/feature-pipeline` и не секрет,
команда не из списка опасных. Иначе и при ошибке хук молчит — обычный запрос; deny-правила сильнее.

## references

- `pipeline-core.md` — общий протокол пресетов: правила, цикл, вопросы (в т.ч. headless под роем),
  шаг 0, имена бумаг и деревьев, треки, пакет диффа, коммит приёмкой, пределы, журнал, Listik.
- `ROLES.md` — почему на роли поставлены эти модели; таблицы генерирует `presets.py`.
- `README.md` + `fetch_aa.py`, `fetch_openrouter.py`, `models.*`, `openrouter.*` — снимки рейтингов
  моделей и скрипты их обновления.
