# listik-pipeline

Конвейер реализации задачи: ТЗ → критика ТЗ → код → приёмка. Оркестратором работает **сессия Claude Code**, а роли — **локальные субагенты и внешние харнессы**: плагины `pi`, `devin`, `codex` и отдельно ставимый `grok`.

Интерактивная схема — `README.html`: клик по станции открывает досье роли, клик по статусу — словарь ответов.

## Что нужно

Claude Code и внешние харнессы строки режима — каждому режиму нужно минимум два (например, `high`: `pi` + `grok`; `nano`: `devin` + `codex`). Плагин `grok` в маркетплейс этого проекта не входит — ставится отдельно. Нужного в строке режима нет — конвейер стоит до первого действия и спрашивает человека.

## Запуск

```bash
/listik-pipeline:high "Добавить экспорт в CSV"   # описание задачи
/listik-pipeline:nano listik-abc1                # номер карточки Listik
```

Режим задаёт скил запуска, флагов нет: `xhigh`, `high`, `medium`, `low` — ТЗ, критика, код, линза, судья; `xlow`, `nano` — без ТЗ, критики и линз: код и судья.

## Раскладка

Кто на каком харнессе в каждом режиме — [`references/lineups.md`](references/lineups.md). Там же модели, права, команды запуска и формат задания. Здесь не дублируется.

Правила потока — [`references/orchestrator.md`](references/orchestrator.md): порядок шагов, переходы, кто коммитит, что делать при выбывшей роли.

## Этапы

```
00 Запуск     человек: описание / карточка         → нет id — listik new
01 ТЗ         stage s1-spec → writer → DRAFT
02 Критика    stage s2-review → оркестратор зовёт критиков режима
              writer по рецензиям → READY + RISK   → set spec_path/checklist_path
03 Код        listik worktree; implementer → DONE   → stage s3-impl
04 Линза      lens → PASS / FIXES                  → на s3-impl; FIXES или RISK: YES → stage s4-judge
   04 Судья   judge → VERDICT: PASS / FAIL; после FAIL — release и снова s3-impl; третий заход без PASS — стоп
05 Закрытие   оркестратор: коммит в дереве, rebase + merge --ff-only task/<id>, done, удаление worktree и ветки
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
│   ├── xhigh/ … nano/    # Скилы запуска — по режиму, вызывает человек
│   ├── writer/           # Писатель ТЗ (ручной запуск роли)
│   ├── implementer/      # Исполнитель
│   ├── lens/             # Линза приёмки
│   └── judge/            # Судья
├── agents/               # Агенты: усилие во frontmatter, тело роли не копируется
│   ├── writer-{xhigh,high,medium,low}.md
│   ├── critic-{xhigh,high,medium,low}.md
│   ├── implementer-{xhigh,high,medium,low}.md
│   ├── lens-{xhigh,high,medium,low}.md
│   └── judge-{xhigh,high,medium,low}.md
├── hooks/                # Автоодобрение команд ролей (настройка auto_approve_agents)
│   ├── hooks.json
│   └── approve-agents.py
└── references/           # Тело роли, одно на все усилия
    ├── orchestrator.md   # Правила потока — читает сессия, не субагент
    ├── lineups.md        # Раскладка: режим → роль → харнесс, модель, усилие, права
    ├── statuses.md       # Словарь статусов и флаг риска
    ├── writer.md
    ├── critic.md
    ├── implementer.md
    ├── lens.md
    └── judge.md
```

Не все варианты в `agents/` задействованы текущей раскладкой: `critic-low`, `implementer-low`,
`lens-xhigh`/`lens-high` и все `judge-*` — запас под будущие режимы (`references/lineups.md`).
## Конвенции

Бумаги — в `docs/specs/` основного дерева: ТЗ — `<id>.spec.md`, чек-лист — `<id>.checklist.md`, рецензии — `<id>.review-<критик>.md`, задание судье — `<id>.judge.r<N>.task.md`, где `<id>` — идентификатор задачи (например, `listik-abc1`). Формат ТЗ и чек-листа — `references/writer.md`, рецензии — `references/critic.md`.
