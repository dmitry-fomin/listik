# Порция 05.c. Доска по Main/CardStates: колонки, рельса переходов, компактная карточка, без DnD

## Контекст

Дерево `/Users/dmitry.fomin/Projects/Listik-ui`, правится только `web/*`. Закоммичены порции a
(примитивы `components/marks/*`, `lib/{health,harness,projects,stages}.ts`, витрина) и b
(`UiTabs`, инбокс, тулбар с чипами, `store.counts`/`inbox`/`filters.health`, порог 1024).

Референс — секция доски в `docs/prototype/Main-html/Main.dc.html` (от «по этапу … Новая задача»
до `.done-rail`) и лист состояний `docs/prototype/CardStates-html/CardStates.dc.html`
(восемь карточек: здорова, под угрозой, брошена, молчит, нужен ты, без держателя, шаг, заведена;
легенда сверху). Размеры прототипа: колонка «Заведена» 232px, колонки s1–s4 по 252px, рельса
«Готово» 56px, промежуток 16px; карточка — `padding --space-3`, `gap --space-2`, левая полоса 3px,
`--radius-md`, `--shadow-xs`; заголовок `--text-base` medium в две строки; подвал `--text-xs`.

Сейчас: `views/BoardView.vue` (сегмент группировки, кнопка «Новая задача», рельса `listik-pipeline-rail`
на flex-заглушках, колонки с HTML5 DnD → `store.moveTask` → PATCH, откат при ошибке),
`components/board/BoardColumn.vue` (шапка со счётчиками-бейджами «всего / WIP / ты / брошено / ждёт»
и строкой «ждут: …», приёмник drop, пагинация по 60), `components/board/TaskCard.vue` (id +
копирование, бейджи проекта/типа/приоритета/связей/состояний, держатель, заметка, возраст,
метки, «обновлена», кнопки «Открыть» и «Переместить в…» с `UiPopover`), `useBoardDrag.ts`,
в сторе `moveTask`, в `app.css` разделы «Доска» и «Карточка задачи». Сервер (`/api/board?group_by=stage`)
отдаёт колонки в порядке `s1-spec, s2-review, s3-impl, s4-judge, none («без этапа»), done`;
задачи в колонке уже отсортированы по приоритету (P0 сверху), затем по обновлению.

Решения автора: DnD и поповер «Переместить в…» убрать совсем, этап меняется только из панели;
в шапке колонки показывать харнессы держателей задач, которые сейчас в колонке; метка проекта —
из `/api/meta.projects`. Допущения плана: «шаг/порция N/M» и прогресс по порциям на карточке —
вне шага (данных о детях в `/api/board` нет); «красный ×N» — вне шага.

Прочитать перед началом: три файла доски, `useBoardDrag.ts`, `store/listik.ts` (`columns`,
`allBoardTasks`, `depsSummary`, `moveTask`, `act`), `lib/stages.ts`, `lib/health.ts`,
`components/marks/*`, `assets/app.css` (разделы доски/карточки), `scripts/smoke.mjs`; в ките —
`UiBadge`, `UiEmptyState`, `UiSkeleton`, `UiButton`, `UiTooltip`, `UiSegmented`.

## Что сделать

### 1. Убрать DnD

- Удалить `components/board/useBoardDrag.ts`, `store.moveTask`, все `drag*`/`drop` обработчики и
  события в `BoardView`/`BoardColumn`/`TaskCard`, `draggable`, `moveTargets`, `currentColumnKey`,
  `UiPopover` «Переместить в…», слушатель `dragend` на `window`, `localError` «Перемещение
  отменено», классы `.is-dragging`, `.is-drop-target`, `cursor: grab`. Подсказка у сегмента
  группировки: «колонки — этапы конвейера; этап меняет агент через `stage` (моноширинно),
  доска только показывает».

### 2. Стор

- `doneWeekCount: Ref<number>` и `loadDoneWeek()`: `api.tasks({ status: 'done', include_closed: true,
  order: 'updated', limit: 200, project: filters.project || undefined })`, считать задачи с
  `closed_at` не старше 7 суток. Вызывать из `refresh()` вместе с остальными загрузками; ошибка
  не роняет доску (как `loadDeps`).
- Порядок колонок для `group_by=stage` в `columns`: `none` («Заведена») первой, затем
  `s1-spec…s4-judge`, затем `done`. Для остальных группировок — порядок сервера. Клиентская
  фильтрация — как сейчас, но `keepEmpty` для стадийной группировки расширить до
  `['none', 's1-spec', 's2-review', 's3-impl', 's4-judge', 'done']`: колонка «Заведена» и колонка
  «Готово» (когда сервер её прислал, т.е. при `include_closed`) не должны исчезать из ряда, когда
  чип или фильтр опустошает их — иначе сетка рельсы и колонок разъезжается. Сейчас `keepEmpty`
  держит только s1–s4 (`store/listik.ts`, `const keepEmpty`).
- Что лежит в колонках сервера (`listik/store.py`, `board()`): ключ колонки — `stage` задачи,
  если он из s1–s4, **независимо от статуса**; иначе `done`, если статус `done`/`cancelled`, иначе
  `none`. Следствия, которые UI обязан отражать честно: закрытая на `s4-judge` задача при
  `include_closed` остаётся в колонке s4, а не в «Готово»; в «Готово» попадают только закрытые
  задачи без этапа или со `stage = 'done'`; открытая задача со `stage = 'done'` (прошла `stage`
  после s4, но не закрыта) лежит в `none`, т.е. в «Заведена». Поэтому карточка получает бейдж
  статуса, когда он не «обычный открытый»: `UiBadge success sm` «закрыта» при `status = done`,
  `UiBadge neutral sm` «отменена» при `cancelled`, `UiBadge info sm` «после s4» при открытом статусе
  и `stage = 'done'` (см. п. 5).

### 3. `BoardView.vue`

- Шапка секции: слева `UiSegmented` группировки (как сейчас) и подсказка из п. 1; справа
  `.listik-section__hint.tnum` «{total} задач в выборке» и `UiButton secondary sm` «Новая задача»
  (`emit('create')`, иконка `plus`).
- Сетка ряда — **не статичная**: набор дорожек вычисляется из списка реально отображаемых
  колонок и их режимов. В `BoardView` — `computed` `boardTracks: string` (значение
  `grid-template-columns`), собранный по порядку рендера: свёрнутая «Заведена» →
  `var(--listik-rail-w)`; развёрнутая «Заведена» → `var(--listik-intake-w)`; колонки s1–s4 и
  колонка «Готово» (при `includeClosed`) → `var(--listik-col-w)`; рельса «Готово» (при
  `!includeClosed`) → `var(--listik-rail-w)`. Один и тот же `boardTracks` ставится инлайн-стилем
  и рельсе переходов, и ряду колонок; `column-gap: var(--listik-col-gap)` у обоих. Это покрывает
  четыре режима: 1440 развёрнуто, <1280 свёрнуто, «закрытые» включены (последняя дорожка —
  полноценная колонка), любой клиентский фильтр (колонки не выпадают благодаря `keepEmpty`).
  Проверка руками: при свёрнутой «Заведена» первая дорожка 56px, при включённых закрытых
  последняя — 208/252px.
- Рельса переходов (только при группировке «по этапу»): `div.listik-rail` — первая строка того
  же прокручиваемого контейнера, что и ряд колонок (общий `div.listik-board-scroll` с
  `overflow-x: auto`; рельса и ряд — два grid с одинаковым `boardTracks`, чтобы подписи ехали
  вместе с колонками при прокрутке); в ячейках s1–s4 — линия `--listik-rail-line` (2px, переменная
  в `app.css`) цветом `--hairline-strong`; подписи переходов стоят над промежутками между s1–s2,
  s2–s3, s3–s4 и s4–«Готово» (абсолютно спозиционированный `span` на правом крае ячейки,
  `transform: translateX(calc(50% + var(--listik-col-gap) / 2))` — центр подписи в центре
  промежутка), текст — `transitionOut(stage)` из `lib/stages.ts`: `sticky` цветом `--accent-700`,
  `handoff` — `--ink-3`, `--font-mono`, `--text-xs`, фон `--surface-2` (фон страницы), чтобы
  разрывать линию; перед подписью `ListikIcon` xs (`refresh` для sticky — «тот же держатель»,
  `expand`/стрелка для handoff). Между «Заведена» и s1 подписи нет (ячейка «Заведена» пустая).
  Старую `.listik-pipeline-rail` удалить.
- Ряд колонок `div.listik-board` — grid с `boardTracks`, `align-items: start`, **без боковых
  отступов** (`padding-inline: 0`; вертикальный отступ снизу оставить), внутри общего
  прокручиваемого контейнера. При группировке не «по этапу» — прежний flex с `--listik-col-w` и
  прокруткой того же контейнера.
- «Заведена» (`none`): при ширине окна ≥1280 — обычная колонка шириной `--listik-intake-w`
  (232px); при <1280 — свёрнута в рельсу (`BoardColumn` в режиме `collapsed`): вертикальная
  подпись «Заведена» и `UiBadge neutral sm` с числом, клик по рельсе разворачивает колонку (доска
  тогда уходит в горизонтальную прокрутку), повторный клик сворачивает. Состояние — `ref` в
  `BoardView`, начальное значение — по `window.matchMedia('(max-width: 1279px)')`, следить за
  изменением.
- «Готово»: при `includeClosed = false` — `aside.listik-done-rail` (56px): `ListikIcon check md`,
  вертикальная подпись «Готово · 7 дн» (`writing-mode: vertical-rl; transform: rotate(180deg)`),
  `UiBadge success sm` с `doneWeekCount`; при `includeClosed = true` — колонка `done` рендерится
  обычной колонкой «Готово» после s4, рельса не показывается. В этой колонке лежат **только**
  закрытые задачи без этапа или со `stage = 'done'` (так группирует сервер, см. п. 2); закрытые
  на s1–s4 остаются в своих колонках с бейджем «закрыта»/«отменена» на карточке — это не ошибка
  UI, а серверная семантика, и в README это записывается одной фразой. Существующий костыль
  `is-done-rail` по regex заголовка удалить.

### 4. `BoardColumn.vue`

- Props: `column`, `loading?`, `collapsed?` (для «Заведена»), `depsSummary?`, `projects?:
  ProjectRow[]`; emits: `open`, `toggle` (для рельсы). Никаких drag-пропсов.
- Шапка `.listik-column__head`: строка заголовка — для стадийных ключей `span.listik-column__code`
  (`stageCode`, моноширинно, `--ink-3`) + `h2` название из `stageTitle`; для `none` — «Заведена»;
  для `done` — «Готово»; для других группировок — `column.title`. Справа `span.listik-column__count`
  (моноширинно, `.tnum`) — `column.count`.
  Вторая строка `.listik-column__meta`: слева `.listik-column__harness` — для `none` текст
  «без этапа»; иначе уникальные харнессы держателей задач колонки (`harnessOf(task.holder)`,
  без `human` и `null`), каждый — `HarnessIcon` + подпись из `HARNESS_TITLES`, через `·`; если
  держателей-агентов нет — пусто. Справа `.listik-health-row` — по одной `HealthDot sm` на каждую
  задачу колонки в её порядке (`taskHealth`), не больше 12, дальше `span.tnum` «+N»; `title`
  точки — `healthReason(task)`.
  Бейджи «всего/WIP/ты/брошено/ждёт» и строку «ждут: …» удалить.
- Тело: как сейчас — скелетоны при загрузке, `UiEmptyState compact` «Здесь пусто» без подсказки
  про перетаскивание, карточки, кнопка «Показать ещё {min(60, rest)} · осталось {rest}».
- Режим `collapsed`: рендерится только `button.listik-column__rail` (вертикальная подпись
  «Заведена», бейдж с `column.count`, `aria-expanded="false"`), ширина `--listik-rail-w`.

### 5. `TaskCard.vue` — компактная карточка по CardStates

- Props: `task`, `deps?: DepsSummary | null`, `project?: ProjectRow | null`; emit `open`.
  `article.listik-task-card` с `role="button"`, `tabindex="0"`, `aria-label="Открыть задачу {title}"`,
  клик/Enter/Space → `open`. Курсор `pointer`.
- Состояние карточки — **одна ветка**, проверки строго по порядку (как в `taskHealth` порции a
  и в инбоксе порции b): `needs_owner` → `taskHealth === 'dead'` → `taskHealth === 'at-risk'` →
  ничего. Первая сработавшая задаёт и модификатор полосы, и единственный бейдж состояния:
  `is-needs` — полоса `--accent-500`, бейдж `accent` «нужен ты»; `is-dead` — `--danger-500`,
  бейдж `danger` с текстом `healthReason(task)` («брошена 40 ч» / «брошена · без держателя»);
  `is-at-risk` — `--warning-600`, бейдж `warning` с `healthReason(task)` («молчит 18 мин»; при
  at-risk только по `stage_warn` бейджа нет — об этом говорит цвет возраста). Задача, которая
  одновременно `needs_owner` и `dead` (на моке `listik-api-c3d4`), получает только акцентную
  полосу и бейдж «нужен ты»; о брошенности говорит точка здоровья в подвале (она считается
  независимо, по `taskHealth`). Отдельно `is-muted` (`taskHealth === 'unknown'` и есть блокеры) —
  `opacity: .72`, совместим с любой веткой. Фон карточки всегда `--surface`: тонирование фона по
  состоянию из прототипа **не переносить** (критерий приёмки шага: карточка не закрашивается
  целиком по статусу; допустима только полоса `--listik-card-stripe` слева).
- Строки:
  1. `.listik-task-card__top`: `TaskGlyph kind="type"` · `ProjectMark withTitle size="sm"`
     (проект по `task.project`) · spacer · `TaskGlyph kind="priority"`;
  2. `h3.listik-task-card__title` — две строки с обрезкой (как сейчас);
  3. `.listik-task-card__meta` (рендерится, только если есть содержимое): слева
     `.listik-task-card__badges` — один бейдж состояния по ветке из правила выше плюс, независимо
     от ветки, бейдж статуса, если он есть (`success` «закрыта», `neutral` «отменена», `info`
     «после s4» — см. п. 2); справа `.listik-task-card__counts`
     — `CountGlyph lock warning` «ждёт N» (`N` = `deps.blockedBy.length || task.blocked_by.length`,
     `title` — «ждёт завершения: <id, …>») и `CountGlyph key accent` «её ждут N»
     (`deps.waitingForCount`); «детей открыто N» не показывать (данных нет);
  4. `.listik-task-card__foot`: слева `HealthDot` (`taskHealth`, `title` = `healthReason`) +
     (держатель есть → `HarnessIcon :actor="task.holder"` + `<strong>{{ holder_title }}</strong>`;
     нет → «без держателя»); справа `.listik-task-card__age` (`ListikIcon clock xs` + `stage_age`,
     `.tnum`; класс `is-warn` при `stage_warn` и `stage_hours ≤ 24`, `is-late` при `> 24`).
- Убрать: id и `UiCopyButton`, бейджи проекта/типа/приоритета, `UiTooltip`-обёртки, «исполнитель»,
  `holder_note`, метки, «обновлена», «источник», кнопки. Метки задачи на карточке не показываются
  (прототип), они есть в панели.

### 6. Стили (`app.css`, разделы «Доска» и «Карточка задачи» переписать)

Переменные в `:root` после токенов: `--listik-col-w: 252px; --listik-intake-w: 232px;
--listik-rail-w: 56px; --listik-col-gap: var(--space-4); --listik-rail-line: 2px;
--listik-card-stripe: 3px`; в `@media (max-width: 1279px)`: `--listik-col-w: 208px`.
Арифметика 1440: контейнер `min(100% − 64px, 1440px)` = 1376px, ряд 232 + 4×252 + 56 + 5×16 =
1376px — впритык, поэтому у ряда нет боковых отступов, а вертикальный скроллбар страницы (~15px
в headless-режиме) даёт горизонтальную прокрутку ряда не больше двух десятков пикселей; это
принимается (см. чек-лист), а не лечится ужиманием колонок. Колонка: `--surface`, `--hairline`, `--radius-lg`, `overflow: hidden`,
шапка `padding --space-3 --space-3 --space-2`, тело `gap --space-2`, `padding --space-2`,
`max-height` как сейчас. Никаких `#hex`/`px` вне объявлений переменных (кроме 2–3px полосы —
допустимо через `--listik-card-stripe: 3px`).

### 7. `App.vue`

`BoardView` получает `@create` как сейчас; `BoardColumn`/`TaskCard` получают `projects` из
`store.meta.value?.projects` (через `BoardView`). `MobileTaskList` не трогать.

### 8. Smoke (`scripts/smoke.mjs`)

- `report.board`: число `.listik-column` (при 1440 на моке: «Заведена» + s1–s4 = 5, «Готово»
  — рельса `.listik-done-rail`, не колонка), число `.listik-task-card`, `doneRail: boolean`,
  `rail: число подписей .listik-rail__tr` (4), `intakeCollapsed: boolean` (есть
  `.listik-column__rail`).
- `report.layout.columnsVisible` (из порции a) считать только по стадийным колонкам
  `.listik-column[data-stage="s1-spec"|"s2-review"|"s3-impl"|"s4-judge"]` (атрибут `data-stage="<key>"`
  добавить всем колонкам, но «Заведена» и «Готово» в счёт не входят); добавить
  `report.layout.boardOverflow` = `scrollWidth − clientWidth` прокручиваемого контейнера доски
  и `report.layout.s4Right` = правый край `[data-stage="s4-judge"]` относительно `innerWidth`.
- `report.drawer` открывает панель кликом по `.listik-task-card` — оставить.

### 9. `web/README.md`

Абзац «Drag&drop карточек» в «Особенностях» заменить на: этап меняется только из панели
(`Следующий этап`), доска — для наблюдения; описать раскладку 1024–1279 (колонки 208px,
«Заведена» свёрнута) и одной фразой — серверную семантику колонок по этапу (закрытая на s4
остаётся в s4 с бейджем «закрыта», в «Готово» — закрытые без этапа, открытая со `stage=done` —
в «Заведена» с бейджем «после s4»). В «Слое зависимостей» пункт про карточку — счётчики «ждёт N»/«её ждут N»
иконками, пункт про колонку — убрать строку «ждут: …».

## Границы правки

- Правятся: `views/BoardView.vue`, `components/board/BoardColumn.vue`, `components/board/TaskCard.vue`,
  удаление `components/board/useBoardDrag.ts`, `store/listik.ts` (только `moveTask` удалить,
  `doneWeekCount`/`loadDoneWeek`/порядок колонок/`keepEmpty` с `none` и `done` добавить),
  `App.vue` (только проброс проектов),
  `assets/app.css` (разделы доски/карточки + переменные), `scripts/smoke.mjs`, `web/README.md`.
- Не трогать: `TaskDrawer.vue` (порция d), окно «Новая задача» (порция e), `NeedsYouStrip.vue`,
  `BoardToolbar.vue`, `SearchPanel.vue`, `ListView`/`TimelineView`/`MetricsView`, `MobileTaskList.vue`,
  `AppHeader.vue`, `api/*`, примитивы `marks/*` и `lib/*` (если нужен новый хелпер — добавить,
  не менять сигнатуры существующих), `sheet/*`, `vite.config.ts`, `package.json`.
- Не возвращать на карточку id, кнопки, метки, бейджи проекта/типа; не заводить DnD «пока в
  выключенном виде»; не красить фон карточки/колонки по состоянию.
- Не хардкодить таблицу харнессов по этапам; в шапке колонки — только держатели её задач.
- Никаких токенов и содержимого `config.toml` в файлах и отчёте.

## Как проверить

```sh
cd /Users/dmitry.fomin/Projects/Listik-ui/web
npm run typecheck && npm run build
grep -rn "dragstart\|dragover\|useBoardDrag\|moveTask\|Переместить" src   # пусто
node scripts/mock-api.mjs 8788 &
VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token npm run dev &
npm run smoke -- http://localhost:5173/ 1440   # board.columns 5, rail 4, doneRail true, intakeCollapsed false, boardOverflow ≤ 24
npm run smoke -- http://localhost:5173/ 1024   # intakeCollapsed true, columnsVisible === 4, s4Right ≤ 0, boardOverflow > 24
git -C .. status --porcelain
```

Глазами на 1440 (мок): «Заведена» с эпиком `listik-epic-k9l0` (иконка слоёв, «без держателя»,
пустая точка), s1 с `listik-api-c3d4` (бейдж «нужен ты», полоса акцентом, в подвале точка
`dead` «брошена · без держателя»), s2 с `listik-metrics-g7h8` (замок «ждёт 1», приглушена),
s3 с `listik-web-a1b2` (`idle_hours: 0.3` ≥ порога 0.25 → точка `at-risk`, бейдж «молчит 18 мин»,
полоса warning, `HarnessIcon` dsh, «dsh», «2 ч»), s4 с `listik-sse-e5f6` («брошена 40 ч», полоса
danger, «claude», возраст этапа), в шапке s3 — «dsh», в шапке s4 — «claude»; рельса
`sticky · handoff · sticky · handoff`; справа «Готово · 7 дн». Чип «стоят из-за других» оставляет
одну карточку в s2, но все пять колонок и рельса остаются на месте.
