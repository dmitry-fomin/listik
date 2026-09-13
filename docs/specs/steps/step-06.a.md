# Шаг 06, порция a. Инструменты проверки и починка планшета

Часть шага «Ограниченный мобильный просмотр» (`docs/specs/steps/step-06-limited-mobile-view.md`,
план — `docs/specs/steps/step-06.md`). Эта порция **не вводит режим телефона** — она готовит
инструменты (мок с параметрами и наполнением, smoke с эмуляцией устройства) и выносит строку
списка в общий компонент, чтобы следующие порции могли на неё опираться. Код пишется в корне
репозитория Listik (каталог, где лежат `web/`, `listik/`, `docs/`), правится **только `web/*`**.

## Контекст, который нужно знать

- Фронт: `web/`, Vue 3.5 + TS + Vite, `strictTemplates`. Все `Ui*`-компоненты — только из
  `@zoloto585/facet` (`web/node_modules/@zoloto585/facet`, source-only); перед правками прочитать
  `README.md` кита (разделы «Язык системы», «Рецепты layout») и `docs/facet-components.md`.
  Своя разметка/CSS — только там, где кита нет, и только на токенах (`--space-*`, `--text-*`,
  `--ink-*`, `--radius-*`, `--shadow-*`, `--hairline`, `--surface`), без `#hex`/`px` в разметке.
- `web/src/components/MobileTaskList.vue` — компактный список задач, показывается ниже 1024 px
  вместо доски (App.vue рендерит его рядом с `BoardView`; `.listik-board-only` скрыт и
  `.listik-mobile-queue` включён правилом `@media (max-width: 1023px)` в `web/src/assets/app.css`).
  Одна строка — `<button class="listik-mobile-task">` с id, `UiStatusPill` здоровья, заголовком,
  бейджем проекта, этапом, держателем и heartbeat.
- **Дефект:** все правила `.listik-mobile-task`, `.listik-mobile-task__top`, `__title`, `__meta`,
  `__meta--muted`, `:hover/:focus-visible` лежат внутри `@media (max-width: 640px)` (блок
  «Узкий экран» в `app.css`), причём `.listik-mobile-task` там объявлен дважды (первый набор:
  `radius-xl`, `padding space-4`, цвет рамки через `color-mix`; второй, ниже и потому действующий:
  `radius-lg`, `padding space-3`, `border 1px solid var(--hairline)`, `shadow-xs`, `display:flex`
  и т.д.). Итог: на 641–1023 px строки — голые кнопки браузера (`display: inline-block`,
  `border: outset`, `padding: 1px`), проверено CDP на 800 px.
- **Дефект:** строка показывает `держит ${task.holder_title}` по истинности `holder_title`; живой
  сервер для пустого держателя отдаёт `holder_title: '—'` (строка, не пустая), и на сервере
  строка показала бы «держит —». Признак держателя — `task.holder` (ключ актора, `null` без
  держателя).
- В `app.css` два медиа-блока на `640px`: большой «Узкий экран» (body overflow-x, контейнер,
  строки списка, фильтр-бар, drawer на `100vw`, скрытие действий панели, `.listik-dl`,
  `.listik-dep-grid`, шапка — `.listik-shell__brand-name`/`__hide-compact`/`__only-compact`,
  `.listik-shell__search`, `.listik-shell__actions .ui-button__label`, `.listik-column`,
  `.ui-kpi-card`) и маленький для `.listik-newtask-row`. По спеке шага порог телефона — 768 px,
  поэтому оба блока переезжают на `767px`.
- `web/scripts/mock-api.mjs` — заглушка API: 5 задач в массиве `tasks` (`listik-web-a1b2`,
  `listik-api-c3d4` (needs_owner, abandoned), `listik-sse-e5f6` (review, s4), `listik-epic-k9l0`,
  `listik-metrics-g7h8` (blocked_by c3d4)), фабрика `task(overrides)`, хелпер `iso(hoursAgo)`,
  `details(id)` добавляет одинаковые `comments`/`events`/`deps_state` любой задаче из `tasks`;
  `/api/tasks` сейчас: `ok({ total: tasks.length, limit: 25, offset: 0, tasks })` — параметры
  игнорируются. Порт — `process.argv[2]`. `/api/meta` отдаёт `projects: [{slug:'listik',…}]` и
  `facets.projects`; `/api/board` строится из того же массива `tasks`.
- `web/scripts/smoke.mjs` — CDP-смоук: `node scripts/smoke.mjs [url] [width]`, Chrome
  поднимается с `--window-size=${width},900`, затем пробы (`report.columns`, `cards`, `needsYou`,
  `toolbar`, `views`, `newTask`, `drawer`, `layout`, `errors`, `console`) и `console.log(JSON)`.
  **Факт:** headless Chrome не даёт окно уже ≈500 px — запрос `360` даёт `layout.viewport.width: 500`.
  Пробы считают `querySelectorAll(...).length` без проверки видимости: на телефоне при скрытой
  доске всё равно «колонок 4, карточек 4».
- Сервер `listik/store.py list_tasks` (эталон для мока): без `status` и `include_closed` отдаёт
  только статусы `open, in_progress, blocked, review`; `needs_owner=1` → `needs_owner = 1`;
  `order`: `updated` → `updated_at DESC` (по умолчанию и при незнакомом значении), `created` →
  `created_at DESC`, `priority` → `priority ASC, updated_at DESC`, `stage` → `stage_at ASC`;
  `limit` по умолчанию 200, `offset` 0; `total` — число строк после фильтров **до** `limit/offset`.

## Что сделать

### A1. Мок: `/api/tasks` учитывает параметры запроса

В `web/scripts/mock-api.mjs` обработчик `GET /api/tasks` применяет к массиву `tasks` по порядку:

1. `project` — точное равенство `task.project`;
2. `status` — точное равенство; если `status` не задан и `include_closed` не истинен
   (`1`/`true`), остаются только `open, in_progress, blocked, review`;
3. `needs_owner` (`1`/`true`) — только `needs_owner === true`;
4. `order` — как на сервере (см. контекст), сравнение строк ISO для дат, при равенстве —
   порядок массива (стабильная сортировка);
5. `total` = длина после фильтров; затем `slice(offset, offset + limit)`; `limit` по умолчанию 200,
   `offset` — 0; нечисловые значения → значения по умолчанию.

Ответ: `ok({ total, limit, offset, tasks: page })`. Другие параметры (`stage`, `assignee`, `type`,
`text`, `label`) можно не поддерживать — но и не ронять запрос.

### A2. Мок: наполнение `--fill=N`

Аргумент `--fill=N` (любое место в `process.argv` после порта; `N` — целое ≥ 1) добавляет в
массив `tasks` **после** пяти базовых задач `N` сгенерированных, через ту же фабрику `task()`:

- `id`: `listik-fill-001` … `listik-fill-NNN` (три цифры, с ведущими нулями);
- `title`: `Заполнитель NNN: <короткий текст>` — текст различается хотя бы по номеру;
- `project`: чётный индекс (1-based: 2, 4, …) → `'fill'`, нечётный → `'listik'`;
- `status`: чередование `open` / `in_progress` с соответствующими `status_title`;
- `stage`: по кругу `s1-spec, s2-review, s3-impl, s4-judge, null` с `stage_title` из
  `STAGE_TITLES` (для `null` — `null`);
- держатель: у `in_progress` — `holder: 'agent:dsh', holder_title: 'dsh', holder_at: iso(0.1)`,
  `idle_hours: 0.1`, `idle_age: '6 мин'`, `holder_note: null`; у `open` — `holder: null`,
  `holder_title: ''`, `holder_at: null`, `holder_hours: null`, `idle_hours: null`;
- `updated_at: iso(48 + i)` (i — 1-based номер), `updated_age: '${48 + i} ч'`, `created_at: iso(200 + i)`,
  `stage_at: iso(47 + i)`, `stage_warn: false`, `needs_owner: false`, `stale: false`,
  `abandoned: false`, `blocked_by: []`, `labels: []`, `assignee: null`, `assignee_title: ''`,
  `spec_path: null`, `journal_path: null`, `worktree: null`, `branch: null`.

Все сгенерированные задачи старше пяти базовых по `updated_at` (базовые обновлены не позже, чем
30 ч назад), и строго упорядочены по номеру — порядок `order=updated` детерминирован.

При `--fill` `/api/meta` добавляет в `projects` запись `{ slug: 'fill', title: 'Заполнитель',
kind: 'native', n_tasks: <число задач fill> }` и в `facets.projects` — `'fill'`. **Без флага
поведение мока не меняется вовсе**: те же 5 задач, те же ответы `/api/board`, `/api/meta`,
`/api/ready`, `/api/blocked`.

`details(id)` для сгенерированных задач должен работать как для базовых (он ищет в `tasks`).

### A3. Smoke: эмуляция телефона и блок `phone`

В `web/scripts/smoke.mjs`:

1. Если `width < 768`: Chrome поднимается с `--window-size=1200,900`, а после подключения к
   таргету и **до** `Page.navigate` вызывается
   `Emulation.setDeviceMetricsOverride({ width, height: 740, deviceScaleFactor: 2, mobile: true })`.
   Для `width ≥ 768` поведение прежнее (`--window-size=${width},900`, без эмуляции). В
   `report.layout.viewport` должна оказаться реальная ширина `innerWidth === width`.
2. Новый блок `report.phone` считается **всегда** (на любой ширине), после `report.layout`,
   через `Runtime.evaluate`. **Перед ним панель задачи закрывается**: проба `report.drawer`
   открывает `.ui-drawer` кликом по карточке и не закрывает её, а кит на время панели ставит
   `body { overflow: hidden }`, `inert` на фон и компенсирующий `padding-right` — измерять
   экран в таком состоянии нельзя. Поэтому сразу после `report.layout` (чтобы значения `layout`
   остались сравнимы с HEAD) и до блока `phone`: `window.dispatchEvent(new KeyboardEvent('keydown',
   { key: 'Escape', bubbles: true }))`, пауза 600 мс (как в `report.newTask`); если панель всё
   ещё есть — второй Escape и ещё 600 мс. Сам `report.drawer` не меняется.
   «Видимый» = `el.offsetParent !== null`. Поля блока:
   - `drawerOpen` — `document.querySelectorAll('.ui-drawer').length > 0` (ожидается `false`;
     `true` означает, что остальные поля блока измерены под панелью и недействительны);
   - `innerWidth`, `scrollWidth` (`document.documentElement.scrollWidth`);
   - `bodyOverflowX` — `getComputedStyle(document.body).overflowX` (правило `body { overflow-x:
     hidden }` из переносимого блока: `'hidden'` при ширине ≤ 767, `'visible'` при 800 и выше;
     измеряется после закрытия панели, иначе инлайновый `overflow: hidden` кита исказит значение);
   - `compactPillVisible` — видим ли `.listik-shell__only-compact` (компактная пилюля «●/○» в
     шапке: глобально `display: none`, внутри переносимого блока `display: inline` → `true` при
     ≤ 767, `false` при 800 и выше).
     Ширину `.listik-shell > .ui-container` признаком порога **не брать**: глобальное правило
     `width: min(100% - 64px, 1440px) !important` (app.css ≈ строка 91) перебивает медиа-правила
     того же селектора на любой ширине (на 700 px контейнер 636, на 800 — 736) — это
     предсуществующий дефект, в порции a он не чинится;
   - `rowsVisible` — число видимых `.listik-mobile-task`;
   - `firstRowStyled` — для первой видимой строки `{ display, borderTopStyle, paddingTop }` из
     `getComputedStyle`, либо `null`;
   - `firstRowTop` — `Math.round(getBoundingClientRect().top)` первой видимой строки при
     `scrollY = 0` (перед измерением `window.scrollTo(0, 0)`), либо `null`;
   - `rowsFullyVisible` — число видимых строк, у которых `rect.bottom <= innerHeight` при `scrollY = 0`;
   - `tabsVisible` — видим ли `.ui-tabs`;
   - `inboxVisible` — видим ли `[aria-label="Нужен ты"]`;
   - `chipsVisible` — число видимых `.listik-toolbar .ui-chip`;
   - `headerButtons` — массив видимых кнопок шапки `.ui-app-header .ui-button`: отрисованный
     текст `innerText.trim()` (именно `innerText`, он не включает `display: none`-подписи),
     если пусто — `aria-label`, если и его нет — строка `'icon'`.
   Блок вставляется до `report.errors`. Существующие пробы и их имена **не меняются** — отчёты на
   1024/1440 должны остаться прежними по значениям.
3. Комментарий в шапке файла дополнить: второй аргумент < 768 → эмуляция телефона 360×740 и т.д.

### A4. Общая строка списка `MobileTaskRow.vue`

Новый файл `web/src/components/MobileTaskRow.vue`: `props: { task: Task }`, `emits: { open: [id: string] }`.
Разметка — сегодняшняя кнопка из `MobileTaskList.vue` без изменений структуры и классов
(`button.listik-mobile-task` → `__top` (id `listik-mono` + `UiStatusPill` здоровья), `__title`
(`<strong>`), `__meta` (`UiBadge` проекта или «без проекта» + `taskStageLabel`), `__meta--muted`
(держатель · heartbeat)), `type="button"`, `@click="emit('open', task.id)"`. Единственное
изменение содержимого: подпись держателя — `task.holder ? \`держит ${task.holder_title}\` :
'держателя нет'` (по ключу `holder`, не по `holder_title`); heartbeat — как было
(`task.holder_at ? \`heartbeat ${humanAge(task.holder_at)}\` : 'heartbeat —'`).

`MobileTaskList.vue` использует `MobileTaskRow` вместо собственной кнопки (`@open="openTask"` →
`store.openTask(id)`); всё остальное в нём (сортировка, `PAGE = 24`, заголовок «Очередь / Задачи»,
`UiEmptyState`, «Показать ещё») не меняется. **Обязательно** удалить из `MobileTaskList.vue`
импорты и хелперы, осиротевшие после выноса строки: `UiStatusPill`, `humanAge`, `taskStageLabel`,
`HEALTH_TITLES`, `taskHealth`, функции `healthTone`/`healthLabel` и всё прочее, что после правки
нигде не используется (`UiBadge`, `Task` — если остались без применения). В `web/tsconfig.json`
включены `noUnusedLocals`/`noUnusedParameters`, и без этой чистки `npm run typecheck` упадёт.

### A5. CSS: стили строки вне медиа-блока, порог 640 → 767

В `web/src/assets/app.css`:

1. Правила `.listik-mobile-task`, `:hover/:focus-visible`, `__top`, `__meta`, `__title`,
   `__meta--muted` перенести из медиа-блока в глобальную область — сразу после
   `.listik-mobile-queue { display: none; }` (строка ≈96), с коротким комментарием «строка
   компактного списка: одна для планшета (MobileTaskList) и телефона (порция b)». Два объявления
   `.listik-mobile-task` слить в одно с **действующими** сегодня значениями: `display: flex`,
   `flex-direction: column`, `align-items: stretch`, `gap: var(--space-2)`, `width: 100%`,
   `padding: var(--space-3)`, `border: 1px solid var(--hairline)`, `border-radius: var(--radius-lg)`,
   `background: var(--surface)`, `color: var(--ink-1)`, `text-align: left`, `font: inherit`,
   `cursor: pointer`, `box-shadow: var(--shadow-xs)`, `transition` как было. Первое (перебитое)
   объявление с `radius-xl`/`space-4`/`color-mix` удалить. Внутри медиа-блока правил про
   `.listik-mobile-task*` не остаётся.
2. `@media (max-width: 640px)` → `@media (max-width: 767px)` в обоих блоках (большой «Узкий экран»
   и `.listik-newtask-row`). Комментарий над большим блоком дополнить: «порог телефона 767 px
   (шаг 06)». Содержимое блоков больше не меняется. Блок `@media (max-width: 860px)` и
   `@media (max-width: 1023px)` не трогать.

### A6. README

В `web/README.md`: в разделе «Разработка без сервера» — строка про `--fill=N` (что добавляет, что
без флага мок прежний); в разделе «Смоук-тест живой страницы» — что ширина < 768 включает
эмуляцию телефона 360×740 и блок `phone` отчёта считает только видимые элементы.

## Границы правки

- Правятся только: `web/scripts/mock-api.mjs`, `web/scripts/smoke.mjs`,
  `web/src/components/MobileTaskRow.vue` (новый), `web/src/components/MobileTaskList.vue`,
  `web/src/assets/app.css`, `web/README.md`.
- Не трогать: `web/src/App.vue`, `web/src/store/*`, `web/src/api/*`, `web/src/lib/*`,
  `TaskDrawer.vue`, `AppHeader.vue`, `BoardToolbar.vue`, `NeedsYouStrip.vue`, `views/*`,
  `marks/*`, `web/src/sheet/*`, `listik/*.py`, `bin/listik`, `tests/`, `API.md`, `docs/*`
  кроме перечисленного, `package.json`, `vite.config.ts`, кит в `node_modules`.
- Никакого режима телефона, никакого `PhoneQueue`, никакого `matchMedia` — это порция b.
- Не менять существующие пробы smoke и их имена; не менять базовые 5 задач мока и ответы
  без `--fill`.
- Не переносить в медиа-блоки/из них ничего, кроме правил `.listik-mobile-task*`; не менять
  значения существующих правил (кроме слияния двух объявлений строки в действующее).
- Не «улучшать» `MobileTaskList` (сортировка, размер страницы, заголовок, поведение). Это
  запрет на изменение функциональности, а **не** на чистку: импорты и хелперы, ставшие мёртвыми
  после выноса строки в `MobileTaskRow.vue`, исполнитель **обязан** удалить (см. A4) — иначе
  `noUnusedLocals` в `web/tsconfig.json` уронит `npm run typecheck`.
- Никакого `!important` сверх существующего; никаких `px`-литералов в новой разметке.
- Не запускать `npm run build` с `VITE_API_BASE`/`VITE_LISTIK_TOKEN` в окружении: `web/dist`
  отдаёт живой сервер автора. Не коммитить: `git add`/`commit`/`stash` не выполнять.

## Как проверить

```sh
cd /Users/dmitry.fomin/Projects/Listik/web
npm run typecheck && npm run build          # build — без VITE_* переменных

node scripts/mock-api.mjs 8788 &            # без --fill
curl -s 'http://127.0.0.1:8788/api/tasks?limit=2&offset=1&order=updated' | node -e "process.stdin.on('data',d=>{const r=JSON.parse(d).data;console.log(r.total,r.limit,r.offset,r.tasks.map(t=>t.id))})"
curl -s 'http://127.0.0.1:8788/api/tasks?needs_owner=1' | node -e "process.stdin.on('data',d=>{const r=JSON.parse(d).data;console.log(r.total,r.tasks.map(t=>t.id))})"
curl -s 'http://127.0.0.1:8788/api/tasks?project=nope' | grep -o '"total":[0-9]*'
curl -s 'http://127.0.0.1:8788/api/tasks?status=review' | grep -o '"id":"[^"]*"'
kill %1

node scripts/mock-api.mjs 8788 --fill=50 &
curl -s 'http://127.0.0.1:8788/api/tasks?limit=20&offset=40' | node -e "process.stdin.on('data',d=>{const r=JSON.parse(d).data;console.log(r.total,r.tasks.length,r.tasks.at(-1).id)})"
curl -s 'http://127.0.0.1:8788/api/tasks?project=fill' | grep -o '"total":[0-9]*'
curl -s 'http://127.0.0.1:8788/api/meta' | grep -o '"slug":"[^"]*"'
curl -s 'http://127.0.0.1:8788/api/tasks/listik-fill-007' | grep -o '"comments":\[[^]]*\]' | head -c 80

VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token npx vite --port 5177 --strictPort &
npm run smoke -- http://localhost:5177/ 360    # layout.viewport.width 360, phone.firstRowStyled.display 'flex'
npm run smoke -- http://localhost:5177/ 700    # phone.bodyOverflowX 'hidden', compactPillVisible true (на HEAD: 'visible'/false), headerButtons без «Репозитории», drawerOpen false
npm run smoke -- http://localhost:5177/ 800    # phone.firstRowStyled: flex / solid / 12px
npm run smoke -- http://localhost:5177/ 1024   # прежние значения
npm run smoke -- http://localhost:5177/ 1440   # прежние значения
```

Эталон для 1024/1440 «как было»: прогнать те же smoke на чистом HEAD до правок (мок без
`--fill`) и сравнить поля `columns, cards, needsYou, toolbar, views, newTask, drawer, layout`.
