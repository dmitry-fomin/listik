# Шаг 06, порция b. Режим телефона: очередь по страницам

Часть шага «Ограниченный мобильный просмотр» (`docs/specs/steps/step-06-limited-mobile-view.md`,
план — `docs/specs/steps/step-06.md`). Опирается на закоммиченную порцию a (общая строка
`MobileTaskRow.vue`, мок с `--fill=N` и параметрами `/api/tasks`, smoke с эмуляцией телефона и
блоком `report.phone`). Код пишется в корне репозитория Listik, правится **только `web/*`**.

## Решения автора, которые здесь реализуются

- На телефоне (viewport ≤ 767 px) страница состоит из шапки, алертов «сервер недоступен»/«ошибка»
  и **одной** секции: фильтр «проект» + поиск + постраничная очередь задач. Вкладки
  «Доска/Список/Таймлайн/Метрики», инбокс «Нужен ты», чипы здоровья, чип «закрытые», кнопка
  «Новая задача», алерт «Циклы» и кнопка «Репозитории» в шапке на телефоне **не показываются**.
- Данные телефон берёт **своим** запросом `GET /api/tasks?limit=20&offset=…&order=updated`
  (+ `project`, если выбран). `/api/board`, `/api/ready`, `/api/blocked`, `/api/stats`, запрос
  «done за 7 дней» (`/api/tasks?status=done…`) и `/api/timeline` на телефоне **не выполняются**.
- Планшет (768–1023 px) и десктоп (≥ 1024 px) ведут себя ровно как до порции.
- Карточка задачи на телефоне в этой порции — по-прежнему `TaskDrawer` (её заменит порция c).

## Контекст, который нужно знать

- Фронт: `web/`, Vue 3.5 + TS + Vite, `strictTemplates`. Все `Ui*` — только из `@zoloto585/facet`
  (`web/node_modules/@zoloto585/facet`); прочитать `README.md` кита («Язык системы», «Рецепты
  layout») и `docs/facet-components.md`. Своя разметка/CSS — только на токенах кита, без
  `#hex`/`px` в разметке, без `!important` сверх существующего.
- `web/src/store/listik.ts` — модульный стор (единственный владелец данных). Важное:
  `refresh(options)` грузит `Promise.all([loadBoard(), loadStats(), loadHealth(), loadDeps(),
  loadDoneWeek()])`, затем `loadMeta()` если `meta === null`, `loadTimeline()` для вида
  «таймлайн», ставит `needsToken=false`, `connectionLost=false`, `lastError=null`, `lastSyncAt`,
  и в конце `void loadInboxQuestions()`. При отсутствии `token` — `needsToken=true` и выход.
  `init()` → `loadMeta()`, `refresh()` → `startStream()` (SSE, каждое событие → `scheduleRefresh()`
  → `refresh({silent:true})` с дебаунсом 500 мс), `startHealthPolling()` (30 с). `setToken()` →
  `refresh()` → `startStream()`. Все действия (`act`) после успеха вызывают `refresh({silent:true})`
  и `reloadDetail()`. `handleError()` — 401 → `needsToken=true`, отказ сети → `connectionLost=true`,
  иначе — `lastError`. `filters` — реактивный объект (`project`, `status`, …), `applyFilters()` →
  `refresh({silent:true})`. `openSearch(text)` открывает палитру `UiCommandPalette`
  (`SearchPanel.vue`, результаты из `/api/search`, выбор → `openTask`). `loadListTasks()` уже
  ходит в `/api/tasks`, но применяет все `filters` и `includeClosed` — для телефона **не
  использовать**, нужна отдельная функция (см. B2).
- `web/src/api/client.ts`: `api.tasks(query: TaskQuery) → Promise<TasksPage>`
  (`{ total, limit, offset, tasks[] }`); `TaskQuery` — `project?, status?, …, limit?, offset?,
  order?: 'updated'|'created'|'priority'|'stage'`; `buildQuery` не отправляет `undefined`.
- `web/src/App.vue`: корень `div.listik-shell` → `AppHeader` (пропсы `health, live, loading,
  lastSyncAt`, события `refresh`, `projects`) → `UiContainer` → `div.listik-shell__top`, внутри по
  порядку: `UiAlert` «Сервер Listik недоступен» (`store.connectionLost`, кнопка «Повторить запрос»
  → `refreshAll`), `UiAlert` «Последняя операция завершилась ошибкой» (`store.lastError`),
  `UiAlert` «Циклы в зависимостях» (`store.cycles`), строка `UiTabs` + `UiSaveStatus` + бейдж
  счётчика, `div.listik-stack` (`NeedsYouStrip`, `BoardToolbar`), `SearchPanel`, четыре вида
  (`board` → `MobileTaskList` + `.listik-board-only > BoardView`, `list`, `timeline`, `metrics`).
  Дальше вне контейнера: `TaskDrawer` (`v-model="drawerOpen"`, синхронизирован с `store.openTaskId`
  двумя `watch`), модалка токена (`store.needsToken`), `ProjectSettings`, `NewTaskModal`, `UiToast`.
  `onMounted` → `store.init()`, `onBeforeUnmount` → `store.dispose()`.
- `web/src/components/AppHeader.vue`: `UiAppHeader`, слот `#actions` → `div.listik-shell__actions`
  с пилюлей «живое», бейджем версии, `UiSegmented` гаммы, кнопкой темы, `UiTooltip` + `UiButton`
  «Репозитории» (`emit('projects')`), `UiButton` «Обновить». Подписи прячутся CSS при ≤ 767 px
  (порция a); сами кнопки остаются.
- `web/src/components/BoardToolbar.vue` — образец использования `UiSelect` проекта
  (`projectOptions(store.meta.value)` из `@/components/facets` → `{value,label}`, `placeholder="все
  проекты"`, `v-bind="{ 'aria-label': 'Проект' }"`, `size="sm"`) и `UiInput` поиска (`size="sm"`,
  `#leadingIcon` → `ListikIcon name="search"`, Enter через `v-bind="{ onKeydown }"` → `store.openSearch`).
- `web/src/components/MobileTaskRow.vue` (порция a): `props.task: Task`, `emit('open', id)`,
  корень `button.listik-mobile-task`, стили глобальные.
- `web/src/components/MobileTaskList.vue` — список планшета из `store.columns`; **не трогать**.
- `web/src/assets/app.css`: `.listik-mobile-queue { display: none }` глобально и `display: flex` в
  `@media (max-width: 1023px)` — это класс списка планшета, для телефона его **не использовать**
  (иначе очередь телефона исчезнет при ≥ 1024, а в тестах перепутаются счётчики). Существующие
  классы, которые можно переиспользовать: `.listik-section__title`, `.listik-section__hint`,
  `.listik-row`, `.listik-stack`, `.listik-mono`.
- **Дефект контейнера (найден в порции a, чинится здесь).** Базовое правило
  `.listik-shell > .ui-container { width: min(100% - 64px, 1440px) !important; max-width: none
  !important; … }` (app.css, раздел «Свои классы») перебивает оба медиа-правила того же
  селектора — `@media (max-width: 900px) { width: min(100% - 32px, 1440px) }` и
  `@media (max-width: 767px) { width: min(100% - 24px, 560px) }` — потому что у них нет
  `!important`, а медиа-запрос специфичности не добавляет. Итог: на любой ширине контейнер
  `100% - 64px` (на 360 px — 296 px с полями по 32 px вместо задуманных 12; на 800 — 736 вместо
  768). `!important` в базовом правиле нужен: у `UiContainer` кита собственные `width: 100%` и
  `max-width` той же специфичности, а стили кита встают в бандл позже приложения. Внутренний
  отступ контейнера (`padding-inline: var(--container-pad-x)` = 32 px) — кита, он не меняется.
- `web/scripts/mock-api.mjs 8788 --fill=50` — 55 задач (5 базовых + 50 сгенерированных, проекты
  `listik` и `fill`), `/api/tasks` учитывает `project/status/needs_owner/order/limit/offset`.
  `/api/health` у живого сервера — без токена; всё остальное требует токен (401).
- `web/scripts/smoke.mjs`: при `width < 768` — эмуляция 360×740; блок `report.phone`
  (`innerWidth, scrollWidth, rowsVisible, firstRowStyled, firstRowTop, rowsFullyVisible,
  tabsVisible, inboxVisible, chipsVisible, headerButtons`) считается по видимости
  (`offsetParent !== null`); `client.events` копит все CDP-события с `method`.

## Что сделать

### B1. `web/src/lib/viewport.ts` (новый)

```
export const PHONE_MAX_WIDTH = 767
export const PHONE_MEDIA = `(max-width: ${PHONE_MAX_WIDTH}px)`
export function useIsPhone(): Ref<boolean>
```

`useIsPhone()` создаёт `ref` со значением `window.matchMedia(PHONE_MEDIA).matches` **синхронно**
при вызове, подписывается на `change` и снимает подписку в `onScopeDispose` (вызывается из
`setup`). Если `window`/`matchMedia` недоступны — `ref(false)` без подписки. Никаких других
экспортов.

### B2. Стор: режим телефона

В `web/src/store/listik.ts`:

1. `const phone = ref(false)` — режим телефона; `const queueTick = ref(0)` — счётчик «очередь
   надо перечитать». Оба экспортируются из `useListikStore()`.
2. `function setPhone(next: boolean): void` — если `phone.value === next` → ничего; иначе
   присвоить и, если `init()` уже вызывался (модульный флаг `initialised`, ставится в `init()`),
   — `void refresh({ silent: true })`. Экспортируется.
3. `refresh()` в режиме телефона (`phone.value === true`) делает **только**: проверку токена (как
   сейчас: нет токена → `needsToken=true`, выход), `loading` (если не `silent`),
   `await loadHealth()`, `if (meta.value === null) await loadMeta()`, `lastSyncAt = now`,
   `queueTick.value += 1`, `loading=false`. Не вызывает `loadBoard`, `loadStats`, `loadDeps`,
   `loadDoneWeek`, `loadTimeline`, `loadInboxQuestions`; не трогает `lastError`,
   `connectionLost`, `needsToken` (их выставляет `handleError` из `loadQueuePage`/`loadMeta`, а
   снимает успешный `loadQueuePage`, см. п. 4). Десктопная ветка `refresh()` — без изменений.
4. `async function loadQueuePage(params: { limit: number; offset: number }): Promise<TasksPage | null>`
   — `api.tasks({ project: filters.project || undefined, limit, offset, order: 'updated' })` и
   **ничего больше** (никаких `status`, `include_closed`, `needs_owner`, дат, здоровья). Успех →
   `needsToken=false`, `connectionLost=false`, `lastError=null`, вернуть страницу. Ошибка →
   `handleError(error)`, вернуть `null`. Экспортируется.
5. `act()`, SSE (`scheduleRefresh`), `setToken`, `clearFilters`, `applyFilters` не меняются: все
   они ведут в `refresh()`, который в режиме телефона просто увеличивает `queueTick`.

### B3. `web/src/components/PhoneQueue.vue` (новый)

Секция `section.listik-phone-queue` с `aria-label="Очередь задач"`:

1. **Тулбар** `div.listik-phone-toolbar`: `UiSelect` проекта (`aria-label="Проект"`,
   `size="sm"`) с опциями `[{ value: '', label: 'все проекты' }, ...projectOptions(store.meta.value)]`
   — **первый пункт «все проекты» обязателен**: `UiSelect` кита не умеет сбрасывать значение
   (ни `clearable`, ни пункта-сброса, повторный клик выбор не снимает), а `projectOptions()`
   отдаёт только реальные проекты, и без этого пункта снять фильтр по проекту на телефоне можно
   только перезагрузкой страницы. Значение — `store.filters.project` (строка; `''` = все проекты,
   и тогда выбран пункт «все проекты», `placeholder` не нужен); изменение →
   `store.filters.project = String(value ?? '')` и **ничего больше** — перезагрузку с первой
   страницы делает `watch` из п. 6, чтобы на одно действие уходил один запрос (`buildQuery` не
   отправляет пустой `project`). И `UiInput` поиска (`size="sm"`, `placeholder="поиск"`,
   `aria-label="Поиск по задачам"`, `#leadingIcon` → `ListikIcon name="search" size="sm"`; Enter →
   `store.openSearch(text)`; локальный `ref`, как в `BoardToolbar`). Оба контрола — на всю ширину
   своей колонки.
2. **Шапка** `div.listik-phone-queue__head`: `h2.listik-section__title` «Задачи» и `UiBadge
   tone="neutral" size="sm"` с текстом `${tasks.length} из ${total}` (пока ничего не загружено —
   бейдж не показывать).
3. **Список** `div.listik-phone-queue__list`: `MobileTaskRow` для каждой задачи в порядке ответа
   сервера (**без** клиентской сортировки), `@open="(id) => store.openTask(id)"`.
4. **Состояния**: первая загрузка (`loading && tasks.length === 0`) — три `UiSkeleton
   variant="rect"` высотой в строку; загружено и пусто (`!loading && total === 0`) —
   `UiEmptyState compact title="Задач нет" description="Измените проект или обновите очередь."`
   с иконкой `ListikIcon name="list"`; ошибок своих не рисовать — их показывают алерты `App.vue`
   (`connectionLost`/`lastError`), уже загруженные строки при ошибке остаются.
5. **Пагинация**: `const PHONE_PAGE = 20` (экспортируемая константа модуля). Кнопка
   `UiButton block size="sm" variant="ghost"` с классом `listik-phone-queue__more` и текстом
   `Показать ещё ${Math.min(PHONE_PAGE, total - tasks.length)}`, видна только когда
   `tasks.length < total`; `:loading` во время догрузки. Нажатие → `store.loadQueuePage({ limit:
   PHONE_PAGE, offset: tasks.length })`, ответ **дописывается** в конец с дедупликацией по `id`
   (SSE между страницами мог сдвинуть выборку), `total` берётся из ответа.
6. **Перечитывание** (`reload`): `store.loadQueuePage({ limit: Math.min(200, Math.max(PHONE_PAGE,
   tasks.length)), offset: 0 })` — список **заменяется** ответом, число загруженных строк
   сохраняется (пользователь не теряет «Показать ещё» и место прокрутки). Вызывается: по
   `watch(() => store.queueTick.value)`; при монтировании, если `store.token.value` непуст;
   при смене `store.filters.project` — с предварительным сбросом (`tasks = []`, `total = 0`,
   т.е. первая страница `limit: PHONE_PAGE`). Защита от дублей: пока запрос в полёте, повторный
   `reload` с теми же параметрами не отправляется (флаг; следующий `queueTick` после завершения
   снова перечитает).
7. Ничего другого: ни чипов, ни «закрытых», ни сортировки, ни кнопок действий.

### B4. `App.vue`: ветка телефона

1. `import { useIsPhone } from '@/lib/viewport'`, `import PhoneQueue from '@/components/PhoneQueue.vue'`.
2. В `setup` (синхронно, до монтирования): `const isPhone = useIsPhone()`;
   `store.phone.value = isPhone.value`; `watch(isPhone, (value) => store.setPhone(value))`.
   `onMounted` → `store.init()` как сейчас.
3. Корневой `div.listik-shell` получает `:class="{ 'listik-shell--phone': store.phone.value }"`.
4. `AppHeader` получает `:phone="store.phone.value"`.
5. Внутри `div.listik-shell__top`: два первых `UiAlert` (сервер недоступен / ошибка) остаются
   общими; далее `<template v-if="store.phone.value"><PhoneQueue /></template>` и
   `<template v-else>` … существующее содержимое без изменений: алерт «Циклы», строка вкладок,
   `listik-stack` с `NeedsYouStrip` и `BoardToolbar`, четыре вида … `</template>`. `SearchPanel`
   остаётся один, общий (например, сразу после ветки — палитра телепортируется, место в DOM
   не важно).
6. `TaskDrawer`, модалка токена, `ProjectSettings`, `NewTaskModal`, `UiToast` — без изменений.

### B5. `AppHeader.vue`

Новый проп `phone?: boolean` (по умолчанию `false`). При `phone` **не рендерится** `UiTooltip`
с кнопкой «Репозитории» (`v-if="!phone"`). Остальное — как было.

### B6. CSS

В `web/src/assets/app.css`, новый раздел «Телефон (шаг 06)» рядом с правилами
`.listik-mobile-queue`:

- `.listik-phone-queue` — `display:flex; flex-direction:column; gap: var(--space-3)`;
- `.listik-phone-toolbar` — `display:grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr);
  gap: var(--space-2)`; прямые дети — `min-width: 0`; корни `UiSelect`/`UiInput` внутри —
  `width: 100%` (если кит задаёт им фиксированную ширину, перебить своим правилом, как это
  сделано для `.listik-toolbar__search`);
- `.listik-phone-queue__head` — `display:flex; align-items:center; justify-content:space-between;
  gap: var(--space-2)`;
- `.listik-phone-queue__list` — `display:flex; flex-direction:column; gap: var(--space-2)`.

**Починка контейнера.** В обоих медиа-правилах `.listik-shell > .ui-container` — в блоке
`@media (max-width: 900px)` и в блоке `@media (max-width: 767px)` — добавить `!important` к
`width` с теми же значениями (`min(100% - 32px, 1440px) !important` и `min(100% - 24px, 560px)
!important`). Среди `!important`-объявлений одинаковой специфичности побеждает позднее в файле,
а оба медиа-правила стоят ниже базового — этого достаточно; базовое правило, селекторы и
значения не менять, новых селекторов не вводить. Дописать к базовому правилу одну фразу
комментария: «медиа-переопределения ниже обязаны нести `!important` — иначе они не действуют».
Ожидаемая ширина контейнера после починки: 360 px → 336 (поля по 12), 700 → 560, 800 → 768,
1024 → 960, 1440 → 1376 (1024 и 1440 — как и до починки, там медиа-правила не участвуют).

**Контент влезает в 360 px.** Приёмка порции a установила: эмуляция точна (тривиальная
страница даёт `innerWidth 360`), но у приложения на 360 `innerWidth`/`scrollWidth` = 376 при
`documentElement.clientWidth` = 360 — контент шире экрана, и Chrome раздвигает `innerWidth`
(на HEAD до порции a было 380). Требование этой порции: на 360×740 в режиме телефона
`document.documentElement.scrollWidth <= document.documentElement.clientWidth` и
`innerWidth === 360`. Ожидается, что это закрывает сама ветка телефона (на экране остаются
шапка и `PhoneQueue`; вкладки/тулбар с полем поиска `--listik-search-w: 260px`/инбокс не
рендерятся). Если после B3–B6 переполнение остаётся, **разрешено** дополнительно: в блоке
`@media (max-width: 767px)` правила для шапки (`.listik-shell__actions`, `.listik-shell__search`,
`.ui-app-header__actions`, `--listik-search-w`) и свои правила `PhoneQueue` (`min-width: 0`,
`overflow-wrap: anywhere` для длинных заголовков/id в строках); **не разрешено** менять
десктопные правила, правила блока 1023 px и компоненты вне списка правки. Найденный
виновник переполнения назвать в отчёте.

Другое в существующих медиа-блоках не менять.

### B7. Smoke: пробы очереди и сетевые счётчики

В `web/scripts/smoke.mjs`:

1. До `Page.navigate`: `await client.send('Network.enable')` — события `Network.requestWillBeSent`
   попадают в `client.events`.
2. В блок `report.phone` добавить (после существующих полей):
   - `projectSelectVisible` — элемент `.listik-phone-toolbar [aria-label="Проект"]` существует и
     `getBoundingClientRect().width > 0`; `searchVisible` — то же для
     `.listik-phone-toolbar [aria-label="Поиск по задачам"]`;
   - `clientWidth` — `document.documentElement.clientWidth` (ширина, от которой кит и
     `app.css` считают `100%`; при переполнении контента `innerWidth` растёт, а `clientWidth`
     остаётся равным ширине эмуляции — поэтому «контент влезает» = `scrollWidth <= clientWidth`
     и `innerWidth === clientWidth`);
   - `containerBox` — для первого `.listik-shell > .ui-container`: `{ width:
     getComputedStyle(el).width, left: Math.round(getBoundingClientRect().left) }` (вычисленная
     ширина, а не «ширина по замыслу»; после починки B6 на 360 → `'336px'`/`12`, т.е.
     `clientWidth - 24`);
   - `queueBadge` — `innerText` первого `.listik-phone-queue__head .ui-badge` или `null`;
   - `moreVisible` — видима ли `.listik-phone-queue__more`; `moreLabel` — её `innerText` или `null`;
   - `rowsAfterMore` — если кнопка видима: клик, ожидание 900 мс, число видимых
     `.listik-mobile-task`; иначе `null`;
   - `requests` — счётчики по `client.events` с `method === 'Network.requestWillBeSent'`, по
     `new URL(params.request.url).pathname`: `board` (`/api/board`), `ready` (`/api/ready`),
     `blocked` (`/api/blocked`), `stats` (`/api/stats`), `timeline` (`/api/timeline`),
     `tasksList` (pathname ровно `/api/tasks`, метод GET), `taskDetail` (pathname начинается с
     `/api/tasks/`, метод GET), `meta`, `health`. Считать **в самом конце** блока `phone` (после
     клика «ещё»).
3. Существующие пробы не менять. На телефоне `views.*`/`newTask`/`drawer` честно вернут
   «кнопки нет»/«карточек нет» — это ожидаемо.

## Границы правки

- Правятся только: `web/src/lib/viewport.ts` (новый), `web/src/store/listik.ts`,
  `web/src/components/PhoneQueue.vue` (новый), `web/src/components/AppHeader.vue`,
  `web/src/App.vue`, `web/src/assets/app.css`, `web/scripts/smoke.mjs`.
- Не трогать: `TaskDrawer.vue`, `MobileTaskList.vue`, `MobileTaskRow.vue`, `BoardToolbar.vue`,
  `NeedsYouStrip.vue`, `SearchPanel.vue`, `views/*`, `marks/*`, `api/*`, `lib/*` кроме нового
  файла, `mock-api.mjs`, `listik/*.py`, `bin/listik`, `API.md`, `docs/*`, `package.json`.
- В сторе менять только перечисленное в B2: десктопная ветка `refresh()`, `act()`, `init()`
  (кроме флага `initialised`), `loadListTasks()`, фильтры, поиск — без изменений.
- Десктопная ветка шаблона `App.vue` переносится внутрь `<template v-else>` **дословно**: ни
  порядка, ни пропсов, ни обработчиков не менять.
- В медиа-блоках `app.css` менять только два объявления `width` контейнера (добавить
  `!important`, значения прежние); внутренний `padding-inline` контейнера кита не трогать.
- Не добавлять на телефон ничего сверх B3: ни инбокса, ни чипов, ни «закрытых», ни сортировки,
  ни кнопок действий, ни «Новой задачи».
- Карточку задачи на телефоне не менять (остаётся `TaskDrawer`; замена — порция c).
- Не менять существующие пробы smoke и их имена; не менять мок.
- Не запускать `npm run build` с `VITE_API_BASE`/`VITE_LISTIK_TOKEN` в окружении: `web/dist`
  отдаёт живой сервер автора. Не коммитить: `git add`/`commit`/`stash` не выполнять.

## Как проверить

```sh
cd /Users/dmitry.fomin/Projects/Listik/web
npm run typecheck && npm run build          # build — без VITE_* переменных
node scripts/mock-api.mjs 8788 --fill=50 &
VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token npx vite --port 5177 --strictPort &
npm run smoke -- http://localhost:5177/ 360    # phone.*: вкладок/инбокса/чипов нет, 20 строк, «ещё» → 40, requests.board 0
npm run smoke -- http://localhost:5177/ 800    # как в порции a: вкладки, инбокс, 5 строк, requests.board ≥ 1
npm run smoke -- http://localhost:5177/ 1024   # прежние значения
npm run smoke -- http://localhost:5177/ 1440   # прежние значения
```

Ручные проверки — Chrome DevTools, режим устройства 360×740, вкладка Network с фильтром `api/`:
выбор проекта «Заполнитель», «Обновить» после двух страниц, ввод токена, изменение ширины
360 ↔ 1100 в одной сессии, поиск через Enter. На живом сервере с временной базой (см.
`step-06.md`) — сортировка по `updated_at` и подпись «держателя нет» у незанятых задач.
