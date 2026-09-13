# Приёмка. Шаг 06, порция a — заход r1

Вердикт: **зелёный** (один пункт перенесён на приёмку шага, см. ниже).

Чек-лист: `docs/specs/steps/step-06.check-a.md`. ТЗ: `docs/specs/steps/step-06.a.md`.
Пакет диффа: `.git/feature-pipeline/step-06.diff-a.r1.txt`.

Окружение прогона: мок без `--fill` на 8788, мок HEAD на 8789 (эталон), мок `--fill=50` на 8790,
vite текущего кода на 5177, vite чистого HEAD (`git worktree` от 282078c) на 5178.

## Сборка и границы

1. **зелёный.** `npm run typecheck` (vue-tsc) и `npm run build` — без ошибок, оба запущены с
   `env -u VITE_API_BASE -u VITE_LISTIK_TOKEN`.
2. **зелёный.** `git status`: изменены ровно `web/README.md`, `web/scripts/mock-api.mjs`,
   `web/scripts/smoke.mjs`, `web/src/assets/app.css`, `web/src/components/MobileTaskList.vue`,
   добавлен `web/src/components/MobileTaskRow.vue`. `App.vue`, `store/`, `api/`, `lib/`,
   `TaskDrawer.vue`, `AppHeader.vue`, `listik/*.py` не тронуты. (Помимо кода в дереве изменён
   `docs/specs/listik-product.journal.md` — бумага конвейера, не порция; в коммит не идёт.)
3. **зелёный.** `grep -rn "matchMedia\|PhoneQueue\|\.phone" web/src` → единственное совпадение
   `web/src/views/BoardView.vue:50` (`matchMedia('(max-width: 1279px)')`), оно есть и на HEAD
   (`git show HEAD:web/src/views/BoardView.vue` — та же строка 50). Режим телефона не начат.

## Мок без `--fill`

4. **зелёный.** `limit=2&offset=1&order=updated` → `total 5, limit 2, offset 1`,
   `["listik-sse-e5f6","listik-epic-k9l0"]` — вторая и третья по убыванию `updated_at`.
5. **зелёный.** `needs_owner=1` → `total 1`, `["listik-api-c3d4"]`.
6. **зелёный.** `project=nope` → `total 0`, `tasks: []`; `project=listik` → `total 5`.
7. **зелёный.** `status=review` → `["listik-sse-e5f6"]`.
8. **зелёный.** `order=priority` → `epic-k9l0, api-c3d4, web-a1b2, metrics-g7h8, sse-e5f6` —
   ровно ожидаемый порядок `priority ASC, updated_at DESC` со стабильным тай-брейком.
   `order=garbage` → 200 и тот же массив, что `order=updated`.
9. **зелёный.** `limit=abc&offset=-1` → `limit 200, offset 0`, все пять задач.

## Мок `--fill=50`

10. **зелёный.** `limit=20&offset=40` → `total 55`, `tasks.length 15`, последний
    `listik-fill-050`. `limit=20` → 20 задач, первые пять базовые, далее `fill-001, 002, …`.
11. **зелёный.** `project=fill` → `total 25`; `/api/meta` →
    `{"slug":"fill","title":"Заполнитель","kind":"native","n_tasks":25}`,
    `facets.projects: ["listik","fill"]`.
12. **зелёный.** `/api/tasks/listik-fill-007` → 200, `comments 2`, `events 2`,
    `deps_state.blocked_by []`.
13. **зелёный.** Без `--fill` ответы мока побайтно совпадают с HEAD-моком после нормализации
    ISO-меток: `/api/board?group_by=stage|status|project`, `/api/meta`, `/api/ready`,
    `/api/blocked`, `/api/stats`, `/api/tasks/listik-web-a1b2` — все IDENTICAL. Smoke на
    1024/1440 даёт те же `columns/cards/needsYou`, что HEAD (см. п. 18).

## Smoke

14. **частично зелёный, часть перенесена.**
    - Блок `report.phone` присутствует на всех ширинах (360, 700, 800, 1024, 1440) со всеми
      требуемыми полями; поля `containerWidth` в нём нет — зелёный.
    - `layout.viewport` на 360 = **376 × 771**, а не 360 × 740. Проверено, что эмуляция сама по
      себе точна и реализована по ТЗ: контрольный прогон того же
      `Emulation.setDeviceMetricsOverride({width:360,height:740,deviceScaleFactor:2,mobile:true})`
      на тривиальной странице (`data:text/html`) даёт `innerWidth 360, innerHeight 740`;
      приложение на 400 px даёт `innerWidth 400, innerHeight 740`. На 360 у приложения
      `document.documentElement.clientWidth = 360`, `visualViewport.width = 360`, `scale = 1`, но
      `innerWidth/scrollWidth = 376` — контент не влезает в 360 px, Chrome раздвигает
      `window.innerWidth` до ширины контента. На чистом HEAD в той же эмуляции — 380 (то есть
      порция a переполнение даже уменьшила, с 380 до 376). Причина — телефонная вёрстка, которую
      порция a вводить не должна (границы: «никакого режима телефона… это порция b»).
      → пункт про `layout.viewport.width === 360 / height === 740` **перенесён на приёмку шага**:
      он закрывается только вместе с телефонным режимом порции b.
14a. **зелёный.** `phone.drawerOpen === false` на 360/700/800/1024/1440, при этом
    `report.drawer.открылась === true` на всех ширинах.
15. **зелёный.** 360: `rowsVisible 5`, `firstRowStyled.display 'flex'`, `borderTopStyle 'solid'`,
    `scrollWidth 376 <= innerWidth 376`, `tabsVisible true`, `inboxVisible true`.
16. **зелёный.** 800: `firstRowStyled = { display: 'flex', borderTopStyle: 'solid',
    paddingTop: '12px' }`, `rowsVisible 5`.
17. **зелёный.** 700: `bodyOverflowX 'hidden'`, `compactPillVisible true`,
    `headerButtons ["Включить светлую тему","icon","icon"]` (без «Репозитории»/«Обновить»).
    800: `'visible'`, `false`, `["Включить светлую тему","Репозитории","Обновить"]`.
    360 — как 700.
18. **зелёный.** 1024 и 1440: `columns, cards, needsYou, toolbar, views, newTask, drawer, layout`
    посимвольно совпадают с эталоном HEAD (сравнение JSON-полей — все SAME).
    `console: []` в обоих. `errors` у нового кода — одна запись (404 favicon); у эталона HEAD их
    больше (404 + 403 от vite в worktree с симлинком node_modules), то есть не хуже эталона.
    `phone.tabsVisible true`, `phone.rowsVisible 0` на обеих ширинах.

## Строка списка

19. **зелёный.** `MobileTaskRow.vue` есть, `defineProps<{ task: Task }>`,
    `defineEmits<{ open: [id: string] }>`, `@click="emit('open', props.task.id)"`.
    `MobileTaskList.vue` рендерит `<MobileTaskRow … @open="openTask">`, собственной кнопки
    `listik-mobile-task` в нём нет, осиротевшие импорты (`UiStatusPill`, `humanAge`,
    `taskStageLabel`, `HEALTH_TITLES`, `taskHealth`, `healthTone`/`healthLabel`) удалены —
    `noUnusedLocals` в `web/tsconfig.json:9` включён, typecheck зелёный. Сортировка, `PAGE = 24`,
    заголовок «Очередь / Задачи», `UiEmptyState`, «Показать ещё» на месте
    (`MobileTaskList.vue:12–21, 33–62`).
20. **зелёный.** DOM на 800 px, тексты всех пяти строк совпадают с HEAD по содержанию; для
    `listik-web-a1b2` — «под угрозой | Собрать доску канбан для трекера | listik | 3. Реализация |
    держит dsh | · | heartbeat 33 мин» (33, а не 18 мин — мок работал ~15 мин; у HEAD-инстанса
    на том же моке те же 33 мин). Единственная разница с HEAD — переносы строк в `innerText`:
    на HEAD кнопка `inline-block`, теперь `flex`, что и есть починка планшета.
21. **зелёный.** Подпись держателя в `MobileTaskRow.vue:31` — `props.task.holder ? … :
    'держателя нет'`, то есть по ключу `holder`; `holder_title: '—'` больше не даёт «держит —».

## CSS

22. **зелёный.** `max-width: 640px` — нет ни одного вхождения; `max-width: 767px` — ровно два
    блока (`app.css:964`, `app.css:1177`); `860px/1023px/1279px` — на тех же местах, что на HEAD.
23. **зелёный.** Правила `.listik-mobile-task*` объявлены по одному разу, глобально,
    `app.css:101–158`, сразу после `.listik-mobile-queue`. Объявления с
    `radius-xl`/`space-4`/`color-mix` нет.
24. **зелёный.** Сравнение набора селекторов внутри блока 767 с прежним блоком 640: удалены
    только `.listik-mobile-task`(×2), `:hover/:focus-visible`, `__top`, `__meta`, `__title`,
    `__meta--muted`; остальные правила и их значения не тронуты (см. дифф).

## README

25. **зелёный.** `web/README.md`: строка про `--fill=N` в «Разработка без сервера» и абзац про
    эмуляцию телефона при ширине < 768 и блок `phone` в «Смоук-тест живой страницы».

## Срезанные углы — не найдено

- Хардкода под тест в моке нет: `listTasks` реализует фильтры/сортировку/пагинацию как
  `listik/store.py list_tasks`, значения не подогнаны под конкретные запросы чек-листа.
- Незнакомый `order` не роняет запрос, нечисловые `limit/offset` уводятся в умолчания.
- Ни одна проба smoke не переименована и не изменена; блок `phone` добавлен после `layout`,
  закрытие панели вынесено между ними, отчёты 1024/1440 не сдвинулись.
- Секретов в диффе нет (`.env`, ключи, токены не затрагивались).

## Перенесено на приёмку шага

- Чек-лист п. 14 в части `layout.viewport.width === 360` / `height === 740`: эмуляция
  реализована по ТЗ и точна на странице, которая влезает в 360 px; остаток (376 × 771) — ширина
  контента приложения на телефоне, которую вводит порция b.
