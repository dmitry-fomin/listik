# Приёмка. Шаг 06, порция b — режим телефона: очередь по страницам. Заход r1

Вердикт: **красный** (один красный пункт: смоук читает подписи бейджа и кнопки «показать ещё»
после клика по ней, поэтому пункты 8 и 9 чек-листа не сходятся на собственном инструменте порции).

Проверял: `docs/specs/steps/step-06.check-b.md` против рабочего дерева
`/Users/dmitry.fomin/Projects/Listik` (порция не закоммичена, HEAD = `32e0ec5` — порция a).

## Как гонял

- `cd web && npm run typecheck`, `env -u VITE_API_BASE -u VITE_LISTIK_TOKEN npm run build`.
- Мок: `node scripts/mock-api.mjs 8788 --fill=50`; vite на 5177
  (`VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token`).
- Эталон «как было»: `git worktree add <scratch>/base HEAD` + vite на 5178 с тем же моком; смоук
  **новым** скриптом по старому коду (одинаковые инструменты на обеих сторонах).
- Смоук: 360, 800, 1024, 1440 на новом коде; 800, 1024, 1440 на эталоне.
- Свой CDP-сценарий (`<scratch>/judge-b-probe.mjs`, `<scratch>/judge-b-order.mjs`) для ручных
  пунктов 12–18 и геометрии строк: выбор проекта, «Обновить» после двух страниц, поиск Enter,
  токен, 360 ↔ 1100 в одной сессии, порядок строк на свежем моке и на живом сервере.
- Живой сервер: `LISTIK_DB=<scratch>/step06.db ./bin/listik --local init`, четыре задачи через
  `listik new`, одна взята `claim`, `./bin/listik --port 8797 serve`, vite на 5180.
- Рабочее дерево не перекладывал: ни `stash`, ни `checkout`, ни `reset`. Эталон — отдельный
  worktree (он остался зарегистрированным, см. «Хвосты»).

## Красные пункты

1. **Пункты 8 и 9. `phone.queueBadge` и `phone.moreLabel` в смоуке измеряются после клика по
   «Показать ещё», а не до него.** `web/scripts/smoke.mjs:259-260` запоминает элементы
   (`badgeEl`, `moreEl`), `:264` кликает по кнопке и ждёт 900 мс, а `innerText` читается только в
   возвращаемом объекте — `:290` (`queueBadge`) и `:292` (`moreLabel`), то есть уже по
   изменившемуся DOM. Смоук 360 печатает `"queueBadge": "40 из 55"` и
   `"moreLabel": "Показать ещё 15"` при `"rowsVisible": 20` и `"rowsAfterMore": 40` — в одном
   блоке смешаны состояния до и после клика. Чек-лист требует `phone.queueBadge === '20 из 55'`
   (п. 8) и `phone.moreLabel === 'Показать ещё 20'` (п. 9); ТЗ (B7.2) перечисляет `queueBadge`,
   `moreVisible`, `moreLabel` до `rowsAfterMore` и оговаривает «в самом конце блока, после клика»
   только для `requests`. Первая страница очереди этим инструментом не измеряется вовсе.
   Само приложение здесь ведёт себя верно — своим CDP-сценарием до клика получил
   `{ rows: 20, badge: "20 из 55", more: "Показать ещё 20" }`; дефект именно в пробе.

## Зелёные пункты

1. **зелёный.** `npm run typecheck` — чисто; `npm run build` без `VITE_*` — собрано
   (`dist/assets/index-*.js` 331.59 kB).
2. **зелёный.** `git status`: изменены ровно `web/scripts/smoke.mjs`, `web/src/App.vue`,
   `web/src/assets/app.css`, `web/src/components/AppHeader.vue`, `web/src/store/listik.ts`,
   новые `web/src/components/PhoneQueue.vue`, `web/src/lib/viewport.ts`. Прочее в статусе —
   бумаги шага, к порции не относятся.
3. **зелёный.** `App.vue`: десктопное содержимое целиком внутри `<template v-else>`, в диффе
   только сдвиг отступа и обёртка; порядок, пропсы и обработчики не тронуты. `SearchPanel`
   вынесен один общий сразу после ветки — это прямо разрешено ТЗ (B4.5). `TaskDrawer`, модалка
   токена, `ProjectSettings`, `NewTaskModal`, `UiToast` в диффе не участвуют.
4. **зелёный.** Стор: десктопная ветка `refresh()` не изменена (перед ней добавлен только ранний
   выход `if (phone.value) { await refreshPhone(options); return }`), `act()`, `loadListTasks()`,
   фильтры и поиск не тронуты; добавлены `phone`, `queueTick`, `setPhone`, `loadQueuePage`,
   модульный флаг `initialised`. `loadQueuePage` (`store/listik.ts:839-855`) передаёт в
   `api.tasks` только `project` (если непуст), `limit`, `offset`, `order: 'updated'` — и это же
   видно в Network: `GET /api/tasks?limit=20&offset=0&order=updated`.
5. **зелёный.** 360: `clientWidth 360`, `scrollWidth 360`, `innerWidth 360`,
   `layout.viewport.width 360` — контент влезает (на эталоне порции a в той же эмуляции
   `innerWidth`/`scrollWidth` = 376 при `clientWidth` 360; на 800 эталон давал
   `desktopOverflow 804`, новый код — 800). `errors` — единственный 404 (favicon), `console` пуст.
   Виновник переполнения на 360 — сама десктопная ветка (тулбар с полем поиска
   `--listik-search-w` + вкладки + инбокс) и контейнер `100% - 64px`; отдельных правил в блоке
   767 px не потребовалось.
6. **зелёный.** 360: `tabsVisible false`, `inboxVisible false`, `chipsVisible 0`; `views.*` —
   «кнопки нет» по всем четырём; `newTask` — «кнопки нет»; `drawer` — «карточек нет».
7. **зелёный.** `projectSelectVisible true`, `searchVisible true`.
8. **частично (см. красный 1).** `rowsVisible 20`, `firstRowTop 189` (≤ 240),
   `rowsFullyVisible 3` — сходится; `queueBadge` не сходится.
8a. **зелёный.** 360: `containerBox = { width: '336px', left: 12 }`; 700: `width '560px'`,
   `left 70`; строка `.listik-mobile-task` — `left 44`, `right 316` при
   `padding-inline` контейнера 32 px. До починки на 800 было `'736px'`/`left 32`.
9. **частично (см. красный 1).** `moreVisible true`, `rowsAfterMore 40` — сходится;
   `moreLabel` не сходится.
10. **зелёный.** 360, `phone.requests`: `board 0`, `ready 0`, `blocked 0`, `stats 0`,
    `timeline 0`, `taskDetail 0`, `tasksList 3` (в допуске 2–3), `health 2`, `meta 2`.
11. **зелёный.** `headerButtons` = `["Включить светлую тему", "icon"]` (две кнопки: тема и
    «Обновить» без подписи). «Репозитории» нет ни в тексте, ни в `innerHTML` шапки, ни в тексте
    `body` — `v-if="!phone"` в `AppHeader.vue:117`.
12. **зелёный.** На свежем моке порядок строк ровно серверный: `listik-web-a1b2`,
    `listik-sse-e5f6`, `listik-epic-k9l0`, `listik-metrics-g7h8`, `listik-api-c3d4`,
    `listik-fill-001`, `listik-fill-002` — совпадает с `GET /api/tasks?limit=7&order=updated` от
    мока, клиентской пересортировки нет. `listik-fill-001` показывает «держателя нет» и
    «heartbeat —». (Первый прогон дал другой порядок, потому что мок к тому времени был
    перетоптан правками из смоуков 800/1024/1440 — `PATCH` в моке двигает `updated_at`;
    перезапустил мок и перепроверил.)
13. **зелёный.** В селекторе при загрузке — «все проекты» (значение `''`, не placeholder).
    Выбор «Заполнитель» → ровно один `GET /api/tasks?project=fill&limit=20&offset=0&order=updated`
    (без `status`/`include_closed`/`needs_owner`), 20 строк, бейдж «20 из 25», кнопка
    «Показать ещё 5»; после нажатия — `GET …project=fill&limit=20&offset=20…`, 25 строк,
    «25 из 25», кнопки нет. Возврат к «все проекты» → один
    `GET /api/tasks?limit=20&offset=0&order=updated`, 20 строк, «20 из 55».
14. **зелёный.** После двух страниц (40 строк) и прокрутки: «Обновить» → `GET /api/health` +
    ровно один `GET /api/tasks?limit=40&offset=0&order=updated`; строк по-прежнему 40, бейдж
    «40 из 55», кнопка «Показать ещё 15», `scrollY` 600 → 600. За всю телефонную часть сессии
    `/api/board`, `/api/ready`, `/api/blocked`, `/api/stats` — ноль (появляются только после
    расширения до 1100, п. 17).
15. **зелёный.** Enter в поле поиска с «панель» → `GET /api/search?q=…&mode=hybrid&limit=20`,
    открывается `UiCommandPalette` с результатом мока; выбор результата открывает `TaskDrawer`.
16. **зелёный.** vite без `VITE_LISTIK_TOKEN` (порт 5179), `localStorage.removeItem`,
    перезагрузка на 360 → модалка «Нужен токен Listik», `/api/tasks` в Network нет; ввод
    `mock-token` → один `GET /api/tasks?limit=20&offset=0&order=updated`, 20 строк, «20 из 55»,
    модалка закрыта.
17. **зелёный.** 360 → 1100 в одной сессии: класс `listik-shell--phone` снят, вкладки, инбокс,
    4 колонки доски, `PhoneQueue` размонтирован, в Network появились `board/stats/ready/blocked/
    done-за-7-дней`. 1100 → 360: снова очередь (20 строк, «20 из 55»), новых `/api/board` нет —
    только `health` и `tasks`.
18. **зелёный.** Живой сервер на временной базе: порядок по `updated_at` убыв.
    (`step06-0lxu`, `-ul33`, `-1n4k`, `-xykd`), у взятой — «держит dmitry.fomin» и
    «heartbeat 1 мин», у остальных — «держателя нет» (не «держит —»).
19. **зелёный с оговоркой по эталону.** 800: `tabsVisible true`, `inboxVisible true`,
    `chipsVisible 6`, `firstRowStyled.display 'flex'`, `requests.board 2`,
    `projectSelectVisible false`, `drawer.ширина 720`, `containerBox.width '768px'`.
    `phone.rowsVisible` = **24**, а не 5, как записано в чек-листе, — но ровно 24 даёт и эталон
    порции a на том же моке `--fill=50` (`MobileTaskList` режет список по `PAGE = 24`); цифра 5
    в чек-листе взята из прогона порции a без `--fill`. Регрессии нет, значение эталонное.
20. **зелёный.** 1024 и 1440: пофайловый diff отчётов эталона и нового кода пуст по всем полям
    (`columns, cards, needsYou, toolbar, views, newTask, drawer, layout`, включая `boardOverflow`
    и `s4Right`); `requests.board 2`, `rowsVisible 0`, `containerBox.width` — `'960px'` и
    `'1376px'`. Единственная разница отчётов — лишние 403 в `errors` на эталоне: это артефакт
    моего стенда (в worktree `node_modules` подложен симлинком, vite отдаёт часть `/@fs/` с 403),
    на измеряемые поля не влияет, на новом коде и на эталоне все числа совпали.
    На 800 разница с эталоном ровно ожидаемая: `containerBox` `736px`/`left 32` → `768px`/
    `left 16` и, как следствие, `scrollWidth` 804 → 800, `firstRowTop` 886 → 851.
20a. **зелёный.** `git diff web/src/assets/app.css`: в медиа-блоках изменены ровно два
    объявления `width` контейнера (добавлен `!important`, значения прежние), базовое правило
    тронуто только комментарием, `padding-inline` нигде не переопределён, новых селекторов в
    медиа-блоках нет.
21. **зелёный.** `document.querySelector('.listik-shell').className`: на 360 и 700 —
    `listik-shell listik-shell--phone`, на 1100 — `listik-shell`.
22. **зелёный.** `web/src/lib/viewport.ts:14-27`: `ref(query.matches)` синхронно при вызове,
    `addEventListener('change')`, снятие в `onScopeDispose`, при отсутствии `window`/`matchMedia`
    — `ref(false)` без подписки; других экспортов нет.
23. **зелёный.** `PhoneQueue.vue` не импортирует `BoardToolbar`/`NeedsYouStrip`, не использует
    `listik-mobile-queue`, не содержит `sort(` и `UiChip` (grep по файлу — пусто).
24. **зелёный.** `AppHeader.vue`: добавлен только проп `phone?: boolean` через `withDefaults`
    (`:30-36`) и `v-if="!phone"` на `UiTooltip` «Репозитории» (`:117`); других изменений нет.

## Разбор диффа (сверх чек-листа)

- Срезанных углов не нашёл: хардкода под тест нет, заглушек нет, правки в тех слоях, что
  названы в ТЗ. `PhoneQueue` берёт данные только через `store.loadQueuePage`, сортировки нет,
  дедупликация по `id` при догрузке есть (`mergeById`), при смене проекта список сбрасывается
  до первой страницы.
- `refreshPhone` не ловит исключения сам, но `loadHealth` (`store/listik.ts:349-357`) и
  `loadMeta` (`:359-365`) свои ошибки гасят через `handleError`, так что `void refresh(...)` из
  `setPhone` не оставит необработанный reject.
- Секретов в диффе нет (`.env`, `*.key`, `*.pem`, `credentials.json`, токены не встречаются).

## Перенесено на приёмку шага

Нет.

## Хвосты стенда

- В репозитории остался зарегистрирован worktree эталона
  `<scratch>/base` (detached HEAD `32e0ec5`): `git worktree remove` отказался — внутри лежат
  незакоммиченные артефакты стенда (симлинк `node_modules`, `dist`). Удаление требует `--force`,
  за подтверждением — к автору.
- Временная база живого сервера `<scratch>/step06.db`; сервер на 8797 и все vite (5177–5180) и
  мок остановлены.
