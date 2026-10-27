# listik-pipeline

Конвейер реализации задачи: ТЗ → критика ТЗ → код → приёмка. Оркестратором работает **сессия Claude Code**, а роли — **локальные субагенты и внешние харнессы**: плагины `pi`, `devin`, `codex` и отдельно ставимый `grok`.

Интерактивная схема — `README.html`: клик по станции открывает досье роли, клик по статусу — словарь ответов.

## Что нужно

Claude Code и внешние харнессы раскладки режима (например, `high`: `pi` + `grok`; `xlow`: `devin` + `grok`). Плагин `grok` в маркетплейс этого проекта не входит — ставится отдельно. Нужного из раскладки нет — конвейер стоит до первого действия и спрашивает человека.

## Запуск

```bash
/listik-pipeline:high "Добавить экспорт в CSV"   # описание задачи
/listik-pipeline:xlow listik-abc1                # номер карточки Listik
```

Режим задаёт скил запуска, флагов нет: `xhigh`, `high`, `medium`, `low` — ТЗ, критика, код, линза, судья; `xlow` — без ТЗ, критики и линз: код и судья.

## Раскладка

Кто, на какой модели и с каким усилием — в скиле режима (`skills/<режим>/SKILL.md`): маршрут `lp-<режим>` из Listik (`listik routes --json`, файл поставки — `routes.json` в корне Listik), а пока Listik его не отдаёт — таблица скила. Права, команды запуска, формат задания и маршрута — [`references/lineups.md`](references/lineups.md).

Правила потока — [`references/orchestrator.md`](references/orchestrator.md): порядок шагов, переходы, кто коммитит, что делать при выбывшей роли.

## Этапы

```
00 Запуск     человек: описание / карточка         → нет id — listik new
01 ТЗ         stage s1-spec → writer: ТЗ шага + порции a, b, c… с чек-листами, ТЗ в статусе DRAFT
02 Критика    writer: stage s2-review, сам зовёт критиков режима (CRITICS:), не больше двух заходов;
              блокеры во втором заходе → QUESTION → needs-owner
              → READY + RISK                       → set spec_path; порции — ls <id>.?.md
03 Код        listik worktree (одно на шаг)
   по порциям, по очереди:
              set checklist_path; новый implementer → DONE → stage s3-impl
04 Линза      lens → PASS / FIXES                  → на s3-impl; FIXES или RISK: YES → stage s4-judge
   04 Судья   judge → VERDICT: PASS / FAIL; после FAIL — release и снова s3-impl; третий заход без PASS — стоп
   Коммит     оркестратор: коммит порции в дереве → следующая порция
05 Закрытие   после последней порции: rebase + merge --ff-only task/<id>, done, удаление worktree и ветки
   Стоп       needs-owner и тот же вопрос в чат; worktree не трогается
```

## Словарь ответов

Оркестратор не понимает содержания — он сверяет первое слово ответа со словарём статусов: [`references/statuses.md`](references/statuses.md). Там же флаг риска и что делать со сбоем канала.

## Структура

```
listik-pipeline/
├── .claude-plugin/plugin.json  # Манифест и настройка auto_approve_agents
├── README.md, README.html      # Описание и интерактивная схема
├── skills/               # Точки входа: имя скила, и всё
│   ├── xhigh/ … xlow/    # Скилы запуска — по режиму, вызывает человек
│   ├── writer/           # Писатель ТЗ (ручной запуск роли)
│   ├── implementer/      # Исполнитель
│   ├── lens/             # Линза приёмки
│   └── judge/            # Судья
├── agents/               # Агенты: усилие во frontmatter, тело роли не копируется
│   ├── writer-{xhigh,high,medium,low}.md
│   ├── critic-{xhigh,high,medium,low}.md
│   ├── implementer-{xhigh,high,medium,low}.md
│   ├── lens-{max,xhigh,high,medium,low}.md
│   └── judge-{xhigh,high,medium,low}.md
├── hooks/                # Автоодобрение команд ролей (настройка auto_approve_agents)
│   ├── hooks.json
│   └── approve-agents.py
└── references/           # Тело роли, одно на все усилия
    ├── orchestrator.md   # Правила потока — читает сессия, не субагент
    ├── lineups.md        # Механика ролей: задание, запуск, права, формат маршрута
    ├── statuses.md       # Словарь статусов и флаг риска
    ├── writer.md
    ├── critic.md
    ├── implementer.md
    ├── lens.md
    └── judge.md
```

Текущая раскладка (`routes.json`) задействует `writer-*`, `critic-high`, `implementer-{xhigh,high,medium}` и
`lens-max`; остальные варианты — запас под будущие маршруты.

## Конвенции

Бумаги — в `docs/specs/` основного дерева: ТЗ шага — `<id>.spec.md`, порции — `<id>.<X>.md`, их чек-листы — `<id>.check-<X>.md` (`X` — `a`, `b`, `c`…; та же схема, что читает `listik portions sync`), рецензии — `<id>.review-<критик>.md` (второй заход — `.r2.md`), ответ линзы — `<id>.lens-<X>.md`, задание судье — `<id>.judge-<X>.r<N>.task.md`, где `<id>` — идентификатор задачи (например, `listik-abc1`). Формат ТЗ, порций и чек-листов — `references/writer.md`, рецензии — `references/critic.md`.

Порции нужны ради контекста: на каждую запускается новый исполнитель, который видит только её файл, поэтому его контекст не растёт с размером задачи.
