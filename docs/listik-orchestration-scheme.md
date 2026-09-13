# Листик как шина между харнессами — схема и задачи

## Схема (согласовано 2026-09-10)

**Роли.** Листик — единственная очередь и память между харнессами. Крон-диспетчер и
интерактивные сессии (Claude Code, dsh, Codex, Grok) делают одно и то же:
`ready --harness X → claim → работа + heartbeat → stage | needs-owner | done`.
Диспетчер — скрипт, не нейронка: фильтр ready по харнессу и приоритету, claim, запуск.

**Единица передачи — цикл, а не этап.** Переходы типизированы:

| Переход | Тип | Смысл |
| --- | --- | --- |
| s1 → s2 | sticky | ТЗ и критика в одной сессии, holder остаётся (работает возобновление субагента) |
| s2 → s3 | handoff | holder снят, задача в ready, берёт любой разрешённый харнесс |
| s3 → s4 | sticky | код и приёмка в одной сессии |
| s4 → s3 (красный) | sticky с окном | тот же holder доводит; не подхватил за N мин — release, в ready холодным стартом |
| s4 → done | handoff | закрытие |

Умолчания переопределяются на проекте.

**Тест холодного старта.** Перед handoff всё нужное следующему этапу лежит в карточке:
`spec_path`, чек-лист, `branch`/`worktree`, журнал (`comment kind=journal`),
вердикт (`kind=verdict`). Принимающий начинает с `listik show <id>` и ничего больше.

**Маршрутизация — данные проекта.** Таблица `этап → допустимые харнессы`, например
`s1: claude · s2: grok, gemini · s3: dsh, codex · s4: claude`. `ready?harness=` отдаёт только
подходящее; скилы всех харнессов одинаковы по протоколу.

**Лок на репо.** `claim` защищает задачу, не дерево: один пишущий агент на репозиторий
(позже — worktree на задачу).

**Доска.** Полоса `needs_you` (needs_owner + stale + abandoned) — ежедневный инбокс;
колонки по этапам с holder и `stage_hours` — для наблюдения, не для перетаскивания.

**Отложено.** Учёт квот подписок — считаем безлимитными.

## Задачи для Листика

Эпик и подзадачи; порядок — сверху вниз, первые четыре меняют сервер.

```
listik new "Листик как шина между харнессами" -p listik --type epic --priority 1 --actor me
listik new "Тип перехода этапа: sticky / handoff, умолчания s1→s2 sticky, s2→s3 handoff, s3→s4 sticky, s4→done handoff; настройка на проекте; handoff снимает holder" -p listik --type feature --priority 1
listik new "Таблица маршрутизации проекта (этап → харнессы) и фильтр ready?harness=" -p listik --type feature --priority 1
listik new "Красный вердикт: stage назад с сохранением holder и окном ожидания; по истечении release в ready" -p listik --type feature --priority 2
listik new "Лок «один пишущий агент на репозиторий» при claim" -p listik --type feature --priority 2
listik new "Крон-диспетчер: ready по харнессу и приоритету → claim → запуск харнесса" -p listik --type feature --priority 2
listik new "Общий скил-протокол рабочего (ready/claim/heartbeat/stage/needs-owner/done) + обёртки под claude, dsh, codex, grok" -p listik --type feature --priority 2
listik new "Тест холодного старта: чек-лист полей карточки перед handoff" -p listik --type chore --priority 3
```

После создания связать подзадачи с эпиком (`deps --dep_type parent-child`) и поставить
`blocks` от «тип перехода» и «маршрутизация» к диспетчеру и скилам.
