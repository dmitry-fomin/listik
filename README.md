# Listik — трекер задач и память

Listik заменяет `bd` (beads): один сервер, одна база SQLite, один API. Внутри — все задачи
по всем проектам, гибридный поиск (полнотекстовый + векторный) и доска, на которой видно,
что в работе, кто это держит и сколько времени прошло.

```
Listik/
  bin/listik          CLI (он же клиент API)
  listik/             ядро: база, store, поиск, эмбеддинги, HTTP, MCP
  web/                доска (Vue 3 + @zoloto585/facet)
  docs/               справка по UI-киту и решения
  API.md              контракт API и модель задачи — читать перед работой с данными
  config.toml         порт, токен, модель эмбеддингов
  listik.db           база (единственный источник истины)
  listik.log, listik.pid
```

## Быстрый старт

```sh
cd ~/Projects/Listik

./bin/listik status          # что с сервером, базой и поиском
./bin/listik serve --daemon  # поднять сервер и доску
./bin/listik token           # ссылка на доску с токеном
./bin/listik stop            # остановить
```

Доска — это обычный `http://127.0.0.1:8787/` (для разработки фронта — `vite dev` в `web/`).

## Ежедневная работа

```sh
./bin/listik inbox                  # вопросы к тебе + что висит без движения
./bin/listik ready                  # что можно взять прямо сейчас (не ждёт блокеров)
./bin/listik blocked                # кто кого ждёт: блокеры по каждой задаче
./bin/listik tree <id>              # дерево зависимостей задачи
./bin/listik board                  # состояние доски в терминале
./bin/listik board --group-by stage # по этапам конвейера
./bin/listik list --mine            # мои задачи
./bin/listik list --needs-owner     # где ждут меня
./bin/listik list --stale           # брошенные агентами
./bin/listik search "как я решал X" # поиск по всем проектам сразу
./bin/listik memory "про что-то"    # поиск по долговременной памяти
./bin/listik show <id>              # полная карточка задачи
./bin/listik timeline               # лента событий
./bin/listik stats                  # сводка

./bin/listik projects                # репозитории доски: сколько задач, что скрыто
./bin/listik projects --add ~/Projects/Zoloto585/new-repo   # добавить репозиторий на доску
./bin/listik projects --archive old-repo                    # убрать с доски, задачи остаются
./bin/listik projects --unarchive old-repo                  # вернуть на доску
./bin/listik projects --remove old-repo                     # убрать совсем (с задачами — --force)
```

## Работа агента с задачей

```sh
export LISTIK_ACTOR=agent:dsh        # кто я; можно флагом --actor
./bin/listik ready                   # сначала: что вообще можно брать
./bin/listik ready --harness dsh     # только то, что этому harness разрешено на его этапе
./bin/listik claim <id>        --holder dsh/deepseek-flash --note "беру в работу"
./bin/listik heartbeat <id>    --holder dsh/deepseek-flash --note "пишу порцию B"
./bin/listik stage <id>        --holder dsh/deepseek-flash   # следующий этап конвейера
./bin/listik comment <id> "порция B: готово, коммит abc1234" -k journal
./bin/listik needs-owner <id>  "Как считать лимит: по заказам или по позициям?"
./bin/listik needs-owner <id> --clear "ответ"
./bin/listik done <id> -r "результат в одну строку"
```

## Контекст этапа

Задача может ссылаться на markdown-файлы: ТЗ (`spec_path`), чек-лист приёмки (`checklist_path`),
ревью (`review_path`), решение (`decision_path`, старое имя — `journal_path`). Пути задаются при
`new`/`set`:

```sh
./bin/listik new "Заголовок" --spec docs/specs/steps/step-01.md
./bin/listik set <id> checklist_path=docs/specs/steps/step-01.check.md
./bin/listik set <id> review_path=docs/specs/steps/step-01.review.md decision_path=docs/specs/steps/step-01.decision.md
```

`listik context <id> --stage <этап>` собирает компактный, побайтно стабильный срез задачи под
конкретный этап конвейера — вместо всей карточки и файлов целиком: карточку, критерии приёмки,
зависимости, отобранные разделы документов, а на s4 ещё и последний вердикт, журнал и
состояние рабочего дерева (на s3 этих трёх полей нет). Он же используется harness-протоколом
(`bin/listik-codex` и т.п.), поэтому ответ не должен меняться между вызовами с одними и теми же
аргументами.

```sh
./bin/listik context <id> --stage s2-review --format json                 # ТЗ и чек-лист целиком
./bin/listik context <id> --stage s3-impl --portion "Раздел 2" --format json  # конкретная порция ТЗ
```

Подробный контракт по этапам (что входит на s1/s2/s3/s4, лимиты, поведение параметра `stage`
на голом HTTP-эндпоинте) — в `API.md`, раздел `GET /api/tasks/{id}/context`.

Правила, без которых доска врёт, — один список, в `docs/harness-protocol.md` (тот же текст
ставится в блок `AGENTS.md` проектов командой `listik init-projects`).

Одна задача — один держатель. Если задача занята агентом, второй агент её не берёт
(это видно на доске по `holder` и heartbeat).


## Зависимости между задачами

Агент должен понимать, можно ли брать задачу, не дожидаясь конца предыдущей. Поэтому
«заблокирована» — не статус, который кто-то проставил и забыл снять, а **вычисляемое
состояние**: задача стоит, пока открыт хоть один жёсткий блокер (`blocks`, `blocked-by`,
`waits-for`), и освобождается сама, как только блокер закрыт.

```sh
./bin/listik ready                  # что не ждёт других — по приоритету
./bin/listik ready --project <slug> # то же по одному проекту
./bin/listik blocked                # кто кого ждёт, с держателем и временем простоя блокера
./bin/listik tree <id>              # чего ждёт задача и кто ждёт её
./bin/listik dep add <id> <блокер>  # связать: <id> ждёт <блокер> (от агента без --confirm — предложение)
./bin/listik dep confirm <id> <блокер>  # подтвердить предложение агента → жёсткая связь
./bin/listik dep suggested [--project]  # что агенты предложили, а человек ещё не подтвердил
./bin/listik dep rm  <id> <блокер>  # снять связь (и предложение — suggested-blocks — тоже)
./bin/listik cycles                 # циклы в зависимостях (задача, ждущая саму себя)
./bin/listik dep suggest <id>       # что задача упоминает в тексте, но связи нет
./bin/listik dep link <id>          # связать найденные упоминания (relates-to, не блокирует)
```

**Предложения от агента.** Жёсткую связь (`blocks`/`blocked-by`/`waits-for`/`conditional-blocks`),
поставленную агентом (`--actor agent:…` или `LISTIK_ACTOR=agent:…`) без `--confirm`, сервер не
делает жёсткой сразу — она записывается как `suggested-blocks` (мягкая связь, «предложенный
блокер»): на `ready`/`claim` она не влияет, но видна в `show` («предложены блокеры (не
подтверждены)»), в `context` на s1/s2 и в списке `dep suggested`. Человек подтверждает
(`dep confirm <id> <блокер>`, что равносильно `dep add <id> <блокер> --confirm`) — тогда
предложение становится жёсткой связью — или отклоняет (`dep rm <id> <блокер>`). Жёсткую связь,
которую ставит человек (или агент с `--confirm`), сервер не пропускает, если она замыкает цикл:

```
ошибка: жёсткая зависимость создаёт цикл: A → B → A
```

Мягкие связи — `parent-child`, `relates-to`, `related`, `discovered-from`, `duplicates`,
`supersedes`, `suggested-blocks` — работой не запрещают, но показываются в карточке как
«связано, не блокирует». Эпик отличается отдельно: `can_finish` говорит, закрыты ли его дети.

`claim` **отказывает** в трёх случаях — по блокеру, по чужому держателю и по занятому рабочему
дереву — и всегда говорит, в чём дело и что делать; каждый отказ печатается многострочно, как
его выдаёт CLI (переносы ниже не сжаты в одну строку). При запущенном сервере (обычный режим)
текст идёт с префиксом `ошибка <код>: …` (HTTP-ответ, `client.request`); при `--local` или
лежащем сервере — с префиксом `ошибка: …` (CLI работает с базой напрямую), сам текст отказа —
тот же.

Отказ по блокеру:

```
ошибка 400: задача zoloto585-orders-7iez заблокирована и брать её нельзя.
Ждём завершения: zoloto585-orders-6bqy — Связка 2=1 и совместной оплаты [open];
                zoloto585-orders-vtt5 — «2=1» недостижимы для новых заказов [open]
Варианты: взять сам блокер, поставить блокеру needs-owner,
          или осознанно обойти запрет: claim --force (останется в истории)
```

Отказ по чужому держателю:

```
ошибка: задача demo-bw62 уже удерживается dsh/one (0 мин). Варианты: дождаться,
listik release demo-bw62 (если держатель мёртв), или взять другую задачу из listik ready
```

Отказ по занятому рабочему дереву (лок держат только пишущие задачи — `s3-impl`, `s4-judge`,
без этапа — и только внутри одного проекта):

```
ошибка: рабочее дерево основное проекта demo занято задачей demo-x7wg (Задача A), держит
dsh/one 0 мин. Варианты: дождаться или listik release demo-x7wg, указать этой задаче другое
дерево (listik set demo-fprz worktree=/путь), или взять другую задачу
```

На доске: полоса **«можно брать»** над доской, бейджи «ждёт N» и «её ждут N» на карточках,
разбор блокеров в панели задачи. Данные — `board.ready`, `board.blocked_count` и
`deps_state` в карточке (см. `API.md`).

## Этапы конвейера (feature-pipeline)

`stage` идёт строго по цепочке и совпадает с четырьмя этапами скила:

| Этап | Что это | Кто обычно ведёт |
|---|---|---|
| `s1-spec` | ТЗ и чек-листы | Claude (Opus/Fable) |
| `s2-review` | Второе мнение по ТЗ | внешняя модель / критик |
| `s3-impl` | Реализация | dsh / grok / локальный субагент |
| `s4-judge` | Независимая проверка и коммит | Claude (судья) |
| `done` | задача закрыта | — |

На доске этап — это колонка, время на этапе — от перехода, а не от создания задачи.

Переходы бывают двух видов: sticky (`s1→s2`, `s3→s4`) — держатель остаётся, тот же harness
продолжает или явно передаёт карточку следующему; handoff (`s2→s3`, `s4→done`) — сервер сам
снимает держателя, дальше задачу берут через `ready` → `claim`. Красный вердикт на `s4-judge`
сервер сам возвращает на `s3-impl`. Кто на каком этапе что делает — `docs/harness-protocol.md`,
раздел «Роли по этапам».

**Возврат после красного вердикта и окно.** Держатель на возврате `s4-judge → s3-impl` не
меняется — это тот же `sticky`-переход, что и другие внутри цепочки, сервер только пишет
событие `stage s4-judge → s3-impl` с длительностью проверки. Если держатель не проявил
активности (`heartbeat`/повторный `claim`) в течение `return_window_hours` (24 ч по умолчанию,
настраивается в routing проекта), то при следующем `ready` или чужом `claim` держатель
снимается — сервер пишет событие `release` «истёк срок возврата после красного verdict…» — и
задача уходит в `ready`.

**Рабочее дерево.** `claim` пишущей задачи (`s3-impl`, `s4-judge` или задача вовсе без этапа)
отказывает, если в том же проекте то же `worktree` (пустая строка = основное дерево) уже
держит другой держатель другой пишущей задачи; `s1-spec`/`s2-review` рабочее дерево не занимают
и конфликтов не создают. Освободить дерево — `release` конфликтующей задачи, дать этой задаче
другое дерево (`listik set <id> worktree=/путь/к/дереву` или `PATCH /api/tasks/{id}` с
`worktree`) или взять другую задачу.

## Маршрутизация: кто какой этап ведёт

Какому harness разрешён какой этап — таблица `этап → harnesses` в `config.toml`, `[routing]`
(глобальные умолчания для всех проектов):

```toml
[routing]
default_process = ["s1-spec", "s2-review", "s3-impl", "s4-judge"]
return_window_hours = 24

[routing.harnesses]
s1-spec = ["claude", "dsh", "codex", "grok"]
s2-review = ["claude", "dsh", "codex", "grok"]
s3-impl = ["codex", "dsh", "claude", "grok"]
s4-judge = ["claude", "dsh", "codex", "grok"]

[routing.transitions]
"s1-spec:s2-review" = "sticky"
"s2-review:s3-impl" = "handoff"
"s3-impl:s4-judge" = "sticky"
"s4-judge:s3-impl" = "sticky-return"
"s4-judge:done" = "handoff"
```

Проект переопределяет это своей таблицей — `[routing.projects.<slug>.harnesses]` в
`config.toml`, либо `listik projects <slug> --routing '{"harnesses": {"s3-impl": ["dsh"]}}'` —
пишет переопределение в базу, и оно побеждает переопределение из `config.toml` (`'{}'`
сбрасывает переопределение в базе).
`listik projects <slug>` без `--routing` показывает действующую таблицу и источник
(`config.toml`, база, оба или «по умолчанию»). `listik ready --harness dsh` отдаёт только
задачи, чей этап разрешён harness `dsh` в routing их проекта — задача без этапа разрешена
любому harness. `claim --harness X` — необязательная проверка (совпадает по смыслу с фильтром
`ready`): если она не проходит, `claim` отказывает; harness из держателя задачи наружу не
выводится. `default_process` — подсказка диспетчеру, какой процесс вести по умолчанию; на
саму цепочку переходов `stage` она не влияет. Когда таблицу пора менять — см. «Периодическую
калибровку» ниже.

## Периодическая калибровка harness и размера задач

Routing нельзя считать вечной настройкой. Раз в 1–2 недели и после заметного изменения
модели или промпта просматривайте закрытые задачи по каждому harness и фиксируйте в журнале
проекта:

- какой максимальный объём задачи harness завершает за один проход (примерно строки diff,
  число файлов, число критериев и время работы);
- долю проходов, завершившихся без возврата из `s4-judge`, повторного запуска или ручного
  исправления;
- качество результата по acceptance, число найденных дефектов и типичные причины возврата;
- какие этапы harness выполняет устойчиво, а где ему требуется декомпозиция или второй агент.

После проверки обновляйте routing проекта: в pipeline отправляйте только задачи, которые
попадают в проверенный объём и качество конкретного harness. Большие, межслойные или плохо
оценённые задачи оформляйте обычной карточкой, разбивайте на независимые порции и связывайте
их зависимостями. Не запускайте pipeline ради самого pipeline: если задача меньше порога
калибровки и имеет ясную приёмку, её можно вести прямым `claim → heartbeat → done`.

Результат калибровки храните как решение в `comment -k journal` или отдельном decision-файле,
чтобы следующий harness видел не только выбранный маршрут, но и его измеренное основание.

## Векторы считаются сами

Пока сервер работает, фоновый поток раз в 45 секунд добирает векторы для новых и изменённых
задач, комментариев и заметок — свежая задача находится семантическим поиском без ручных команд.
Если ollama не поднят, поиск продолжает работать лексически, а сервер пишет пропуск в лог.
Отключить: `./bin/listik serve --no-embed`. Разово досчитать всё: `./bin/listik embed`.

## Перевод проектов на Listik

```sh
./bin/listik init-projects --dry-run   # что изменится в AGENTS.md/CLAUDE.md проектов
./bin/listik init-projects            # прописать правила Listik (идемпотентно)
./bin/listik init-projects --remove   # откатить
```

Блок вставляется между маркерами `<!-- BEGIN LISTIK -->` / `<!-- END LISTIK -->`, поэтому
чужой текст в файлах не трогается, а повторный запуск обновляет только сам блок. Тело блока —
весь текст `docs/harness-protocol.md`; после правки протокола повторный `init-projects`
идемпотентно обновляет блок во всех проектах.
Подробности перехода (выключить авто-экспорт beads и хуки) — `docs/beads-migration.md`.

## Импорт из beads

Старые `.beads`-трекеры импортированы разово, `bd` больше не пишет:

```sh
./bin/listik import-beads --dry-run   # посмотреть, что будет импортировано
./bin/listik import-beads            # 2909 задач из 26 проектов
```

При импорте:
- ID задач сохраняются (`external_ref` указывает на прежний beads-ID),
- дубликаты трекеров отсеиваются: `Dif/TN-feed-redesign` — старый каталог того же `tn`,
  что и `Dif/TelegramNews` (побеждает каталог с большим числом задач),
- задачи с `status=in_progress` без держателя импортируются как есть и честно висят
  в линии «нужен ты» — это старый мусор в статусах, а не текущая работа.

## Импорт WriterLLM

Задачи WriterLLM живут в отдельном beads-трекере на dolt (`~/Agents/WriterLLM/.beads`), вне
`~/Projects` — `import-beads` его не видит. Поэтому сначала выгрузка, затем отдельная команда:

```sh
cd ~/Agents/WriterLLM && bd export -o /tmp/writerllm-export.jsonl
# в выгрузке есть личные данные (имя, e-mail) — файл вне репозитория Listik, в git не кладётся
```

`bd list --json > файл.json` тоже подходит как источник, но без комментариев.

```sh
./bin/listik import-writerllm --source /tmp/writerllm-export.jsonl --dry-run   # что будет создано
./bin/listik import-writerllm --source /tmp/writerllm-export.jsonl             # импорт
./bin/listik import-writerllm --source /tmp/writerllm-export.jsonl             # повтор: создано: 0
./bin/listik import-writerllm --source /tmp/writerllm-export.jsonl --update    # доносит diff в журнал
```

При импорте сохраняются: старый ID (как `id`, если свободен, иначе новый — старый в
`external_ref`), заголовки, описания, acceptance, design, notes, статус (маппинг
`closed→done`, `deferred→open`), приоритет, даты `created_at/updated_at/started_at/closed_at`,
исполнитель, метки, комментарии, связи и результат закрытых задач (`close_reason`).

Повторный запуск по умолчанию — `skip` по ключу `(source, project, external_ref)`, недостающие
комментарии и связи дописываются; ничего не удаляется. `--update` дополнительно обновляет поля
содержания и пишет журнальный комментарий `[import-writerllm] update:` с diff; ручные
комментарии `--update` не трогает.

Отчёт печатает счётчики и примеры записей в тексте, `--json` — целиком, с сырой записью `raw`
у каждой ошибки. Код возврата — `1`, если в отчёте есть ошибки (подходит для CI); `--dry-run`
ничего не пишет. Ошибкой считается: битая строка JSON или запись без заголовка — она
пропускается, остальные записи импортируются; связь на задачу, которой нет ни в выгрузке, ни
в базе, — задача создаётся, ребро не создаётся, в отчёте — ошибка `dependency`. В обоих случаях
код возврата `1`, и повторный запуск даёт ту же ошибку, пока источник не исправлен:

```sh
./bin/listik import-writerllm --source tests/fixtures/writerllm/export.jsonl   # висячая связь → код 1
./bin/listik import-writerllm --source tests/fixtures/writerllm/broken.jsonl   # битый JSON → код 1
```

Найти старую задачу: `listik show WriterLLM-xxxx`, `listik search WriterLLM-xxxx --mode text`
(здесь `WriterLLM-xxxx` — условное обозначение старого ID, в фикстурах это, например, `WL-a1`).

Старые `in_progress` без держателя импортируются как есть и висят в линии «нужен ты» — так же,
как у импорта из beads (см. выше).

## Поиск

Гибрид: FTS5 (BM25) + векторы (`ollama` + `bge-m3`, 1024 измерения) + слияние RRF —
тот же рецепт, что в боевом `zoloto585-search`.

```sh
./bin/listik search "запрос" --mode text    # только лексика
./bin/listik search "запрос" --mode vector  # только семантика
./bin/listik embed                          # досчитать векторы для новых/изменённых записей
```

Векторы считаются локально: `ollama serve` + `ollama pull bge-m3`. Если ollama не поднят,
поиск продолжает работать лексически, а не падает.

Поиск находит не только совпадения в задачах и комментариях, но и фразы внутри разделов
индексируемых документов задачи (ТЗ, чек-лист, ревью, решение) — попадание в такой раздел
показывает «раздел: …» (заголовок и путь заголовков найденного места, `heading`/`breadcrumb`
в `hits[]`, см. `API.md`).

## Спецификация следующего этапа

План развития очереди, чанков ТЗ, общего протокола harnesses, импорта WriterLLM, UI и запуска
Codex собран в [docs/specs/listik-product.md](docs/specs/listik-product.md). Реализацию следует
вести по шагам из этого файла; ограничения и отложенные части описаны в
[docs/specs/self-critique.md](docs/specs/self-critique.md).

## Агентам: MCP и CLI

MCP-сервер (stdio) с инструментами `listik_search`, `listik_list`, `listik_show`,
`listik_create`, `listik_update`, `listik_context`, `listik_claim`, `listik_heartbeat`, `listik_stage`,
`listik_comment`, `listik_needs_owner`, `listik_done`, `listik_ready`, `listik_blocked`, `listik_can_take`, `listik_dep_tree`,
`listik_board`, `listik_stats`,
`listik_deps`:

```sh
claude mcp add listik -- /Users/dmitry.fomin/Projects/Listik/bin/listik mcp
```

CLI и MCP — это один и тот же доступ: если сервер не поднят, CLI работает с базой напрямую
(и предупреждает об этом в stderr).

Вопрос через `listik_needs_owner`/`needs-owner` ложится в историю карточки (комментарий
`question`/`answer`) и находится обычным `search`.

## Структура базы

`projects`, `tasks`, `comments`, `deps`, `events`, `actors`, `actor_aliases`,
`memories`, `embeddings`, `task_fts`, `comment_fts`, `memory_fts`, `documents`,
`document_chunks`, `document_chunk_fts` — полное описание полей и эндпоинтов в `API.md`.

## Тесты

```sh
python3 -m unittest discover tests -v
```

Тесты работают на временной базе (создаётся во временном каталоге на время прогона) без
запущенного сервера и без Ollama — не трогают `listik.db` и не требуют сети.
