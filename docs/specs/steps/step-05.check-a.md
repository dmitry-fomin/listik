# Приёмка порции 05.a. Кит под прототип: карта, примитивы, гаммы, витрина, smoke

Все проверки — в дереве `/Users/dmitry.fomin/Projects/Listik-ui`, `cd web`, после `npm install`.
Живая страница — `npm run dev` с `scripts/mock-api.mjs` (или живой сервер на временной базе).

## Сборка и типы

1. `npm run typecheck` зелёный; `npm run build` зелёный, и в `web/dist/` **нет** `sheet.html`
   (`ls dist | grep sheet` пуст).
2. `git diff --stat HEAD` в корне трека содержит только: `docs/facet-prototype-map.md`,
   `web/src/main.ts`, `web/src/lib/health.ts`, `harness.ts`, `projects.ts`, `stages.ts`, `icons.ts`,
   `web/src/components/marks/*`, `web/src/assets/app.css`, `web/sheet.html`, `web/src/sheet/*`,
   `web/scripts/smoke.mjs`, `web/README.md` (и `package-lock.json`, если его обновил `npm install`).
   Любой файл из `views/`, `store/`, `api/`, `App.vue`, `AppHeader.vue`, `TaskDrawer.vue`,
   `board/*`, `vite.config.ts`, `listik/` — красный пункт.

## Карта покрытия

3. `docs/facet-prototype-map.md` существует; в таблице есть строки для всех перечисленных в ТЗ
   классов прототипа (`.hdr`, `.pill`, `.badge`, `.seg` ×2, `.chip`, `.select`, `.input`, `.btn`,
   `.inbox-card`, `.rail`, `.col`, `.tc`, `.done-rail`, `.drawer`, `.steps`, `.cold`, `.dl`, `.tl`,
   `.dep`, `.modal`, `.field`, `.textarea`, `.progress`, `.est`, `.route`, `.pmark`, `.hico`,
   `.hdot`, `.pico`, `.cnt`); у каждой строки «своя разметка» есть причина, почему кит не подходит;
   `.seg` видов → `UiTabs`, `.est` → «вне шага». Внизу раздел о заведённых переменных
   (`--listik-harness-*`, `--listik-pmark-ink`).

## Гаммы

4. `web/src/main.ts` импортирует `themes/amber.css`, `malachite.css`, `garnet.css`, `sapphire.css`
   (все четыре) после `tokens.css` и до `app.css`. Проверка гамм — по акцентным элементам
   (кнопка «Обновить» в шапке — `secondary`, акцента не несёт, по ней не судить):
   (а) на доске и в `sheet.html` при четырёх значениях сегмента гаммы
   `getComputedStyle(document.documentElement).getPropertyValue('--accent-500')` даёт четыре
   разных значения, а `document.documentElement.dataset.colorScheme` — `amber|malachite|garnet|sapphire`;
   (б) в витрине `UiBadge tone="accent"` (секция «Кит: статусы и метки») меняет `background-color`/
   `color` при переключении гаммы — видно на глаз и по `getComputedStyle`.
   **Красный до правки**: сегмент переключается, `dataset.colorScheme` меняется, но `--accent-500`
   и цвет акцентного бейджа остаются amber (загружен только amber).

## Справочники (`web/src/lib/*`)

5. `taskHealth` (порядок проверок из ТЗ: закрыта → dead → unknown → at-risk → healthy):
   - `{status:'open', holder:null, stale:false, abandoned:false}` → `unknown`;
   - `{status:'in_progress', holder:null, abandoned:true}` (брошена без держателя — основной
     случай `abandoned` на сервере) → `dead`, подпись `брошена · без держателя`;
   - `{status:'in_progress', holder:'agent:dsh', stale:true, idle_hours:40}` → `dead`,
     подпись `брошена 40 ч`;
   - `{status:'in_progress', holder:'agent:dsh', stage_warn:false, idle_hours:0.1}` → `healthy`;
     `idle_hours:0.5` → `at-risk`, подпись `молчит …`; `stage_warn:true` при `idle_hours:0.1` →
     `at-risk`, подпись `на этапе дольше порога`;
   - `{status:'done', holder:'agent:claude', idle_hours:300, stale:false}` → `healthy`, подпись
     `закрыта`; то же для `status:'cancelled'` (закрытые по heartbeat не оцениваются).
   Константа `AT_RISK_IDLE_HOURS` равна `0.25` и объявлена один раз. Проверяется в консоли
   dev-сервера или временным вызовом из витрины; постоянных тестов в `web/` нет. Реализация, где
   проверка `!holder` стоит раньше `abandoned` (abandoned без держателя даёт `unknown`) или где
   закрытая задача с давним `holder_at` даёт `at-risk`, — красный пункт.
6. `harnessOf`: `agent:claude` → `claude`, `dsh/deepseek-flash` → `dsh`, `agent:codex` → `codex`,
   `agent:grok` → `grok`, `agent:gemini` → `gemini`, `me` → `human`, `Иван` → `human`, `null` → `null`.
7. `projectMark`: `{slug:'zoloto585-symfony', title:'Symfony', color:'chart-2'}` → `mark 'Sy'`,
   `color 'var(--chart-2)'`; `{slug:'x', title:null, color:null}` → `mark` из slug, `color` вида
   `var(--chart-N)` с N∈1…6, и **тот же N** при повторном вызове (детерминированность);
   `color:'#abcdef'` → `'#abcdef'` как есть; slug `Zoloto585/my-repo` без title → монограмма от
   `my-repo`.
8. `stages.ts`: `stageCode('s3-impl') === 's3'`, `stageTitle('s2-review') === 'Второе мнение'`,
   `transitionOut('s2-review') === 'handoff'`, `transitionOut('s3-impl') === 'sticky'`,
   `transitionOut('s4-judge') === 'handoff'`, `transitionOut(null) === null`.

## Примитивы (`sheet.html`)

9. `http://localhost:5173/sheet.html` открывается без ошибок в консоли; вверху — ровно один
   `UiPageHeader`, переключатели темы и гаммы; при `data-theme="light"` страница светлая.
10. Секция «Легенда» показывает: 5 приоритетов с иконками и `title` `P0 · блокер` … `P4 · потом`,
    3 типа (эпик — `--chart-3`, задача — `--info-500`, баг — `--danger-500`), 3 счётчика связей
    (замок warning, ключ accent, ветка нейтральный), 4 точки здоровья (healthy — цвет
    `--health-healthy`, т.е. нейтральный, **не зелёный**; dead — `--health-dead`; unknown —
    прозрачная с обводкой), 5–6 меток проектов с монограммами (в т.ч. с fallback-цветом),
    6 иконок харнессов (claude/dsh/codex/grok/gemini + человек). Проверяется визуально и через
    `getComputedStyle`: у `HealthDot` для `healthy` `background-color` равен вычисленному
    `--health-healthy`.
11. `CountGlyph` с `count: 0` ничего не рендерит (в DOM нет узла).
12. В секциях кита видны: `UiStatusPill` всех 9 тонов, `UiBadge` всех тонов, `UiChip` статичный
    (`<span>`) и кликабельный (`<button>`, переключается по клику), `UiTabs` с 4 вкладками и
    сменой панели по клику и стрелкам, `UiSegmented`, `UiSteps` из 5 шагов с `currentIndex=2`
    (первые два `done`), `UiProgress`, `UiTimeline dense` с 5 элементами, кнопки открывают
    `UiDrawer` (ширина `.ui-drawer` 720px при viewport ≥ 1280) и `UiModal size="lg"`, поля
    `UiSelect`/`UiInput`/`UiTextarea` внутри `UiField`.
13. Ни один компонент витрины не содержит своих `.btn/.badge/.pill/.seg`-классов; в
    `app.css` новые правила ссылаются только на токены и переменные `--listik-*`; `#hex` встречается
    только в объявлениях `--listik-harness-*` / `--listik-pmark-ink` (проверяется `grep -n '#[0-9a-fA-F]\{3,6\}' web/src/assets/app.css web/src/components/marks web/src/sheet`).

## Smoke

14. `npm run smoke -- http://localhost:5173/ 1024` печатает `report.layout.viewport.width === 1024`,
    поля `boardScrollable` и `columnsVisible` присутствуют, `errors` и `console` — пустые массивы;
    то же для `1440` (ширина 1440). Без второго аргумента — 1440. **Красный до правки**: второй
    аргумент игнорируется, полей нет.
15. Существующие проверки smoke (`views`, `drawer`, `kpi`, `columns`, `cards`, `needsYou`) не
    удалены из скрипта (они обновляются в следующих порциях).

## Не сломано

16. Доска `http://localhost:5173/` выглядит и работает как до порции (шапка, виды, дровер,
    фильтры): визуально и `npm run smoke` без ошибок консоли. Существующие компоненты не
    менялись (пункт 2).
17. `web/README.md`: добавлены строки про `components/marks/`, `sheet.html` и параметр ширины
    smoke; других правок README нет.
18. В диффе нет токенов и содержимого `config.toml`.
