# Шаг 06. Ограниченный мобильный просмотр — план порций

Спека шага: `docs/specs/steps/step-06-limited-mobile-view.md`. Продукт: `docs/specs/listik-product.md`
§7 «UI» (ниже 1024 px — сокращённый режим; полная мобильная доска, метрики и DnD в мобильный MVP
не входят). Дух и объём — `docs/listik-vision.md`: телефон — «быстро посмотреть очередь», ничего
сверх этого.

## Цель шага одной фразой

На телефоне (< 768 px) вместо всей доски показывается лёгкая страница: фильтр «проект», поиск и
постраничная очередь задач; нажатие открывает карточку только для чтения. Планшет (768–1023 px)
и десктоп (≥ 1024 px) не меняются.

## Решения автора (13.09.2026)

1. **Карточка на телефоне — чистый просмотр, без действий**: заголовок, проект/этап/health,
   держатель и heartbeat, критерии приёмки, блокеры, последний review/verdict, журнал. Никаких
   кнопок claim/comment/needs-owner/heartbeat, никаких полей ввода.
2. **Над списком на первом экране телефона — только фильтр «проект» и поиск.** Вкладки
   «Доска/Список/Таймлайн/Метрики», инбокс «Нужен ты», чипы здоровья и чип «закрытые» на телефоне
   скрыты.
3. **Данные телефон берёт своим постраничным запросом** `GET /api/tasks?limit=20&offset=…&order=updated`
   (+ `project`), кнопка «Показать ещё 20». `/api/board`, `/api/ready`, `/api/blocked`, `/api/stats`
   и запрос «done за 7 дней» на телефоне не выполняются. Мок `web/scripts/mock-api.mjs`
   дорабатывается, чтобы `/api/tasks` учитывал `limit/offset/order/project/needs_owner`.

Согласованные допущения планировщика:

- порог телефона — **767 px включительно** (`max-width: 767px`), по спеке шага; существующие
  «телефонные» правила CSS на `640px` переезжают на `767px`;
- планшет 768–1023 px остаётся как есть (вкладки, инбокс, тулбар, существующий `MobileTaskList`
  вместо доски, полный `TaskDrawer`), чинится только стилизация строк списка;
- карточка на телефоне — **отдельный лёгкий компонент** поверх `UiDrawer` на всю ширину;
  `TaskDrawer.vue` не трогается;
- «последний review» = последний комментарий `kind ∈ {review, verdict}`; «журнал» = последние
  20 комментариев (все виды), новые сверху, без событий (`events`).

## Что уже есть и что найдено при разведке (проверено на HEAD `282078c`, мок + CDP)

- `web/src/components/MobileTaskList.vue` (шаг 05): список из `store.columns` (т.е. из `/api/board`),
  сортировка по `updated_at` в памяти, показ по 24 с «Показать ещё». Показывается ниже 1024 px
  вместо доски (`.listik-board-only` скрыт, `.listik-mobile-queue` — flex в `@media (max-width: 1023px)`).
  Строка уже содержит id, health-пилюлю, заголовок, проект, этап, держателя и heartbeat.
- **Стили строки `.listik-mobile-task*` лежат внутри `@media (max-width: 640px)`** в
  `web/src/assets/app.css`, поэтому на 641–1023 px (планшет) строки списка — голые `<button>`
  (`display: inline-block`, `border: outset`, `padding: 1px`). Это дефект, чинится в порции a.
- Строка показывает `держит ${task.holder_title}` по истинности `holder_title`; живой сервер для
  пустого держателя отдаёт `holder_title: '—'` (см. шаг 05, порция d) — на сервере вышло бы
  «держит —». Чинится в порции a: решать по `task.holder`.
- На 360×740 (эмуляция телефона) горизонтального скролла **нет**, но очередь начинается на
  ≈1170 px от верха: шапка 56 px + вкладки + инбокс «Нужен ты» ≈740 px + тулбар с чипами ≈196 px.
  Первый экран задач не показывает.
- Панель задачи на телефоне — тот же `TaskDrawer` на `100vw` (правило на ≤640 px), ряд действий
  скрыт CSS, но остаются «Холодный старт» (7 строк с копированием), «Связи» с деревом, форма
  комментария, «Дизайн», «Заметки». Скролл списка после закрытия панели сохраняется (кит
  блокирует прокрутку `body`, позиция не теряется).
- `web/scripts/smoke.mjs`: headless Chrome через `--window-size` **не даёт окно уже 500 px**
  (запрос 360 → viewport 500); пробы считают DOM-элементы без проверки видимости, поэтому на
  телефоне «колонок 4, карточек 4» при скрытой доске. Нужна эмуляция устройства
  (`Emulation.setDeviceMetricsOverride`) и пробы по видимости.
- `web/scripts/mock-api.mjs`: `/api/tasks` отдаёт все 5 задач и игнорирует любые параметры;
  `/api/health` у живого сервера без токена, всё остальное — с токеном.
- Сервер (`listik/store.py list_tasks`): параметры `project, status, stage, assignee, holder,
  needs_owner, type, label, text, include_closed, include_archived, limit (200), offset (0),
  order ∈ updated|created|priority|stage`; без `status` и `include_closed` отдаёт только
  `open, in_progress, blocked, review`; ответ `{total, limit, offset, tasks[]}`, строки той же
  формы, что на доске (есть `stale`, `abandoned`, `idle_hours`, `stage_warn`, `holder_title`).
- `GET /api/tasks/{id}` отдаёт `comments[]`, `events[]`, `deps_state` (в т.ч. `blocked_by[]`
  с `id, title, stage, holder, holder_title, holder_age, missing`).

## Общие имена (контракт между порциями)

| Имя | Что это |
| --- | --- |
| `PHONE_MAX_WIDTH = 767`, `PHONE_MEDIA = '(max-width: 767px)'` | `web/src/lib/viewport.ts` (порция b) |
| `useIsPhone()` | реактивный `Ref<boolean>` по `matchMedia(PHONE_MEDIA)` (порция b) |
| `store.phone`, `store.setPhone()`, `store.queueTick`, `store.loadQueuePage()` | режим телефона в `web/src/store/listik.ts` (порция b) |
| `PHONE_PAGE = 20` | размер страницы очереди (порция b) |
| `MobileTaskRow.vue` (`button.listik-mobile-task`) | общая строка списка для планшета и телефона (порция a) |
| `PhoneQueue.vue` → `.listik-phone-queue`, `.listik-phone-toolbar`, `.listik-phone-queue__list`, `.listik-phone-queue__more` | очередь телефона (порция b) |
| `PhoneTaskSheet.vue` → `.listik-phone-sheet` | карточка телефона (порция c) |
| `.listik-shell--phone` | класс корня `App.vue` в режиме телефона (порция b) |
| `node scripts/mock-api.mjs 8788 --fill=50` | мок с 50 сгенерированными задачами (порция a) |
| `report.phone` в `smoke.mjs` | блок мобильных проб по видимости (порции a, b, c) |

## Порции (по порядку, каждая опирается на закоммиченные предыдущие)

| Порция | Суть | Файлы ТЗ |
| --- | --- | --- |
| a | Инструменты и планшет: мок учитывает параметры `/api/tasks` и умеет `--fill=N`; smoke с эмуляцией устройства и блоком `phone`; строка списка вынесена в `MobileTaskRow.vue`, её стили — вне медиа-блока (починка планшета), порог «телефонных» правил 640 → 767 | `step-06.a.md`, `step-06.check-a.md` |
| b | Режим телефона: `useIsPhone`, ветка стора без доски/графа/статистики, `PhoneQueue` (проект + поиск + постраничная очередь по 20), ветка `App.vue`, шапка без «Репозиториев» | `step-06.b.md`, `step-06.check-b.md` |
| c | Карточка только для чтения `PhoneTaskSheet` вместо `TaskDrawer` на телефоне, возврат к месту в списке, README | `step-06.c.md`, `step-06.check-c.md` |

## Вне шага

- Любые правки `listik/*.py`, `bin/listik`, `tests/`, `API.md`, alembic, `config.toml`.
- Доступ к серверу с телефона по сети (сервер слушает `127.0.0.1`) — не здесь.
- Любые действия над задачей с телефона (claim, комментарий, ответ на вопрос, heartbeat) —
  решение автора: только просмотр.
- Инбокс «Нужен ты», вкладки, чипы, «закрытые», «Новая задача», «Репозитории» на телефоне.
- Изменения `TaskDrawer.vue`, `BoardView`, `ListView`, `TimelineView`, `MetricsView`,
  `NeedsYouStrip`, `BoardToolbar`, `NewTaskModal`, `ProjectSettings`.
- Планшет 768–1023 px: поведение не меняется (только починка стилей строк списка в порции a).
- PWA, офлайн, установка на домашний экран, push.

## Как проверять любую порцию

```sh
cd /Users/dmitry.fomin/Projects/Listik/web
npm install                       # если нет node_modules; postinstall патчит кит — это штатно
npm run typecheck                 # vue-tsc --noEmit, strictTemplates
npm run build                     # ТОЛЬКО без VITE_API_BASE/VITE_LISTIK_TOKEN в окружении:
                                  # web/dist отдаёт живой сервер автора, мок туда зашивать нельзя
node scripts/mock-api.mjs 8788 --fill=50          # заглушка API (в другом терминале; --fill с порции a)
VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token npx vite --port 5177 --strictPort
npm run smoke -- http://localhost:5177/ 360       # с порции a: <768 → эмуляция телефона 360×740
npm run smoke -- http://localhost:5177/ 800
npm run smoke -- http://localhost:5177/ 1024
npm run smoke -- http://localhost:5177/ 1440
```

Живой сервер на временной базе, если нужны настоящие данные:
`LISTIK_DB=/tmp/listik-step06.db ./bin/listik --local init`, затем
`LISTIK_DB=/tmp/listik-step06.db ./bin/listik serve --port 8797` и `VITE_API_BASE=http://127.0.0.1:8797`.
Токен — `./bin/listik token`; в ТЗ, чек-листы, отчёты и коммиты токен не копировать.

Ручная проверка на телефоне — Chrome DevTools, режим устройства, 360×740 (или любой телефон);
`npm run dev`/`vite` слушает 127.0.0.1, поэтому настоящий телефон в этом шаге не требуется.
