# Карта покрытия прототипа китом

Источник классов — `docs/prototype/{Main,BoardLight,CardStates,TaskPanel,NewTask}-html/<Name>.dc.html`
(`<helmet><style>` + разметка с инлайн-стилями, референс, не код — см. `README.md` рядом).
Кит — `@zoloto585/facet@0.4.1` (`docs/facet-components.md`, README пакета). Для «своих» разметок
ниже — одна фраза, почему в ките нет аналога (по разделу «Что выбрать»/каталогу компонентов),
и какие токены используются.

| Блок прототипа (класс в `.dc.html`) | Экран | Чем делается | Почему не кит / замечание |
|---|---|---|---|
| `.hdr`/`.mark`/`.brand-name` | все | `UiAppHeader` + `UiBrandMark` | уже реализовано в `AppHeader.vue`; своя надпись — слот `#brand` сжимается, многоточие ставит приложение |
| `.pill` | все | `UiStatusPill` | тона `healthy/at-risk/dead/unknown` встроены в кит |
| `.badge` | все | `UiBadge` | — |
| `.seg` для видов (Доска/Список/Таймлайн/Метрики) | Main | `UiTabs` (решение автора) | вкладки переключают контент — семантика `tablist`, а не radiogroup со счётчиками |
| `.seg` для группировки/фильтра ленты | Main | `UiSegmented` | это не навигация по видам, а radiogroup-переключатель одного параметра (группировка/фильтр), семантика ближе к сегменту |
| `.chip` | Main, NewTask | `UiChip` с привязкой `selected` | статичная метка (лейбл) и кликабельный фильтр-чип — оба режима нативные в ките |
| `.select` | NewTask, TaskPanel | `UiSelect` | — |
| `.input` поиска | Main | `UiInput` + `UiCommandPalette` | Cmd/K палитра команд поверх обычного поля поиска |
| `.btn` | все | `UiButton` | — |
| `.inbox-card` | Main (лента «нужен ты») | своя разметка (`listik-needs-card`) | нет готовой карточки с полосой-акцентом слева + вопросом строкой; токены: `--hairline`, `--accent-500`/`--danger-500`/`--warning-600` (полоса), `--surface`, `--radius-lg` |
| `.rail`/`.rail__tr` | Main | своя (`listik-pipeline-rail`) | рельса переходов конвейера с подписями sticky/handoff по центру линии — нет аналога в каталоге; токены: `--hairline-strong`, `--accent-700`, `--font-mono` |
| `.col`/`.col__head`/`.health-row` | Main | своя (`listik-column`) | канбан-колонка — в ките нет канбана вовсе (см. «Чего в ките нет»); токены: `--surface`, `--hairline`, `--radius-lg` |
| `.tc` | Main | своя (`listik-task-card`, компактная карточка) | `UiEntityCard` не подходит: нет слотов под верхнюю строку (тип+проект+приоритет) и подвал (харнесс+heartbeat), нет состояний здоровья/полосы-акцента |
| `.done-rail` | Main | своя (`listik-column.is-done-rail`) | свёрнутая колонка «готово» с вертикальным заголовком — нет в ките |
| `.drawer` | TaskPanel | `UiDrawer size="lg"` + правило ширины 720px в `app.css` | у кита только `sm/md/lg` (560px у lg); правило ширины уже есть |
| `.steps`/`.step` | TaskPanel | `UiSteps` | подписи переходов (sticky/handoff) — в `description` шага: слота на коннекторе у кита нет |
| `.cold` | NewTask (холодный старт) | своя (`listik-cold`, чек-лист «Холодный старт») | построчный чек-лист «поле заполнено / нет» с моно-значением — нет аналога; токены: `--hairline`, `--success-50/700`, `--warning-50/700` |
| `.dl` | TaskPanel | своя `listik-dl` | уже есть в `app.css` |
| `.tl` | TaskPanel | `UiTimeline dense` | — |
| `.dep` | TaskPanel | своя `listik-dep` | уже есть в `app.css` |
| `.modal`/`.modal__*` | NewTask | `UiModal size="lg"` | — (`NewTaskModal.vue`, порция e) |
| `.field`/`.field__label` | NewTask | `UiField` | — |
| `.seg` типа/приоритета задачи | NewTask | `UiSegmented` + `TaskGlyph` рядом | `UiSegmented` не умеет иконки в опциях и `disabled` на отдельной опции — глиф типа/приоритета кладётся отдельным элементом слева/под сегментом (`TaskGlyph`), а недоступный `P0` для не-бага просто не входит в список опций сегмента (не рисуется задизейбленным) |
| `.textarea` | NewTask | `UiTextarea` | — |
| `.progress` | Main (карточка-шаг) | `UiProgress size="sm"` | — |
| `.est`/`.scale` | NewTask | нет — решение автора | оценщик DeepSeek (`.est`/`.scale`, «Применить совет», метка «совет») не делается вовсе |
| `.route`/`.route__cell`/`.chain` | NewTask | своя `.listik-route` (`NewTaskModal.vue`, порция e) | таблица «харнесс × процесс» (`radiogroup`/`radio` с цепочкой иконок ТЗ→критика→код→приёмка) — нет аналога в ките; токены: `--hairline`, `--hairline-strong`, `--accent-500`/`--accent-50`, `--focus-ring`, `--control-h-md` |
| `.pmark` | Main, CardStates | своя `ProjectMark.vue` (`.listik-pmark`) | монограмма проекта на цветном фоне — нет аналога; токены: `--radius-xs`, `--text-xs`, `--weight-semibold`, `--chart-1…6`, свой `--listik-pmark-ink` |
| `.hico` | Main, CardStates | своя `HarnessIcon.vue` (`.listik-harness-icon`) | глиф харнесса фирменным цветом — нет ни иконок, ни цветов харнессов в ките; свои `--listik-harness-claude/-dsh/-codex/-grok/-gemini` |
| `.hdot` | CardStates, Main | своя `HealthDot.vue` (`.listik-health-dot`) | точка здоровья — вычисляемое состояние вне статусов кита; токены `--health-healthy/at-risk/dead/unknown` (уже есть в ките) |
| `.pico` | CardStates, Main | своя `TaskGlyph.vue` (`.listik-glyph`) | иконка типа/приоритета цветом — обёртка над `icons.ts`/`ListikIcon`, у кита нет иконного набора |
| `.cnt` | CardStates, Main | своя `CountGlyph.vue` (`.listik-count-glyph`) | счётчик связи (иконка+число) тоном `warning/accent/neutral` — нет аналога, число через `.tnum` кита |

## Токены, которых нет в ките и которые заведены в `app.css`

- `--listik-pmark-ink` — цвет монограммы `ProjectMark` поверх цветного фона: тёмный
  (`--neutral-900`) в тёмной теме, светлый (`--neutral-0`) в светлой — как в прототипе
  (`Main.dc.html` vs `BoardLight.dc.html`, переменная `--pmark-ink`).
- `--listik-harness-claude` (`#D97757`), `--listik-harness-dsh` (`#4D6BFE`),
  `--listik-harness-codex` (`#4A8CFF`), `--listik-harness-grok` (`currentColor`),
  `--listik-harness-gemini` (`#8E7CF2`) — фирменные цвета харнессов из прототипа
  (`.hico`), в ките цветов держателей нет вовсе.
