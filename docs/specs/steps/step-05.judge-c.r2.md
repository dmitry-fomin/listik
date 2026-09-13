# Приёмка порции 05.c, заход r2 — разбор

Дерево порции: `/Users/dmitry.fomin/Projects/Listik-ui` (ветка `pipeline-ui`, база `4bed59b`).
Повторный заход: смотрится только разница между `step-05.diff-c.r1.txt` и `step-05.diff-c.r2.txt`.

## Вердикт: зелёный

## Что изменилось между заходами

`diff` дампов r1 → r2 даёт ровно одну содержательную правку — в двух местах:

- `web/src/components/board/BoardColumn.vue` — `computed headToggleable` (`column.key === 'none'`),
  обработчик `onHeadKeydown` (Enter / Space / Spacebar, с `preventDefault`), и на `<header
  class="listik-column__head">` появились `:class` с модификатором, `:role="button"`,
  `:tabindex="0"`, `:aria-expanded="'true'"` — все четыре только при `headToggleable`;
- `web/src/assets/app.css` — `.listik-column__head--toggleable { cursor: pointer }` и
  `:focus-visible` с `outline: 2px solid var(--accent-500)`.

Больше в дампе не изменилось ничего (остальная разница — только счётчики строк в `--stat`
и хеши blob'ов).

## Красный пункт r1 — закрыт

### п.17: развёрнутая «Заведена» не отдавала `aria-expanded="true"` и не сворачивалась с клавиатуры

**Закрыт.** Замер в headless Chrome (CDP) на 1024, мок-API:

```
старт (свёрнуто):      rail aria-expanded="false", ширина 56, дорожки 56px 208px×4 56px, overflow 64
клик по рельсе:        колонка none, ширина 232, head role="button" tabindex="0" aria-expanded="true",
                       дорожки 232px 208px×4 56px (у .listik-rail те же), overflow 240
фокус на шапке + Enter: свернулась обратно, вернулась рельса с aria-expanded="false"
фокус на рельсе + Enter: развернулась (232, aria-expanded="true")
фокус на шапке + Space: свернулась обратно
```

Клавиатурный путь замкнут в обе стороны, атрибут состояния есть на обоих элементах,
`grid-template-columns` рельсы и ряда совпадают в обоих состояниях, ширины и прокрутка по п.17
прежние (232px, overflow 240).

## Фикс ничего не сломал в пределах той же разницы

- `npm run typecheck` — чисто; `npm run build` — 364 модуля, без ошибок.
- Интерактивность досталась **только** колонке `none`: у `s1-spec…s4-judge` `role`/`tabindex`/
  `aria-expanded` = `null`, клик по их шапке ничего не делает (проверено на 1440).
- `preventDefault` в `onHeadKeydown` работает: `window.scrollY` до Space — 0, после — 0
  (страница не прыгает от пробела).
- Порядок обхода `Tab` внутри `.listik-board-scroll`: `listik-column__head[aria-expanded=true]`,
  далее пять `listik-task-card` подряд — карточки по-прежнему идут по порядку (п.14), шапка
  добавляет одну остановку перед ними.
- Клик по карточке открывает `UiDrawer` (п.14) — не задет.
- Smoke не изменился против r1: 1440 — `board {columns 5, cards 5, doneRail true, rail 4,
  intakeCollapsed false}`, `layout {columnsVisible 4, boardOverflow 0, s4Right −104,
  безГоризонтальнойПрокрутки true}`; 1024 — `columnsVisible 4`, `boardOverflow 64`,
  `s4Right −40`, `intakeCollapsed true`. Единственная запись в `errors` обоих прогонов —
  404 на `/favicon.ico`, он 404 и на пустой странице дев-сервера.
- `git status --porcelain` в дереве порции — те же 9 путей, что и в r1, ничего лишнего;
  `useBoardDrag.ts` удалён.
- п.22: в дампе r2 нет токенов, ключей и содержимого `config.toml` (совпадения по `token`
  — это `<токен>`-плейсхолдер в README и имена рефов `token`/`needsToken` в сторе, не значения).

## Наблюдения (не красные, полную проверку заново не гонял)

- При 1440 клик или Space по шапке «Заведена» сворачивает колонку, хотя на этой ширине
  свёрнутый режим ТЗ не предписывает. Поведение не новое: `onHeadClick` с тем же условием
  `column.key === 'none'` был уже в r1, фикс лишь дал ему клавиатурный вход. П.17 требует
  `intakeCollapsed === false` **без кликов** — это выполняется (проверено на 1440).
- `role="button"` навешен на `<header>`, внутри которого лежит `h2` — заголовок внутри
  интерактивного элемента. На пункты чек-листа не влияет.
- Замечания r1 по `.listik-shell > .ui-container` с `!important` и по формулировке «в „Готово“
  попадают только закрытые без этапа» остаются в силе как записи r1; кода r2 они не касаются.

## Перенесено на приёмку шага

- Формулировка ТЗ/README про колонку «Готово»: `listik done <id> -r "…"` закрывает задачу вместе
  с этапом (`bin/listik:557`, `local_kwargs={"status": "done", "stage": "done"}`), поэтому сценарий
  «закрытая на s4 остаётся в s4» из п.9 чек-листа воспроизводится только закрытием без смены этапа
  (`PATCH {"status":"done"}` при `stage = s4-judge`; `listik/store.py:698`). UI в обоих случаях
  честно рисует то, что прислал сервер. Вопрос к тексту ТЗ и фразе в `web/README.md`, не к коду
  порции (перенесено ещё из r1).

## Коммит

`git add` по девяти явным путям из дампа (включая удаление `useBoardDrag.ts`), один коммит на
порцию, не запушен.
