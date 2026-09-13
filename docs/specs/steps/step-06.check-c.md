# Чек-лист приёмки. Шаг 06, порция c — карточка задачи на телефоне, только чтение

ТЗ: `docs/specs/steps/step-06.c.md`. Все команды — из `web/`, мок `node scripts/mock-api.mjs 8788
--fill=50`, vite на 5177 с `VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token`.
Эталон для планшета/десктопа — smoke на коммите порции b с тем же моком.

## Сборка и границы

1. `npm run typecheck` и `npm run build` (без `VITE_*`) проходят.
2. `git status`: изменены только `web/src/components/PhoneTaskSheet.vue` (новый),
   `web/src/App.vue`, `web/src/assets/app.css`, `web/scripts/smoke.mjs`, `web/README.md`.
3. `git diff web/src/App.vue`: у `TaskDrawer` добавлен только `v-if="!store.phone.value"`
   (пропсы/события те же), добавлен `PhoneTaskSheet v-else` с `v-model="drawerOpen"`, `task`,
   `loading`, `error`, `projects`, `@reload`; `TaskDrawer.vue` в diff отсутствует.
4. `PhoneTaskSheet.vue` не импортирует `store`, не эмитит ничего, кроме `update:modelValue` и
   `reload`; в шаблоне нет `UiInput`, `UiTextarea`, `UiSelect`, `UiCopyButton`, `UiSplitButton`,
   `<input`, `<textarea`, `<button` кроме `UiButton` «Повторить» внутри ветки `error`
   (проверяется чтением/`grep`).

## Телефон 360×740 (smoke 360, мок `--fill=50`)

5. `phone.sheet.open === true`, `phone.sheet.title` — заголовок `listik-web-a1b2` («Собрать
   доску канбан для трекера»), `phone.sheet.drawerWidth === 360`,
   `phone.sheet.drawerScrollWidth <= 360` (нет горизонтального скролла внутри панели).
6. `phone.sheet.sections` ровно `['Кто держит', 'Критерии приёмки', 'Блокеры', 'Последний review',
   'Журнал']` в этом порядке; нет «Где стоит процесс», «Холодный старт», «Связи», «Описание · ТЗ»
   (до правки на 360 открывался `TaskDrawer` с девятью заголовками).
7. `phone.sheet.actionButtons === 0`, `phone.sheet.inputs === 0` (до правки: кнопки «Отправить»,
   «Дерево связей» и поле ввода журнала были видны).
8. `phone.sheet.timelineItems === 2` (журнал мока: verdict и journal), `phone.sheet.closed === true`,
   `phone.sheet.scrollRestored === true`, `phone.sheet.rowsAfterClose === phone.rowsAfterMore`
   (40 — список не перемонтирован и не сброшен).
9. `phone.requests.taskDetail === 1` (одна карточка — один `GET /api/tasks/<id>`), `board/ready/
   blocked/stats` по-прежнему `0`; `errors` — не более 404 favicon, `console` пуст.

## Ручные проверки (DevTools, устройство 360×740)

10. Карточка `listik-web-a1b2`: шапка — глиф типа, метка проекта «Listik», заголовок, пилюля
    здоровья («под угрозой» — `idle_hours 0.3` ≥ 0.25 по `lib/health.ts`), бейдж этапа
    «3. Реализация», id моноширинным; «Кто держит»: «dsh · 18 мин», heartbeat с датой и «18 мин
    назад», «что делает» — «пишу панель задачи»; «Критерии приёмки» — «npm run build и vue-tsc
    --noEmit проходят без ошибок»; «Блокеры» — «нет»; «Последний review» — «вердикт · me · 1 ч»
    и «ок, собирай»; «Журнал» с бейджем 2: сверху вердикт (1 ч), ниже журнал «взял в работу» (2 ч).
11. Карточка `listik-metrics-g7h8`: «Блокеры» с бейджем 1 и строкой `listik-api-c3d4` «Отдать
    needs_you одной лентой» · `s1` · «без держателя»; id — текст, не кнопка (клик ничего не
    открывает).
12. Карточка `listik-api-c3d4`: бейдж «нужен ты», «держит» → «никто», heartbeat «—», пилюля
    «брошена».
13. Карточка `listik-fill-011` (нечётный заполнитель: `open`, `holder: null`, `holder_title: ''`):
    пилюля «без держателя», «держит» → «никто», heartbeat «—», «что делает» — «—», «Блокеры» —
    «нет», «Критерии приёмки» — унаследованный текст мока «npm run build и vue-tsc --noEmit
    проходят без ошибок» (у заполнителей `acceptance` непуст; пустые критерии проверяются на
    живом сервере, п. 16), без ошибок в консоли. (`listik-fill-010` не подходит: чётные
    заполнители — `in_progress` с держателем `agent:dsh`.)
14. Закрытие крестиком, Escape и тапом по фону — все три возвращают к списку на прежней
    прокрутке (проверить `scrollY` до/после при прокрутке ~1500 px) с тем же числом строк и
    кнопкой «Показать ещё» на месте.
15. Ошибка загрузки: в Network заблокировать `/api/tasks/listik-fill-013`, открыть строку →
    в панели `UiAlert` «Не удалось загрузить задачу» и единственная кнопка «Повторить»; снять
    блокировку, нажать — карточка загружается.
16. Живой сервер с временной базой: задача, созданная без критериев приёмки (`bin/listik create`
    без `--acceptance`), → «Критерии приёмки» — «—» (на моке не воспроизвести: `acceptance`
    у всех задач непуст); задача с `comment -k review` (раньше) и `comment -k verdict`
    (позже) → «Последний review» показывает verdict; задача с 25 комментариями → в «Журнале» 20
    записей, бейдж «25», самая свежая сверху; у задачи без держателя (`holder_title` `'—'` с
    сервера) — «держит: никто», а не «—».
17. Длинный `spec_path`/id в заголовке или в блокерах не расширяет панель: `.ui-drawer`
    `scrollWidth` равен ширине вьюпорта (проверить на живой задаче с длинным заголовком без
    пробелов, например 60 символов).

## Планшет и десктоп не изменились

18. smoke 800: `drawer.открылась true`, `drawer.ширина 720`, `drawer.естьРазделПроцесса true`,
    `drawer.естьХолодныйСтарт true`, `drawer.splitButton true`, `phone.sheet === null`
    (`.listik-phone-queue` на планшете нет).
19. smoke 1024 и 1440: `drawer.*`, `columns, cards, needsYou, toolbar, views, newTask, layout`
    идентичны эталону порции b; `phone.sheet === null`.
20. На 1100 px открытие карточки из инбокса «Ответить» по-прежнему ставит фокус в поле ответа
    `TaskDrawer` (регрессия `drawerRef` при `v-if`): в режиме продакшн-сборки или на живом
    сервере — как в шаге 05.

## README

21. `web/README.md` содержит раздел «Телефон (< 768 px)» с перечнем показываемого/скрытого,
    запросами телефона (и списком тех, что он не делает), порогами 767/1023 и способом проверки;
    в «Структуре» упомянуты `PhoneQueue`, `PhoneTaskSheet`, `MobileTaskRow`, `lib/viewport.ts`.
