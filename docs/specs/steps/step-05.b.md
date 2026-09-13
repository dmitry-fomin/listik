# Порция 05.b. Каркас страницы по Main: вкладки, инбокс «Нужен ты», ряд чипов и компактные фильтры

## Контекст

Дерево трека `/Users/dmitry.fomin/Projects/Listik-ui`, правится только `web/*`. Порция a
закоммичена: есть `docs/facet-prototype-map.md`, примитивы `web/src/components/marks/`
(`ProjectMark`, `HarnessIcon`, `HealthDot`, `TaskGlyph`, `CountGlyph`), справочники
`web/src/lib/{health,harness,projects,stages}.ts`, все гаммы, витрина `web/sheet.html`, smoke с
параметром ширины. Прочитать их перед началом, чтобы не дублировать.

Референс — `docs/prototype/Main-html/Main.dc.html` (тёмная) и `BoardLight` (светлая), от шапки до
начала доски: строка вкладок (`.seg` «Доска/Список/Таймлайн/Метрики» слева; справа `Сохранено ·
22:26:19` и бейдж `642 задачи`), секция «Нужен ты» (`.sec-title` с иконкой руки и бейджем-счётчиком,
подсказка «вопрос автору, брошенные и молчащие держатели — то, без чего конвейер стоит», сетка из
трёх `.inbox-card`), затем строка: слева чипы `1 брошена · 2 под угрозой · 62 стоят из-за других ·
15 можно брать (активный) · 9 в конвейере`, справа `select «все проекты» · select «любой харнесс»
· chip «закрытые» · input «поиск по задачам · Cmd K»`. Фильтра «любой харнесс» **не делать** до
шага 04 (решение автора). Доска, панель, окно создания — следующие порции; здесь они не трогаются
сверх того, что нужно каркасу.

Сейчас в `web/src/App.vue`: `UiSegmented` видов, `UiSaveStatus` + бейдж, алерты (сервер недоступен /
последняя ошибка / циклы), `NeedsYouStrip` (карточки с id, «Открыть/Снять флаг/Комментарий»),
свои кнопки `.listik-health-chip`, `TaskFilters` (`UiFilterBar` на 10 полей), `SearchPanel`
(`UiSegmented` режима поиска, строка ввода, палитра `UiCommandPalette`, инлайн-список результатов),
`MobileTaskList` (≤640px) и `desktop-only` обёртка видов. Стор `web/src/store/listik.ts`:
`Filters` (`project, status, stage, assignee, type, needsOwner, abandonedOnly, updatedFrom,
updatedTo, deps`), `matchesFilters`, `columns`, `needsYou` (= `board.needs_you` с клиентскими
фильтрами), `kpi`, `openTask`, `act`. Мёртвые файлы: `components/KpiRow.vue`, `ReadyStrip.vue`,
`ProblemRow.vue`; скрипт `scripts/verify-changes.mjs` проверяет как раз старые чипы и после порции
станет бессмысленным.

Прочитать перед началом: `App.vue`, `store/listik.ts`, `NeedsYouStrip.vue`, `TaskFilters.vue`,
`SearchPanel.vue`, `MobileTaskList.vue`, `views/ListView.vue` (куда переезжает фильтр-бар),
`assets/app.css`, `scripts/smoke.mjs`, в ките — `UiTabs` (`tabs: {key,label}`, `v-model`,
слоты `panel-<key>`, DOM `.ui-tabs__list [role=tab]`), `UiChip` (интерактивен только при привязке
`selected`; слотов нет — точку-индикатор в чип не положить, поэтому чипы без точки),
`UiSelect` (слотов опций нет), `UiInput` (слот `leadingIcon`), `UiCommandPalette`.

## Что сделать

### 1. Стор (`web/src/store/listik.ts`)

- `Filters`: убрать `abandonedOnly`, добавить `health: '' | 'dead' | 'at-risk'`. В
  `matchesFilters`: `health === 'dead'` → `taskHealth(task) === 'dead'`, `'at-risk'` →
  `taskHealth(task) === 'at-risk'`. `needsClientFilter` учитывает `health`. В `loadListTasks`
  вместо `abandonedOnly` фильтровать по `health` тем же способом. Все места, где использовался
  `abandonedOnly` (`App.vue showAbandonedOnly`, `TaskFilters`, `ListView`), перевести на `health`.
- `inbox` (computed, заменяет `needsYou`): объединение `board.needs_you` и задач из колонок с
  `taskHealth(task) === 'at-risk'`, у которых причина — heartbeat (`idle_hours ≥ AT_RISK_IDLE_HOURS`),
  без дублей по id, с клиентскими фильтрами как раньше. Порядок: сначала `needs_owner`, затем
  `dead`, затем `at-risk`; внутри — по `idle_hours` убыв. Экспортировать `inbox`; `needsYou`
  удалить (или оставить алиасом — но использовать одно имя).
- `inboxQuestions: Ref<Record<string, string | null>>` — текст последнего комментария
  `kind === 'question'` для задач инбокса с `needs_owner`. Заполняется функцией
  `loadInboxQuestions()`: для первых 12 таких задач — `api.task(id)` параллельно
  (`Promise.all`), берётся последний по `created_at` комментарий `question`; ошибки одной задачи
  не роняют остальные (`null`). Вызывается из `refresh()` после `loadBoard()` (в том же
  `Promise.all` нельзя — нужен `board`; вызвать после), и не блокирует `loading`.
- `counts` (computed, заменяет `kpi`): `{ dead, atRisk, blocked, ready, inPipeline, total }`:
  `dead`/`atRisk` — по `taskHealth` над всеми задачами колонок (`allBoardTasks`), `blocked` —
  `board.blocked_count`, `ready` — `board.ready.length`, `inPipeline` — задачи со `stage` из
  `PIPELINE_STAGE_KEYS`, `total` — `board.total`. Удалить `kpi`; `closed7`/`stageWarn` больше не
  нужны (использовать `counts` везде, где был `kpi`).
- `answerQuestion(id, text)`: `act('answer', () => api.needsOwner(id, false, text))` — ответ автора
  (сервер пишет комментарий `kind=answer` и снимает флаг). Экспортировать; в этой порции им
  пользуется только «Ответить» через панель (см. п. 3), полноценная форма ответа — порция d.
- `paletteOpen` остаётся; добавить `openSearch(text: string)`: если `text.trim()` непустой —
  ставит `query = text`, запускает `runSearch()` и открывает палитру; если пустой — только
  открывает палитру, **не трогая** `query` и `searchResponse`. Важно для повторных открытий:
  `UiCommandPalette` при каждом открытии обнуляет собственное поле и эмитит `search('')`, а
  `runSearch()` на пустом тексте стирает результаты — поэтому пустая строка от палитры нигде не
  должна доходить ни до `query`, ни до `runSearch` (см. п. 5). Пропа для начального текста у
  палитры нет: её поле после открытия пустое, но список `items` показывает результаты текущего
  `searchResponse`.

### 2. `App.vue`: строка вкладок и статус

- Вместо `UiSegmented` видов — `UiTabs` с `tabs = [{key:'board', label:'Доска (N)'}, {key:'list',
  label:'Список'}, {key:'timeline', label:'Таймлайн (M)'}, {key:'metrics', label:'Метрики'}]`
  (`N` = `counts.total`, `M` = `store.timeline.length`, как сейчас) и `v-model` через
  `store.view`/`store.setView`. Панели — через слоты `panel-board`, `panel-list`, `panel-timeline`,
  `panel-metrics`; внутри панелей соответствующие виды (`BoardView`, `ListView`, `TimelineView`,
  `MetricsView`). Секция «Нужен ты» и строка чипов **не** внутри панелей — они над вкладками?
  Нет: по прототипу порядок сверху вниз — вкладки, «Нужен ты», строка чипов/фильтров, доска.
  Поэтому: `UiTabs` рендерится **без** панелей (только tablist, `panel-*` слоты не передавать),
  а под ним — инбокс, строка чипов и затем блок текущего вида по `store.view` (`v-if`, как
  сейчас). `UiTabs` допускает работу без панелей (см. комментарий в шапке `UiTabs.vue`).
- Справа от вкладок в той же строке: `UiSaveStatus` (как сейчас) и `UiBadge neutral sm`
  `{{ tasksCountLabel(counts.total) }}`; бейдж `операция: …` убрать (спиннер есть в `UiSaveStatus`
  и на кнопках).
- Алерты (`connectionLost`, `lastError`, `cycles`) остаются над строкой вкладок.

### 3. Инбокс «Нужен ты»: `components/NeedsYouStrip.vue` → переписать (имя файла можно оставить)

- Шапка секции: `h2.listik-section__title` с `ListikIcon hand md`, текст «Нужен ты»,
  `UiBadge tone="accent" size="sm"` с числом (при нуле — `neutral`); справа `.listik-section__hint`
  «вопрос автору, брошенные и молчащие держатели — то, без чего конвейер стоит».
- Пусто → `UiEmptyState compact` «Никто тебя не ждёт» (как сейчас).
- Сетка `.listik-inbox`: 3 колонки при ширине ≥1280px, 2 — от 1024 до 1279, иначе 1
  (`@media` литералами). Показываются первые 6 карточек; если больше — под сеткой `UiButton ghost sm`
  «Показать ещё N» (раскрывает все).
- Состояние карточки определяется **одной** веткой, проверки строго по порядку: `needs_owner` →
  `taskHealth === 'dead'` → `at-risk`; первая сработавшая задаёт и бейдж, и модификатор, и строку
  причины, и набор кнопок. Задача, которая одновременно `needs_owner` и `dead` (на моке —
  `listik-api-c3d4`: `needs_owner: true`, `abandoned: true`), рисуется как «вопрос автору»: бейдж
  `accent`, без `is-danger`, кнопки «Ответить»/«Открыть»; о брошенности говорит только подвал
  («без держателя · …»).
- Карточка `article.listik-inbox-card` (модификаторы `is-danger` при ветке `dead`, `is-warning`
  при ветке `at-risk`, без модификатора при ветке `needs_owner` — левая полоса 3px
  `--accent-500`/`--danger-500`/`--warning-600`, остальное — `--surface`, `--hairline`,
  `--radius-lg`, `--shadow-xs`; целиком карточку по статусу **не красить**):
  1. верхняя строка: `TaskGlyph type` · `ProjectMark withTitle` (проект из
     `store.meta.projects` по `task.project`) · spacer · бейдж состояния: `needs_owner` →
     `UiBadge accent` «вопрос автору»; `dead` → `UiBadge danger` «брошена {idle_age}» (для
     `abandoned` без держателя — «брошена · без держателя»); `at-risk` → `UiBadge warning`
     «молчит {idle_age}»;
  2. заголовок `h3.listik-inbox-card__title` (одна строка, многоточие);
  3. строка причины `p.listik-inbox-card__q`: для `needs_owner` — `HarnessIcon :actor="task.holder"`
     + `<strong>{{ holder_title || 'агент' }} спрашивает:</strong>` + текст из
     `inboxQuestions[task.id]`, а пока не загружен/нет — `holder_note` или «вопрос без текста —
     откройте карточку»; для `dead` со держателем — «держатель **{holder_title}** замолчал на
     {stageCode} без heartbeat; последняя заметка: «{holder_note}»» (без заметки — без второй
     части); для `abandoned` — «в работе без держателя с {started_at → humanAge}»; для `at-risk`
     — «держатель **{holder_title}** без heartbeat {idle_age} на {stageCode}; порог брошенности —
     24 ч»;
  4. подвал: слева `.listik-section__hint.tnum` с `ListikIcon clock xs`: «держит {holder_title}
     · на этапе {stage_age} · {stageCode}» (без держателя — «без держателя · …»); справа кнопки:
     `needs_owner` → `UiButton primary sm` «Ответить» (emit `answer`) и `UiButton ghost sm`
     «Открыть» (emit `open`); `dead` → `UiButton secondary sm` «Освободить» (emit `release`) и
     «Открыть»; `at-risk` → только «Открыть».
- В `App.vue`: `answer` → `openTaskWithComment(task)` (открывает панель и ставит фокус в поле
  комментария — существующий механизм `drawerRef.focusComment()`; переключение этого поля в режим
  ответа — порция d); `release` → `store.releaseTask(id)` с тостами как у `onRelease`;
  `open` → `store.openTask`.
- Старые кнопки «Снять флаг»/«Комментарий» и показ id на карточке инбокса убрать; `needsYouReason`/
  `needsYouTone` в `format.ts` удалить, если больше никем не используются.

### 4. Ряд чипов и компактные фильтры: новый `components/BoardToolbar.vue`

Одна строка `.listik-toolbar` (flex, `justify-content: space-between`, перенос при нехватке):

- Слева, `UiChip size="sm"`:
  - «{dead} брошена» — `v-model:selected` ↔ `filters.health === 'dead'` (клик по активному снимает);
  - «{atRisk} под угрозой» — ↔ `filters.health === 'at-risk'`;
  - «{blocked} стоят из-за других» — ↔ `filters.deps === 'blocked'`;
  - «{ready} можно брать» — ↔ `filters.deps === 'ready'`;
  - «{inPipeline} в конвейере» — статичный (без привязки `selected`).
  `hint` каждого — расшифровка («держатель молчит дольше суток или задача в работе без
  держателя», «heartbeat старше 15 мин или дольше порога этапа», «есть незакрытые жёсткие
  блокеры», «нет блокеров и держателя», «на этапах s1–s4»). Числа в лейбле — как есть; в ките
  чип без слотов, точки-индикатора нет — это записано в карте покрытия.
- Справа: `UiSelect size="sm"` проекта (`placeholder="все проекты"`, опции `projectOptions(meta)`,
  `aria-label`), рядом с ним слева `ProjectMark` выбранного проекта (пусто, если «все»);
  `UiChip size="sm"` «закрытые» ↔ `store.includeClosed` (`setIncludeClosed`);
  `UiInput size="sm"` класса `.listik-toolbar__search` (ширина 260px через переменную
  `--listik-search-w` в `app.css`), `placeholder="поиск по задачам · Cmd K"`, слот `leadingIcon` —
  `ListikIcon search sm`, `v-model` — **локальный `ref` тулбара** (`searchText`), а не
  `store.query`: поле не должно зависеть от того, что палитра делает со `store.query`;
  `Enter` → `store.openSearch(searchText)`; при открытии тулбара `searchText` инициализируется из
  `store.query`. Фокус в поле не открывает палитру сам (иначе она перекроет ввод). Повторный
  `Enter` с тем же или другим текстом открывает палитру с результатами именно этого текста.
- Переключение чипов/селекта/закрытых зовёт `store.applyFilters()`/`setIncludeClosed` так же,
  как сейчас `patchFilters`.
- `BoardToolbar` рендерится в `App.vue` под инбоксом для всех видов (глобальные фильтры).

### 5. Поиск: `SearchPanel.vue`

Оставить только `UiCommandPalette` (палитра, Cmd/Ctrl+K, `@search` с дебаунсом, `@select` →
`openTask`); `UiSegmented` режима поиска, строку ввода и инлайн-список результатов удалить
(`searchMode` в сторе остаётся `'hybrid'`). Бейджи «N результатов · took_ms» — в `empty`-слот
палитры не класть; палитра показывает результаты сама. В `App.vue` блок `SearchPanel` со слотом
`input` убрать — вход теперь в тулбаре и по хоткею.

Контракт обработчика `@search` (`onPaletteSearch`): **пустая строка игнорируется полностью** —
не пишется в `store.query`, не зовёт `runSearch`, не сбрасывает дебаунс-таймер с непустым
запросом; непустая — как сейчас (`store.query = text`, `runSearch` через 400 мс). Причина: палитра
при каждом открытии обнуляет своё поле и эмитит `search('')`; без этого правила второе открытие
через тулбар стирало бы и текст в поле тулбара, и результаты. Очистка результатов — только явно:
`store.clearSearch()` (кнопки для неё в этой порции нет; закрытие палитры результаты не трогает).

### 6. Фильтр-бар только в «Списке»

- `TaskFilters` (`UiFilterBar`) удалить из `App.vue` и вставить первым блоком в
  `views/ListView.vue` (над `listik-section__head`). Из полей убрать «Проект» и чекбокс
  «закрытые» (они глобальные в тулбаре); чекбокс «только брошенные» заменить на `UiSegmented`
  «здоровье» (`все / брошены / под угрозой` ↔ `filters.health`). Остальные поля — как есть.
  `activeCount` пересчитать под новый набор.
- Дублирующий `UiSegmented` зависимостей в шапке `ListView` убрать (он остаётся в фильтр-баре).

### 7. Порог 1024 и компактный список

- Ниже 1024 px компактным списком заменяется **только доска**; строка вкладок, строка статуса,
  алерт циклов, инбокс, тулбар и виды «Список»/«Таймлайн»/«Метрики» остаются видимыми и рабочими
  на любой ширине. Сейчас в `App.vue` под общим классом `.desktop-only` лежат и строка вкладок,
  и алерт циклов, и блок инбокса/чипов, и обёртка всех четырёх видов, а `app.css` под 640px прячет
  `.desktop-only` целиком — переносить это правило на 1023px нельзя. Сделать так:
  - класс `.desktop-only` и правило `.desktop-only { display: none !important }` удалить
    совсем; вкладки, алерт, инбокс и тулбар — без обёрток-переключателей;
  - `BoardView` обернуть в `.listik-board-only`, `MobileTaskList` — в `.listik-mobile-queue`;
    в `app.css`: по умолчанию `.listik-mobile-queue { display: none }`, под
    `@media (max-width: 1023px)` — `.listik-board-only { display: none }` и
    `.listik-mobile-queue { display: flex; … }` (существующие мобильные правила очереди перенести
    под этот порог);
  - `MobileTaskList` рендерится только при `store.view === 'board'` (на «Списке» ниже 1024
    остаётся `ListView` с таблицей — её мобильный вид решает шаг 06).
  Правила про сжатие шапки (`.listik-shell__hide-compact`, подписи кнопок, ширина контейнера
  24px, ширина дровера 100vw и скрытие частей панели) оставить под 640px как есть. Содержимое
  `MobileTaskList` не менять (шаг 06), кроме замены собственного расчёта здоровья
  (`healthTone`/`healthLabel`) на `taskHealth`/`HEALTH_TITLES` и `UiStatusPill` с тонами
  `healthy|at-risk|dead|unknown`.
- `.listik-shell > .ui-container` ширина остаётся `min(100% - 64px, 1440px)`.

### 8. Уборка

- Удалить `components/KpiRow.vue`, `ReadyStrip.vue`, `ProblemRow.vue`, `scripts/verify-changes.mjs`;
  из `app.css` — `.listik-health-strip`, `.listik-health-chip*`, `.listik-ready*`, правила
  `.ui-kpi-*`, старые `.listik-needs*`; из `web/README.md` — упоминания `verify-changes.mjs`,
  `KpiRow`, `ProblemRow`, `ReadyStrip`, «полосы „Можно брать“» (раздел «Слой зависимостей» —
  пункт про полосу заменить на «чип „можно брать“ в тулбаре»), и описание фильтров/поиска
  привести к новому (тулбар + фильтр-бар в «Списке»).
- `scripts/smoke.mjs`: `report.views` ищет вкладки по `.ui-tabs__list [role="tab"]` и тексту;
  `report.kpi` удалить; `report.needsYou` считает `.listik-inbox-card`; добавить `report.toolbar`
  = число `.ui-chip` в `.listik-toolbar` (ожидается 6: пять счётчиков + «закрытые») и
  `report.filterBarOnBoard` = число `.ui-filter-bar` при активной вкладке «Доска» (ожидается 0)
  и `.ui-filter-bar` на «Списке» (1).

## Границы правки

- Правятся: `App.vue`, `store/listik.ts`, `NeedsYouStrip.vue`, новый `BoardToolbar.vue`,
  `SearchPanel.vue`, `TaskFilters.vue`, `views/ListView.vue` (только вставка фильтр-бара и
  удаление дубля сегмента), `MobileTaskList.vue` (только здоровье), `lib/format.ts` (удаление
  неиспользуемого), `assets/app.css`, `scripts/smoke.mjs`, `web/README.md`, удаление файлов из п. 8.
- Не трогать: `BoardView.vue`, `BoardColumn.vue`, `TaskCard.vue`, `useBoardDrag.ts` (порция c —
  кроме замены `store.kpi`/`needsYou`/`abandonedOnly`, если они там используются: тогда минимальная
  подстановка), `TaskDrawer.vue` (порция d; вызов `focusComment` остаётся), `AppHeader.vue`,
  `ProjectSettings.vue`, `TimelineView.vue`, `MetricsView.vue` (кроме замены `store.kpi` на
  `counts`, если используется), `api/*` (новых эндпоинтов нет), `sheet.html`/`src/sheet/*`,
  примитивы порции a, `vite.config.ts`, `package.json`.
- Не добавлять фильтр «харнесс», KPI-ряд, полосу «можно брать», кнопки на карточку.
- Не красить карточку инбокса целиком по статусу (только левая полоса); чипы — только `UiChip`.
- Не менять контракт API-клиента; вопрос автора читать через существующий `api.task`.
- Никаких токенов и содержимого `config.toml` в файлах и отчёте.

## Как проверить

```sh
cd /Users/dmitry.fomin/Projects/Listik-ui/web
npm run typecheck && npm run build
node scripts/mock-api.mjs 8788 &
VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token npm run dev &
npm run smoke -- http://localhost:5173/ 1440   # views через role=tab, needsYou ≥ 1, toolbar 6, filterBarOnBoard 0
npm run smoke -- http://localhost:5173/ 1024
npm run smoke -- http://localhost:5173/ 900    # компактный список вместо доски
git -C .. diff --stat HEAD
```

На мок-данных инбокс должен показать три карточки в этом порядке: `listik-api-c3d4` как «вопрос
автору» (она же `abandoned`, но ветка `needs_owner` выигрывает; текст вопроса — из `comments`,
если мок его отдаёт, иначе подсказка «вопрос без текста»), `listik-sse-e5f6` как «брошена 40 ч»
с кнопкой «Освободить», `listik-web-a1b2` как «молчит 18 мин» (`idle_hours: 0.3 ≥ 0.25`) с
одной кнопкой «Открыть». Чип «стоят из-за других» = 1 (`listik-metrics-g7h8`), клик по нему
оставляет на доске только её. При 900 px вкладки, инбокс и тулбар на месте, доска заменена
компактным списком.
