# Приёмка. Шаг 06, порция b — режим телефона: очередь по страницам. Заход r2

Вердикт: **зелёный**. Коммит `bbafcb6`.

Повторный заход: смотрел только разницу между пакетами
`.git/feature-pipeline/step-06.diff-b.r1.txt` и `…r2.txt`. Полную проверку заново не гонял —
зелёные пункты r1 (1–7, 8a, 10–24) остаются в силе, код вне `smoke.mjs` между заходами
не изменился (дифф пакетов показывает только один хунк в `web/scripts/smoke.mjs` плюс строки
статистики и blob-хеша).

## Что изменилось между r1 и r2

Ровно фикс красного пункта, 4 добавленные строки в `web/scripts/smoke.mjs:262-265`:

```
+    // Читаем badge/more до клика — после клика DOM (счётчики, подпись кнопки)
+    // уже отражает следующую страницу, а не текущее состояние.
+    const queueBadge = badgeEl ? badgeEl.innerText : null
+    const moreLabel = moreEl ? moreEl.innerText : null
```

и замена `queueBadge: badgeEl ? badgeEl.innerText : null` / `moreLabel: moreEl ? … : null`
в возвращаемом объекте (`:294`, `:296`) на уже снятые значения. Чтение происходит до
`moreEl.click()` (`:268`). Порядок остальных проб и их имена не тронуты.

## Красный пункт r1 — закрыт

1. **Пункты 8 и 9: `queueBadge`/`moreLabel` измерялись после клика — закрыт.**
   Смоук 360 (мок `--fill=50`, свежий; vite 5177 с `VITE_API_BASE`/`VITE_LISTIK_TOKEN`):

   ```
   "rowsVisible": 20, "queueBadge": "20 из 55",
   "moreVisible": true, "moreLabel": "Показать ещё 20", "rowsAfterMore": 40
   ```

   Оба поля теперь описывают состояние первой страницы, `rowsAfterMore` — состояние после
   клика, как и требует ТЗ (B7.2). Пункт 8 сходится целиком (`firstRowTop 189` ≤ 240,
   `rowsFullyVisible 3`), пункт 9 сходится целиком.

## Проверка, не сломал ли фикс что-то в пределах разницы

- **Счётчики `requests` по-прежнему считаются в самом конце, после клика** (п. 10):
  360 даёт `board 0, ready 0, blocked 0, stats 0, timeline 0, taskDetail 0, tasksList 3`
  (первая страница + «ещё» + один дубль от `queueTick`, допуск 2–3), `health 2`, `meta 2`.
  То есть перенос чтения подписей вверх не забрал с собой запрос второй страницы.
- **Остальной блок `phone` на 360 не поехал**: `clientWidth 360`, `scrollWidth 360`,
  `innerWidth 360`, `containerBox {width: '336px', left: 12}`, `tabsVisible false`,
  `inboxVisible false`, `chipsVisible 0`, `projectSelectVisible true`, `searchVisible true`,
  `headerButtons ["Включить светлую тему", "icon"]`, `views.*` — «кнопки нет», `newTask` —
  «кнопки нет», `drawer` — «карточек нет». `errors` — единственный 404 (favicon),
  `console` пуст.
- **Планшет не задет** (п. 19): смоук 800 — `tabsVisible true`, `inboxVisible true`,
  `chipsVisible 6`, `firstRowStyled.display 'flex'`, `projectSelectVisible false`,
  `containerBox.width '768px'`, `requests.board 2`, `drawer.ширина 720`;
  новые поля очереди честно `null` (`queueBadge`, `moreLabel`, `rowsAfterMore`),
  `moreVisible false` — клика нет, побочных запросов от пробы тоже.
  `rowsVisible` = 24, как и на эталоне порции a с тем же моком (в чек-листе записано 5 —
  цифра из прогона без `--fill`; см. разбор в r1).
- **Сборка** (п. 1): `npm run typecheck` — чисто; `npm run build` без `VITE_*` — собрано
  (`dist/assets/index-D_HeBLVl.js` 331.59 kB).
- **Границы** (п. 2): `git status` по `web/` — ровно семь путей порции.
- Секретов в диффе нет (`.env`, `*.key`, `*.pem`, `credentials.json`, токены не встречаются).

## Перенесено на приёмку шага

Нет.

## Коммит

```
bbafcb6 Шаг 06, порция b: режим телефона — очередь по страницам
```

Закоммичены явным списком: `web/scripts/smoke.mjs`, `web/src/App.vue`,
`web/src/assets/app.css`, `web/src/components/AppHeader.vue`,
`web/src/components/PhoneQueue.vue`, `web/src/lib/viewport.ts`, `web/src/store/listik.ts`.
Не пушил.

## Хвосты стенда

- С r1 остался зарегистрирован worktree эталона в скретч-каталоге предыдущей сессии
  (detached HEAD `32e0ec5`): `git worktree remove` отказался из-за незакоммиченных артефактов
  стенда, удаление требует `--force` — за подтверждением к автору. Проверить можно
  `git worktree list`.
- Мок 8788 и vite 5177 остановлены.
