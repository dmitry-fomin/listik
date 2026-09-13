# Порция 05.e. Окно «Новая задача» по NewTask: тип/проект/приоритет, ТЗ, критерии, spec_path, матрица маршрута

## Контекст

Дерево `/Users/dmitry.fomin/Projects/Listik-ui`, правится только `web/*` (плюс
`docs/facet-prototype-map.md`). Закоммичены порции a–d: примитивы `components/marks/*`
(`ProjectMark`, `HarnessIcon`, `TaskGlyph`), `lib/{harness,projects,stages}.ts`, тулбар, доска с
кнопкой «Новая задача» (`BoardView` → `emit('create')` → `App.vue` `createOpen = true`), панель.

Референс — `docs/prototype/NewTask-html/NewTask.dc.html`, блок `.modal` (`max-width: 760px`):
шапка «Новая задача» + подсказка «что делаем, в каком проекте и как: кто исполняет и по какому
процессу»; тело: строка из трёх полей `Тип` (сегмент «эпик · ТЗ / задача / баг» с иконками),
`Проект` (селект с меткой-монограммой), `Приоритет` (сегмент из пяти иконок P0…P4, P0 приглушён
с подсказкой «P0 блокер · только баги»); `Заголовок`; `Описание · ТЗ` (textarea 112px, подсказка
«markdown · эпик режется на шаги и порции на этапе s1, здесь только суть»); `Критерии приёмки`
(textarea 56px); секция «Как делать» с оценкой DeepSeek (**не делать** — решение автора) и
`Маршрут · кто исполняет и по какому процессу` — сетка `.route` 132px + 4 колонки
(`feature-pipeline`, `cheap-pipeline`, `local-pipeline`, `прямая задача`), строки `claude`, `codex`,
`dsh (DeepSeek)`, `grok`; ячейки — цепочки иконок «ТЗ → критика → кто пишет код → приёмка»
(`.chain`), выбранная — `.route__cell--on` с меткой «совет» (метку «совет» не делать — оценщика
нет), недоступные — `.route__cell--off` пунктиром с «—» или «эпик так не делают»; легенда под
сеткой (ТЗ и чек-лист · критика · кто пишет код · приёмка и коммит судьёй); подсказка «эпик
всегда начинается с ТЗ, поэтому прямые маршруты закрыты; для задачи и бага открыты все четыре
строки. Выбранное уходит в карточку полями harness и skill»; подвал «Отмена» / «Создать эпик».

Решение автора: без блока оценки; поля Тип/Проект/Приоритет/Заголовок/Описание·ТЗ/Критерии
приёмки/spec_path **плюс** матрица харнесс × процесс, выбор сохраняется в `labels`
(`harness:<x>`, `process:<y>`); «базовая декомпозиция» — только подсказка про s1, без поля.

Сейчас окно — `UiModal size="md"` в `App.vue` с пятью `UiInput` (заголовок, проект, тип,
приоритет, описание) и `store.createTask({title, project, type, priority, description, actor:'me'})`
→ `POST /api/tasks` (принимает `title, project, description, acceptance, type, priority, labels[],
spec_path, actor, …`). Таблица процессов из `docs/listik-vision.md` («Как проходит работа»):
feature-pipeline — ТЗ/критика у claude или codex, код dsh (запасной codex), приёмка в той же
сессии; cheap-pipeline — ТЗ claude, критика gemini, код dsh, приёмка sonnet; local-pipeline — всё
на моделях claude в субагентах; прямая задача — dsh или grok без процесса; эпик — только конвейер.

Прочитать перед началом: `App.vue` (модалка и `createTask`), `store/listik.ts` (`createTask`,
`meta`), `components/facets.ts` (`projectOptions`), `marks/*`, `lib/harness.ts`; в ките —
`UiModal` (слоты `header`/default/`footer`, `size: 'lg'`), `UiField` (`label, required, hint,
error`), `UiSegmented` (`options: {value,label}[]` — без иконок и без `disabled` на опции),
`UiSelect`, `UiInput`, `UiTextarea` (`autosize`, `rows`), `UiButton`.

## Что сделать

### 1. `lib/routes.ts` — таблица маршрутов

```
export type ProcessKey = 'feature-pipeline' | 'cheap-pipeline' | 'local-pipeline' | 'direct'
export const PROCESSES: { key: ProcessKey; title: string }[]      // подписи как в прототипе, direct → «прямая задача»
export const ROUTE_HARNESSES: HarnessKey[] = ['claude', 'codex', 'dsh', 'grok']
export function routeAllowed(harness: HarnessKey, process: ProcessKey, type: string): boolean
export function routeChain(harness: HarnessKey, process: ProcessKey): { spec: boolean; review: boolean; coders: HarnessKey[]; judge: boolean }
export const DEFAULT_ROUTE: { harness: 'claude'; process: 'feature-pipeline' }
export function routeLabels(harness, process): string[]           // ['harness:claude', 'process:feature-pipeline']
```

`routeAllowed`: claude и codex — три конвейера (`feature`, `cheap`, `local`), `direct` — нет; dsh и
grok — только `direct`; при `type === 'epic'` `direct` закрыт для всех. `routeChain`: для
конвейеров `spec/review/judge = true`, `coders` — `feature`: `['dsh','codex']`, `cheap`: `['dsh']`,
`local`: `[harness]`; для `direct` — `spec/review/judge = false`, `coders: [harness]`.

### 2. `components/NewTaskModal.vue`

Вынести окно из `App.vue` в компонент: props `open` (`v-model`), `projects: ProjectRow[]`,
`pending: boolean`; emit `update:modelValue`, `submit: [body]`. `UiModal size="lg"`, слот
`header`: заголовок «Новая задача» + `.listik-section__hint` «что делаем, в каком проекте и
как: кто исполняет и по какому процессу». Тело — `listik-stack`:

1. Строка из трёх `UiField` (grid `minmax(0,1fr) 200px 208px`, на ширине окна < 640 — в столбец):
   - «Тип» — `UiSegmented` с опциями `epic` «эпик · ТЗ», `task` «задача», `bug` «баг» (иконки в
     сегмент кита не положить — рядом слева от сегмента показывать `TaskGlyph type` выбранного
     типа; записать это в карту покрытия);
   - «Проект» — `UiSelect` (`projectOptions`, `placeholder="проект"`, обязателен) + `ProjectMark`
     выбранного проекта слева от селекта;
   - «Приоритет» — `UiSegmented` с опциями `1` «P1», `2` «P2», `3` «P3», `4` «P4`, а при типе
     `bug` — ещё `0` «P0» первой; под сегментом `.listik-section__hint`: `TaskGlyph priority` +
     слово приоритета (`PRIORITY_TITLES`), для не-бага — «P0 — только для багов». При смене типа
     с `bug` на другой при выбранном `0` — сбросить на `1`. Значение по умолчанию — `2`.
2. «Заголовок» — `UiInput` (обязателен; `error` «нужен заголовок» после попытки отправки).
3. «Описание · ТЗ» — `UiTextarea autosize :rows="5"`, `hint` «markdown · эпик режется на шаги и
   порции на этапе s1, здесь только суть».
4. «Критерии приёмки» — `UiTextarea autosize :rows="3"`.
5. «Путь к ТЗ (spec_path)» — `UiInput` с плейсхолдером `docs/specs/….md`, `hint` «относительно
   корня репозитория проекта; можно заполнить позже».
6. Секция «Маршрут · кто исполняет и по какому процессу» (`h4` как в панели): сетка
   `.listik-route` (grid `132px repeat(4, minmax(0,1fr))`, `gap --space-2`): верхняя строка —
   подписи процессов (`--text-xs`, `--ink-3`, по центру); каждая строка — `HarnessIcon md` +
   название харнесса (для dsh — доп. подпись «DeepSeek» `--ink-3`), затем четыре ячейки
   `button.listik-route__cell` (`role="radio"`, `aria-checked`, группа `role="radiogroup"` с
   `aria-label`): доступная — рамка `--hairline`, фон `--surface`, содержимое — цепочка
   `.listik-route__chain`: `ListikIcon list xs` (ТЗ) – `ListikIcon search xs` (критика) –
   `HarnessIcon` кодеров – `ListikIcon check xs` (приёмка), разделители-чёрточки 6px
   `--hairline-strong`; для `direct` — только `HarnessIcon` строки; выбранная — `is-on`
   (рамка/фон `--accent-500`/`--accent-50`, `--focus-ring`); недоступная — `is-off`, пунктир,
   `disabled`, текст «—», а для эпика в колонке «прямая задача» — `HarnessIcon` + «эпик так не
   делают». Клавиатура: стрелки внутри группы, Enter/Space выбирают.
   Легенда под сеткой (`--text-xs`, `--ink-3`): «ТЗ и чек-лист · критика · кто пишет код
   (иконки dsh/codex/claude) · приёмка и коммит судьёй». Подсказка `.listik-section__hint`: «эпик
   всегда начинается с ТЗ, поэтому прямые маршруты закрыты; для задачи и бага открыты все четыре
   строки. Выбор сохраняется метками `harness:<…>` и `process:<…>` — их читает человек,
   автоматической раздачи задач по ним нет; кто допущен до этапа, решает сервер по routing
   проекта». Фактическое состояние сервера (шаг 04 закоммичен): полей `harness`/`skill` у
   карточки **нет и не появится в этом шаге**, маршрутизация живёт на уровне проект + этап
   (`routing` проекта, `ready --harness`, отказ `claim` для недопущенного харнесса), метки
   `harness:`/`process:` никто из бэкенда не читает — они только для человека и поиска.
   Значение по умолчанию — `DEFAULT_ROUTE`; при смене типа на `epic` с выбранным `direct` — сброс
   на `DEFAULT_ROUTE`.
   Стили — свои, на токенах (`app.css`, раздел «Матрица маршрута»); блока оценки, шкал `S/M/L/XL`,
   «Применить совет», метки «совет» — нет.
7. Слот `footer`: `UiButton ghost md` «Отмена», `UiButton primary md` «Создать эпик» / «Создать
   задачу» / «Создать баг» по типу, иконка `plus`, `loading = pending`, `disabled` при пустом
   заголовке или невыбранном проекте.

Отправка: `emit('submit', { title, project, type, priority, description, acceptance, spec_path
(только если непусто), labels: routeLabels(harness, process), actor: 'me' })`. После успешного
создания (родитель закрыл окно) — форма сбрасывается к значениям по умолчанию; при ошибке окно
остаётся открытым, значения сохраняются.

### 3. `App.vue`

Заменить старую модалку на `<NewTaskModal v-model="createOpen" :projects="store.meta.value?.projects ?? []"
:pending="store.pending.value === 'create'" @submit="createTask" />`; `createTask(body)` →
`store.createTask(body)`; успех — тост «Задача создана» + закрыть; ошибка — `toast.danger(lastError)`.
Локальные `newTitle/newProject/…` удалить.

### 4. Документы и smoke

- `docs/facet-prototype-map.md`: строки `.route/.chain`, `.field`, `.seg` типа/приоритета,
  `.modal` обновить по факту (в т.ч. «иконки в `UiSegmented` не кладутся — глиф рядом»).
- `web/README.md` — состояние после порций a–d уже актуально: KPI-ряда, полосы «можно брать» и
  старой модалки там нет, строка «перетаскивания карточек между колонками нет» и описание чипа
  «можно брать» — верные утверждения о текущем UI, их **не трогать**. Задача порции:
  (а) раздел «Структура»: добавить `NewTaskModal` в `components/` (и `MobileTaskList`, которого в
  перечне нет), `lib/routes.ts` — в `lib/`; (б) новый абзац «Создание задачи»: кнопка «Новая
  задача» на доске, какие поля уходят в `POST /api/tasks` (`title, project, type, priority,
  description, acceptance, spec_path, labels, actor`), что маршрут сохраняется метками
  `harness:*`/`process:*`, которые читает только человек (полей харнесса на карточке нет и после
  шага 04; таблица маршрутов зашита в UI и не связана с `routing` проекта на сервере — кто
  реально допущен до этапа, решает сервер: `ready --harness`, отказ в `claim`), что оценки
  DeepSeek нет; (в) сверить раздел про доску/панель с порциями b–d и поправить только то, что
  расходится с кодом.
- `scripts/smoke.mjs`: `report.newTask` — клик по кнопке «Новая задача» на доске, затем: `modal`
  (есть `.ui-modal`), `routeCells` (число `.listik-route__cell`, 16), `routeOff` (число `is-off`
  при типе по умолчанию `task`: 8 — dsh/grok × три конвейера и claude/codex × прямая, итого
  6 + 2 = 8), `typeOptions` (число опций сегмента типа, 3); закрыть окно `Escape`.

## Границы правки

- Правятся: новый `components/NewTaskModal.vue`, новый `lib/routes.ts`, `App.vue` (только замена
  модалки), `assets/app.css` (раздел матрицы и формы), `scripts/smoke.mjs`, `web/README.md`,
  `docs/facet-prototype-map.md`.
- Не трогать: `store/listik.ts` (`createTask` принимает `Record<string, unknown>` — достаточно),
  `api/*`, доску, панель, инбокс, тулбар, примитивы (`TaskGlyph`/`ProjectMark`/`HarnessIcon`
  использовать как есть), `sheet/*`, `vite.config.ts`, `package.json`.
- Не добавлять оценщик, поля `harness`/`skill`/`process` в тело запроса (сервер их не хранит),
  поле декомпозиции, `UiMarkdownEditor` (достаточно `UiTextarea`), `UiFormModal`/`UiFieldForm`
  (матрица в их схему полей не ложится — обычная `UiModal`).
- Никаких токенов и содержимого `config.toml` в файлах и отчёте.

## Как проверить

```sh
cd /Users/dmitry.fomin/Projects/Listik-ui/web
npm run typecheck && npm run build
node scripts/mock-api.mjs 8788 &
VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token npm run dev &
npm run smoke -- http://localhost:5173/ 1440   # newTask.modal true, routeCells 16, routeOff 8, typeOptions 3
```

Живой сервер (временная база, `serve --port 8797`): создать через окно задачу типа «баг» с P0,
проектом, критериями, `spec_path` и маршрутом dsh × прямая; затем
`./bin/listik --local show <id> --json` (с тем же `LISTIK_DB`) показывает `issue_type: bug`,
`priority: 0`, `acceptance`, `spec_path`, `labels: ["harness:dsh", "process:direct"]`.
