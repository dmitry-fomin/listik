# Приёмка порции 05.a, заход r1 — зелёный

Дерево порции: `/Users/dmitry.fomin/Projects/Listik-ui` (ветка `pipeline-ui`).
Прогон: `npm install` → `npm run typecheck` → `npm run build` → dev-сервер (`mock-api.mjs` на 8788,
Vite на 5173) → свои CDP-пробники по `/sheet.html` и `/` → `npm run smoke` на 1024/1440/без аргумента.

## Чек-лист

### Сборка и типы

1. **зелёный.** `npm run typecheck` — без вывода ошибок; `npm run build` — «✓ built in 444ms»,
   `ls dist` → `assets`, `index.html`; `ls dist | grep -c sheet` → `0`.
2. **зелёный.** `git status --porcelain` в корне трека: изменены `web/README.md`,
   `web/scripts/smoke.mjs`, `web/src/assets/app.css`, `web/src/lib/icons.ts`, `web/src/main.ts`;
   новые — `docs/facet-prototype-map.md`, `web/sheet.html`, `web/src/components/marks/`,
   `web/src/lib/{harness,health,projects,stages}.ts`, `web/src/sheet/`. Ни одного файла из
   `views/`, `store/`, `api/`, `App.vue`, `AppHeader.vue`, `TaskDrawer.vue`, `board/*`,
   `vite.config.ts`, `listik/`. `web/package-lock.json` не изменён (`git diff --stat` пуст).
   В `app.css` — только две вставки (блок токенов и раздел примитивов в конце), существующие
   правила не тронуты.

### Карта покрытия

3. **зелёный.** `docs/facet-prototype-map.md` есть, таблица из 30 строк. Скрипт по файлу:
   все классы из ТЗ присутствуют (`.hdr .pill .badge .seg×2 .chip .select .input .btn .inbox-card
   .rail .col .tc .done-rail .drawer .steps .cold .dl .tl .dep .modal .field .textarea .progress
   .est .route .pmark .hico .hdot .pico .cnt`). `.seg` видов → `UiTabs` («вкладки переключают
   контент — семантика tablist»), `.seg` ленты → `UiSegmented`, `.est` → «вне шага порции».
   Внизу раздел «Токены, которых нет в ките»: `--listik-pmark-ink` и пять `--listik-harness-*`.
   Замечание (не блокирует, см. ниже): строки `.dl` и `.dep` в колонке причины несут только
   «уже есть в `app.css`».

### Гаммы

4. **зелёный.** `web/src/main.ts` (строки 4–8): `tokens.css` → `themes/amber.css`,
   `malachite.css`, `garnet.css`, `sapphire.css` → `utilities.css` → `app.css`. Те же четыре
   импорта в `web/src/sheet/main.ts`.
   (а) Переключение сегмента гаммы даёт разные `--accent-500` и правильный `dataset.colorScheme`
   и в витрине, и на доске:

   | гамма | `dataset.colorScheme` | `--accent-500` (доска) | `--accent-500` (sheet) |
   |---|---|---|---|
   | amber | amber | `#DC9D2F` | `#DC9D2F` |
   | malachite | malachite | `#60C192` | `#60C192` |
   | garnet | garnet | `#E2909D` | `#E2909D` |
   | sapphire | sapphire | `#7DAFE8` | `#7DAFE8` |

   (б) `UiBadge tone="accent"` в секции «Кит: статусы и метки» меняется вместе с гаммой:
   фон `rgb(54,34,4)` / `rgb(10,46,32)` / `rgb(70,15,32)` / `rgb(15,38,70)`, текст
   `rgb(249,205,124)` / `rgb(172,227,198)` / `rgb(244,199,205)` / `rgb(189,215,245)`.
   Проверено, что дефолт доски не уехал: `:root` без `data-color-scheme` объявляет только
   `amber.css` (строка 9), остальные три файла начинаются с `:root[data-color-scheme="…"]`.

### Справочники

5. **зелёный.** Модули импортированы в живой странице (`import('/src/lib/health.ts')`),
   вызовы дали: `open/holder=null` → `unknown`; `in_progress + abandoned, holder=null` → `dead`,
   `брошена · без держателя`; `in_progress + stale, idle_hours=40` → `dead`, `брошена 40 ч`;
   `idle_hours=0.1` → `healthy`; `idle_hours=0.5` → `at-risk`, `молчит 30 мин`;
   `stage_warn=true, idle_hours=0.1` → `at-risk`, `на этапе дольше порога`;
   `done`/`cancelled` с `idle_hours=300` → `healthy`, `закрыта`. `AT_RISK_IDLE_HOURS === 0.25`,
   объявлена один раз (`web/src/lib/health.ts:10`), порядок проверок в коде — закрыта → stale/
   abandoned → `!holder` → stage_warn/idle → healthy (`health.ts:34-41`).
6. **зелёный.** `harnessOf`: `agent:claude→claude`, `dsh/deepseek-flash→dsh`, `agent:codex→codex`,
   `agent:grok→grok`, `agent:gemini→gemini`, `me→human`, `Иван→human`, `null→null`, `''→null`;
   дополнительно `agent:opus/agent:sonnet/fable→claude`, `deepseek→dsh`.
7. **зелёный.** `projectMark`: `{zoloto585-symfony, Symfony, chart-2}` → `mark 'Sy'`,
   `color 'var(--chart-2)'`; `{x, null, null}` → `mark 'X'`, `color 'var(--chart-1)'` и тот же
   `var(--chart-1)` при повторном вызове; `color:'#abcdef'` → `'#abcdef'`;
   `Zoloto585/my-repo` без title → `mark 'My'`.
8. **зелёный.** `stageCode('s3-impl')='s3'`, `stageTitle('s2-review')='Второе мнение'`,
   `transitionOut('s2-review')='handoff'`, `'s3-impl'→'sticky'`, `'s4-judge'→'handoff'`,
   `transitionOut(null)=null`. `PIPELINE_STAGE_KEYS`/`stageIndex` не продублированы —
   реэкспорт из `format.ts` (`stages.ts:8-10`).

### Примитивы (sheet.html)

9. **зелёный.** `http://localhost:5173/sheet.html` открывается, `Runtime.exceptionThrown` пуст,
   `Log.entryAdded level=error` — только `favicon.ico 404` (см. «Замечания»). Ровно один
   `.ui-page-header`. Переключатель темы: `data-theme` → `light`, `body` фон `rgb(245,244,238)`,
   `--listik-pmark-ink` в светлой `rgb(255,255,255)` = `--neutral-0`, в тёмной `rgb(14,13,11)` =
   `--neutral-900`.
10. **зелёный.** Легенда: 5 приоритетов с `title` `P0 · блокер`, `P1 · критичный`, `P2 · обычный`,
    `P3 · низкий`, `P4 · потом` и цветами `--danger-500`/`--warning-600`/`--ink-3`/`--info-500`/
    `--ink-4` (сверено с `getComputedStyle` по каждому токену); 3 типа — эпик `rgb(156,134,249)`
    = `--chart-3`, задача `rgb(113,125,229)` = `--info-500`, баг `rgb(251,89,80)` = `--danger-500`;
    3 счётчика — `--warning-700` / `--accent-700` / `--ink-3`; 4 точки здоровья — healthy
    `rgb(229,226,218)` ровно `--health-healthy` (нейтральный, не зелёный), at-risk `--health-at-risk`,
    dead `--health-dead`, unknown `rgba(0,0,0,0)` + `inset 0 0 0 1.5px rgb(168,164,154)` =
    `--health-unknown`; 6 меток проектов (`chart-2`, пустой цвет → fallback, произвольный
    `#CD68CB`, `chart-3`, `chart-6`, `null` → fallback), у каждой монограмма и `title` = slug;
    6 харнессов — 5 `.listik-harness-icon` (claude `rgb(217,119,87)`, dsh `rgb(77,107,254)`,
    codex `rgb(74,140,255)`, grok = currentColor, gemini `rgb(142,124,242)`) + `human` через
    существующую иконку `user`.
11. **зелёный.** Смонтировал `CountGlyph` вне витрины: при `count:0` `innerHTML` = `<!--v-if-->`,
    узлов 0; при `count:3` — `span.listik-count-glyph` с svg и `.tnum`. Заодно `HarnessIcon`
    с `actor:null` не рендерит ничего, `actor:'me'` → иконка `user`.
12. **зелёный.** `UiStatusPill` — 9 штук (healthy, at-risk, dead, unknown, info, success, danger,
    warning, neutral); `UiBadge` — 6, что и есть весь союз `BadgeTone` в ките
    (`UiBadge.vue:11`); `UiChip` — два статичных (`ui-chip__label` = `SPAN`, без `aria-pressed`)
    и один кликабельный (`BUTTON`, `aria-pressed` `false` → `true` по клику, корень получает
    `ui-chip--on ui-chip--interactive`); `UiTabs` — 4 вкладки, клик по третьей показывает
    «панель «Таймлайн»», ArrowRight переводит на «Метрики»; `UiSegmented` — 2 (гамма и
    группировка); `UiSteps` — 5 шагов, классы `done, done, current, upcoming, upcoming`;
    `UiProgress` — 1; `UiTimeline` — `ui-timeline__list--dense` с 5 `li`; кнопки открывают
    `UiDrawer` шириной 720px при 1440 и `UiModal size="lg"` шириной 760px; `UiField` ×3 с
    `UiSelect`/`UiInput`/`UiTextarea`.
13. **зелёный.** `grep '\.btn|\.badge|\.pill|\.seg\b'` по `marks/*.vue`, `sheet/*.vue`, новым
    правилам `app.css` — пусто. `grep '#[0-9a-fA-F]{3,6}'` по `app.css`, `components/marks`,
    `src/sheet` даёт только пять объявлений `--listik-harness-*` (`app.css:40-44`) и
    `fixtures.ts:13` — фикстура проекта с произвольным CSS-цветом, которую прямо требует ТЗ
    (§6 п.1: «5–6 фиктивных `ProjectRow` с разными `color`: `chart-2`, пустой, произвольный
    CSS-цвет»); это данные, а не разметка/стиль, поэтому пункт считаю выполненным.
    Новые правила `app.css` ссылаются только на токены кита и `--listik-*`.

### Smoke

14. **зелёный.** `npm run smoke -- http://localhost:5173/ 1024` → `layout.viewport.width = 1024`,
    `boardScrollable: true`, `columnsVisible: 3`, `console: []`; на 1440 — `width 1440`,
    `columnsVisible: 5`; без второго аргумента — `width 1440`. `errors` содержит только
    `favicon.ico 404` (см. «Замечания»).
15. **зелёный.** В `smoke.mjs` на месте `views` (все четыре вида), `drawer`, `kpi`, `columns`,
    `cards`, `needsYou`; правки затронули только шапку, `--window-size` и `report.layout`.

### Не сломано

16. **зелёный.** Smoke по доске: 5 колонок, 5 карточек, 2 карточки «нужен ты», переключение
    всех четырёх видов, дровер открывается (720px, 4 шага, раздел «Где стоит процесс»),
    исключений в консоли нет. Существующие компоненты не менялись (пункт 2). Дефолтная гамма
    доски не сместилась — см. пункт 4.
17. **зелёный.** `web/README.md`: строки про `components/marks/`, `sheet/` и ширину smoke.
    Есть ещё одна правка в том же блоке «Структура» — строка `lib/` дополнена
    «здоровье/харнесс/проект/этапы»; это описание модулей, заведённых этой же порцией,
    поэтому не считаю сторонней правкой.
18. **зелёный.** Ни токенов, ни содержимого `config.toml`, ни `.env`/`*.key`/`*.pem`/
    `credentials.json` в диффе нет (`grep -niE 'token|secret|password|api[_-]?key'` по новым
    файлам даёт только `import '@zoloto585/facet/tokens.css'`).

## Разбор диффа на срезанные углы

- Хардкода под проверку нет: `taskHealth`/`healthReason`/`projectMark` — чистые функции без
  ветвлений «под пример из чек-листа»; порядок проверок в коде совпадает с ТЗ построчно.
- Витрина не подгоняет вид: `Sheet.vue` не заводит своих `.btn/.badge/.pill/.seg`, все размеры
  и цвета — через токены и `--listik-*`, а служебные классы (`listik-section`, `listik-row`,
  `listik-stack`, `listik-prose`) — уже существующие в `app.css` (строки 121, 139, 150, 650, 656, 682).
- Правка в том слое: гаммы подключены в `main.ts`, а не костылём в `AppHeader.vue`; ширина окна
  smoke — аргумент скрипта, а не правка `vite.config.ts`; `sheet.html` не попал в сборку именно
  потому, что `vite.config.ts` не трогали (rollup input по умолчанию = `index.html`).
- Заглушек вместо реализации нет: примитивы рендерят реальные глифы, `CountGlyph` честно
  отсутствует при нуле, `HarnessIcon` — при пустом акторе.
- `--listik-harness-grok: currentColor` — не пропуск, а значение из прототипа; в браузере
  раскрылось в унаследованный `--ink` (`rgb(247,248,250)`).

## Замечания (не блокируют, для приёмки шага)

- `docs/facet-prototype-map.md:28,30` — строки `.dl` и `.dep` в колонке «Почему не кит /
  замечание» несут только «уже есть в `app.css`»: это констатация состояния, а не причина,
  почему кит не подошёл. Обе строки описывают классы, заведённые в прежних шагах, а не решения
  этой порции, и колонка по заголовку допускает «замечание», поэтому пункт 3 зачтён; стоит
  дописать причину при итоговой правке карты.
- `favicon.ico 404` в консоли и на витрине, и на доске: у Vite-dev нет фавиконки, а Chrome
  просит её всегда. Ни `index.html`, ни `sheet.html` фавиконку не объявляют, `index.html` эта
  порция не трогала — поведение существовало до неё и к порции отношения не имеет.
- `web/src/assets/app.css` в новом разделе использует px-литералы вне объявлений переменных
  (`gap: 3px`, `box-shadow: inset … 1.5px`, дефолты `var(--listik-*-size, 18px)`). Это стиль
  всего файла (рядом `padding-bottom: 32px`, `width: 260px`), чек-лист таких литералов не
  запрещает — запрет пункта 13 и «Границ правки» касается `#hex` и разметки/scoped-стилей.

## Вердикт

Зелёный. Порция закоммичена в `/Users/dmitry.fomin/Projects/Listik-ui` (ветка `pipeline-ui`)
перечисленными явно путями.
