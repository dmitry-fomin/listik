# listik-pipeline

Конвейер реализации задачи: ТЗ → критика ТЗ → код → приёмка. Оркестратором работает **сессия Claude Code**, а роли — **локальные субагенты и внешние харнессы**: плагины `pi`, `devin`, `codex` и отдельно ставимый `grok`.

Интерактивная схема — `README.html`: клик по станции открывает досье роли, клик по статусу — словарь ответов.

## Что нужно

Claude Code и хотя бы один внешний харнесс: `codex`, `pi`, `devin`, `grok`. Плагин `grok` в маркетплейс этого проекта не входит — ставится отдельно. Нужного в строке режима нет — конвейер стоит до первого действия и спрашивает человека.

## Запуск

```bash
/listik-pipeline:high "Добавить экспорт в CSV"   # описание задачи
/listik-pipeline:nano listik-abc1                # номер карточки Listik
```

Режим задаёт скил запуска, флагов нет: `xhigh`, `high`, `medium`, `low` — ТЗ, критика, код, линза, судья; `xlow`, `nano` — без ТЗ, критики и линз: код и судья.

## Раскладка

Кто на каком харнессе в каждом режиме — [`references/lineups.md`](references/lineups.md). Там же модели, права, команды запуска и формат задания. Здесь не дублируется.

Правила потока — [`agents/orchestrator.md`](agents/orchestrator.md): порядок шагов, переходы, кто коммитит, что делать при выбывшей роли.

## Этапы

```
00 Запуск     человек: описание / карточка         → claim
01 ТЗ         writer → READY + RISK: NO|YES        → set spec_path/checklist_path, stage s1-spec
   02 Критика writer зовёт критиков строки режима → stage s2-review
03 Код        listik worktree; implementer → DONE   → stage s3-impl
04 Линза      lens → PASS / FIXES                  → stage s4-judge (только при FIXES или RISK: YES)
   04 Судья   judge → VERDICT: PASS / FAIL; после FAIL — release и снова s3-impl
05 Закрытие   оркестратор: коммит в дереве, вливание task/<id>, done, удаление worktree
```

## Словарь ответов

Оркестратор не понимает содержания — он сверяет первое слово ответа со словарём статусов: [`references/statuses.md`](references/statuses.md). Там же флаг риска и что делать со сбоем канала.

## Структура

```
listik-pipeline/
├── skills/               # Точки входа: имя скила, и всё
│   ├── xhigh/ … nano/    # Скилы запуска — по режиму, вызывает человек
│   ├── writer/           # Писатель ТЗ (ручной запуск роли)
│   ├── implementer/      # Исполнитель
│   ├── lens/             # Линза приёмки
│   └── judge/            # Судья
├── agents/               # Агенты: усилие во frontmatter, тело роли не копируется
│   ├── orchestrator.md
│   ├── writer-{xhigh,high,medium,low}.md
│   ├── critic-{xhigh,high,medium,low}.md
│   ├── implementer-{xhigh,high,medium,low}.md
│   ├── lens-{xhigh,high,medium,low}.md
│   └── judge-{xhigh,high,medium,low}.md
└── references/           # Тело роли, одно на все усилия
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

Имена файлов: ТЗ — `<id>.spec.md`, чек-лист — `<id>.checklist.md`, где `<id>` — идентификатор задачи (например, `listik-abc1`). Подробности формата — в `references/writer.md`, у роли, которая их пишет.
