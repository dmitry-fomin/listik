# feature-pipeline

Пресеты конвейера реализации задачи: ТЗ → критика ТЗ → код → приёмка с коммитом. Основной контекст
только маршрутизирует, носит вопросы автору и ведёт журнал; код пишет исполнитель, коммитит судья
(в `high-pipeline` и `xhigh-pipeline` при чистых линзах приёмки порцию коммитит оркестратор; в пресетах с
коллегией судей — `epic-pipeline`, `feat-pipeline`, `refactor-pipeline`, `bug-pipeline` — порцию при единогласном зелёном
коммитит оркестратор).
Каждый скил запускается только по явному имени — маршрутом карточки Listik (`launch_route` или
метка `process:<ключ>`) или когда человек назвал пресет.

## Скилы

Все — `feature-pipeline:<имя>`. «—» — этапа нет.

| Скил | Когда брать | ТЗ | Критика ТЗ | Код | Приёмка + коммит |
| --- | --- | --- | --- | --- | --- |
| `xhigh-pipeline` | ошибка дороже прогона; ~$4 на задачу | Opus 5.5 xhigh (`pipeline-spec-writer-xhigh`, `model: opus`) | Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`) + DeepSeek V4.1 Flash в pi + devin SWE-2 max, кворум, сводит оркестратор | Opus 5.5 xhigh (`pipeline-implementer-xhigh`) | линзы GLM 5.3 Flash ×3 в pi, при находке — Grok 4.7 xhigh |
| `high-pipeline` | расклад по умолчанию; ~$4 | Opus 5.5 high (`pipeline-spec-writer`, `model: opus`) | Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`) + DeepSeek V4.1 Flash в pi + devin SWE-2 max, кворум, сводит оркестратор | Opus 5.5 high (`pipeline-implementer-high`, `model: opus`) | линзы GLM 5.3 Flash ×3 в pi, при находке — Grok 4.7 xhigh |
| `medium-pipeline` | работа понятная, хватит пониженного усилия; ~$3 | Opus 5.5 medium (`pipeline-spec-writer-medium`, `model: opus`) | Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`) + DeepSeek V4.1 Flash в pi, кворум, сводит оркестратор | Opus 5.5 medium (`pipeline-implementer`, `model: opus`) | Grok 4.7 high |
| `sol-pipeline` | проверяющие от OpenAI вместо Grok, автор выбрал сам | Opus 5.5 medium (`pipeline-spec-writer-medium`, `model: opus`) | Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`) + GPT-6.1 Sol medium в Codex, кворум, сводит оркестратор | Opus 5.5 medium (`pipeline-implementer`, `model: opus`) | GPT-6.1 Sol high в Codex |
| `low-pipeline` | поджимает лимит Max: код вне квоты; ~$3 | Opus 5.5 low (`pipeline-spec-writer-low`, `model: opus`) | DeepSeek V4.1 Flash + GLM 5.3 Flash в pi, кворум, сводит оркестратор | devin SWE-2 max (`devin:devin-delegate --thinking max`) | Grok 4.7 high |
| `xlow-pipeline` | задача в один прогон, нужна независимая приёмка | — | — | devin SWE-2 max (`devin:devin-delegate --thinking max`) | Grok 4.7 high |
| `nano-pipeline` | то же, дешевле; при пустом `write_scope` сперва ход на чтении за границами правки | — | — | devin SWE-2 high (`devin:devin-delegate --thinking high`) | GLM 5.3 Flash (`pi:pi-delegate --channel glm`) |
| `cross-pipeline` | автор ТЗ и исполнитель на разных вендорах: Devin пишет ТЗ, GLM в pi — код | Devin (SWE-2, max) | DeepSeek V4.1 Flash в pi + Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`), кворум, сводит оркестратор | GLM 5.3 Flash в pi | Grok 4.7 xhigh |
| `opus-pipeline` | понятная работа в один заход, приёмка не нужна; $0 внешних | — | — | Opus medium (`pipeline-implementer-solo`, `model: opus`), сам коммитит | — |
| `epic-pipeline` | эпик, автор выбрал состав сам | Opus 5.5 high (`pipeline-spec-writer`, `model: opus`) | Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`) + GPT-6.1 Sol medium в Codex, кворум, сводит оркестратор | Sonnet 5.5 high (`pipeline-implementer-high`) | коллегия Sonnet 5.5 high (`pipeline-judge`, `model: sonnet`) + Opus 5.5 high (`pipeline-judge`) + GPT-6.1 Sol high в Codex, единогласно, коммитит оркестратор |
| `feat-pipeline` | фича, автор выбрал состав сам | Opus 5.5 medium (`pipeline-spec-writer-medium`, `model: opus`) | Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`), один, кворум — он | Sonnet 5.5 high (`pipeline-implementer-high`) | коллегия Sonnet 5.5 high (`pipeline-judge`, `model: sonnet`) + Opus 5.5 high (`pipeline-judge`) + GPT-6.1 Sol high в Codex, единогласно, коммитит оркестратор |
| `refactor-pipeline` | рефакторинг: внешнее поведение не меняется | Opus 5.5 medium (`pipeline-spec-writer-medium`, `model: opus`) | Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`), один, кворум — он | Sonnet 5.5 high (`pipeline-implementer-high`) | коллегия Sonnet 5.5 high (`pipeline-judge`, `model: sonnet`) + Opus 5.5 high (`pipeline-judge`) + GPT-6.1 Sol high в Codex, единогласно, коммитит оркестратор |
| `bug-pipeline` | баг: первая порция — тест, падающий до правки | Opus 5.5 medium (`pipeline-spec-writer-medium`, `model: opus`) | — | Sonnet 5.5 high (`pipeline-implementer-high`) | коллегия Sonnet 5.5 high (`pipeline-judge`, `model: sonnet`) + Opus 5.5 high (`pipeline-judge`), единогласно, коммитит оркестратор |
| `chore-pipeline` | мелкая правка по описанию, нужна независимая приёмка | — | — | Opus 5.5 medium (`pipeline-implementer`, `model: opus`) | Sonnet 5.5 high (`pipeline-judge`, `model: sonnet`), один судья, сам коммитит |
| `question-pipeline` | вопрос по коду и документам: ответ в карточку, кода и коммита нет | — | — | ответ — `pipeline-answerer`: Haiku, Sonnet 5.5 medium или Opus 5.5 medium по сложности вопроса | — |

## Агенты

Модель и effort — из frontmatter `agents/*.md`. Effort задаётся только во frontmatter; `model` в
вызове перебивает frontmatter.

| Агент | Модель / effort | Роль | Где используется (перебивка model) |
| --- | --- | --- | --- |
| `pipeline-spec-writer` | opus / high | ТЗ шага, порции, чек-листы; только каталог шагов, неясное — вопросом автору | `high-pipeline`, `epic-pipeline` — **`model: opus`** |
| `pipeline-spec-writer-xhigh` | opus / xhigh | то же | `xhigh-pipeline` — **`model: opus`** |
| `pipeline-spec-writer-medium` | opus / medium | то же | `medium-pipeline`, `sol-pipeline`, `feat-pipeline`, `refactor-pipeline`, `bug-pipeline` — **`model: opus`** |
| `pipeline-spec-writer-low` | opus / low | то же | `low-pipeline` — **`model: opus`** |
| `pipeline-critic` | sonnet / high | штатный критик ТЗ и чек-листа (`model: sonnet`), критик `sonnet` в составе | этап 2 xhigh/high/medium/cross/sol/epic/feat/refactor-pipeline |
| `pipeline-implementer` | sonnet / medium | реализует одну порцию, не коммитит | `medium-pipeline`, `sol-pipeline`, `chore-pipeline` — **`model: opus`** |
| `pipeline-implementer-high` | sonnet / high | то же для неочевидных порций | `high-pipeline` — **`model: opus`**; `epic-pipeline`, `feat-pipeline`, `refactor-pipeline`, `bug-pipeline` — без `model` |
| `pipeline-implementer-xhigh` | opus / xhigh | то же на максимальном усилии | `xhigh-pipeline` |
| `pipeline-implementer-solo` | sonnet / medium | задача в один проход и сам коммитит | `opus-pipeline` — **`model: opus`** |
| `pipeline-judge` | opus / high | приёмка: чек-лист, срезанные углы в диффе, вердикт, при зелёном — коммит; код не правит | коллегия `epic`/`feat`/`refactor`/`bug-pipeline`: судья `sonnet` — **`model: sonnet`**, судья `opus` — без `model`; `chore-pipeline` — один судья, **`model: sonnet`** |
| `pipeline-answerer` | sonnet / medium | ответ на вопрос по коду и документам проекта; пишет только файл ответа, ничего не правит и не коммитит | `question-pipeline` — `sonnet` без `model`; `haiku` или `opus` — **`model`** по выбору оркестратора |

Исполнители и судья преднагружают скил `listik:listik` (поле `skills:`) и при названном в задаче id (`Listik, карточка <id>`) ведут карточку сами — раздел «Карточка Listik» в теле агента.

## Хуки

`hooks/hooks.json` → `approve-pipeline-agents.py` на `PermissionRequest` (Bash/Edit/Write/MultiEdit/
NotebookEdit): автоодобряет запросы исполнителей и судьи, только если в настройках плагина включён
`auto_approve_agents`, правка лежит в проекте/его worktree/`.git/feature-pipeline` и не секрет,
команда не из списка опасных. Иначе и при ошибке хук молчит — обычный запрос; deny-правила сильнее.

## references

- `pipeline-core.md` — общий протокол пресетов: правила, цикл, вопросы (в т.ч. headless под роем),
  шаг 0, имена бумаг и деревьев, треки, пакет диффа, коммит приёмкой, приёмка линзами, пределы, журнал, Listik.
- `ROLES.md` — почему на роли поставлены эти модели; таблицы генерирует `presets.py`.
- `README.md` + `fetch_aa.py`, `fetch_openrouter.py`, `models.*`, `openrouter.*` — снимки рейтингов
  моделей и скрипты их обновления.
