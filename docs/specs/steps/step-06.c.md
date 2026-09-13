# Шаг 06, порция c. Карточка задачи на телефоне — только чтение

Часть шага «Ограниченный мобильный просмотр» (`docs/specs/steps/step-06-limited-mobile-view.md`,
план — `docs/specs/steps/step-06.md`). Опирается на закоммиченные порции a (строка
`MobileTaskRow`, мок `--fill`, smoke с эмуляцией и блоком `report.phone`) и b (режим телефона:
`store.phone`, `PhoneQueue`, ветка `App.vue`). Код пишется в корне репозитория Listik, правится
**только `web/*`**.

## Решение автора, которое здесь реализуется

Карточка задачи на телефоне — **чистый просмотр, без действий**: заголовок, проект/этап/health,
держатель и heartbeat, критерии приёмки, блокеры, последний review/verdict, журнал. Никаких
кнопок claim/comment/needs-owner/heartbeat/stage/release/done, никаких полей ввода, никаких
кнопок копирования и переходов к другим задачам. Нажатие на строку очереди открывает карточку,
закрытие возвращает к тому же месту списка. Планшет и десктоп продолжают открывать `TaskDrawer`.

Согласованные определения: «последний review» — последний комментарий с `kind ∈ {review,
verdict}`; «журнал» — последние 20 комментариев любого вида, новые сверху, **без** событий
(`events`).

## Контекст, который нужно знать

- Фронт: `web/`, Vue 3.5 + TS + Vite, `strictTemplates`. Все `Ui*` — только из `@zoloto585/facet`
  (`web/node_modules/@zoloto585/facet`); прочитать `README.md` кита и `docs/facet-components.md`.
  Своя разметка/CSS — только на токенах кита, без `#hex`/`px`, без `!important` сверх
  существующего.
- `web/src/App.vue` после порции b: `store.phone` определяет ветку контейнера (`PhoneQueue` vs
  десктоп). `TaskDrawer` смонтирован вне ветки: `ref="drawerRef"`, `v-model="drawerOpen"`,
  пропсы `task="store.detail.value"`, `loading="store.detailLoading.value"`,
  `error="store.detailError.value"`, `pending`, `actors`, `projects="store.meta.value?.projects"`,
  `load-tree`, события `reload → store.reloadDetail()`, `open-other → store.openTask`, и десять
  обработчиков действий. `drawerOpen` синхронизирован со `store.openTaskId` двумя `watch`
  (открытие по `openTaskId`, закрытие → `store.closeTask()`). `openTaskWithComment` использует
  `drawerRef.value?.focusAnswer()/focusComment()` — с необязательной цепочкой, при отсутствии
  панели ничего не делает.
- Стор: `openTask(id)` → `detail = await api.task(id)` (`TaskDetail`: все поля `Task` плюс
  `comments[]` (`{id, author, kind, text, created_at}`), `events[]`, `dependencies[]`,
  `dependents[]`, `deps_state?: DepsState | null` (`blocked_by: DepInfo[]` — `{id, title, status,
  stage?, holder?, holder_title?, holder_age?, missing?, dep_title?, dep_type}`)). `detailLoading`,
  `detailError`, `reloadDetail()`, `closeTask()`.
- `holder_title` с живого сервера для пустого держателя — строка `'—'`, у мока — `''`; признак
  держателя — `holder` (ключ актора, `null` без держателя). То же для `dep.holder`.
- `web/src/lib/format.ts`: `humanAge(iso)`, `formatDateTime(iso)`, `datetimeAttr(iso)`,
  `commentKindTitle(kind)`, `taskStageLabel(task)`. `web/src/lib/health.ts`: `taskHealth(task)`,
  `HEALTH_TITLES`. `web/src/lib/stages.ts`: `stageCode(stage)` → `'s1'…'s4' | null`.
  `web/src/components/marks/ProjectMark.vue` (`:project :slug with-title size="sm"`),
  `marks/TaskGlyph.vue` (`kind="type" :value`). `TaskDrawer.vue` — образец использования
  `UiDrawer` со слотом `#header`, `UiTimeline dense`, `UiSkeleton`, `UiAlert`; `projectOf(task)`
  там ищет проект по `slug` в `props.projects`.
- Кит: `UiDrawer` (`v-model`, `side`, `size`, `title`, слоты `#header`/default/`#footer`; кнопка
  закрытия — `.ui-drawer__close`, это `UiTableActionButton`, **не** `.ui-button`; кит блокирует
  прокрутку `body` на время открытия и восстанавливает — позиция списка не теряется).
  `UiTimeline` — `items: UiTimelineItem[]` (`{ id, title, description?, timestamp?, datetime?,
  author?, tone? … }` — точную форму читать в `UiTimeline.vue`), `dense`, `emptyTitle`.
- CSS (`web/src/assets/app.css`): в `@media (max-width: 767px)` уже есть правило, растягивающее
  `.ui-drawer--right.ui-drawer--lg` на `100vw`, и правила скрытия `.listik-drawer__process` и
  др. Классы панели, которые можно переиспользовать в шапке карточки: `.listik-drawer__head`,
  `.listik-drawer__title`, `.listik-drawer__pills`, `.listik-drawer__meta-row`; общие:
  `.listik-section`, `.listik-section__title`, `.listik-section__hint`, `.listik-dl`,
  `.listik-prose`, `.listik-mono`, `.listik-row`, `.listik-stack`.
- `web/scripts/smoke.mjs` после порции b: блок `report.phone` заканчивается кликом по «Показать
  ещё» (`rowsAfterMore`) и счётчиками `requests`; `client.events` копит CDP-события.
- Мок (`--fill=50`): `details(id)` у любой задачи отдаёт `comments`: `journal` «взял в работу»
  (2 ч назад, `agent:dsh`) и `verdict` «ок, собирай» (1 ч назад, `me`); `deps_state.blocked_by`
  непуст только у `listik-metrics-g7h8` (блокер `listik-api-c3d4`, без держателя, `s1-spec`).
  Заполнители `listik-fill-NNN`: нечётные — `open` без держателя (`holder: null`,
  `holder_title: ''`, `holder_at: null`), чётные — `in_progress`, держит `agent:dsh`;
  `acceptance` у всех унаследован от базовой задачи (непуст), `holder_note: null`.
  Первая строка очереди на 360 — `listik-web-a1b2` (держит dsh, heartbeat 18 мин, acceptance
  «npm run build и vue-tsc --noEmit проходят без ошибок»).

## Что сделать

### C1. `web/src/components/PhoneTaskSheet.vue` (новый)

Пропсы: `modelValue: boolean`, `task: TaskDetail | null`, `loading?: boolean`,
`error?: string | null`, `projects?: ProjectRow[]`. События: `update:modelValue`, `reload`.

Корень — `UiDrawer side="right" size="lg"` (на телефоне уже `100vw` по CSS порции a),
`:title="task ? task.id : 'Задача'"`, `@update:model-value` → `emit('update:modelValue')`.

**Слот `#header`** (когда `task` есть): `div.listik-drawer__head`: строка `TaskGlyph kind="type"`
+ `ProjectMark` (проект по `slug` из `projects`, `with-title`, `size="sm"`); `h2.listik-drawer__title`
с заголовком; строка `.listik-drawer__pills`: `UiStatusPill` здоровья (`tone = taskHealth(task)`,
текст `HEALTH_TITLES[...]`), `UiBadge tone="info"` с `taskStageLabel(task)`, `UiBadge tone="accent"`
«нужен ты» при `needs_owner`, `span.listik-mono` с id. Без `UiCopyButton`. Без `task` — `span.listik-mono` «Задача».

**Тело** — `div.listik-phone-sheet` (класс обязателен: по нему smoke отличает карточку телефона):

1. `loading && !task` → три `UiSkeleton` (как в `TaskDrawer`).
2. `error` → `UiAlert tone="danger"` «Не удалось загрузить задачу» + текст + `UiButton size="sm"
   variant="secondary"` «Повторить» → `emit('reload')`. Это единственная кнопка компонента, и
   она есть только в состоянии ошибки.
3. `task` → секции `section.listik-section` с `h4.listik-section__title`, по порядку:
   - **«Кто держит»** — `dl.listik-dl`: «держит» → `task.holder ? \`${holder_title} · ${holder_age}\`
     : 'никто'`; «heartbeat» → `holder_at ? \`${formatDateTime(holder_at)} · ${humanAge(holder_at)}
     назад\` : '—'`; «что делает» → `holder_note ? «…» : '—'`; «этап с» → `stage_at ?
     \`${formatDateTime(stage_at)} · ${stage_age}\` : '—'`.
   - **«Критерии приёмки»** — `p.listik-prose` с `task.acceptance || '—'`.
   - **«Блокеры»** (в заголовке `UiBadge tone="warning" size="sm"` с числом, если > 0):
     `deps_state` отсутствует или `null` → `p.listik-section__hint` «сервер не отдал вердикт по
     зависимостям»; `blocked_by` пуст → `p.listik-section__hint` «нет»; иначе
     `ul.listik-phone-sheet__deps` → `li` на каждый блокер: `span.listik-mono` id, заголовок,
     `UiBadge tone="info" size="sm"` `stageCode(dep.stage)` если есть, `span.listik-section__hint`
     `dep.holder ? \`держит ${dep.holder_title}\` : 'без держателя'`, при `missing` — `UiBadge
     tone="danger" size="sm"` «задача не найдена». Только текст — без кнопок и ссылок.
   - **«Последний review»** — из `task.comments` берётся запись с `kind ∈ {review, verdict}` с
     максимальным `created_at`; при равенстве — та, что позже в массиве. Есть →
     `p.listik-section__hint` `${commentKindTitle(kind)} · ${author || '—'} · ${humanAge(created_at)}`
     и `p.listik-prose` с текстом; нет → `p.listik-section__hint` «ещё нет».
   - **«Журнал»** (в заголовке `UiBadge tone="neutral" size="sm"` с общим числом комментариев) —
     `UiTimeline dense` с `empty-title="Записей нет"`, элементы — последние 20 комментариев,
     отсортированные по `created_at` по убыванию (при равенстве — позже в массиве выше), каждый:
     `id: comment.id`, `title: \`${commentKindTitle(kind)} · ${author || '—'}\``,
     `description: text`, `timestamp: humanAge(created_at)`, `datetime: datetimeAttr(created_at)`.
     События (`events`) не показываются.
4. Не показывать: описание, дизайн, заметки, результат, «Холодный старт», «Связи» (кроме
   блокеров), дерево связей, форму ввода, `UiSplitButton`, `UiCopyButton`, кнопки к другим
   задачам, переходы этапов.

Стили: `.listik-phone-sheet { overflow-wrap: anywhere; }` (длинные id/пути не должны расширять
панель), `.listik-phone-sheet__deps` — список без маркеров, `display:flex; flex-direction:column;
gap: var(--space-2)`, `li` — `display:flex; flex-wrap:wrap; gap: var(--space-2); align-items:center`.
Добавить в раздел «Телефон (шаг 06)» `app.css`.

### C2. `App.vue`: карточка по режиму

`<TaskDrawer v-if="!store.phone.value" …>` — все пропсы/события как были; сразу за ним
`<PhoneTaskSheet v-else v-model="drawerOpen" :task="store.detail.value"
:loading="store.detailLoading.value" :error="store.detailError.value"
:projects="store.meta.value?.projects" @reload="store.reloadDetail()" />`. Логика `drawerOpen`/`watch`
не меняется: `store.openTask(id)` из `PhoneQueue` открывает карточку, закрытие (крестик, Escape,
фон) → `store.closeTask()`. `drawerRef` остаётся привязан к `TaskDrawer`; на телефоне он `null`,
и `openTaskWithComment` там не вызывается (инбокса на телефоне нет).

### C3. Возврат к месту в списке

Список `PhoneQueue` остаётся смонтированным под панелью; после закрытия карточки `scrollY` и
число загруженных строк те же, что до открытия. Своего кода для этого не нужно — только не
ломать: не перемонтировать `PhoneQueue` при открытии/закрытии, не сбрасывать его состояние по
`queueTick` (перечитывание из порции b сохраняет число строк).

### C4. Smoke: проба карточки телефона

В `web/scripts/smoke.mjs`, в конец блока `report.phone` (после `rowsAfterMore`, **до**
`requests`), добавить `sheet`: если есть `.listik-phone-queue` и видимая `.listik-mobile-task` —
`window.scrollTo(0, 300)`, ожидание 200 мс, запомнить `scrollY`, клик по первой видимой строке,
ожидание 1500 мс, затем:
`open` (есть ли `.listik-phone-sheet`), `title` (`innerText` `.ui-drawer .listik-drawer__title`),
`drawerWidth` (`Math.round(rect.width)` `.ui-drawer`), `drawerScrollWidth` (`.ui-drawer`
`scrollWidth`), `sections` (массив `innerText` видимых `.listik-phone-sheet .listik-section__title`,
обрезанных до текста без бейджей — достаточно `innerText.split('\n')[0].trim()`),
`actionButtons` (число видимых `.ui-drawer .ui-button, .ui-drawer .ui-split-button`),
`inputs` (число `.ui-drawer input, .ui-drawer textarea, .ui-drawer select`),
`timelineItems` (число `.ui-drawer .ui-timeline__item` или другого элемента записи ленты — сверить
класс в `UiTimeline.vue`), затем Escape (`window.dispatchEvent(new KeyboardEvent('keydown',
{ key: 'Escape', bubbles: true }))`), ожидание 600 мс: `closed` (нет `.ui-drawer`), `scrollAfter`,
`scrollRestored` (`scrollAfter === scrollBefore`), `rowsAfterClose` (видимые строки). Иначе `sheet: null`.

### C5. README

В `web/README.md` добавить раздел «Телефон (< 768 px)»: что показывается (шапка без
«Репозиториев», проект + поиск, очередь по 20 через `GET /api/tasks?limit&offset&order=updated`,
карточка только для чтения: держатель/heartbeat, критерии, блокеры, последний review, журнал
из 20 комментариев), чего нет (вкладки, инбокс, чипы, «закрытые», доска, действия, «Новая
задача»), какие запросы телефон не делает, порог 767 px и планшет 768–1023 px (как раньше),
как проверять (`--fill=50`, smoke 360). В разделе «Структура» дописать `PhoneQueue`,
`PhoneTaskSheet`, `MobileTaskRow`, `lib/viewport.ts`.

## Границы правки

- Правятся только: `web/src/components/PhoneTaskSheet.vue` (новый), `web/src/App.vue`,
  `web/src/assets/app.css`, `web/scripts/smoke.mjs`, `web/README.md`.
- Не трогать: `TaskDrawer.vue` (ни разметку, ни CSS-скрытия его секций), `PhoneQueue.vue`,
  `MobileTaskList.vue`, `MobileTaskRow.vue`, `store/listik.ts`, `api/*`, `lib/*`, `marks/*`,
  `views/*`, `mock-api.mjs`, `listik/*.py`, `bin/listik`, `API.md`, `docs/*`, `package.json`.
- В `App.vue` — только `v-if`/`v-else` на `TaskDrawer`/`PhoneTaskSheet` и импорт; пропсы и
  обработчики `TaskDrawer` не менять.
- Никаких действий в карточке телефона: ни одного `emit` в стор, кроме `reload` при ошибке;
  никаких `input/textarea/select`, `UiCopyButton`, `UiSplitButton`, кнопок «открыть другую задачу».
- Не грузить в карточку ничего сверх `store.detail` (никаких `loadDepTree`, `/api/ready`,
  документов).
- Существующие пробы smoke не менять; новые — только внутри `report.phone.sheet`.
- Не запускать `npm run build` с `VITE_API_BASE`/`VITE_LISTIK_TOKEN` в окружении. Не коммитить:
  `git add`/`commit`/`stash` не выполнять.

## Как проверить

```sh
cd /Users/dmitry.fomin/Projects/Listik/web
npm run typecheck && npm run build          # build — без VITE_* переменных
node scripts/mock-api.mjs 8788 --fill=50 &
VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token npx vite --port 5177 --strictPort &
npm run smoke -- http://localhost:5177/ 360    # phone.sheet.*: открыта, 360, 5 секций, 0 кнопок, 0 полей, scrollRestored
npm run smoke -- http://localhost:5177/ 800    # drawer.* как раньше (TaskDrawer 720, кнопки, ленты)
npm run smoke -- http://localhost:5177/ 1024
npm run smoke -- http://localhost:5177/ 1440
```

Ручные проверки — DevTools, устройство 360×740: открыть `listik-metrics-g7h8` (блокер),
`listik-api-c3d4` (вопрос автору, без держателя), `listik-fill-011` (open, без держателя:
нечётные заполнители — `open` без `holder`, чётные — `in_progress` с `agent:dsh`; `acceptance`
у всех заполнителей унаследован от базовой задачи и непуст, `holder_note` — `null`).
Пустые критерии приёмки на моке не воспроизвести — только на живом сервере с временной базой:
задача, созданная без `acceptance` → «—»; там же — задача с `comment -k review` и `comment -k
verdict` (последний review — verdict), задача с 25+ комментариями (в журнале 20, бейдж — общее
число).
