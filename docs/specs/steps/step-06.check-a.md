# Чек-лист приёмки. Шаг 06, порция a — инструменты проверки и починка планшета

ТЗ: `docs/specs/steps/step-06.a.md`. Все команды — из `web/`, мок на 8788, vite на 5177 с
`VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token`. Эталон «как было» для
пунктов про десктоп — smoke на чистом HEAD до правок (мок без `--fill`).

## Сборка и границы

1. `npm run typecheck` и `npm run build` (без `VITE_*` в окружении) проходят без ошибок.
2. `git status` показывает изменения только в `web/scripts/mock-api.mjs`, `web/scripts/smoke.mjs`,
   `web/src/components/MobileTaskRow.vue` (новый), `web/src/components/MobileTaskList.vue`,
   `web/src/assets/app.css`, `web/README.md`. Никаких правок в `App.vue`, `store/`, `api/`,
   `lib/`, `TaskDrawer.vue`, `AppHeader.vue`, `listik/*.py`.
3. В `web/src` нет новых `matchMedia`, `PhoneQueue`, `store.phone` — режим телефона не начат
   (проверяется `grep -rn "matchMedia\|PhoneQueue\|\.phone" web/src`).

## Мок: параметры `/api/tasks` (без `--fill`)

4. `GET /api/tasks?limit=2&offset=1&order=updated` → `total: 5`, `limit: 2`, `offset: 1`, две
   задачи — вторая и третья по убыванию `updated_at` среди пяти (до правки: `total 5, limit 25,
   offset 0`, все пять задач).
5. `GET /api/tasks?needs_owner=1` → `total: 1`, единственная задача `listik-api-c3d4`.
6. `GET /api/tasks?project=nope` → `total: 0`, `tasks: []`; `GET /api/tasks?project=listik` →
   `total: 5`.
7. `GET /api/tasks?status=review` → только `listik-sse-e5f6`.
8. `GET /api/tasks?order=priority` → порядок `priority ASC, updated_at DESC` (как на сервере):
   `listik-epic-k9l0` (P1, updated 0.3 ч), `listik-api-c3d4` (P1, 30 ч), затем P2 —
   `listik-web-a1b2`, `listik-metrics-g7h8` (одинаковый `updated_at`, порядок массива), затем
   `listik-sse-e5f6` (P3). `GET /api/tasks?order=garbage` → тот же порядок, что `order=updated`
   (не 500).
9. `GET /api/tasks?limit=abc&offset=-1` не падает: `limit 200`, `offset 0`, все пять задач.

## Мок: `--fill=50`

10. `GET /api/tasks?limit=20&offset=40` → `total: 55`, `tasks.length: 15`, последний id
    `listik-fill-050`; `GET /api/tasks?limit=20` → 20 задач, первые пять — базовые (их
    `updated_at` новее), далее `listik-fill-001`, `002`, …
11. `GET /api/tasks?project=fill` → `total: 25`; `GET /api/meta` содержит `slug: 'fill'` с
    `title: 'Заполнитель'` и `n_tasks: 25`; `facets.projects` содержит `'fill'`.
12. `GET /api/tasks/listik-fill-007` → 200, задача с `comments`, `events`, `deps_state`
    (`blocked_by: []`).
13. Без `--fill` (`node scripts/mock-api.mjs 8788`) `GET /api/meta` не содержит `fill`,
    `GET /api/board?group_by=stage` — те же колонки и то же число задач, что до правки (smoke 1440:
    `columns 4, cards 4, needsYou 3`).

## Smoke: эмуляция и блок `phone`

14. `npm run smoke -- http://localhost:5177/ 360` → `layout.viewport.width === 360`,
    `layout.viewport.height === 740` (до правки: 500×757). `report.phone` присутствует со всеми
    полями: `drawerOpen, innerWidth, scrollWidth, bodyOverflowX, compactPillVisible, rowsVisible,
    firstRowStyled, firstRowTop, rowsFullyVisible, tabsVisible, inboxVisible, chipsVisible,
    headerButtons`. Поля `containerWidth` в блоке нет (ширина контейнера перебита глобальным
    `!important` и порог не показывает).
14a. На всех ширинах (360, 700, 800, 1024, 1440) `phone.drawerOpen === false`: панель, открытая
    пробой `drawer`, закрыта до измерения блока `phone` (при `true` остальные поля блока
    измерены под панелью и не принимаются). `report.drawer.открылась` при этом по-прежнему `true`.
15. На 360 (мок без `--fill`): `phone.rowsVisible === 5`, `phone.firstRowStyled.display === 'flex'`,
    `borderTopStyle === 'solid'`, `phone.scrollWidth <= phone.innerWidth`, `phone.tabsVisible === true`,
    `phone.inboxVisible === true` (в этой порции вкладки/инбокс ещё на месте — фиксируется
    как исходное состояние для порции b).
16. `npm run smoke -- … 800` → `phone.firstRowStyled` = `{ display: 'flex', borderTopStyle:
    'solid', paddingTop: '12px' }` (до правки: `inline-block / outset / 1px`), `rowsVisible 5`.
17. `npm run smoke -- … 700` → `phone.bodyOverflowX === 'hidden'` и
    `phone.compactPillVisible === true` (на HEAD при 700: `'visible'` и `false` — блок 640 px
    не срабатывал), `phone.headerButtons` не содержит строк «Репозитории» и «Обновить» (подписи
    скрыты правилом `767px`, в массиве — `aria-label` темы и `'icon'`). При 800 —
    `phone.bodyOverflowX === 'visible'`, `phone.compactPillVisible === false`, `headerButtons`
    содержит и «Репозитории», и «Обновить». При 360 — как при 700. (Ширина панели и ширина
    `.listik-shell > .ui-container` признаком порога не служат: первая и до правки
    `min(720px, 100vw)`, вторая перебита глобальным `!important` — 636 на 700 px, 736 на 800.)
18. `npm run smoke -- … 1024` и `… 1440`: поля `columns, cards, needsYou, toolbar, views, newTask,
    drawer, layout` идентичны эталону на HEAD; `errors`/`console` не хуже эталона (единственная
    допустимая запись — 404 favicon, как на HEAD). `phone.tabsVisible true`, `phone.rowsVisible 0`
    (список скрыт выше 1024).

## Строка списка

19. `MobileTaskRow.vue` существует, принимает `task`, эмитит `open` с id; `MobileTaskList.vue`
    рендерит строки через него и не содержит собственного `<button class="listik-mobile-task">`.
    В `MobileTaskList.vue` не осталось осиротевших импортов/хелперов (`UiStatusPill`, `humanAge`,
    `taskStageLabel`, `HEALTH_TITLES`, `taskHealth`, `healthTone`, `healthLabel`) — иначе п. 1
    (`npm run typecheck` с `noUnusedLocals`) красный; при этом сортировка, `PAGE = 24`,
    заголовок, `UiEmptyState` и «Показать ещё» в компоненте на месте.
20. В DOM на 800 px строка по-прежнему содержит id, пилюлю здоровья, заголовок, бейдж проекта,
    этап, «держит …/держателя нет» и «heartbeat …» — сравнить текст первой строки с HEAD для
    `listik-web-a1b2`: «держит dsh», «heartbeat 18 мин».
21. Задача без держателя, у которой `holder_title` — строка `'—'` (временно подменить ответ мока
    или проверить на живом сервере с временной базой: `./bin/listik --local create …` без claim),
    показывает «держателя нет», а не «держит —». Проверяется чтением
    `MobileTaskRow.vue`: условие по `task.holder`, не по `holder_title`.

## CSS

22. `grep -n "max-width: 640px" web/src/assets/app.css` — пусто; `grep -n "max-width: 767px"` —
    ровно два блока (бывшие 640). `grep -n "max-width: 860px\|max-width: 1023px\|max-width: 1279px"` —
    как на HEAD.
23. Правила `.listik-mobile-task*` в `app.css` объявлены один раз каждое, вне `@media`;
    объявления `.listik-mobile-task` с `radius-xl`/`space-4`/`color-mix` нет (проверяется
    чтением файла).
24. Внутри блока `767px` набор селекторов совпадает с прежним блоком `640px` минус
    `.listik-mobile-task*` (сравнить `git diff` блока: только удалённые строки про строку списка
    и изменённая строка `@media`).

## README

25. `web/README.md` упоминает `--fill=N` и эмуляцию телефона в smoke при ширине < 768.
