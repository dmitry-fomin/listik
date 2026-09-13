# Порция 05.a. Кит под прототип: карта, общие примитивы, гаммы, dev-витрина, smoke на 1024

## Контекст

Доска Listik — `web/` (Vue 3.5 + TS + Vite, кит `@zoloto585/facet@0.4.1`, source-only, в
`web/node_modules/@zoloto585/facet`; в дереве трека `node_modules` нет — `cd web && npm install`,
postinstall патчит кит, это штатно). Работа идёт в дереве
`/Users/dmitry.fomin/Projects/Listik-ui` (ветка `pipeline-ui`), правится только `web/*`.

Прототипы — `docs/prototype/{Main,BoardLight,CardStates,TaskPanel,NewTask}-html/<Name>.dc.html`:
файл `<x-dc>` с блоком `<helmet><style>` (значения токенов и классы прототипа) и разметкой с
инлайн-стилями; `README.md` рядом объясняет, что это референс, а не код. Открыть в браузере:
`python3 -m http.server` в каталоге прототипа. `BoardLight` — та же `Main`, но в светлой теме.
Прототипные классы, которые понадобятся везде: `.pmark` (метка проекта: квадрат 18px, монограмма
из двух букв, фон `--chart-N`, цвет текста `--pmark-ink`), `.hico` (иконка харнесса 14–18px с
фирменным цветом), `.hdot` (точка здоровья 8px: healthy — `--health-healthy`, at-risk —
`--health-at-risk`, dead — `--health-dead`, unknown — прозрачная с обводкой `--health-unknown`),
`.pico` (иконка типа/приоритета 16px), `.cnt` (счётчик связи: иконка 12px + число). Легенда типов,
приоритетов, связей, здоровья и проектов — верх `CardStates.dc.html`.

Перед началом прочитать: `README.md` кита (разделы «Тёмная тема и цветовые гаммы», «Язык системы»,
«Что выбрать», «Иконки», «Как найти нужный компонент»), `docs/facet-components.md` (таблица
компонентов и «Ловушки»), `web/src/main.ts`, `web/src/lib/theme.ts`, `web/src/lib/icons.ts`,
`web/src/components/ListikIcon.vue`, `web/src/components/AppHeader.vue`, `web/scripts/smoke.mjs`,
`web/src/api/types.ts` (`Task`, `ProjectRow`, `Meta`).

Задача порции — не экраны, а фундамент, который автор посмотрит первым: карта «какой блок прототипа
чем делается в ките», общие примитивы, которых в ките нет, рабочие гаммы, dev-витрина, где всё это
видно живьём, и smoke с параметром ширины.

## Что сделать

### 1. `docs/facet-prototype-map.md` — карта покрытия прототипа китом

Документ в корне `docs/` (не в `web/`). Одна таблица со столбцами: «Блок прототипа (класс в
`.dc.html`)», «Экран», «Чем делается», «Почему не кит / замечание». Строки — все смысловые блоки
пяти прототипов, минимум: `.hdr/.mark/.brand-name` (→ `UiAppHeader` + `UiBrandMark`), `.pill`
(→ `UiStatusPill`), `.badge` (→ `UiBadge`), `.seg` для видов (→ `UiTabs`, решение автора),
`.seg` для группировки/фильтра ленты (→ `UiSegmented`), `.chip` (→ `UiChip` с привязкой `selected`),
`.select` (→ `UiSelect`), `.input` поиска (→ `UiInput` + `UiCommandPalette`), `.btn` (→ `UiButton`),
`.inbox-card` (своя разметка: карточка инбокса), `.rail/.rail__tr` (своя: рельса переходов),
`.col/.col__head/.health-row` (своя: колонка канбана), `.tc` (своя: компактная карточка; `UiEntityCard`
не подходит — нет слотов под верхнюю строку/подвал и состояний здоровья), `.done-rail` (своя),
`.drawer` (→ `UiDrawer size="lg"` + существующее правило ширины 720px в `app.css`), `.steps/.step`
(→ `UiSteps`; подписи переходов sticky/handoff — в `description` шага, слота на коннекторе у кита
нет), `.cold` (своя: чек-лист «Холодный старт»), `.dl` (своя `listik-dl`), `.tl` (→ `UiTimeline dense`),
`.dep` (своя `listik-dep`), `.modal/.modal__*` (→ `UiModal size="lg"`), `.field/.field__label`
(→ `UiField`), `.textarea` (→ `UiTextarea`), `.progress` (→ `UiProgress size="sm"`), `.est/.scale`
(вне шага: оценщика нет), `.route/.route__cell/.chain` (своя: матрица маршрута, порция e),
`.pmark/.hico/.hdot/.pico/.cnt` (свои примитивы этой порции). Для каждой «своей» разметки — одна
фраза, почему в ките нет аналога (по «Что выбрать»/каталогу кита), и какие токены используются.
Внизу — короткий раздел «Токены, которых нет в ките и которые заведены в `app.css`»: цвета харнессов.

### 2. Гаммы кита

`web/src/main.ts`: после `tokens.css` импортировать все четыре файла гамм
(`@zoloto585/facet/themes/amber.css`, `malachite.css`, `garnet.css`, `sapphire.css`), затем
`utilities.css` и `app.css` — как сейчас. Проверить, что переключатель гаммы в шапке (`useColorScheme`,
`AppHeader.vue`) реально меняет акцент: сейчас загружен только amber и переключение не видно.
Проверять по акцентным элементам, а не по кнопке «Обновить» — она `variant="secondary"` и акцента
не несёт: значение `--accent-500` на `<html>` и цвет `UiBadge tone="accent"` в витрине (п. 6)
должны отличаться для четырёх гамм. Своих тем/гамм не заводить.

### 3. Модули-справочники в `web/src/lib/`

Все — чистые функции с типами, без обращения к стору.

- `health.ts`: `export type Health = 'healthy' | 'at-risk' | 'dead' | 'unknown'`;
  константы `AT_RISK_IDLE_HOURS = 0.25` (heartbeat старше 15 мин, легенда CardStates) и
  `HEALTH_TITLES: Record<Health, string>` (`здорова`, `под угрозой`, `брошена`, `без держателя`);
  `taskHealth(task: Task): Health` — проверки **строго в этом порядке**, первая сработавшая
  возвращает результат:
  1. `task.status === 'done' || task.status === 'cancelled'` → `healthy` (закрытая задача по
     heartbeat не оценивается: сервер при закрытии держателя и `holder_at` не снимает, и без этого
     правила каждая закрытая задача с `holder_at` старше 15 мин стала бы `at-risk`);
  2. `task.stale || task.abandoned` → `dead` (на сервере `abandoned` — это в первую очередь задача
     «в работе без держателя», поэтому эта проверка идёт **до** проверки держателя);
  3. `!task.holder` → `unknown`;
  4. `task.stage_warn` или `task.idle_hours !== null && task.idle_hours >= AT_RISK_IDLE_HOURS`
     → `at-risk`;
  5. иначе `healthy`.
  `healthReason(task): string` — короткая подпись для тултипа/бейджа, тем же порядком: `закрыта`
  для done/cancelled, `брошена {idle_age}` для stale, `брошена · без держателя` для abandoned без
  держателя, `без держателя` для unknown, `молчит {idle_age}` при at-risk по heartbeat, `на этапе
  дольше порога` при stage_warn, `hb {holder_age}` для healthy.
- `harness.ts`: `export type HarnessKey = 'claude' | 'dsh' | 'codex' | 'grok' | 'gemini' | 'human'`;
  `harnessOf(actor: string | null | undefined): HarnessKey | null` — `null` для пустого, по
  подстрокам ключа в нижнем регистре (порядок как в `listik/actors.py AGENT_HINTS`: claude/opus/
  sonnet/fable → `claude`; dsh/deepseek → `dsh`; grok; codex; gemini), всё остальное (`me`,
  произвольные имена) → `human`; `HARNESS_TITLES` (`claude`, `dsh`, `codex`, `grok`, `gemini`,
  `человек`).
- `projects.ts`: `projectMark(project: Pick<ProjectRow,'slug'|'title'|'color'> | null | undefined, slug?: string | null): { mark: string; color: string; title: string }`.
  `title` — `project.title || slug`; `mark` — первые две буквы `title` (первая заглавная, вторая
  строчная; если слово одно и короче двух букв — одна буква; для slug с категорией `a/b` — по части
  после `/`); `color` — если `project.color` непустой: строка вида `chart-N` → `var(--chart-N)`,
  иначе значение как есть (CSS-цвет); если пустой — `var(--chart-N)`, где N = 1 + (сумма кодов
  символов slug mod 6) — детерминированно и стабильно между перезагрузками. Функция ничего не
  запрашивает: список проектов ей передают из `store.meta.value.projects`.
- `stages.ts`: `PIPELINE: { key: 's1-spec'|…, code: 's1'|'s2'|'s3'|'s4', title: string }[]` с
  названиями из прототипа (`ТЗ и чек-лист`, `Второе мнение`, `Реализация`, `Проверка и коммит`);
  `stageCode(stage)`, `stageTitle(stage)`; `TRANSITIONS: Record<'s1-spec:s2-review'|…, 'sticky'|'handoff'>`
  с дефолтом сервера (s1→s2 sticky, s2→s3 handoff, s3→s4 sticky, s4→done handoff) и
  `transitionOut(stage): 'sticky'|'handoff'|null`. `PIPELINE_STAGE_KEYS` и `stageIndex` из
  `format.ts` не дублировать — либо реэкспортировать, либо оставить их в `format.ts` и ссылаться.

### 4. Иконки: `web/src/lib/icons.ts`

Добавить пути (16×16, `currentColor`, тот же контракт, что у существующих) для: типов —
`epic` (слои), `task` (галочка в круге/квадрате), `bug` (жук); приоритетов — `p0` (знак блокера:
круг с чертой/восклицание), `p1` (двойной шеврон вверх), `p2` (равно), `p3` (шеврон вниз),
`p4` (двойной шеврон вниз); связей — `lock` («ждёт N»), `key` уже есть («её ждут N»), `branch`
(«детей N»). Пути можно взять из `<svg>` прототипа (это ассеты, а не разметка) или нарисовать
свои того же оптического веса. Иконки харнессов — отдельный компонент (п. 5), не в этот словарь.

### 5. Общие примитивы: `web/src/components/marks/`

Все — SFC `<script setup lang="ts">`, без своего CSS сверх минимально необходимого, классы с
префиксом `listik-`; стили — в `web/src/assets/app.css` (новый раздел «Примитивы прототипа»),
только на токенах кита.

- `ProjectMark.vue` — props `project: ProjectRow | null`, `slug?: string | null`,
  `size?: 'sm'|'md'` (16/18 px), `withTitle?: boolean`. Рендерит квадрат с монограммой
  (`projectMark()` из п. 3): фон — цвет, текст — `--pmark-ink`? Такого токена в ките нет: завести в
  `app.css` переменную `--listik-pmark-ink` (тёмный текст на цветном фоне в обеих темах — взять
  `--neutral-900` в тёмной теме и `--neutral-0` в светлой, как в прототипе: `Main.css` vs
  `BoardLight.css`, `--pmark-ink`), радиус `--radius-xs`, шрифт 10px → ближайший токен
  `--text-xs`, `--weight-semibold`. `title` (нативный) — slug. При `withTitle` рядом название
  проекта (`--text-sm`, `--ink-2`, `--weight-medium`, обрезка многоточием).
- `HarnessIcon.vue` — props `actor: string | null | undefined` (ключ держателя/исполнителя) или
  `harness: HarnessKey`, `size?: 'xs'|'sm'|'md'` (14/16/18 px). Глиф на харнесс: SVG-пути из
  прототипа (`.hico` → `<svg>`) для claude/dsh/codex/grok/gemini; для `human` — существующая
  иконка `user`. Цвета — переменные `--listik-harness-claude`, `-dsh`, `-codex`, `-grok`,
  `-gemini`, объявленные в `app.css` после токенов (значения из прототипа: `#D97757`, `#4D6BFE`,
  `#4A8CFF`, `currentColor`, `#8E7CF2`); в разметке `#hex` не писать. `title` — название харнесса.
  Для `null` (нет актора) ничего не рендерит.
- `HealthDot.vue` — props `health: Health`, `size?: 'sm'|'md'` (8/10 px), `label?: string`
  (для `title`/`aria-label`, по умолчанию `здоровье: <health>`). Цвета строго из токенов
  `--health-*`; `unknown` — прозрачный с внутренней обводкой `--health-unknown` (правило кита:
  healthy — нейтральная `--ink-2`, не зелёная; красный — только dead).
- `TaskGlyph.vue` — props `kind: 'type' | 'priority'`, `value: string | number`,
  `size?: 'sm'|'md'`. Для `type`: `epic` → иконка `epic` цветом `--chart-3`, `bug` → `bug`
  цветом `--danger-500`, всё остальное (`task`, `feature`, `chore`, `decision`, `question`) →
  `task` цветом `--info-500`; `title` — подпись типа (`typeTitle` из `format.ts`; для эпика
  `эпик · ТЗ`). Для `priority` 0…4: иконки `p0…p4`, цвета `--danger-500`, `--warning-600`,
  `--ink-3`, `--info-500`, `--ink-4`; `title` — `P{n} · {блокер|критичный|обычный|низкий|потом}`.
  Экспортировать из компонента (или из `lib/format.ts`) `PRIORITY_TITLES` с этими словами.
- `CountGlyph.vue` — props `icon: 'lock'|'key'|'branch'`, `count: number`, `title: string`,
  `tone: 'warning'|'accent'|'neutral'` → цвета `--warning-700`, `--accent-700`, `--ink-3`;
  число `tabular-nums` (класс `.tnum` из utilities кита). Ничего не рендерит при `count <= 0`.

`ListikIcon.vue` остаётся обёрткой над `icons.ts`; новые примитивы используют её.

### 6. Dev-витрина `web/sheet.html` + `web/src/sheet/`

- `web/sheet.html` рядом с `index.html`, тот же `<html lang="ru" data-theme="dark">`, монтирует
  `/src/sheet/main.ts`, который импортирует те же css, что `src/main.ts` (вынести общий список
  импортов css в `src/assets/index.ts` или просто повторить — но гаммы должны быть те же четыре), и
  монтирует `src/sheet/Sheet.vue`.
- В `vite.config.ts` **ничего не менять**: `vite build` собирает только `index.html`
  (rollup input по умолчанию), а `vite dev` отдаёт `/sheet.html`. Проверить, что после
  `npm run build` в `dist/` нет `sheet.html`.
- `Sheet.vue` — одна длинная страница из секций с `UiPageHeader` (ровно один) и подзаголовками
  (`h2` классом `listik-section__title`), по порядку:
  1. «Легенда» — те же ряды, что верх `CardStates`: приоритеты P0…P4 (`TaskGlyph`), типы
     (`TaskGlyph`), связи (`CountGlyph` ×3), здоровье (`HealthDot` ×4 с подписями из
     `HEALTH_TITLES` и легендой порогов), проекты (`ProjectMark` для 5–6 фиктивных
     `ProjectRow` с разными `color`: `chart-2`, пустой, произвольный CSS-цвет), харнессы
     (`HarnessIcon` ×6).
  2. «Кит: статусы и метки» — `UiStatusPill` всех тонов (`healthy`, `at-risk`, `dead`,
     `unknown`, `info`, `success`, `danger`, `warning`, `neutral`), `UiBadge` всех тонов,
     `UiChip` статичный и с `v-model:selected` (кликабельный, с точкой-иконкой в тексте).
  3. «Кит: навигация» — `UiTabs` с четырьмя вкладками (`Доска (12)`, `Список`, `Таймлайн`,
     `Метрики`) и панелями-заглушками; `UiSegmented` группировки (по этапу/статусу/проекту/держателю).
  4. «Кит: процесс» — `UiSteps` из пяти шагов (`s1 · ТЗ и чек-лист`, `s2 · Второе мнение`,
     `s3 · Реализация`, `s4 · Проверка и коммит`, `done`) с `description` вида `claude · 34 мин · sticky →`
     и `currentIndex=2`; `UiProgress size="sm" :value="40"`.
  5. «Кит: лента» — `UiTimeline dense` с пятью элементами разных `tone`, как в TaskPanel.
  6. «Кит: оверлеи» — кнопки, открывающие `UiDrawer size="lg"` (пустой, с заголовком) и
     `UiModal size="lg"` (пустой), чтобы увидеть ширину 720px дровера из `app.css` и модалку.
  7. «Кит: поля» — `UiField` + `UiSelect` (с `ProjectMark` в опции невозможно — отметить в карте,
     что метка проекта рендерится рядом с селектом, не внутри), `UiInput`, `UiTextarea autosize`.
  8. Переключатели темы и гаммы вверху (те же `useTheme`/`useColorScheme`, что в `AppHeader`).
  Данные — фиктивные, в файле `src/sheet/fixtures.ts`; в API витрина не ходит.

### 7. Smoke с параметром ширины: `web/scripts/smoke.mjs`

- Второй позиционный аргумент — ширина окна (по умолчанию 1440): `node scripts/smoke.mjs <url> [width]`,
  `--window-size=<width>,900`. Сообщение об использовании — в шапке файла.
- В `report.layout` добавить `boardScrollable` (у `.listik-board` `scrollWidth > clientWidth`)
  и `columnsVisible` (сколько `.listik-column` целиком внутри `innerWidth` по
  `getBoundingClientRect().right <= innerWidth`). Остальные проверки не трогать: они начнут
  меняться в следующих порциях, и там же будут обновлены.
- `package.json`: скрипт `smoke` без изменений (аргументы пробрасываются через `--`).

### 8. `web/README.md`

Раздел «Структура»: добавить `components/marks/` и `sheet.html`/`src/sheet/` с одной строкой
назначения и командой открытия (`npm run dev` → `http://localhost:5173/sheet.html`); строку про
smoke дополнить параметром ширины. Больше README не трогать (итоговая правка — порция e).

## Границы правки

- Правятся: `docs/facet-prototype-map.md` (новый), `web/src/main.ts`, `web/src/lib/{health,harness,projects,stages}.ts`
  (новые), `web/src/lib/icons.ts`, `web/src/components/marks/*` (новые), `web/src/assets/app.css`
  (только новый раздел примитивов и переменные харнессов/pmark-ink), `web/sheet.html`,
  `web/src/sheet/*` (новые), `web/scripts/smoke.mjs`, `web/README.md` (две строки).
- Не трогать: `App.vue`, все `views/*`, `store/listik.ts`, `api/*`, `TaskDrawer.vue`,
  `TaskCard.vue`, `BoardColumn.vue`, `BoardView.vue`, `NeedsYouStrip.vue`, `TaskFilters.vue`,
  `AppHeader.vue`, `vite.config.ts`, `tsconfig.json`, `package.json` (кроме случая, если
  `npm install` сам обновит lock — тогда только `package-lock.json`), `scripts/mock-api.mjs`,
  `scripts/verify-*.mjs`. Экраны в этой порции не меняются — только фундамент.
- Ничего вне `web/` и `docs/facet-prototype-map.md`; бэкенд не трогать.
- Не копировать разметку прототипа (`.dc.html`) в компоненты; SVG-пути иконок брать можно.
- Не дублировать CSS кита (свои `.btn`, `.badge`, `.pill` и т.п. запрещены); `#hex` и `px`-литералы
  только внутри объявлений переменных в `app.css`, а не в разметке/scoped-стилях.
- Не глушить `strictTemplates`, `noUnusedLocals`; не править `scripts/patch-facet.mjs`.
- Никаких токенов/содержимого `config.toml` в файлах и отчёте.

## Как проверить

```sh
cd /Users/dmitry.fomin/Projects/Listik-ui/web && npm install
npm run typecheck && npm run build && ls dist | grep -c sheet   # 0
node scripts/mock-api.mjs 8788 &
VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token npm run dev &
open http://localhost:5173/sheet.html      # витрина: легенда, кит, оверлеи; переключить тему и все 4 гаммы
npm run smoke -- http://localhost:5173/ 1024   # report.layout.viewport.width === 1024, ошибок в консоли нет
npm run smoke -- http://localhost:5173/ 1440
git -C .. diff --stat HEAD                  # только файлы из «Границ правки»
```
