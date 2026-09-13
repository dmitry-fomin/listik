# Чек-лист приёмки. Шаг 06, порция b — режим телефона: очередь по страницам

ТЗ: `docs/specs/steps/step-06.b.md`. Все команды — из `web/`, мок `node scripts/mock-api.mjs 8788
--fill=50`, vite на 5177 с `VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token`.
Эталон «как было» для десктопа/планшета — smoke на коммите порции a с тем же моком.

## Сборка и границы

1. `npm run typecheck` и `npm run build` (без `VITE_*`) проходят.
2. `git status`: изменены только `web/src/lib/viewport.ts` (новый), `web/src/store/listik.ts`,
   `web/src/components/PhoneQueue.vue` (новый), `web/src/components/AppHeader.vue`,
   `web/src/App.vue`, `web/src/assets/app.css`, `web/scripts/smoke.mjs`.
3. `git diff web/src/App.vue`: десктопное содержимое `.listik-shell__top` (алерт «Циклы», строка
   вкладок, `NeedsYouStrip`, `BoardToolbar`, четыре вида) перенесено внутрь `<template v-else>`
   без изменения строк (diff показывает только сдвиг отступа/обёртку), `TaskDrawer` и модалки
   не тронуты.
4. `git diff web/src/store/listik.ts`: десктопная ветка `refresh()`, `act()`, `loadListTasks()`
   не изменены; добавлены `phone`, `queueTick`, `setPhone`, `loadQueuePage`, флаг `initialised`;
   `loadQueuePage` передаёт в `api.tasks` только `project`, `limit`, `offset`, `order: 'updated'`
   (проверяется чтением).

## Телефон 360×740 (smoke 360, мок `--fill=50`)

5. Контент влезает в 360: `phone.clientWidth === 360`, `phone.scrollWidth <= phone.clientWidth`
   и `phone.innerWidth === 360` (то же — `layout.viewport.width === 360`). На коммите порции a
   в той же эмуляции `innerWidth`/`scrollWidth` = 376 при `clientWidth` 360 — контент был шире
   экрана (пункт перенесён с приёмки порции a). `errors` — не более 404 favicon, `console` пуст.
6. `phone.tabsVisible === false`, `phone.inboxVisible === false`, `phone.chipsVisible === 0`
   (в порции a на 360 было `true / true / 6`); `views.*` — «кнопки нет» по всем четырём
   вкладкам; `newTask` — «кнопки нет»; `drawer` — «карточек нет».
7. `phone.projectSelectVisible === true`, `phone.searchVisible === true`.
8. `phone.rowsVisible === 20`, `phone.queueBadge === '20 из 55'`, `phone.firstRowTop <= 240`,
   `phone.rowsFullyVisible >= 3` (в порции a на 360 первая строка была на ≈1170 px).
8a. Боковые поля контейнера на телефоне (формулы — от `phone.clientWidth`, не от `innerWidth`):
   при 360 `phone.containerBox.width === '336px'` (`clientWidth - 24`) и
   `phone.containerBox.left === 12` (до починки: `'296px'`/`32` — базовое `!important`
   перебивало медиа-правило); при 700 — `'560px'`; строки очереди `.listik-mobile-task` имеют
   `getBoundingClientRect().left === 44` (12 поля + 32 внутреннего отступа кита) и
   `right === phone.clientWidth - 44` (316 при 360).
9. `phone.moreVisible === true`, `phone.moreLabel === 'Показать ещё 20'`,
   `phone.rowsAfterMore === 40`.
10. `phone.requests`: `board 0`, `ready 0`, `blocked 0`, `stats 0`, `timeline 0`, `taskDetail 0`,
    `tasksList` от 2 до 3 (первая страница + «ещё»; допустим один дубль от `queueTick` при
    инициализации), `health >= 1`, `meta >= 1`.
11. `phone.headerButtons.length === 2` (тема и «Обновить» без подписи; кнопки «Репозитории» в DOM
    нет — `document.querySelector('.ui-app-header')` не содержит текста «Репозитории» даже
    скрытого; проверить дополнительно `grep`/DevTools).
12. Первые пять строк — базовые задачи мока по убыванию `updated_at`: у `listik-web-a1b2`,
    `listik-sse-e5f6`, `listik-epic-k9l0`, `listik-metrics-g7h8` оно одинаковое (`iso(0.3)`), и
    стабильная сортировка мока оставляет их в порядке массива — именно в этом порядке; пятая —
    `listik-api-c3d4` (30 ч); далее `listik-fill-001`, `002`, … — порядок сервера, без клиентской
    пересортировки. Строка `listik-fill-001` (open, без держателя) показывает «держателя нет»
    и «heartbeat —».

## Ручные проверки (DevTools, устройство 360×740, Network с фильтром `api/`)

13. При загрузке в селекторе проекта показан пункт «все проекты» (первый в списке, не
    placeholder). Выбор проекта «Заполнитель» → **один** запрос
    `/api/tasks?project=fill&limit=20&offset=0&order=updated` (без `status`, `include_closed`,
    `needs_owner`), 20 строк, бейдж «20 из 25», кнопка «Показать ещё 5»; после нажатия — 25
    строк, бейдж «25 из 25», кнопки нет. Выбор пункта «все проекты» → один запрос
    `/api/tasks?limit=20&offset=0&order=updated` (без `project`), снова 20 строк, «20 из 55».
14. После двух страниц (40 строк) прокрутить вниз и нажать «Обновить» в шапке: один запрос
    `/api/tasks?limit=40&offset=0&order=updated`, строк по-прежнему 40, `scrollY` не изменился,
    кнопка «Показать ещё 15» на месте. Запросов `/api/board`, `/api/ready`, `/api/blocked`,
    `/api/stats` в Network за всю сессию — ноль.
15. Enter в поле поиска с текстом `панель` → открывается палитра `UiCommandPalette` с результатами
    мока, выбор результата открывает `TaskDrawer` (в этой порции панель ещё десктопная — это
    ожидаемо).
16. Токен: vite запущен **без** `VITE_LISTIK_TOKEN`, `localStorage.removeItem('listik.token')`,
    перезагрузка на 360 → модалка «Нужен токен Listik», в Network нет `/api/tasks`; ввод
    `mock-token` → список загружается (один `/api/tasks?limit=20…`), модалка закрыта.
17. Смена ширины в одной сессии: 360 → 1100 — появляются вкладки, инбокс, доска; в Network
    появляется `/api/board`; 1100 → 360 — снова очередь по страницам, новых `/api/board` нет.
18. Живой сервер с временной базой (`step-06.md`, «Как проверять»), 3 задачи через `bin/listik
    create`, одна взята `claim`: на 360 порядок по `updated_at` убыв., у взятой — «держит …» и
    «heartbeat …», у остальных — «держателя нет» (не «держит —»).

## Планшет и десктоп не изменились

19. smoke 800: `phone.tabsVisible true`, `phone.inboxVisible true`, `phone.chipsVisible 6`,
    `phone.rowsVisible 5`, `phone.firstRowStyled.display 'flex'`, `phone.requests.board >= 1`,
    `phone.projectSelectVisible false` (тулбара телефона нет), `drawer.ширина 720`;
    `phone.containerBox.width === '768px'` (до починки `'736px'` — единственное ожидаемое
    отличие планшета от эталона порции a, это исправление медиа-правила 900 px).
20. smoke 1024 и 1440: поля `columns, cards, needsYou, toolbar, views, newTask, drawer, layout`
    идентичны эталону порции a; `phone.requests.board >= 1`, `phone.rowsVisible 0`;
    `phone.containerBox.width` — `'960px'` и `'1376px'` соответственно (как на эталоне: медиа-
    правила там не участвуют, `layout.boardOverflow`/`s4Right` не изменились).
20a. `git diff web/src/assets/app.css` в медиа-блоках 900/767: изменены ровно два объявления
    `width` контейнера (добавлен `!important`), базовое правило и его значения не тронуты,
    `padding-inline` контейнера нигде не переопределён.
21. `.listik-shell--phone` отсутствует на корне при ширине ≥ 768 и присутствует при 360
    (`document.querySelector('.listik-shell').classList`).

## Код (проверяется чтением)

22. `useIsPhone()` читает `matchMedia` синхронно при создании, снимает подписку через
    `onScopeDispose`, при отсутствии `matchMedia` возвращает `ref(false)`.
23. `PhoneQueue.vue` не импортирует ничего из `BoardToolbar`/`NeedsYouStrip`, не использует класс
    `listik-mobile-queue`, не содержит `sort(`, не содержит `UiChip`.
24. В `AppHeader.vue` кнопка «Репозитории» под `v-if="!phone"`; других изменений нет.
