# Приёмка 02.c, заход r1 — вердикт: зелёный

Дифф: `.git/feature-pipeline/step-02.diff-c.r1.txt` (3 файла, +25/−22, только документация).

## Предусловие

- `grep -n "def set_needs_owner" listik/store.py` → `318:` — непусто.
- `grep -n "def body" listik/migrate.py` → `45:` — непусто.
- `migrate.upsert(Path("AGENTS.md"), dry_run=True)` → `unchanged`.

Порции a и b закоммичены, приёмка проводится.

## API.md

**1. `POST /api/tasks/{id}/needs-owner` — зелёный.** Строка таблицы описывает `value=true|false`,
`note`, `actor`, `harness`; комментарий `question`/`answer` при непустом `note`; событие при каждом
вызове; ответ — полная карточка. Живая сверка на временной базе (`--local`, `LISTIK_DB` в
scratchpad), два вызова подряд с одним и тем же вопросом:

- `comments` → `[('question', …), ('question', …)]` — два комментария;
- ключа `unchanged` в ответе нет ни в первом, ни во втором вызове;
- ответ — полная карточка (`abandoned, acceptance, archived, assignee, … comments, created_at`);
- `events` → `question: 2` при уже поднятом флаге.

Код сверен: `store.set_needs_owner` (`listik/store.py:318–350`) пишет комментарий при непустом
тексте и событие безусловно; `listik/server.py:360–363` прокидывает `value/note/actor/harness`.
Документация совпадает с кодом дословно.

**2. Оговорка про `PATCH` — зелёный.** В той же строке сказано, что `PATCH` с `needs_owner` меняет
только флаг и комментария не пишет. Сверка: до `set <id> needs_owner=0` — 4 комментария, после — 4,
`needs_owner=False`.

**3. `POST /api/tasks/{id}/comment` — зелёный.** Строка поясняет, что `kind=question`/`answer` — те
же виды, что пишет `needs-owner`, руками их ставить можно, флаг они не меняют. Сверка:
`comment <id> "x ручной" -k question` → `needs_owner` остался `False`.

**4. CLI — зелёный.** `API.md` строка 254 (раздел «CLI»): `listik needs-owner <id> --clear "ответ"`.
Команда выполнена на временной базе: `needs_owner: False`, в `comments` появился
`('answer', 'Считаем по позициям')`.

**5. Раздел «Правила работы агента с задачами» — зелёный.** `grep -n "Перед работой посмотри"
API.md` пуст, семипунктного списка нет. На его месте (`API.md:268–275`) ссылка на
`docs/harness-protocol.md` с упоминанием блока `<!-- BEGIN LISTIK -->` и `listik init-projects`.
Оставшиеся API-факты сверены чтением кода:

- «заблокированную задачу `claim` не возьмёт (400 с перечнем блокеров)» — `store.claim`
  (`listik/store.py:385–395`): при `state["blocked_by"]` и без `force` бросает `ValueError`
  с перечислением до пяти блокеров;
- «handoff (`s2→s3`, `s4→done`) снимает держателя, sticky (`s1→s2`, `s3→s4`) — нет» —
  `store.next_stage` (`listik/store.py:470–477`): `if transition == "handoff": fields["holder"] = ""`;
  `config.DEFAULTS["routing"]["transitions"]` даёт ровно `s1-spec:s2-review=sticky`,
  `s2-review:s3-impl=handoff`, `s3-impl:s4-judge=sticky`, `s4-judge:done=handoff`;
- «красный вердикт на `s4-judge` сервер сам возвращает на `s3-impl`» — `store.add_comment`
  (`listik/store.py:440–446`).

## README.md

**6. «Работа агента с задачей» и «Контекст этапа» — зелёный.** В `sh`-блоке (`README.md:66`) стоит
`./bin/listik needs-owner <id> --clear "ответ"`. `grep -n 'Взял задачу — \`claim\`' README.md` пуст.
Пятипунктный список заменён на том же месте, в «Контексте этапа» (`README.md:98–99`), ссылкой на
`docs/harness-protocol.md` с фразой про блок в `AGENTS.md` проектов; абзац «Одна задача — один
держатель» (`README.md:101–102`) сохранён. По диффу остальной текст раздела (пути документов,
примеры `context`) не тронут — в ханке только удаление списка и вставка двух строк.

**7. «Этапы конвейера» — зелёный.** Абзац `README.md:155–159` описывает sticky/handoff по
переходам и возврат по красному вердикту; список совпадает с
`config.DEFAULTS["routing"]["transitions"]` (см. пункт 5). Живая сверка полного прохода на
временной базе:

| переход | ожидание | факт |
|---|---|---|
| claim на `s2-review` | holder стоит | `holder='dsh/test'` |
| `s2-review → s3-impl` | handoff, holder снят | `stage=s3-impl, holder=''` |
| `s3-impl → s4-judge` | sticky, holder остался | `stage=s4-judge, holder='dsh/test2'` |
| `s4-judge → done` | handoff, holder снят | `stage=done, holder=''` |

Ссылка на раздел «Роли по этапам» не висячая: `docs/harness-protocol.md:18` — `### Роли по этапам`.

**8. «Перевод проектов на Listik» — зелёный.** `README.md:200–203`: тело блока берётся из
`docs/harness-protocol.md`, повторный `init-projects` обновляет блок идемпотентно. Проверено
чтением, без записи: `migrate.block()` содержит десять нумерованных правил протокола
(`['1'…'10']`, длина 5133); `migrate.upsert(Path("AGENTS.md"), dry_run=True)` → `unchanged`.
`init-projects` не запускался ни разу — ни с `--dry-run`, ни без него; следов запуска без
`--dry-run` в отчёте исполнителя нет.

Непринципиальная неточность (не красный пункт, вне формулировок чек-листа): `migrate.body()`
(`listik/migrate.py:34–42`) возвращает `INTRO + protocol`, то есть блок кроме текста протокола
содержит ещё вводный абзац про beads; README говорит «Тело блока — весь текст
`docs/harness-protocol.md`». Требование чек-листа («блок содержит протокол из
`docs/harness-protocol.md`») выполнено, текст протокола в блоке действительно целиком.

**9. «Агентам: MCP и CLI» — зелёный.** `README.md:261–262`: вопрос через
`listik_needs_owner`/`needs-owner` ложится в историю карточки и находится `search`. Сверка:
`./bin/listik --local search "квазилимит" --mode text --json` находит задачу, попадание —
`{"kind": "comment", "snippet": "Как считать [[квазилимит]] бурундука?"}`.

**10. Примеры команд — зелёный.** Единственная добавленная команда в диффе README —
`needs-owner <id> --clear "ответ"` (и та же строка в CLI-разделе `API.md`); выполнена на временной
базе без ошибок. Прочие вставки диффа — проза, не команды. `init-projects` и `init-projects --remove`
не запускались.

## CLAUDE.md

**11. Зелёный.** `CLAUDE.md:80–81`: «The block body is read from `docs/harness-protocol.md`
(`migrate.body()`) — change the protocol there, not in `migrate.py`». По диффу это единственное
изменение файла (+2/−1 в одном ханке).

## Границы и общие проверки

**12. Зелёный.** `git diff --stat HEAD -- . ':!docs/specs'` → ровно `API.md`, `CLAUDE.md`,
`README.md` (25 вставок, 22 удаления). `git status --porcelain -- listik bin tests web AGENTS.md
docs/harness-protocol.md` пуст.

**13. Зелёный.** `grep -n "harness-protocol"`: `README.md:98,159,201`, `API.md:270`, `CLAUDE.md:80`.

**14. Зелёный.** `grep -c "Чужую карточку не переписывай" README.md API.md` → `0` и `0`.

**15. Зелёный.** `python3 -m unittest discover tests` → `Ran 78 tests … OK`. В диффе нет токенов,
ключей, `.env`/`*.pem`/`credentials.json` и содержимого `config.toml` (имя файла встречается только
в неизменённой строке контекста `CLAUDE.md`).

## Срезанные углы

Не найдено. Дифф чисто документационный, ни строки кода/тестов/схемы не тронуто; все утверждения
документации сверены либо живым вызовом на временной базе, либо чтением кода — расхождений нет.
Заглушек, хардкода и обходов требований нет.

## Перенесено на приёмку шага

Нет.

## Вердикт

Зелёный. Коммит: `README.md`, `API.md`, `CLAUDE.md`.
