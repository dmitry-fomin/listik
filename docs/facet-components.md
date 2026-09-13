# UI-кит `@zoloto585/facet@0.4.1` — справка для кода доски

Источник: README пакета (776 стр.), CHANGELOG, исходники `src/components/<Name>/<Name>.vue`.
Точные props/события/слоты сняты из `defineProps`/`withDefaults`/`defineEmits`/`defineModel`.

## Подключение

```ts
// main.ts — порядок важен
import '@fontsource-variable/geist'
import '@fontsource-variable/jetbrains-mono'
import '@zoloto585/facet/tokens.css'
import '@zoloto585/facet/themes/amber.css'      // дефолтная гамма обязательна
import '@zoloto585/facet/styles/utilities.css' // необязательные утилиты (.tnum, .font-mono)
import App from './App.vue'
```

- Vue **≥ 3.5** обязателен: 15 компонентов (`UiField`, `UiSelect`, `UiMultiSelect`, `UiRadioGroup`,
  `UiFieldForm`, `UiTabs`, `UiModal`, `UiFormModal`, `UiConfirmDialog`, `UiDrawer`,
  `UiFullscreenViewer`, `UiCommandPalette`, `UiPopover`, `UiTooltip`, `UiHeatmap`) используют `useId()`.
  На 3.4 — `useId is not a function` на первом рендере.
- `tsconfig.json`: `"vueCompilerOptions": { "strictTemplates": true }` — иначе неверный prop молча
  уезжает в `$attrs`.
- Пакет source-only: `.vue`/`.ts` как есть, Vite компилирует сам.
- `UiToast` монтируется один раз в корне (`useToast()` — программный API: `success/danger/info/warning`,
  `dismiss`, `clear`, `pause`, `resume`; тона `'error'` больше нет — только `'danger'`).
- Тёмная тема: `data-theme="dark"` на `<html>` (без атрибута — по системе). Гамма:
  `data-color-scheme="amber|malachite|garnet|sapphire"` + файл `themes/<scheme>.css`;
  переключение — `useColorScheme()`.
- `data-motion="essential"` — только для индикаторов состояния (спиннер, indeterminate-полоса),
  и обязательно со своим замедлением в `@media (prefers-reduced-motion: reduce)`.

Токены для вёрстки: `--space-1…24` (шаг 4px), `--text-xs…4xl`, `--radius-2xs…pill`,
`--shadow-xs…xl` (xs — покой карточек, sm — hover, md — hover кликабельной карточки, lg — поповер/тултип,
xl — модалки/дровер), `--control-h-sm|md|lg` 28/32/40, `--header-h` 56px, `--container-max` 1280px,
`--bp-compact` 640px (в `@media` — литералом, `var()` там не работает), `--hairline`,
`--surface`/`--surface-2`, `--ink-1…4`, `--health-healthy|at-risk|dead|unknown`, `--chart-1…6`,
`--icon-xs|sm|md|lg` = 12/14/16/20px, `--z-sticky|header|floating|backdrop|modal|popover|toast` = 1/20/30/40/50/60/70.

## Компоненты, нужные доске

Формат: `имя: тип = дефолт`.

| Компонент | Назначение | Ключевые props | События | Слоты |
|---|---|---|---|---|
| `UiKpiRow` | Ряд метрик | `columns?: 2\|3\|4 = 4`, `dense?: boolean = false` | — | default |
| `UiKpiCard` | Карточка-виджет: значение + дельта + спарклайн | `label`, `value`, `hint?`, `delta?: {value, direction: 'up'\|'down'\|'flat'}`, `sparklineData?: number[]`, `goodDirection?: 'up'\|'down' = 'up'`, `deltaTone?: 'auto'\|'positive'\|'negative'\|'neutral' = 'auto'`, `accent?: boolean = false` | — | `icon` |
| `UiStat` | Число+подпись внутри блока, без тренда | `label`, `value`, `hint?`, `size?: 'sm'\|'md'\|'lg' = 'md'` | — | нет |
| `UiCard` | Поверхность с шапкой/телом/подвалом | `variant?: 'default'\|'hero' = 'default'`, `padding?: 'none'\|'sm'\|'md'\|'lg' = 'md'`, `interactive?: boolean = false`, `ariaBusy?` | — | `header`, default, `footer` |
| `UiEntityCard` | Компактная кликабельная карточка сущности | `title`, `meta?`, `size?: 'sm'\|'md'\|'lg' = 'md'`, `interactive?`, `selected?`, `disabled?`, `loading?` | `click` | `avatar`, `meta`, `actions` |
| `UiEntityHeader` | Шапка панели деталей | `title`, `eyebrow?`, `subtitle?`, `descriptions?: {label,value}[] = []`, `variant?: 'default'\|'hero' = 'default'`, `loading?` | — | `avatar`, `titleMeta`, `descriptions`, `actions` |
| `UiBadge` | Пилюля: тег, счётчик | `tone?: 'neutral'\|'accent'\|'success'\|'danger'\|'warning'\|'info' = 'neutral'`, `size?: 'sm'\|'md' = 'md'` | — | default |
| `UiStatusPill` | Статус с точкой-индикатором | `tone?: BadgeTone\|'healthy'\|'at-risk'\|'dead'\|'unknown' = 'neutral'`, `size?`, `pulse?: boolean = false` | — | default |
| `UiChip` | Чип-фильтр **или** статичная метка | `label`, `hint?`, `selected?: boolean` (нет привязки → статичный `<span>`), `closable?`, `disabled?`, `size?` | `update:selected`, `click`, `close` | нет |
| `UiButton` | Кнопка | `variant?: 'primary'\|'secondary'\|'ghost'\|'danger' = 'primary'`, `size?: 'sm'\|'md'\|'lg' = 'md'`, `type?`, `disabled?`, `loading?`, `block?`, `ariaLabel?`, `form?` | `click` | `icon`, default, `trailingIcon` |
| `UiTableActionButton` | Действие в строке (28px) | `tone?: 'default'\|'accent'\|'success'\|'danger'\|'danger-quiet' = 'default'`, `disabled?`, `label?`, `type?`, `ariaPressed?`, `dataAction?` | `click` | `icon`, default |
| `UiHeaderActionButton` | Действие в шапке | `href?`, `target?`, `disabled?`, `loading?`, `ariaLabel?`, `ariaExpanded?` | `click` | `icon`, default |
| `UiSplitButton` | Главное действие + меню | `items: {key,label,icon?,disabled?,danger?}[]`, `variant?`, `size?`, `disabled?`, `loading?`, `menuLabel? = 'Ещё действия'` | `click`, `select` | `icon`, default |
| `UiInput` | Текстовое поле (`v-model: string`) | `type? = 'text'`, `placeholder?`, `disabled?`, `invalid?`, `error?: string\|null = null`, `size?` | `update:modelValue` | `leadingIcon`, `trailingIcon` |
| `UiTextarea` | Многострочное (`v-model: string`) | `placeholder?`, `rows? = 3`, `disabled?`, `invalid?`, `error?`, `autosize?` | `update:modelValue` | нет |
| `UiSelect<T = string>` | Селект с клавиатурой (`v-model: T\|null = null`) | `options: {value,label,disabled?}[]`, `placeholder? = 'Выбрать…'`, `disabled?`, `invalid?`, `size?`, `ariaLabel?` | `update:modelValue` | нет |
| `UiMultiSelect<T = string>` | Мультивыбор с чипами (`v-model: T[] = []`) | `options`, `placeholder?`, `disabled?`, `invalid?`, `size?`, `maxChips? = 3` | `update:modelValue` | нет |
| `UiSegmented` | Переключатель 2–5 опций | `modelValue: string` (required), `options: {value,label}[]`, `size?: 'sm'\|'md' = 'md'`, `disabled?`, `label?` | `update:modelValue` | нет |
| `UiTabs` | Вкладки | `modelValue: string` (required), `tabs: {key,label,disabled?}[]` | `update:modelValue` | `panel-<key>` |
| `UiFilterBar` | Полоса фильтров | `activeCount? = 0`, `resetLabel? = 'Сбросить'` | `reset` | default, `actions` |
| `UiFilterField` | Поле внутри панели фильтров | `label?`, `fieldId?`, `active?`, `clearable?`, `clearLabel?`, `disabled?`, `error?` | `clear` | default |
| `UiFacetedSidebar` | Фасетные фильтры-чекбоксы (`v-model: string[] = []`, пусто = «Все») | `groups: {key,label,options:{value,label,count?,disabled?}[]}[]`, `allLabel? = 'Все'`, `allCount?`, `loading?` | `update:modelValue` | нет |
| `UiDataTable<T>` | Таблица с сортировкой и липкой шапкой | `columns: {key,label,width?,align?,sortable?}[]`, `rows: T[]`, `loading?`, `density?: 'comfortable'\|'compact'`, `rowKey?`, `sort?: UiDataTableSort\|null`, `emptyTitle? = 'Нет данных'`, `emptyDescription?`, `skeletonRows? = 5`, `stickyHeader?`, `maxHeight?: string\|number`, `frameless?` | `sort` | `cell-<key>`, `emptyIcon` |
| `UiDrawer` | Боковая панель (`v-model: boolean = false`) | `title?`, `side?: 'left'\|'right'\|'top'\|'bottom' = 'right'`, `size?: 'sm'\|'md'\|'lg' = 'md'`, `persistent? = false`, `ariaLabel?` | `close` | `header`, default, `footer` |
| `UiModal` | Модалка (`v-model: boolean = false`) | `title?`, `size?: 'sm'\|'md'\|'lg'\|'fullscreen' = 'md'`, `persistent?`, `role?`, `closable? = true` | `close` | `header`, default, `footer` |
| `UiPopover` | Поповер (`v-model: boolean = false`) | `placement? = 'bottom'`, `offset? = 8`, `disabled?`, `label?` | `close` | `trigger`, default |
| `UiTooltip` | Подсказка по hover/focus | `text?`, `placement? = 'top'`, `offset? = 6`, `openDelay? = 150`, `disabled?` | — | default, `content` |
| `UiTimeline` | Лента событий | `items: {id,title,description?,timestamp?,datetime?,meta?,tone?}[]`, `loading?`, `error?: string\|null`, `skeletonItems? = 3`, `emptyTitle? = 'Событий пока нет'`, `emptyDescription?`, `dense?` | — | `marker`, `content` |
| `UiSteps` | Степпер процесса | `items: {label,description?,status?: 'done'\|'current'\|'upcoming'\|'error'}[]`, `currentIndex? = 0`, `label? = 'Шаги'`, `clickable? = false` | `select: [item, index]` | нет |
| `UiProgress` | Линейный прогресс | `value? = 0`, `max? = 100`, `size?: 'sm'\|'md'`, `indeterminate?`, `label?` | — | нет |
| `UiAvatar` | Аватар с фолбэком | `src?`, `alt?`, `name?`, `size?: 'sm'\|'md'\|'lg'\|'xl' = 'md'` | `error` | `icon` |
| `UiSkeleton` | Плейсхолдер | `variant?: 'text'\|'circle'\|'rect' = 'text'`, `width?`, `height?` | — | нет |
| `UiEmptyState` | «Здесь пусто» | `title`, `description?`, `compact? = false` | — | `icon`, `illustration`, `action` |
| `UiAlert` | Плашка сообщения | `tone?: 'info'\|'success'\|'warning'\|'danger' = 'info'`, `closable? = false` | `close` | `icon`, `title`, default |
| `UiPaginator` | Постраничная навигация | `modelValue: number`, `total: number`, `pageSize? = 20`, `siblingCount? = 1`, `size?`, `disabled?` | `update:modelValue`, `change` | нет |
| `UiCommandPalette` | Палитра команд, Cmd/Ctrl+K встроен (`v-model: boolean = false`) | `items: {id,label,description?,hint?,group?,disabled?,keywords?[]}[]`, `placeholder?`, `loading?`, `emptyTitle?`, `hotkey? = true`, `filterable? = true` | `select: [item, index]`, `search`, `close` | `item`, `icon`, `leadingIcon`, `empty` |
| `UiConfirmDialog` | Подтверждение (`v-model: boolean = false`) | `title`, `description?`, `confirmLabel? = 'Подтвердить'`, `cancelLabel? = 'Отмена'`, `tone?: 'default'\|'danger' = 'default'`, `loading?` | `confirm`, `cancel` | default |
| `UiBulkEditModal` | Массовая правка (`v-model: boolean = false`) | `title?`, `fields: UiFieldFormField[]`, `selectedCount?`, `size? = 'lg'`, `errors?`, `loading?`, `submitLabel?`, `cancelLabel?` | `submit: [changes]`, `cancel` | `summary` |
| `UiMarkdownEditor` | Markdown-редактор (`v-model: string`) | `placeholder?`, `disabled?`, `invalid?`, `error?`, `rows? = 10`, `showPreview? = true`, `label?`, `focusMode?: 'off'\|'toggle'\|'trigger'`, `triggerLabel?` | `update:modelValue` | нет |
| `UiDiffViewer` | Построчный дифф | `previous: string\|null`, `current: string`, `placeholder?`, `previousPlaceholder?`, `wasLabel?`, `diffLabel?`, `nowLabel?`, `maxLines?`, `loading?` | — | нет |
| `UiGanttGrid` | Gantt-сетка (внутренний DnD интервалов) | `columns`, `rows`, `items: {id,rowId,colStart,colEnd,label,description?,tone?}[]`, `loading?`, `movable?`, `rowLabelWidth? = 168`, `columnWidth? = 56`, `rowHeight? = 40`, `emptyTitle?` | `select-item`, `select-cell`, `item-move` | `item-details` |
| `UiSavedPresetManager` | Пресеты фильтров (`v-model: boolean = false`) | `presets: {id,name}[]`, `loading?`, `saveLoading?`, `disabled?`, `loadingId?`, `placement?`, `triggerLabel?`, `saveLabel?`, `namePlaceholder?`, `emptyTitle?` | `save`, `apply`, `rename`, `delete` | — |
| `UiColumnSetting` | Видимость/порядок колонок (`v-model: {key,label,visible,order}[] = []`) | `disabled?`, `triggerLabel? = 'Колонки'`, `searchPlaceholder?`, `emptyTitle?`, `noMatchText?`, `resetLabel?` | `reset` | `icon` |
| `UiAppHeader` | Липкая шапка приложения | `contained? = false`, `sticky? = true` | — | `brand`, default, `actions` |
| `UiAppNav` | Навигация приложения | `items: {id,label,href?,badge?,disabled?}[]`, `modelValue? = null`, `orientation?: 'horizontal'\|'vertical' = 'horizontal'`, `ariaLabel?` | `update:modelValue`, `select` | `icon` |
| `UiPageHeader` | Шапка страницы (ровно одна) | `title`, `description?`, `variant?: 'default'\|'hero' = 'default'` | — | `breadcrumb`, `actions` |
| `UiContainer` | Центрирующая колонка | `size?: 'page'\|'narrow' = 'page'` (1280/480), `padded? = true` | — | default |
| `UiSectionNav` | Навигация по секциям (scrollspy) | `items: {id,label,disabled?}[]`, `offset? = 0`, `ariaLabel? = 'Разделы'`, `emptyTitle?` | `update:modelValue` | `empty`, `icon` |
| `UiSaveStatus` | Индикатор автосохранения | `status: 'idle'\|'saving'\|'saved'\|'error'`, `at?` | `retry` | нет |
| `UiExportButton` | Кнопка экспорта со спиннером | `disabled?`, `loading?`, `progress?: number\|null` | `click` | `icon`, default |
| `UiScrollToTopButton` | Плавающая «наверх» | `threshold? = 240`, `target?: string\|HTMLElement`, `side?: 'left'\|'right'`, `label?` | `click` | default |
| `UiSpinner` | Спиннер | `size?: 'sm'\|'md'\|'lg' = 'md'`, `tone?: 'accent'\|'current' = 'accent'`, `label? = 'Загрузка…'` | — | нет |
| `UiSparkline` | Мини-график | `data: number[]`, `width? = 96`, `height? = 32`, `stroke?`, `strokeWidth? = 1.5`, `area?`, `areaOpacity? = 0.16` | — | нет |
| `UiBarChart` / `UiLineChart` / `UiHBarList` / `UiHeatmap` | Графики (inline SVG) | `UiHeatmap`: `cells: {date,value,bucket?}[]`, `weekStart? = 'monday'`, `label?`, `valueFormatter?`, `showLegend?`, `loading?` | `UiHBarList`: `select` | — |
| `UiTree` | Иерархия | `nodes: {id,label,meta?,badge?,disabled?,children?}[]`, `label`, `openLevel? = 1`, `selectedId? = null`, `loading?` | `select` | `icon` |
| `UiJobQueueTable` | Таблица запусков агентов | `jobs: {…, status: 'pending'\|'running'\|'done'\|'error', progress?, processed?, total?}[]` | — | `title`, `status`, `progress`, `timestamp`, `actions` |

## Ловушки

Молчаливые (не ловятся ни typecheck, ни рантаймом):

1. `UiChip` **без привязки `selected`** — статичная метка (`<span>`), не кнопка. Режим определяется
   наличием любой привязки (`v-model:selected`, `:selected`, `@update:selected`). Своего состояния
   чип не хранит.
2. `v-model:open` у `UiPopover`/`UiSavedPresetManager` **больше не существует** — только `v-model`.
3. `error` — **строка** (`string | null`) во всём ките; `errorMessage` удалён. `:error="true"` нарисует
   алерт со словом «true».
4. Событие выбора везде `select: [item, index]` (`UiSteps` раньше был `[index]`, у `UiHBarList`
   `rowClick` → `select`).
5. `UiStat` ≠ `UiKpiCard`: первая — встраиваемая метрика, вторая — самостоятельный виджет
   (композиция `UiStat` + `UiSparkline` + `UiBadge`), вручную её не собирать.
6. `UiKpiCard.accent` по умолчанию `false`; правило: ≤4 в ряд и **ровно одна** с `accent`.
7. Ровно один `UiPageHeader` на страницу.
8. `stickyHeader` у `UiDataTable` работает только вместе с `maxHeight`.
9. Оверлеи внутри `#brand` у `UiAppHeader` и внутри пунктов `UiAppNav` выносить `Teleport`-ом в body;
   слот `#brand` сжимается — многоточие ставит потребитель.
10. Числа — `tabular-nums` / класс `.tnum`, включая счётчики и «висит N ч».
11. Не красить карточку/строку целиком по статусу: максимум `border-left: 2–3px`.
    `--health-healthy` — нейтральный `--ink-2`, **не зелёный**; `dead` — единственное место `--danger-*`.
12. Градиент — только на акцентных элементах (`UiKpiCard accent`, hero-варианты).
13. Одна тень — один слой. Кнопки/поля — не больше `--radius-lg`; `--radius-pill` — для пилюль.
14. Максимум 1–2 инлайн-действия в строке, 3+ — в меню `UiSplitButton`.
15. Отступ между секциями — `--space-8`, внутри секции — `--space-4`/`--space-5`.
16. Нет токена — заводите переменную после `tokens.css`, а не `#hex`/`14px` в разметке.

## Чего в ките нет

- **Канбан-доски и кросс-колоночного drag-and-drop нет.** DnD есть только внутри компонентов:
  `UiRecordList` (порядок своих строк), `UiColumnSetting` (порядок колонок), `UiGanttGrid` (`movable`),
  `UiFileUpload`. Карточки между колонками — нативные HTML5 drag-события, с ловушками:
  - `dataTransfer.setData('text/plain', id)` обязателен (иначе Firefox не начнёт перенос),
    `effectAllowed = 'move'`, `setDragImage(cardEl, …)` если ручка отдельная;
  - `draggable` — на ручке, а не на карточке, если в карточке есть выделяемый текст;
  - `event.preventDefault()` в `dragover` — только когда действительно несёте карточку;
  - коммит один раз в `drop`, сброс состояния в `dragend`;
  - не снимать `pointer-events` с источника в том же кадре (Chrome оборвёт drag);
  - обязательна клавиатурная альтернатива (меню «Переместить в…»), с клавиатуры HTML5 DnD не работает.
- Нет `UiAppShell`/layout'а (собирается из `UiAppHeader` + `UiAppNav` + `UiBrandMark` + `UiContainer`).
- Нет иконного набора и шрифтов (иконки — свои SVG через слоты, размер по `--icon-*`).
- Нет data-слоя, стора, fetch/кеша, роутинга, i18n, виртуализации списков.
- Нет экспорта в файл (`UiExportButton` — только кнопка с прогрессом).
