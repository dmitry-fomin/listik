# Приёмка. Шаг 06, порция c — карточка задачи на телефоне. Заход r2

Вердикт: **зелёный**. Красный пункт r1 (п. 17) закрыт. Коммит сделан.

Повторный заход: смотрел только разницу между `step-06.diff-c.r1.txt` и `step-06.diff-c.r2.txt`.
Полную проверку заново не гонял — пункты 1–16, 18–21 закрыты в r1
(`docs/specs/steps/step-06.judge-c.r1.md`), к ним фикс не прикасается, но задетые им пробы
(сборка, smoke 360/800/1440) перепрогнаны.

## Что изменилось между заходами

Дифф r2 минус дифф r1 — ровно три места, только про ширину шапки:

- `web/src/components/PhoneTaskSheet.vue:98` и `:111` — к обоим `div.listik-drawer__head`
  в слоте `#header` добавлен второй класс `listik-phone-sheet__head` (ветка с `task` и ветка
  «Задача»). Больше в компоненте не тронуто ничего: разметка секций, пропсы, `emit`,
  вычисления — байт в байт как в r1.
- `web/src/assets/app.css:154–163` — два новых правила в разделе «Телефон (шаг 06)»:
  `.listik-phone-sheet__head { overflow-wrap: anywhere; min-width: 0 }` и
  `.listik-phone-sheet__head .listik-drawer__title { overflow-wrap: anywhere }`, с комментарием
  почему (`#header` рендерится вне `div.listik-phone-sheet`).
- `web/src/components/PhoneTaskSheet.vue:195–199` — комментарий-хвост про стили дополнен
  упоминанием нового класса.

`App.vue`, `smoke.mjs`, `README.md` в r2 не менялись (побайтно те же куски диффа).
Токенов кита правило не ломает: `anywhere`/`0` — не `px` и не `#hex`, `!important` не добавлен.
Класс `.listik-phone-sheet__head` встречается только в `PhoneTaskSheet.vue`
(`grep -rn listik-phone-sheet__head web/src` — две строки шаблона и два правила CSS),
`TaskDrawer.vue` по-прежнему отсутствует в диффе и в `git status`.

## Красный пункт r1 — закрыт

**17. Длинный `spec_path`/id в заголовке не расширяет панель — ЗАКРЫТ.**
Живой сервер на временной базе (`LISTIK_DB`/`LISTIK_CONFIG` в scratchpad, порт 8799,
vite 5179), эмуляция 360×740 через CDP, та же задача с 65-символьным заголовком без пробелов
«ОченьДлинныйЗаголовокБезПробеловДляПроверкиШириныПанели1234567890»:

| элемент | r1 (было) | r2 (стало) |
|---|---|---|
| `.ui-drawer` `scrollWidth` | **738** при ширине 360 | **359** при ширине 360 |
| `.ui-drawer__header` `scrollWidth` | 738 при ширине 359 | 359 при ширине 359 |
| `h2.listik-drawer__title` | `scrollWidth 714` при ширине 271, `overflow-wrap: normal` | `scrollWidth 271` при ширине 271, `overflow-wrap: anywhere` |
| `document.documentElement.scrollWidth` | 360 | 360 |

Заголовок переносится внутри шапки, хвост больше не уходит за край экрана.
Тело карточки как и было в норме: `.listik-phone-sheet` 311/311; на задаче
«Заблокирована длинным блокером» (блокер с тем же 65-символьным заголовком)
`.listik-phone-sheet__deps` 311/311, `.ui-drawer` 359.

## Что фикс мог сломать в пределах этой же разницы — не сломал

- Секции карточки на живом сервере на всех четырёх пробах (длинный заголовок, длинный блокер,
  «Без критериев приёмки», «Двадцать пять комментариев»): ровно
  `['Кто держит','Критерии приёмки','Блокеры','Последний review','Журнал']`,
  `actionButtons 0`, `inputs 0`, `.listik-phone-sheet` на месте.
  Заодно подтвердились п. 16: пустые критерии → «—», «Последний review» → «ещё нет» у задачи
  без review, «Журнал 25» с 20 записями и свежей («запись номер 25») сверху, у задачи без
  держателя «держит: никто», heartbeat «—», «что делает» «—», «этап с» «—».
  Консоль — только 404 favicon, `Runtime.exceptionThrown` пусто.
- `npm run typecheck` — чисто; `npm run build` без `VITE_*` — успешно (337.42 kB js / 136.51 kB css).
- smoke 360 (мок `--fill=50`): `sheet.open true`, `title` «Собрать доску канбан для трекера»,
  `drawerWidth 360`, `drawerScrollWidth 359`, пять секций в нужном порядке, `actionButtons 0`,
  `inputs 0`, `timelineItems 2`, `closed true`, `scrollRestored true`,
  `rowsAfterClose 40 === rowsAfterMore 40`; `requests`: `taskDetail 1`,
  `board/ready/blocked/stats/timeline` = 0; `errors` — только 404 favicon, `console` пуст.
- smoke 800: `drawer.открылась true`, `ширина 720`, `естьРазделПроцесса true`,
  `естьХолодныйСтарт true`, `splitButton true`, `кнопок 6`, `phone.sheet null`,
  `phone.scrollWidth 800`.
- smoke 1440: `drawer.*` те же (720, 5 шагов, 6 кнопок, split), `columns 5`, `cards 55`,
  `needsYou 3`, `toolbar 6`, `phone.sheet null`. Правило CSS на планшет/десктоп не действует —
  оно требует класс, которого нет в `TaskDrawer`.

## Границы и срезанные углы

`git status`: изменены ровно `web/README.md`, `web/scripts/smoke.mjs`, `web/src/App.vue`,
`web/src/assets/app.css`, новый `web/src/components/PhoneTaskSheet.vue`; прочее — бумаги шага.
Подгонки под smoke нет: фикс проверяется на живых данных, а не на пробе smoke (в `smoke.mjs`
r2 не трогал вовсе). Секретов в диффе нет (единственное совпадение по шаблону —
`?token=<токен>` в README, плейсхолдер). Временный сервер, мок, оба vite и Chrome остановлены,
временная база — в scratchpad, рабочее дерево не перекладывалось.

## Перенесено на приёмку шага

Нет.

## Коммит

`Шаг 06, порция c: карточка задачи на телефоне — только чтение`,
файлы перечислены явно (пять путей выше).
