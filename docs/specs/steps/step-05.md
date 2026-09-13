# Шаг 05. Desktop/tablet UI по прототипам — план порций

Спека шага: `docs/specs/steps/step-05-desktop-tablet-ui.md`. Продукт: `docs/specs/listik-product.md`
(§4 «Процесс и гранулярность», §7 «UI»). Дух и объём — `docs/listik-vision.md`: ничего сверх спеки
и прототипов. Визуальный референс — `docs/prototype/{Main,BoardLight,CardStates,TaskPanel,NewTask}-html`
(согласованные макеты, не «вдохновение»).

## Трек и границы

- Код пишется в рабочем дереве `/Users/dmitry.fomin/Projects/Listik-ui` (git worktree, ветка
  `pipeline-ui`, база `640c4c5`). Правится **только `web/*`**. Бэкенд (`listik/*.py`, `bin/listik`,
  `tests/`) — другой трек, его не трогать; API уже покрывает шаг (см. `API.md`, `web/src/api/client.ts`).
- В дереве трека нет `web/node_modules`: перед работой `cd web && npm install` (postinstall
  патчит кит — это штатно).
- Кит: `@zoloto585/facet@0.4.1` (`web/node_modules/@zoloto585/facet`, source-only). Перед любой порцией
  читать `README.md` кита (разделы «Язык системы», «Что выбрать», «Рецепты layout», «Иконки») и
  `docs/facet-components.md`. Все `Ui*` — только из кита; своя разметка/CSS — только для того, чего в
  ките нет (карточка канбана, колонка, инбокс-карточка, рельса переходов, чек-лист «Холодный старт»,
  матрица маршрута), и только на токенах кита (`--space-*`, `--text-*`, `--ink-*`, `--health-*`,
  `--chart-*`, `--radius-*`, `--shadow-*`), без `#hex`/`px` в разметке. HTML прототипов не копировать,
  CSS кита не дублировать.

## Что уже есть в `web/` (до шага)

Vue 3.5 + TS + Vite, `strictTemplates`, стор `src/store/listik.ts` (единственный владелец данных;
все действия через `act()` → `refresh()` + `reloadDetail()`; ошибки в `lastError`, 401 → форма токена,
отказ сети → `connectionLost`), `src/api/client.ts` (все нужные эндпоинты + SSE), `AppHeader`
(марка, пилюля «сервер жив», «живое», версия, гамма, тема, «Репозитории», «Обновить» — совпадает с
прототипом), `App.vue` (переключатель видов `UiSegmented`, алерты, `NeedsYouStrip`, свои чипы здоровья,
`TaskFilters` = `UiFilterBar` с 10 полями, `SearchPanel`, четыре вида, `TaskDrawer`, модалка токена,
`ProjectSettings`, простая модалка «Новая задача» из пяти `UiInput`), `BoardView`/`BoardColumn`/`TaskCard`
(своя карточка с id, бейджами, кнопками «Открыть»/«Переместить в…», HTML5 DnD → PATCH), `ListView`
(`UiDataTable` + `UiColumnSetting` + `UiPaginator` + `UiBulkEditModal`), `TimelineView`, `MetricsView`,
`MobileTaskList` (показывается ≤640px), `scripts/smoke.mjs` (CDP, viewport 1440), `scripts/mock-api.mjs`.
Мёртвые файлы: `components/KpiRow.vue`, `ReadyStrip.vue`, `ProblemRow.vue` (никем не импортируются).

Факты API, важные для UI (проверены по `listik/store.py`):

- `/api/board?group_by=stage` отдаёт колонки в порядке `s1-spec, s2-review, s3-impl, s4-judge, none
  («без этапа»), done («завершённые»)`; `done` непуст только при `include_closed=1`.
- У задачи есть `stale` (держатель молчит > `board.stale_hours`=24 ч), `abandoned` (в работе без
  держателя/heartbeat), `stage_warn` (на этапе > `wip_warn_hours`=8 ч), `idle_hours` (часов от
  последнего heartbeat), `holder_hours`, `stage_hours`, `holder_note`, `blocked_by[]`; **нет** полей
  `harness`, `parent`, `children`, «красных вердиктов».
- Держатель/исполнитель — ключ актора (`me`, `agent:claude`, `agent:dsh`, `dsh/deepseek-flash`…);
  харнесс из него выводится по подстрокам (как `listik/actors.py AGENT_HINTS`): claude/opus/sonnet/fable
  → claude, dsh/deepseek → dsh, grok, codex, gemini; иначе — человек.
- `/api/meta.projects[]` — `slug, title, color, kind, n_tasks, n_open, n_wip` (`color` может быть null).
- Событие `stage` при переходе через `POST …/stage` имеет `note` вида `этап -> s2-review (sticky)`
  и `duration_s` прошлого этапа. Типы переходов по умолчанию: s1→s2 sticky, s2→s3 handoff,
  s3→s4 sticky, s4→done handoff (config, наружу не отдаётся — на клиенте константа).
- Вопрос автору: `POST …/needs-owner {value:true, note}` пишет комментарий `kind=question`;
  ответ: `{value:false, note}` пишет `kind=answer` и снимает флаг. Комментарии — только в
  `GET /api/tasks/{id}`, в `/api/board` их нет.

## Решения автора (12.09.2026)

1. DnD карточек и поповер «Переместить в…» убрать совсем; этап меняется только из панели.
2. Переключатель видов — `UiTabs`.
3. Фильтр «любой харнесс» не делать до шага 04 (в компактной строке его нет).
4. В шапке колонки показывать харнессы держателей задач, которые сейчас в колонке.
5. Метка проекта: монограмма и цвет из `/api/meta.projects` (`title`/`color`), при пустом `color` —
   детерминированно `chart-1…6` по slug.
6. NewTask: без блока оценки DeepSeek; поля Тип/Проект/Приоритет/Заголовок/Описание·ТЗ/Критерии
   приёмки/spec_path **плюс** матрица харнесс × процесс, выбор сохраняется в `labels`
   (`harness:<x>`, `process:<y>`). «Базовая декомпозиция» — только подсказка «эпик режется на шаги
   на s1», без отдельного поля.
7. `UiFilterBar` остаётся только внутри вкладки «Список»; над доской — только прототипная строка.
8. Результат первой порции: документ-карта «блок прототипа → компонент кита / своя разметка» +
   общие примитивы + dev-витрина `web/sheet.html` (вне сборки).

Допущения планировщика (автор не спрашивался; менять при несогласии):

- Здоровье задачи считается на клиенте, проверки по порядку: закрытая (`done`/`cancelled`) —
  `healthy` с подписью «закрыта» (сервер при закрытии держателя не снимает, по heartbeat закрытые
  не оцениваются); `dead` — `stale || abandoned` (раньше проверки держателя: на сервере
  `abandoned` — это в первую очередь «в работе без держателя»); `unknown` — нет держателя;
  `at-risk` — `stage_warn` или `idle_hours ≥ 0.25` (heartbeat старше 15 мин, как в легенде
  CardStates); иначе `healthy`. Пороги — константы в одном модуле.
- Подключаются все четыре css-гаммы кита (сейчас импортирован только amber, и переключатель гаммы в
  шапке ничего не меняет); «одна тема/гамма» из спеки = никаких своих тем.
- Ниже 1024 px вместо доски показывается существующий компактный список (продуктовая спека §7);
  на 1024–1279 px колонки уже (208 px), «Заведена» свёрнута в рельсу, s1–s4 видны без прокрутки.
- «Шаг / порция N/M» и прогресс по порциям на карточке — вне шага (нужна сводка по детям в
  `/api/board`); в панели «порция шага …» берётся из `deps_state.parent`.
- Редкие действия панели, которых нет в прототипе («Закрыть с результатом», «Взять всё равно»,
  «Добавить связь»), живут в меню `UiSplitButton` рядом с «Взять в работу».

## Порции (по порядку, каждая опирается на закоммиченные предыдущие)

| Порция | Суть | Файлы |
| --- | --- | --- |
| a | Кит под прототип: карта «блок → компонент», общие примитивы (`ProjectMark`, `HarnessIcon`, `HealthDot`, `TaskGlyph`, `lib/health|harness|projects|stages`), все гаммы, dev-витрина `sheet.html`, smoke с параметром viewport | `step-05.a.md`, `step-05.check-a.md` |
| b | Каркас страницы по Main: `UiTabs`, строка статуса, инбокс «Нужен ты» с текстом вопроса и «Ответить», ряд чипов-счётчиков + компактные фильтры, `UiFilterBar` → только «Список», порог 1024, удаление мёртвых файлов | `step-05.b.md`, `step-05.check-b.md` |
| c | Доска: колонки s1–s4 с кодом/названием/харнессами держателей/точками здоровья, «Заведена», рельса переходов, рельса «Готово · 7 дн», компактная карточка по CardStates, без DnD, раскладка 1024 | `step-05.c.md`, `step-05.check-c.md` |
| d | Панель задачи по TaskPanel: шапка, «Где стоит процесс» (`UiSteps` + действия), «Холодный старт», «Кто держит», «Журнал и вердикты», «Связи», описание/критерии, ответ на вопрос | `step-05.d.md`, `step-05.check-d.md` |
| e | Окно «Новая задача» по NewTask (тип/проект/приоритет/заголовок/ТЗ/критерии/spec_path, матрица маршрута → labels), обновление `web/README.md`, чистка `verify-changes.mjs` | `step-05.e.md`, `step-05.check-e.md` |

## Вне шага

- Любые правки `listik/*.py`, `bin/listik`, `tests/`, `API.md`, `README.md` корня, alembic.
- Оценщик DeepSeek, поля `harness`/`skill` на карточке, `ready --harness`, таблица routing в UI (шаг 04).
- Мобильный режим < 768 px и его критерии (шаг 06); здесь только порог 1024 и уже существующий
  компактный список.
- Прогресс по порциям и подпись «порция N/M» на карточке доски; счётчик «красный ×N».
- Виды «Список», «Таймлайн», «Метрики» по содержанию не меняются (только переезд фильтр-бара).

## Как проверять любую порцию

```sh
cd /Users/dmitry.fomin/Projects/Listik-ui/web
npm install                       # один раз; postinstall патчит кит
npm run typecheck                 # vue-tsc --noEmit, strictTemplates
npm run build                     # vite build → dist (sheet.html в сборку не входит)
node scripts/mock-api.mjs 8788    # заглушка API (в другом терминале)
VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token npm run dev
npm run smoke -- http://localhost:5173/ 1024      # после порции a: второй аргумент — ширина
npm run smoke -- http://localhost:5173/ 1440
```

Живой сервер на временной базе, если нужны настоящие данные: в дереве трека
`LISTIK_DB=/tmp/listik-step05.db ./bin/listik --local init`, затем
`LISTIK_DB=/tmp/listik-step05.db ./bin/listik serve --port 8797` и `VITE_API_BASE=http://127.0.0.1:8797`.
Токен — `./bin/listik token`; в ТЗ, чек-листы, отчёты и коммиты токен не копировать.
