---
name: devin
description: "Listik-pipeline, режим devin: ТЗ и критика, код пишет devin, принимает судья Sonnet — без линзы. Вызывай для обычной задачи, которую надо провести через ТЗ и критику и отдать в код devin."
disable-model-invocation: true
argument-hint: "<описание задачи или номер карточки Listik>"
---

# Listik-pipeline · devin

Режим этого запуска — `devin`: ТЗ, критика, код и судья. Линзы нет — судья зовётся на каждую порцию.

## Раскладка

Источник — маршрут `lp-devin` в Listik. Берёшь его до первого действия:

```bash
listik routes --json | python3 -c 'import json,sys; d=json.load(sys.stdin); d=d.get("routes", d) if isinstance(d, dict) else d; r=[x for x in d if x.get("key")==sys.argv[1]]; print(json.dumps(r[0]["roles"], ensure_ascii=False, indent=1) if r else "NO_ROUTE")' lp-devin
```

Роли пришли — идёшь по ним, они главнее таблицы ниже (формат — `${CLAUDE_PLUGIN_ROOT}/references/lineups.md`, «Маршрут»). `NO_ROUTE`, ошибка или Listik не отвечает — таблица ниже, в журнал карточки строка `ROUTE: fallback skill table`. Модель канала pi (`glm`, `deepseek`) в задание не кладёшь: мост сам берёт её из Listik (`listik harnesses pi --json`).

| Роль | Харнесс | Модель | Усилие | Вызов |
| --- | --- | --- | --- | --- |
| ТЗ | claude | Opus 5.5 | medium | `Agent listik-pipeline:writer-medium`, `model: opus` |
| Критика | pi deepseek | DeepSeek V4.1 Flash | high | скил `pi:pi-delegate --channel deepseek --thinking high` (зовёт писатель) |
|  | pi glm | GLM 5.3 Flash | high | скил `pi:pi-delegate --channel glm --thinking high` (зовёт писатель) |
| Код | devin | SWE-2 | max | `devin-run.sh run --permission write --thinking max` |
| Судья | claude | Sonnet 5.5 | high | `Agent listik-pipeline:judge-high`, `model: sonnet` |

Линза — нет в этом режиме.

Правила потока — `${CLAUDE_PLUGIN_ROOT}/references/orchestrator.md`: читаешь его до первого действия, он единственный источник. Механика вызова ролей (задание, команды, окна) — `${CLAUDE_PLUGIN_ROOT}/references/lineups.md`. Тела ролей — в `${CLAUDE_PLUGIN_ROOT}/references/`: этот абсолютный путь и уходит внешнему харнессу.

Запуск скила — согласие автора на отправку задачи, ТЗ, чек-листа и порции наружу (критикам и исполнителю на другие харнессы). Коммит — всегда оркестратор.
