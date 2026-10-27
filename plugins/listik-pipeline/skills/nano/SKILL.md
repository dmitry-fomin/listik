---
name: nano
description: "Listik-pipeline, режим nano: без ТЗ, критики и линз — сразу код и судья, самые дешёвые каналы. Вызывай для мелкой правки, которую всё равно надо принять независимой проверкой."
disable-model-invocation: true
argument-hint: "<описание задачи или номер карточки Listik>"
---

# Listik-pipeline · nano

Режим этого запуска — `nano`: писателя и критиков нет — сразу worktree, код и судья. Судья — единственная проверка, флаг `RISK:` не читается.

## Раскладка

Источник — маршрут `lp-nano` в Listik. Берёшь его до первого действия:

```bash
listik routes --json | python3 -c 'import json,sys; d=json.load(sys.stdin); d=d.get("routes", d) if isinstance(d, dict) else d; r=[x for x in d if x.get("key")==sys.argv[1]]; print(json.dumps(r[0]["roles"], ensure_ascii=False, indent=1) if r else "NO_ROUTE")' lp-nano
```

Роли пришли — идёшь по ним, они главнее таблицы ниже (формат — `${CLAUDE_PLUGIN_ROOT}/references/lineups.md`, «Маршрут»). `NO_ROUTE`, ошибка или Listik не отвечает — таблица ниже, в журнал карточки строка `ROUTE: fallback skill table`. Модель канала pi (`glm`, `deepseek`) в задание не кладёшь: мост сам берёт её из Listik (`listik harnesses pi --json`).

| Роль | Харнесс | Модель | Усилие | Вызов |
| --- | --- | --- | --- | --- |
| Код | devin | SWE-2 | high | `devin-run.sh run --permission write --thinking high` |
| Судья | pi glm | GLM 5.3 Flash | high | `pi-run.sh run --permission write --channel glm --thinking high` |

ТЗ, Критика, Линза — нет в этом режиме.

Правила потока — `${CLAUDE_PLUGIN_ROOT}/references/orchestrator.md`: читаешь его до первого действия, он единственный источник. Механика вызова ролей (задание, команды, окна) — `${CLAUDE_PLUGIN_ROOT}/references/lineups.md`. Тела ролей — в `${CLAUDE_PLUGIN_ROOT}/references/`: этот абсолютный путь и уходит внешнему харнессу.

Запуск скила — согласие автора на отправку задачи и диффа порции наружу (судье на другой харнесс). Коммит — всегда оркестратор.
