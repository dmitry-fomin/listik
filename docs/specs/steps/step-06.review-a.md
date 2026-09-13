# Замечания к ТЗ и чек-листу. Шаг 06, порция a

Читал: `docs/specs/steps/step-06.a.md`, `docs/specs/steps/step-06.check-a.md`, `docs/specs/steps/step-06.md`
и код на HEAD `282078c` (`web/scripts/mock-api.mjs`, `web/scripts/smoke.mjs`, `web/src/assets/app.css`,
`web/src/components/MobileTaskList.vue`, `web/src/components/AppHeader.vue`, `web/src/store/listik.ts`,
`listik/store.py`, `listik/actors.py`, кит в `web/node_modules/@zoloto585/facet`).

Предпосылки ТЗ, которые я проверил и подтверждаю: два объявления `.listik-mobile-task` внутри
`@media (max-width: 640px)`, действующее — второе (`app.css:918` и `app.css:929`); живой сервер
отдаёт `holder_title: '—'` для пустого держателя (`listik/store.py:596` → `listik/actors.py:101-106`);
`priority ASC, updated_at DESC` и `limit=200` в `list_tasks` (`listik/store.py:638,674`);
`<meta name="viewport" content="width=device-width">` в `web/index.html:5` (без неё `mobile: true`
дал бы `innerWidth` 980, а не 360 — эмуляция сработает); `--space-3: 12px`.

## Блокирующие

1. **Чек-лист, п. 8: ожидание по `order=priority` противоречит правилу сортировки из ТЗ.**
   ТЗ (A1.4 + «Контекст») требует серверный порядок `priority ASC, updated_at DESC`
   (подтверждено `listik/store.py:674`). В моке приоритет 1 у двух задач: `listik-api-c3d4`
   (`updated_at = iso(30)`, `mock-api.mjs:111`) и `listik-epic-k9l0` (`updated_at` не
   переопределён → `iso(0.3)`, `mock-api.mjs:134-150`). При `updated_at DESC` первой идёт
   `listik-epic-k9l0`, а не `listik-api-c3d4`. Как есть, пункт заставит исполнителя либо
   «поправить» сортировку под неверное ожидание (разойтись с сервером), либо встать.
   Что изменить: в п. 8 ожидание — `listik-epic-k9l0`, затем `listik-api-c3d4`, затем
   `listik-web-a1b2`/`listik-metrics-g7h8`, затем `listik-sse-e5f6`.

2. **Чек-лист, п. 17: ложная предпосылка про ширину панели 720 и проверка, которая не может
   упасть.** Глобальное правило `app.css:558-560` уже задаёт
   `.ui-drawer--right.ui-drawer--lg { width: min(720px, 100vw) }`, т.е. на вьюпорте 700 панель и
   **до** правки 700, а не 720. Перенос порога 640 → 767 добавляет на 700 px только
   `width: 100vw; max-width: 100vw` (`app.css:993-996`) — тот же результат. Пункт пройдёт
   одинаково и на HEAD, и после правки, а заявленное «до правки 720 — панель шире вьюпорта»
   неверно. Что изменить: убрать утверждение про 720; в качестве признака сработавшего порога
   767 взять то, что действительно меняется на 700 px — `.listik-shell > .ui-container`
   становится `min(100% - 24px, 560px)` (`app.css:914-916`, до правки на 700 px действует
   `min(100% - 32px, 1440px)` из блока 900 px) и/или уже имеющееся `phone.headerButtons` без
   подписей. Если брать контейнер — добавить в A3 поле `containerWidth` (computed `width`
   первого `.listik-shell > .ui-container`), иначе проверить нечем.

3. **ТЗ, A3.2: блок `phone` считается при открытой панели задачи — измеряется не то, что
   написано.** `report.drawer` открывает `.ui-drawer` кликом по карточке и **не закрывает её**
   (`smoke.mjs:159-180`, в отличие от `report.newTask`, который шлёт Escape). Кит на время
   дровера ставит `body.style.overflow = 'hidden'` и `inert` на фон
   (`node_modules/@zoloto585/facet/src/composables/useDialogA11y.ts:213-222, 277-280`), плюс
   компенсирующий `padding-right`. Значит `firstRowTop` «при `scrollY = 0`», `rowsFullyVisible`,
   `scrollWidth` и все `*Visible` считаются на экране, который целиком закрыт панелью (на
   телефоне она `100vw`), при заблокированной прокрутке и сдвинутой геометрии. Именно
   `firstRowTop`/`rowsFullyVisible` — метрика, ради которой блок заводится (план шага: «очередь
   начинается на ≈1170 px»), и в порциях b/c по ней будут судить. Чек-лист это не поймает: п. 15
   и 16 проходят и в таком состоянии. Что изменить: в A3.2 потребовать перед блоком `phone`
   закрыть панель (Escape + пауза, как в `report.newTask`) и добавить в блок поле
   `drawerOpen: document.querySelectorAll('.ui-drawer').length > 0`; в чек-лист — пункт
   `phone.drawerOpen === false` на всех ширинах. Альтернатива (проще, но меняет порядок полей):
   считать `phone` до `report.drawer`.

## Существенные

4. **Чек-лист не говорит, с каким моком гоняются пп. 14, 16, 17, 18.** В п. 15 оговорено «мок без
   `--fill`», в остальных — нет, а `docs/specs/steps/step-06.md:114-119` предлагает как раз
   `--fill=50` перед smoke. С `--fill=50` меняются `cards`, `views.Список`, `phone.rowsVisible`
   (24 из-за `PAGE = 24`), и сравнение с эталоном в п. 18 становится бессмысленным.
   Что изменить: в преамбуле чек-листа явно — «пп. 14–21 выполняются на моке **без** `--fill`,
   пп. 10–12 — с `--fill=50`».

5. **Чек-лист, п. 15: `phone.scrollWidth <= phone.innerWidth` рискует пройти вхолостую.** На
   ширинах ≤ 767 действует `body { overflow-x: hidden }` (переезжающее правило `app.css:905-907`),
   а при открытой панели — ещё и `body { overflow: hidden }` от кита (см. п. 3). Проверка
   «нет горизонтального выезда» опирается на величину, которую сама же страница и глушит, — и
   ровно это правило порция a переносит на новый порог, т.е. проверяется в том числе оно.
   Что изменить: либо добавить в блок `phone` независимое измерение (`maxRight` —
   `Math.max(...[...document.querySelectorAll('.listik-shell *')].map((el) => Math.round(el.getBoundingClientRect().right)))`)
   и в п. 15 требовать `maxRight <= innerWidth + 1`, либо честно пометить `scrollWidth` как
   справочное поле и не выводить из него вывод о выезде.

6. **Нет проверки, что строка по-прежнему открывает задачу.** A4 меняет способ доставки клика
   (`@click="openTask(task)"` → `@open="openTask"` c `id`), а единственный функциональный смысл
   строки — открыть панель. `report.drawer` кликает по `.listik-task-card` (карточка доски), не
   по строке, п. 19-20 проверяют только состав DOM. Что изменить: добавить в чек-лист пункт —
   на 800 px клик по первой `.listik-mobile-task` открывает `.ui-drawer`, в котором виден id этой
   задачи (одним `Runtime.evaluate` вручную или временной пробой, не меняя имён существующих проб).

7. **A2: сгенерированные задачи наследуют от базовой поля держателя и тем самым не воспроизводят
   дефект, который чинит A4.** Фабрика `task()` кладёт `holder_age: '18 мин'` и `idle_age: '18 мин'`
   (`mock-api.mjs:55-58`); ТЗ для `open`-заполнителей переопределяет `holder`, `holder_title: ''`,
   `holder_at`, `holder_hours`, `idle_hours`, но не `holder_age`/`idle_age` — выйдет задача без
   держателя с возрастом «18 мин», чего сервер не отдаёт. И главное: сервер для пустого держателя
   отдаёт `holder_title: '—'`, а ТЗ велит `''` — то есть на моке проверить исправление A4 нельзя
   (отсюда и вымученный п. 21 чек-листа: «временно подменить ответ мока… проверяется чтением»).
   Что изменить: в A2 для `open`-заполнителей задать `holder_title: '—'`, `holder_age: ''`,
   `idle_age: ''` (для `in_progress` — `holder_age: '6 мин'`), и переписать п. 21 чек-листа в
   исполнимый: с `--fill=50` на 800 px первая строка заполнителя без держателя показывает
   «держателя нет», а не «держит —».

8. **A4 и граница «`MobileTaskList` не меняется» противоречат `noUnusedLocals`.** В
   `web/tsconfig.json:9-10` включены `noUnusedLocals`/`noUnusedParameters`; после выноса кнопки
   в `MobileTaskRow.vue` в `MobileTaskList.vue` остаются неиспользованными `UiStatusPill`,
   `humanAge`, `taskStageLabel`, `HEALTH_TITLES`, `taskHealth`, функции `healthTone`/`healthLabel`
   и, возможно, `UiBadge`/`Task` — `npm run typecheck` упадёт. Что изменить: в A4 добавить фразу
   «из `MobileTaskList.vue` удаляются импорты и хелперы, переехавшие в строку; остальное
   (сортировка, `PAGE`, заголовок, `UiEmptyState`, «Показать ещё») не трогается».

9. **Чек-лист, п. 18: список сравниваемых полей уже отчёта.** В отчёте есть ещё `title`,
   `filterBarOnBoard` и объект `board` (`doneRail`, `rail`, `intakeCollapsed`) —
   `smoke.mjs:108-120`; именно `board.*` первым поймает случайный побочный эффект правки CSS.
   Что изменить: требовать совпадение **всего** отчёта, кроме нового блока `phone`
   (и, при необходимости, `errors`/`console`), а не восьми перечисленных полей.

## Заметки

10. `--fill` без порта (`node scripts/mock-api.mjs --fill=50`) даст `Number('--fill=50') = NaN`
    и слушание на случайном порту: `port` берётся из `process.argv[2]` (`mock-api.mjs:11`).
    Одна строка в A2 — «нечисловой `argv[2]` игнорируется, порт остаётся 8788» — снимет ловушку.

11. A1 разрешает не поддерживать `stage/assignee/type/text/label`, но «не ронять запрос», а
    проверки на это в чек-листе нет. Один `curl` вида
    `'/api/tasks?stage=s3-impl&assignee=agent:dsh&type=feature&text=abc&label=frontend'` → 200 и
    непустой `tasks` закрывает пункт.

12. Чек-лист, п. 4: у четырёх базовых задач `updated_at` совпадает посимвольно (`iso(0.3)`
    вычисляется один раз), порядок держится только на стабильности сортировки. Лучше назвать
    ожидаемые id прямо: `listik-sse-e5f6`, `listik-epic-k9l0`.

13. ТЗ молча выбирает глобальный `app.css` для стилей строки. Альтернатива — `<style scoped>` в
    самом `MobileTaskRow.vue` (так живут `NeedsYouStrip.vue`, `BoardToolbar.vue`, `ListView.vue`):
    правила физически рядом с разметкой и в медиа-блок уже не уедут. Против — порция b (`PhoneQueue`)
    переиспользует тот же класс, а `smoke.mjs` селектирует его снаружи; выбор в пользу app.css
    защитим одной фразой в A5, иначе он выглядит как инерция.

14. A2 описывает только правку `/api/meta` при `--fill`, но заполнители попадают и в `/api/board`,
    `/api/ready`, `/api/blocked`, `/api/stats.running`, `/api/health.counts` (все строятся из того
    же массива `tasks`). Стоит сказать, что это намеренно, — иначе исполнитель может начать
    фильтровать заполнители из доски.

15. ТЗ перечисляет пробы smoke как «`report.columns`, `cards`, `needsYou`, `toolbar`, `views`,
    `newTask`, `drawer`, `layout`, `errors`, `console`», опуская `title`, `filterBarOnBoard`,
    `board` — мелкая неточность в описании существующего кода, но именно из неё вырос п. 9.
