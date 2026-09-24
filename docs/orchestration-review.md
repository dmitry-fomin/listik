# Разбор подходов к оркестрации агентов

Карточка: `listik-yf5z`. Задача — разобрать оркестратор
[darrenhinde/OpenAgentsControl](https://github.com/darrenhinde/OpenAgentsControl), сравнить с
общепринятыми подходами и с конвейером этого репозитория, и предложить, что делать, чтобы
**автора при выполнении задачи не тревожили вопросами**.

Каждый внешний факт — со ссылкой. Смотрелись исходники и первоисточники, не обзоры.

---

## 0. Короткий ответ

1. **OpenAgentsControl — не тот образец.** Это не автономный оркестратор, а наоборот:
   его центральное правило — `approval_gate` («спрашивай разрешения перед любой правкой»)
   и `stop_on_failure` («NEVER auto-fix without approval»). README прямо пишет: «Skip OAC if
   you want fully autonomous execution without approval gates». Оркестрации подзадач с
   слиянием там нет вовсе: git-ветки и worktree в его схеме не участвуют.
2. **«Наколхожено» — не про конвейер этого репозитория.** По декомпозиции, изоляции в
   worktree, зависимостям подзадач, независимой приёмке и уборке он совпадает с признанной
   практикой и местами её опережает (карточка как durable-состояние, `claim`/`heartbeat`,
   независимый судья). Самодельное — не архитектура, а *дисциплина вопросов*: протокол
   намеренно требует носить вопросы этапов автору.
3. **Вопросы убираются не сменой фреймворка, а четырьмя приёмами**, которые применяют все
   изученные подходы: контракт приёмки фиксируется до старта; вместо вопроса берётся дефолт
   с записью решения; истина — автотесты, а не мнение автора; вопрос не блокирует, а уходит
   в очередь.

Рекомендация — в разделе 7.

---

## 1. OpenAgentsControl — что это на самом деле

Смотрелся склон `github.com/darrenhinde/OpenAgentsControl` на версии `VERSION` = **0.7.1**
(≈1200 файлов). Это **не программа-оркестратор**, а библиотека markdown-промптов: агенты,
скилы, «контекстные» файлы и команды для opencode и Claude Code, ставится через `install.sh` /
`bin/oac.js`. Исполняемый код в репозитории есть, но не для оркестрации: это CLI установщика
(`packages/cli`), слой совместимости, фреймворк евалов (`evals/framework`) и два вспомогательных
CLI на TypeScript (`task-cli.ts`, `stage-cli.ts`).

### 1.1. Модель оркестрации

Роли: `OpenCoder` — основной агент-оркестратор (`.opencode/agent/core/opencoder.md`, 502 строки
промпта), плюс субагенты `TaskManager`, `CoderAgent`, `TestEngineer`, `CodeReviewer`,
`ContextScout` (поиск внутреннего контекста), `ExternalScout` (документация библиотек),
`BatchExecutor`. У Claude-Code-плагина (`plugins/claude-code/`) свой, урезанный набор: 7 агентов
и 12 скилов.

Заявленный «большой» процесс — 8 стадий
(`.opencode/skill/project-orchestration/workflows/8-stage-delivery.md`):
Architecture Decomposition → Story Mapping → Prioritization → Enhanced Task Breakdown →
Contract Definition → Parallel Execution → Integration & Validation → Release & Learning.
Стадии держит `stage-cli.ts` через `router.sh stage-init/stage-status/stage-complete/
stage-rollback/stage-validate/stage-abort`.

### 1.2. Как ставятся подзадачи

Скил `task-breakdown` (агент `task-manager`) пишет **файлы JSON** в `.tmp/tasks/<feature>/`:

* `task.json` — objective (≤200 символов), `context_files` (только стандарты),
  `reference_files` (только исходники проекта), `exit_criteria` (бинарные), `subtask_count`;
* `subtask_NN.json` — `id`, `seq`, `status`, **`depends_on`** (массив), **`parallel`** (bool),
  `suggested_agent` (CoderAgent / TestEngineer / CodeReviewer / …), `acceptance_criteria`
  («binary pass/fail only»), `deliverables` (конкретные пути файлов).

Порог применения: «feature touches 4 or more files». Атомарность — «completable in 1–2 hours».
Состоянием управляет `bash .opencode/skills/task-management/router.sh` →
`task-cli.ts`: `status`, `next` (задачи с удовлетворёнными зависимостями), `parallel`, `deps`,
`blocked`, `complete <feature> <seq> "msg"`, `validate`, `contracts`.

Это ровно **queue-of-work с графом зависимостей**, только состояние — JSON-файлы в `.tmp/`.
Явная мотивировка в самом скиле: «Subagents don't share memory. JSON files are the only
reliable state».

### 1.3. Параллель

Скил `parallel-execution`: «Make multiple task() calls in SINGLE message», «Both task() calls in
SAME message—not separate messages». Батчи по `parallel: true`, следующий батч не стартует, пока
не закрыт текущий. Оптимальный размер батча 2–4 (до 8). Прямой запрет: не параллелить задачи,
трогающие один файл, — «This causes merge conflicts—run sequentially instead».

### 1.4. Как агенты отчитываются

Текстом в отчёте субагента плюс `router.sh complete <feature> <seq> "msg"`, который переводит
`status` в `completed` в JSON. Никакой машинной проверки отчёта нет. Единственная попытка
автоматики — opencode-плагин `.opencode/plugins/coder-verification/index.ts`: на хуках
`tool.execute.before` / `tool.execute.after` он ловит вызовы `task` с
`subagent_type === "CoderAgent"` и **ищет в тексте результата подстроки** `"Self-Review"`,
`"✅ Types clean"`, `"Deliverables:"`, после чего показывает toast «checks passed» или «needs
attention». Это индикатор в интерфейсе, а не ворота: ничего не блокируется.

Сильная часть — скил `verification-before-completion`: «NO COMPLETION CLAIMS WITHOUT FRESH
VERIFICATION EVIDENCE», таблица «claim → чем доказывается», и отдельной строкой
«Agent completed → требуется VCS diff shows changes; недостаточно: agent reports "success"»,
«Trusting agent success reports» в списке red flags. То есть *недоверие к отчёту исполнителя*
там сформулировано как правило — но реализовано только как текст в промпте.

### 1.5. Слияние результатов — его нет

Это главная находка по репозиторию. `git worktree`, ветки под подзадачу и `git merge` в
оркестрации OAC **не участвуют**:

* поиск по всему складу даёт `worktree` только в `.opencode/command/worktrees.md` — это
  отдельная ручная слеш-команда «управь worktree для открытых PR» (`gh pr list` →
  `git worktree add ./tree/<branch>`), никак не связанная со скилами `task-breakdown` /
  `parallel-execution` / 8 стадиями;
* `git merge` встречается только в документах планирования (`docs/planning/…`) и в гайдах
  для контрибьюторов, не в исполняемом процессе.

Все параллельные `CoderAgent` правят **одно и то же рабочее дерево**. Именно поэтому в
`parallel-execution` конфликт лечится не слиянием, а запретом: «DO NOT parallelize tasks that
modify same file». «Интеграция» в стадии 7 — это не merge, а «wire components together» и
прогон интеграционных тестов в том же дереве. Коммит — отдельной ручной командой
(`.opencode/command/commit-openagents.md`).

Вывод: по критерию «сам сливает результаты» OAC даёт **ноль**. Изоляция подзадач в нём слабее,
чем в конвейере этого репозитория.

### 1.6. Где человек всё-таки нужен — и его там нужно очень много

Ощущение «он ничего не спрашивает» не подтверждается: OAC построен *вокруг* вопросов.

`.opencode/agent/core/opencoder.md`, блок `<critical_rules priority="absolute"
enforcement="strict">`:

* `approval_gate`: «Request approval before ANY implementation (write, edit, bash)»;
* `stop_on_failure`: «STOP on test fail/build errors — NEVER auto-fix without approval»;
* `report_first`: «On fail: REPORT error → PROPOSE fix → REQUEST APPROVAL → Then fix (never
  auto-fix)»;
* `incremental_execution`: «Implement ONE step at a time, validate each step before proceeding»;
* в конце файла: «NEVER skip approval gate», «NEVER auto-fix errors».

Стадия 2 процесса (`<stage id="2" name="Propose" required="true" enforce="@approval_gate">`):
«Goal: Get user buy-in BEFORE creating any files or plans» → «**Approval needed before
proceeding.**» Файлы не создаются до одобрения вообще. Скил `oac-approach` то же самое:
`<HARD-GATE>Do NOT write any code or make any file changes until the user has approved your
proposed approach.</HARD-GATE>`

README это не скрывает, а продаёт как фичу. Таблица сравнения: «Approval Gates: ✅ Always
required», «Execution Speed: ⚠️ Sequential with approval», «Error Recovery: ✅ Human-guided
validation». И прямым текстом:

> **Skip OAC if you:** Want fully autonomous execution without approval gates · Prefer
> "just do it" mode over human-guided workflows · Need multi-agent parallelization
> (use Oh My OpenCode instead)

То есть автор репозитория сам отправляет за автономностью и параллелью в другой проект. По
заявленному критерию автора карточки OAC — прямая противоположность нужного.

Единственное, что OAC делает для сокращения вопросов, — это **правило «допущение вместо
вопроса»** в `oac-approach`: «Assumptions over questions — State assumptions in proposal rather
than asking for every detail», «If you can make a reasonable assumption, state it in the
proposal instead of asking», «No context = hint, not block». Приём полезный, и он
перекликается с тем, что нужно автору, — но применён к *одному* вопросу «а как?»,
который всё равно упирается в обязательный аппрув.

### 1.7. Что у OAC стоит перенять

1. **`acceptance_criteria` бинарные и записаны в файл подзадачи до старта** — исполнителю не
   надо спрашивать «что считается готовым».
2. **Разделение `context_files` (стандарты) и `reference_files` (исходники)** и правило
   «предзагрузить общий контекст один раз до параллельного батча, не давать каждому искать
   заново».
3. **`verification-before-completion`** как отдельное короткое правило с таблицей
   «утверждение → доказательство», и в нём — «отчёт агента не доказательство, смотри дифф».
4. **`deliverables` как список путей** — даёт машинную проверку «сделано ли» без чтения кода.

---

## 2. Общепринятые подходы: как каждый решает три вещи

Ниже — только механика: (а) декомпозиция, (б) автономность без вопросов, (в) приёмка и слияние.

### 2.1. LangGraph

* **(а)** Граф задаёт разработчик (`StateGraph`, узлы, рёбра). Динамическая декомпозиция —
  **Send API**: условное ребро возвращает `[Send("worker", {...}) for x in state["items"]]`,
  число ветвей определяется в рантайме. Это канонический map-reduce
  ([graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)). Мультиагентные
  формы — supervisor, subagents (агент-как-инструмент), handoffs, router
  ([multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent)).
  `create_supervisor(agents=..., model=..., output_mode="full_history"|"last_message")`,
  хендофф — `Command(goto=agent, graph=Command.PARENT, update=...)`
  ([langgraph-supervisor-py](https://github.com/langchain-ai/langgraph-supervisor-py)).
* **(б)** Дефолт — полная автономность: пока в коде нет `interrupt()` или
  `interrupt_before`/`interrupt_after`, граф не останавливается ни на чём.
  `interrupt(payload)` останавливает граф, payload выходит наружу через `result["__interrupt__"]`,
  возобновление — `Command(resume=value)`; обязателен checkpointer и `thread_id`
  ([interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)). Важная деталь: при
  resume **узел перезапускается с начала**, поэтому код до `interrupt()` обязан быть
  идемпотентным.
* **(в)** Слияние результатов воркеров делает **не агент, а reducer состояния**:
  `bar: Annotated[list[str], add]` — параллельные узлы пишут свои куски, редьюсер их
  конкатенирует (graph API). Встроенной «приёмки» нет: это ваш узел-агрегатор либо supervisor;
  retry — условное ребро назад. Отказоустойчивость — чекпоинты, режимы `durability="sync"|
  "async"|"exit"` ([durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)).

### 2.2. OpenAI Agents SDK (и его предшественник Swarm)

* **(а)** Два примитива: **handoff** — полная передача управления, для модели это инструмент
  `transfer_to_<agent>` ([handoffs](https://openai.github.io/openai-agents-python/handoffs/));
  **agents-as-tools** — `agent.as_tool(...)`, оркестратор остаётся у руля
  ([tools](https://openai.github.io/openai-agents-python/tools/)). Дока прямо делит стратегии на
  «оркестрация через LLM» и «оркестрация через код» (структурированные выходы → ветвление
  кодом, цепочки, `asyncio.gather`, петля «исполнитель + оценщик»)
  ([multi-agent](https://openai.github.io/openai-agents-python/multi_agent/)). В Swarm хендофф был
  ещё проще: функция **возвращала другого агента**
  ([openai/swarm](https://github.com/openai/swarm), помечен deprecated).
* **(б)** Дефолт — автономно: `Runner.run()` крутит цикл до финального вывода, единственный
  ограничитель — `max_turns`. HITL привязан **к инструменту**, а не к процессу:
  `needs_approval=True` (можно колбэком); прогон паузится, `RunResult.interruptions` содержит
  `ToolApprovalItem`, решение — `state.approve(..., always_approve=True)` / `state.reject(...)`,
  дальше `Runner.run(agent, state)`. Состояние сериализуемо (`state.to_string()` →
  `RunState.from_string`), так что согласование может идти часами и в другом процессе
  ([human in the loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)).
  То есть «вопрос человеку» здесь — не блокировка воркера, а сохранённое состояние.
* **(в)** Автоматические ворота — **guardrails**: `@input_guardrail` / `@output_guardrail`,
  возврат `GuardrailFunctionOutput(tripwire_triggered=True)` мгновенно бросает исключение
  ([guardrails](https://openai.github.io/openai-agents-python/guardrails/)). Важное ограничение:
  input-гардрейл срабатывает только для первого агента цепочки, output — только для дающего
  финальный ответ; промежуточные никто не проверяет. Self-verification — паттерн
  «исполнитель + оценщик», агрегация параллельного — кодом.

### 2.3. CrewAI

* **(а)** `Process.sequential` — порядок задач задан человеком, выход одной идёт в `Task.context`
  следующей. `Process.hierarchical` — задачи **не назначены заранее**, распределяет
  менеджер (`manager_llm` или свой `manager_agent`), и он же отвечает за planning, delegation и
  validation ([processes](https://docs.crewai.com/en/concepts/processes)). `allow_delegation=True`
  добавляет агенту инструменты `Delegate work to coworker` и `Ask question to coworker`
  ([collaboration](https://docs.crewai.com/en/concepts/collaboration)). Отдельно
  `Crew(planning=True, planning_llm=...)` — `AgentPlanner` расписывает задачи по шагам перед
  каждой итерацией ([planning](https://docs.crewai.com/en/concepts/planning)).
* **(б)** Дефолт автономен; HITL — **флаг на задаче**: `Task(..., human_input=True)`
  ([tasks](https://docs.crewai.com/en/concepts/tasks),
  [human input on execution](https://docs.crewai.com/en/learn/human-input-on-execution)).
  Durability — только у Flows: декоратор `@persist`, режимы fork/resume
  ([flows](https://docs.crewai.com/en/concepts/flows)).
* **(в)** Единственный из трёх, где **приёмка и retry встроены в примитив задачи**:
  `guardrail` / `guardrails` — либо Python-функция (принимает task output, возвращает
  `(bool, Any)`, детерминированно), либо строковое описание критерия, которое судит LLM;
  при провале задача переигрывается, число попыток `guardrail_max_retries` (по умолчанию 3).
  Плюс структурная валидация `output_pydantic` / `output_json` (tasks). В иерархии финальную
  приёмку делает менеджер. Слияние в `sequential` механическое: выход → контекст следующей.

### 2.4. AutoGen 0.2 / AG2

* **(а)** Явной декомпозиции как объекта нет. Либо человек режет работу в цепочку
  `initiate_chats()` (где `summary_method` предыдущего чата становится carryover следующего),
  либо резка эмерджентна: Planner-агент пишет план текстом, а `GroupChatManager` с
  `select_speaker_method="auto"` раздаёт ходы
  ([conversation patterns](https://microsoft.github.io/autogen/0.2/docs/tutorial/conversation-patterns/)).
  В AG2 это формализовано в handoffs: `add_llm_conditions`, `add_context_conditions`,
  `set_after_work`, цели `AgentTarget`, `NestedChatTarget`, `TerminateTarget`, `AskUserTarget`
  ([handoffs](https://docs.ag2.ai/latest/docs/user-guide/advanced-concepts/orchestration/group-chat/handoffs/)).
* **(б)** Единственный переключатель автономности — `human_input_mode` у `ConversableAgent`:
  `ALWAYS` (спрашивает на каждое сообщение), `TERMINATE` (только на терминирующем сообщении или
  при достижении `max_consecutive_auto_reply`), **`NEVER`** («никогда не спрашивает; разговор
  останавливается, когда число авто-ответов достигло `max_consecutive_auto_reply` или когда
  `is_termination_msg` истинно»)
  ([reference](https://microsoft.github.io/autogen/0.2/docs/reference/agentchat/conversable_agent/),
  [HITL](https://docs.ag2.ai/latest/docs/user-guide/basic-concepts/human-in-the-loop/)).
  То есть **вместо вопроса подставляется жёсткий лимит**: `max_consecutive_auto_reply`,
  `max_round`, `max_turns`, стоп-строка `is_termination_msg`. Механизма «вопрос → асинхронный
  ответ» нет.
* **(в)** Приёмки как фазы в ядре нет. Работают три механики: Critic-агент в чате с вердиктом
  текстом и терминацией по стоп-строке; **тесты как истина** — `UserProxyAgent` с
  `code_execution_config` исполняет сгенерированный код и возвращает stdout/exit code
  сообщением, и агент правит по traceback (это и есть retry-петля); **слияние** —
  `SocietyOfMindAgent(chat_manager, response_preparer)`: внутренний GroupChat спрятан как
  «inner monologue», наружу отдаётся один ответ
  ([SocietyOfMindAgent](https://microsoft.github.io/autogen/0.2/docs/reference/agentchat/contrib/society_of_mind_agent/)).

### 2.5. Anthropic: паттерны и Claude Agent SDK

**«Building effective agents»**
([статья](https://www.anthropic.com/engineering/building-effective-agents)) — пять
workflow-паттернов, из которых для нашей задачи важны три:

* *prompt chaining* — шаги друг за другом с **программными** воротами между ними (код, не
  LLM-судья);
* *orchestrator-workers* — «центральная LLM **динамически** разбивает задачу, делегирует
  worker-LLM и синтезирует их результаты»; подзадачи не предзаданы;
* *evaluator-optimizer* — «одна LLM генерирует ответ, другая даёт оценку и обратную связь **в
  цикле**»; требует явных критериев оценки. Это автономный retry без человека.

**Multi-agent research system**
([статья](https://www.anthropic.com/engineering/multi-agent-research-system)) — та же схема в
продакшне:

* **(а)** lead agent строит стратегию и спавнит субагентов. Каждому даётся **явная цель, формат
  вывода, рекомендуемые инструменты и границы задачи** — «без детальных описаний задач агенты
  дублируют работу, оставляют пробелы или не находят нужное». Масштаб прописан в промпте:
  простой факт — 1 агент и 3–10 вызовов; сравнение — 2–4 субагента по 10–15 вызовов; сложное —
  «более 10 субагентов с явно поделённой ответственностью».
* **(б)** Человека в цикле нет вообще. Вместо вопроса: план сохраняется в память (контекст сверх
  200k обрезается), большие выходы уезжают во внешнее хранилище, наверх идут лёгкие ссылки; при
  сбое инструмента «сообщить агенту, что инструмент падает, и дать ему адаптироваться, работает
  удивительно хорошо»; регулярные чекпойнты и возобновление с места остановки вместо перезапуска.
* **(в)** Приёмка — отдельная роль: `CitationAgent` прогоняет отчёт и расставляет ссылки.
  Оценка — LLM-as-judge по рубрике (фактическая точность, точность цитирования, полнота,
  качество источников, эффективность инструментов) → 0.0–1.0 + pass/fail, стартовать можно с
  ~20 запросов. Человеческое тестирование остаётся, но **на выборке, не в цикле** — оно поймало
  систематический сдвиг к SEO-фермам против авторитетных PDF. Цена: мультиагент жжёт ~15×
  токенов против чата.

**Claude Agent SDK / Claude Code.** Здесь механика (б) самая богатая и она нам прямо доступна.

* Субагенты: `AgentDefinition` с `description`, `prompt`, `tools`/`disallowedTools`, `model`,
  `maxTurns`, `background`, `effort`, `permissionMode`
  ([subagents](https://code.claude.com/docs/en/agent-sdk/subagents)). Изоляция контекста:
  не-fork субагент стартует с чистого контекста, **единственное, что переходит от родителя, —
  строка prompt**, а наружу возвращается только финальное сообщение. Это и есть встроенное
  «слияние»: промежуточные tool-результаты в родителя не попадают. Границы:
  `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` (3), `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (20),
  `maxBudgetUsd`.
* Порядок проверки прав строго:
  **hooks → deny → ask → permission mode → allow → `canUseTool`**
  ([permissions](https://code.claude.com/docs/en/agent-sdk/permissions)). Режимы: `default`,
  `acceptEdits`, `bypassPermissions`, `plan`, `auto` (решает модель-классификатор) и
  **`dontAsk`** — «любой запрос, который спросил бы человека, — отказ», `canUseTool` не
  вызывается вовсе. Менять на ходу — `setPermissionMode()`.
* **Hooks как замена вопросу** ([hooks](https://code.claude.com/docs/en/hooks)). Ключевые для нас:
  * `PreToolUse` → `permissionDecision: allow|deny|ask` + `permissionDecisionReason`, плюс
    `updatedInput` и `additionalContext`. **`deny` — мягкий блок: модель получает причину и
    пробует иначе, без человека**; `ask` — прерывание на человека.
  * `PermissionDenied` может вернуть `{"hookSpecificOutput":{"retry":true}}` — разрешить ретрай
    без вопроса.
  * **`Stop` / `SubagentStop` могут вернуть `continue: true` и `additionalContext` — то есть не
    дать агенту остановиться, пока критерий не выполнен.** Это приёмка на уровне харнесса
    («тесты не зелёные — продолжай»), без участия человека.
  * `PostToolUse` возвращает `additionalContext` — канал, чтобы подсунуть агенту вывод линтера
    или тестов сразу после правки.
  * `TeammateIdle` / `TaskCreated` / `TaskCompleted`: «Exit with code 2 to prevent completion and
    send feedback» — те же ворота для команд агентов
    ([agent teams](https://code.claude.com/docs/en/agent-teams)).

**Родная параллель и слияние в Claude Code.** Три уровня
([worktrees](https://code.claude.com/docs/en/worktrees),
[agent teams](https://code.claude.com/docs/en/agent-teams)):

1. `claude --worktree <имя>` — сессия в своём git worktree под `.claude/worktrees/<имя>/` на
   ветке `worktree-<имя>`; `worktree.baseRef: "head"` — бранчить от текущего HEAD, а не от
   дефолтной ветки; `.worktreeinclude` — копировать в дерево gitignore-файлы вроде `.env`.
   Изоляция **принудительная**: харнесс блокирует `Edit`/`Write` в основной чекаут, bash с
   рабочим каталогом в основном чекауте и попытки увести git туда через `git -C`, `--git-dir`,
   `GIT_DIR`, `cd`.
2. `isolation: worktree` во фронтматтере субагента — каждый субагент в своём временном дереве,
   убирается автоматически, если правок не было; пока агент работает, харнесс держит
   `git worktree lock`.
3. **Agent teams** (экспериментально, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`): lead + товарищи,
   у каждого свой контекст, **общий список задач с зависимостями** (`~/.claude/tasks/<team>/`),
   почтовые ящики JSON (`~/.claude/teams/<team>/inboxes/<agent>.json`). «Task claiming uses file
   locking to prevent race conditions», «when a teammate completes a task that other tasks depend
   on, it unblocks the dependent tasks without any action from you». Товарищи сами берут
   следующую незанятую незаблокированную задачу. Важное для критерия автора: «Teammate permission
   prompts appear in the lead session, so approve them there yourself» — то есть без
   `--dangerously-skip-permissions` у лида вопросы **сходятся к человеку**, и лечится это
   предварительным allowlist'ом. Исключение специально сделано для планов: «Claude Code approves
   the plan in the lead's session as soon as the request arrives, without the lead reviewing it».

**Ближайший к запросу автора образец — «Building a C compiler with a team of parallel Claudes»**
([Anthropic](https://www.anthropic.com/engineering/building-c-compiler)). 16 агентов, Rust-компилятор C:

* bare git-репозиторий, у каждого агента свой docker-контейнер, он клонирует копию в
  `/workspace`;
* **claim через файл**: «Claude takes a "lock" on a task by writing a text file to
  `current_tasks/`»;
* **слияние делает сам агент**: «pull from upstream, merges changes from other agents, pushes its
  changes, and removes the lock. Merge conflicts are frequent, but Claude is smart enough to
  figure that out»;
* петля без остановки: «When it finishes one task, it immediately picks up the next»;
* **истина — верификатор**: «Claude will work autonomously to solve whatever problem I give it.
  So it's important that the task verifier is nearly perfect»;
* человек: автор «(mostly) walked away», но заранее вложился в инфраструктуру.

Это ровно та схема, которую автор карточки описал словами «запускает, сам сливает, тестирует,
ничего не спрашивает». И она стоит не на фреймворке, а на трёх вещах: файловый lock, merge
силами самого агента, **почти идеальный автоматический верификатор**.

### 2.6. Классические паттерны

* **Supervisor / orchestrator-workers.** Начальник владеет декомпозицией и маршрутизацией,
  воркеры друг о друге не знают. Слабое место: супервизор — и автор задачи, и судья; отсюда
  отдельная роль критика/судьи.
* **Blackboard** (Hearsay-II, 1970-80-е). Доска с задачами и частичными решениями, независимые
  «источники знания» и scheduler, который решает, кто работает. Рассуждение оппортунистическое:
  специалисты **сами смотрят на доску** и включаются, когда появился подходящий кусок
  ([blackboard system](https://en.wikipedia.org/wiki/Blackboard_system)). Декомпозиции сверху нет,
  она эмерджентна; человек не нужен по построению; тупик — «никто не может сработать», и это
  видно по доске. **Практический вывод: очередь задач в трекере с полями этап/держатель — это
  blackboard, а не supervisor. Истина живёт в доске, а не в контексте оркестратора.**
* **Plan-and-execute.** План → исполнение → **re-plan**. Академическая опора — Plan-and-Solve
  prompting ([arXiv:2305.04091](https://arxiv.org/abs/2305.04091)): «devise a plan» с разбиением
  на подзадачи лечит missing-step errors zero-shot CoT. **BabyAGI**
  ([репозиторий](https://github.com/yoheinakajima/babyagi),
  [архив оригинала](https://github.com/yoheinakajima/babyagi_archive)): список задач →
  `execution_agent` берёт первую → результат в векторное хранилище → `task_creation_agent`
  порождает новые задачи → `prioritization_agent` пересортирует → снова. Декомпозиция идёт
  непрерывно, человек не предусмотрен, **приёмки нет никакой** — и это главный дефект паттерна:
  задачи размножаются, результат никто не проверяет, цель дрейфует.
* **Queue-of-work.** Очередь + пул воркеров, аренда задачи (claim/lease), heartbeat, при падении
  задача возвращается в очередь; отдельный статус «на приёмке» и отдельный судья; retry —
  возврат в очередь со счётчиком, безнадёжное — в dead-letter. Ровно модель Temporal task queue
  ([understanding Temporal](https://docs.temporal.io/evaluate/understanding-temporal)).
  Автономность здесь **структурная**: воркер физически не может спросить — он может только
  закрыть, отпустить или пометить «нужен владелец», и очередь при этом не стоит.
* **Contract Net Protocol** (Reid G. Smith, 1980; FIPA SC00029H, стандарт с 2002-12-03,
  [спецификация](https://www.fipa.org/specs/fipa00029/SC00029H.pdf)). `cfp` с описанием задачи,
  условиями и **дедлайном** → `propose` / `refuse` → `accept-proposal` / `reject-proposal` →
  `inform-result` / `failure`. Дедлайн `cfp` заменяет вопрос: кто не ответил — выпал. Полезная
  деталь: **«отказаться от задачи» (`refuse`) — первоклассное сообщение, а не ошибка.**

### 2.7. Durable execution: как спрашивать, не блокируя

**Temporal** ([понимание](https://docs.temporal.io/evaluate/understanding-temporal)). Workflow —
оркестрация обычным кодом, Activity — единица работы, трогающая внешний мир. Workflow-код обязан
быть детерминированным, «потому что Temporal перезапускает его, чтобы восстановить состояние
после сбоя»; Event History — «полная упорядоченная запись всего, что произошло». Упал воркер —
новый реплеит историю и продолжает с места сбоя. Retry Policy, таймауты и heartbeat задаются
**конфигурацией, а не кодом**.

Главное для нашего критерия: живой workflow «принимает Signals и Updates и отвечает на Queries,
пока работает», плюс durable timers на «минуты или месяцы». Рецепт HITL
([cookbook](https://docs.temporal.io/ai-cookbook/human-in-the-loop-python)): `@workflow.signal`
принимает решение человека асинхронно, workflow висит на
`await workflow.wait_condition(lambda: self.current_decision is not None, timeout=...)`, ожидание
«не потребляет вычислительных ресурсов» и переживает краши и деплои, **а по истечении таймаута
управление возвращается в код и он решает сам**. В мультиагентном варианте
([блог](https://temporal.io/blog/durable-flexible-multi-agent-systems)) агент — child workflow,
родитель запускает несколько параллельно и синтезирует их результаты; «человек становится
асинхронным API»; и требование: «tool calls должны быть идемпотентны: retry, который повторно
списывает с карты или дважды бронирует водителя, — это баг».

**DBOS** ([архитектура](https://docs.dbos.dev/architecture)) — то же библиотекой: `@DBOS.workflow`
/ `@DBOS.step`, чекпойнты в Postgres («каждый вход workflow и выход каждого step»),
восстановление в три фазы, durable queues с лимитами конкурентности.
**Restate** ([AI agents](https://docs.restate.dev/use-cases/ai-agents)) — персистит шаги агента,
автоматический retry транзиентных ошибок, журнал всей линии исполнения и **awakeables** —
«durable waiting для человеческих решений с crash-proof таймаутами».

### 2.8. Сквозной вывод по трём вещам

* **(а) Декомпозиция** сводится к трём механикам: *статический конвейер* (цепочка шагов с
  воротами), *динамический оркестратор* (orchestrator-workers: lead режет по входу), *рынок или
  доска* (contract net, blackboard, очередь). Урок Anthropic конкретен: подзадаче нужны цель,
  формат вывода, рекомендуемые инструменты и **границы** — иначе дубли и дырки.
* **(б) Автономность.** Выключить HITL технически тривиально во всех фреймворках
  (`human_input_mode="NEVER"`, `permissionMode: dontAsk`, граф без user-ноды,
  `--dangerously-skip-permissions`). Содержательный вопрос — **что вместо вопроса**, и ответов
  ровно четыре:
  1. **бюджеты и лимиты** — `max_consecutive_auto_reply`, `maxTurns`, `maxBudgetUsd`, глубина и
     конкурентность субагентов, дедлайн `cfp`;
  2. **политика вместо человека** — hook `deny` с причиной (модель пробует иначе),
     `PermissionDenied` + `retry: true`, deny/allow-правила;
  3. **записать вопрос в общее состояние и не блокироваться** — статус «нужен владелец» в
     очереди, `refuse` в contract net;
  4. **durable-ожидание** — signal/awakeable с таймаутом и **дефолтным решением по таймауту**.

  Четвёртый — единственный, который не жертвует ни автономностью, ни участием человека.
* **(в) Приёмка.** Кто проверяет: **код и тесты** (программные ворота, исполнение кода,
  `Stop`-hook, не дающий остановиться) — единственный источник истины, не подверженный
  льстивости; **независимый LLM-судья** (evaluator-optimizer, rubric 0.0–1.0 + pass/fail,
  отдельный CitationAgent) — критично, чтобы судья не был автором; **человек** — на выборке, а
  не в цикле. Слияние во всех случаях делается так, что наружу отдаётся сводка, а не сырая
  история подработы (`SocietyOfMindAgent.response_preparer`, финальное сообщение субагента,
  синтез в parent workflow, reducer состояния).

---

## 3. Что уже сделано в этом репозитории: где совпало, где «наколхожено»

Смотрелись `plugins/feature-pipeline/references/pipeline-core.md` (844 строки),
`plugins/feature-pipeline/skills/*` (10 пресетов), `listik/launcher.py`, `listik/worktree.py`,
`listik/routes_store.py`, `routes.json`, `docs/harness-protocol.md`,
`plugins/listik/skills/listik/SKILL.md`.

### 3.1. Совпадает с признанной практикой (и местами опережает)

| Что сделано здесь | Признанный аналог |
| --- | --- |
| Карточка Listik как единственное состояние; этапы `s1-spec → s2-review → s3-impl → s4-judge → done`; `claim` / `heartbeat` / `release`; `holder_taken=false` отличает «выдана» от «взята» | **Queue-of-work / blackboard**: аренда задачи с heartbeat, доска как источник истины, воркер не держит состояние в контексте (Temporal task queue; blackboard) |
| `listik/deps.py`: «блокирован» — вычисляемое состояние; hard-связи гейтят `claim`/`done` | `depends_on` в подзадачах OAC; «shared task list» с зависимостями в agent teams; автоматическое разблокирование зависимых |
| Разбиение шага на порции с бумагами, границами правки и чек-листом приёмки до старта | OAC `subtask_NN.json` (`acceptance_criteria` бинарные, `deliverables` путями); Anthropic: подзадаче нужны цель, формат вывода, границы |
| Своё git worktree и ветка `task/<id>` на трек; `listik worktree`; запрет «в одном дереве — одна пишущая порция» | Claude Code `--worktree` / `isolation: worktree`; C-компилятор: контейнер и клон на агента |
| Этап 4 — **независимый судья**, который кодa не правит, прогоняет чек-лист, ищет срезанные углы в диффе и сам коммитит при зелёном | evaluator-optimizer; rubric-judge Anthropic; `CitationAgent`; CrewAI `guardrail` + менеджер-валидатор. Требование «судья не автор» здесь выполнено буквально |
| Критик ТЗ (этап 2) как отдельная роль, которая **не спрашивает никогда** | «Program gates» в prompt chaining; независимая критика до реализации |
| Счётчики `M` / `K` / `V`, «полный круг» один раз на порцию, ведутся в журнале, а не в голове («после `/clear` невидимый счётчик равен нулю») | `guardrail_max_retries=3` в CrewAI; Retry Policy в Temporal; `max_consecutive_auto_reply` в AutoGen. Эскалация по исчерпании попыток — ровно тот приём |
| `listik/launcher.py`: сервер сам поднимает процесс по `routes.command` без shell, пишет `launch_pid`/`launch_log`/`launch_exit_code`, поток слежения, `recover()` после перезапуска сервера | Durable-execution-лайт: внешнее состояние прогона переживает падение оркестратора (Temporal Event History, DBOS-чекпойнты) — в упрощённом виде |
| `needs-owner <id> "вопрос"` / `--clear "ответ"` и правило протокола: «Questions never stall the pipeline: keep working other tasks, apply the answer when it comes» | **Это ровно приём №3 и №4 из раздела 2.8**: вопрос уходит в общее состояние, работа не блокируется. Механизм уже есть и правильный |
| `--dangerously-skip-permissions` / `--always-approve` / `--dangerously-bypass-approvals-and-sandbox` во всех `routes.command` | `permissionMode: bypassPermissions`; `human_input_mode="NEVER"`. Прав у исполнителей не спрашивают уже сейчас |

Итого: **архитектура не самодельна.** Это blackboard + queue-of-work + orchestrator-workers +
evaluator-optimizer, собранные из правильных частей, с изоляцией в worktree и независимой
приёмкой — то есть сильнее, чем OAC, и сильнее, чем дефолт CrewAI/AutoGen.

### 3.2. Где действительно «наколхожено»

1. **Оркестратор — это промпт, а не программа.** 844 строки `pipeline-core.md` — прозаическая
   машина состояний: счётчики, маршрутизация вердиктов, имена бумаг, команды merge и уборки.
   Всё это исполняет модель, читая текст. В LangGraph/Temporal/AG2 те же переходы — код или
   конфиг, и они не забываются после `/clear`. Здесь защита от забывания — «счёт ведётся в
   журнале», то есть тот же промпт просит не забыть. Это единственная по-настоящему хрупкая
   часть.
2. **Приёмка не привязана к тестам машинно.** Судья — LLM с чек-листом; в репозитории есть
   `python3 -m unittest discover -s tests -t .`, `npm run typecheck`, `web/scripts/verify-*.mjs`, но
   нигде нет ворот вида «красные тесты → порция не может быть закоммичена». В изученных
   подходах это делается хуком (`Stop` / `SubagentStop` с `continue: true`) или программными
   воротами, а не доверием к отчёту. Правило OAC «отчёт агента не доказательство, смотри дифф»
   здесь не формализовано.
3. **Слияние — ручная прозаическая инструкция** (см. раздел 5). `listik/worktree.py` умеет
   только `ensure` (создать дерево); `merge`, `worktree remove`, `branch -d` в коде нет вовсе.
4. **Вопросы блокируют трек, хотя протокол Listik этого не требует.** Механизм отложенного
   вопроса (`needs-owner`) есть и правильный, но `pipeline-core.md` для этапов 1 и 3 предписывает
   другое: остановку и `AskUserQuestion`. Подробно — раздел 4.
5. **Нет бюджета на прогон.** `maxTurns` / `maxBudgetUsd` / лимита конкурентности в маршрутах
   нет; ограничитель — только терпение автора и `dsh`-обрыв на 15 минутах. В SDK эти рычаги
   есть готовыми.
6. **Дублирование схемы в трёх местах** (`routes.json`, промпт-инструкция в `command`, текст
   `pipeline-core.md`) — и оно уже кусало: `roles` не редактируются из UI и правятся только
   через `routes export/import`.

---

## 4. Откуда берутся вопросы человеку — и как их убирают

Это главный критерий автора, поэтому разбор точечный.

### 4.1. Источники вопросов в текущей схеме

`pipeline-core.md` перечисляет их явно; ниже — все, с причиной.

**A. Структурная причина.** Строка 10:

> Субагенты и внешние харнессы **не могут спросить автора сами**: инструмента
> `AskUserQuestion` у них нет. Поэтому вопрос этапа — это остановка с отчётом, а спрашивает
> автора и возобновляет исполнителя ответом ты. Это единственная причина, по которой
> оркестратор живёт в основном контексте, а не в ещё одном субагенте.

То есть вся конструкция «оркестратор в основном контексте» существует **ради вопросов**. Убери
вопросы — и оркестратор можно увести в фон.

**B. Кто спрашивает по протоколу.** Раздел «Протокол вопросов»: спрашивают только **автор ТЗ
(этап 1)** и **исполнитель (этап 3)**, отчётом с первой строкой `вопрос`. Критик и приёмка не
спрашивают никогда. Предел не поставлен: «`вопрос` — без предела, одним пакетом за заход».

**C. Обязательные остановки «стоп и вопрос автору»** — их в файле около десятка:

* канал харнесса не готов (шаг 0) — «стоп и вопрос автору, с дословным текстом ошибки»;
* имя дерева/бумаг занято — «стоп и вопрос автору; сам не перезаписывает и не переименовывает»;
* грязное основное дерево или незакоммиченные правки в дереве трека;
* дифф порции пуст, а журнал молчит;
* код трека появился в основном дереве;
* конфликт при `git merge` — «стоп и доклад автору… Сам его не разрешаешь»;
* `worktree remove` отказал из-за незакоммиченного — «решение принимает автор», `stash`/`clean`
  запрещены;
* `branch -D` — «это ответ автора на вопрос "удалить с потерей неслитого?"»;
* **первое нарушение границ порции** (`V = 1`) — «стоп, `AskUserQuestion` автору»;
* **полный круг** — «решение автора, не твоё: покажи ему, что случилось, и спроси, переписывать
  ТЗ порции или остановиться»;
* эпик под пресетом без писателя ТЗ — `needs-owner` и стоп.

**D. Запрет отвечать за автора.** Строка 21: «Вопрос этапа идёт автору дословно, и ответ автора
— этапу дословно. Ты не отвечаешь за автора, даже когда ответ кажется очевидным». В таблице
ошибок: «Ответил на вопрос этапа сам | показалось очевидным | автор отвечает на всё».

**E. Права.** Сами права уже не спрашиваются (`--dangerously-skip-permissions` и аналоги во всех
маршрутах), так что этот источник закрыт. Но в родных agent teams он вернулся бы: «Teammate
permission prompts appear in the lead session».

Замечание в память автора («Автоприём критики в конвейере»: блокирующие замечания критика
принимать самому, без `AskUserQuestion`; вопросы этапов 1/3 — как раньше) — это уже один шаг в
нужную сторону, сделанный вручную для одного источника (C-критик). Остальные источники живы.

### 4.2. Чем это убирают в изученных подходах

Шесть приёмов, все со ссылками из раздела 2. Против каждого — что он закрывает здесь.

1. **Контракт приёмки фиксируется до старта, а не выясняется в процессе.**
   OAC: `acceptance_criteria` «binary pass/fail only», `deliverables` — конкретные пути,
   `exit_criteria` в `task.json`. Anthropic: подзадаче даются цель, формат вывода и границы,
   иначе «агенты дублируют работу и оставляют пробелы».
   *Закрывает здесь:* большую часть вопросов этапа 1 и первое нарушение границ (`V = 1`):
   границы перестают быть предметом переговоров, если они бинарны и записаны.
2. **Дефолт вместо вопроса, с записью решения.**
   OAC `oac-approach`: «Assumptions over questions — State assumptions in proposal rather than
   asking», «If you can make a reasonable assumption, state it in the proposal instead of
   asking», «No context = hint, not block». Temporal: по истечении таймаута «управление
   возвращается в код и он решает сам».
   *Закрывает здесь:* правило D («ты не отвечаешь за автора») в текущем виде **запрещает** этот
   приём. Инверсия правила — самое дешёвое изменение с самым большим эффектом: исполнитель
   обязан выбрать свой предпочтительный вариант и записать «решение: … основание: …» в журнал,
   а вопрос уходит в `needs-owner` как уведомление, не как блокировка.
3. **Self-verification и цикл «исполнитель + оценщик» вместо обращения к человеку.**
   Anthropic evaluator-optimizer: «одна LLM генерирует, другая оценивает и даёт обратную связь в
   цикле». CrewAI: `guardrail` + `guardrail_max_retries=3`. AutoGen: код исполняется и traceback
   возвращается агенту.
   *Закрывает здесь:* это уже есть (этапы 2 и 4, счётчик `K`). Не хватает только того, чтобы
   исход «полный круг» не обязательно шёл к автору (см. приём 5).
4. **Тесты как единственный источник истины.**
   C-компилятор: «Claude will work autonomously… So it's important that the task verifier is
   nearly perfect». Claude Code hooks: `PostToolUse` с `additionalContext` подсовывает агенту
   вывод тестов; `Stop`/`SubagentStop` с `continue: true` **не дают остановиться, пока критерий
   не выполнен**; `PreToolUse` с `permissionDecision: deny` и причиной — мягкий блок, «модель
   получает причину и пробует иначе, без человека».
   *Закрывает здесь:* дефект 3.2.2. Хук `Stop`, который прогоняет
   `python3 -m unittest discover -s tests -t .` и `npm run typecheck` и при красном возвращает
   `continue: true` с выводом, снимает целый класс поводов доложить автору — и, главное,
   снимает нужду в доверии к отчёту.
5. **Эскалация только по исчерпании попыток, а не при первой развилке.**
   `max_consecutive_auto_reply` (AutoGen), `maxTurns`/`maxBudgetUsd` (SDK),
   `guardrail_max_retries` (CrewAI), Retry Policy (Temporal), дедлайн `cfp` (contract net).
   *Закрывает здесь:* архитектура уже такая (`M`/`K`/`V`), но у `вопрос` предела нет («без
   предела»), а `V = 1` и «полный круг» ходят к автору **до** исчерпания попыток. Сдвиг порогов
   — правка текста, не кода.
6. **Отложенный вопрос вместо блокировки.**
   Temporal signal + `wait_condition(timeout=…)` («человек становится асинхронным API», ожидание
   бесплатно и переживает краши, по таймауту — дефолт); Restate awakeables; OpenAI Agents SDK —
   сериализация состояния (`state.to_string()` → `RunState.from_string`), согласование «часами и
   в другом процессе»; contract net — `refuse` как первоклассное сообщение.
   *Закрывает здесь:* **механизм уже есть** — `needs-owner` плюс правило протокола «Questions
   never stall the pipeline». Не хватает того, чтобы `pipeline-core.md` для этапов 1 и 3
   использовал его вместо `AskUserQuestion`, и дефолта по таймауту: «ответа нет за N — берём
   предпочтительный вариант, пишем решение в журнал».

### 4.3. Честный вывод по критерию

Полная тишина при выполнении задачи достигается не переходом на другой фреймворк, а тремя
правками *правил*: (1) исполнитель обязан взять дефолт и записать решение вместо остановки;
(2) вопрос уходит в `needs-owner` и не блокирует трек; (3) автотесты подключены воротами
(`Stop`-hook), а не доверием к отчёту. Всё это — правка `pipeline-core.md` плюс один хук.

Цена, которую надо назвать прямо: автор перестаёт видеть развилки в момент выбора и начинает
видеть их **постфактум в журнале**. Это не бесплатно. Это ровно то, за что OAC ругает
автономные инструменты, и ровно то, ради чего автор карточки просит автономность.

---

## 5. Что происходит после готовности подзадачи: сливается или нет

Это отдельный вопрос из комментария к карточке. Ответ разный для трёх случаев.

### 5.1. В текущей схеме — конвейерные маршруты

Слияние **есть**, но оно прозаическая инструкция оркестратору, а не код. `pipeline-core.md`,
раздел «Сведение и уборка»:

```
git -C <основное дерево> merge --no-ff task/<id>
git worktree remove .worktrees/<id>
git branch -d task/<id>
```

Кто: **оркестратор** (основной контекст), по одному треку, проверяя после каждого. Когда: трек
закрыт — то есть зелёный вердикт приёмки по последней порции и `listik done` по карточке. Почему
`--no-ff`: «ветки треков расходятся между собой, и для второго перемотка невозможна по
построению». Коммитит порции не оркестратор, а **судья этапа 4** при зелёном вердикте; оркестратор
коммитит только бумаги шага. Пуш один — после последнего сведённого трека.

Правило уборки сформулировано отдельно: «работа шла в отдельном рабочем дереве, закрыта и слита
— дерево и его ветка удаляются в тот же заход», иначе «брошенные деревья копятся молча». Проверка
хвостов — `git worktree list`.

Границы: конфликт при merge — **стоп и доклад автору**, «Сам его не разрешаешь: значит треки
делили файл и независимыми не были». `worktree remove` отказал из-за незакоммиченного — не
`--force`, а вопрос автору; `branch -D` — только по ответу автора. `stash`, `clean`,
`checkout <файл>` запрещены. И важная оговорка: «Сведя все треки, прогони то, чем проверяется
проект… Зелёное **не** значит, что интеграция сделана — она отдельная задача автора».

В коде этого нет: `listik/worktree.py` содержит только `ensure()` (создать дерево, дописать
`.worktrees/` в `.gitignore`, вернуть путь/ветку/базу) и вспомогательные `is_dirty`,
`branch_exists`, `worktree_list`. Ни `merge`, ни `remove`, ни `branch -d` не реализованы.

### 5.2. В текущей схеме — прямые маршруты (`pi-glm`, `pi-deepseek`, `codex`, `grok`)

Слияния **нет**. Судя по `routes.json`, промпт прямого маршрута велит харнессу создать
`.worktrees/<task_id>` на ветке `task/<task_id>`, работать там, сделать `done`, потом
`git worktree remove` — и дословно: «(без `--force`; ветка `task/{task_id}` остаётся)». Ветка
остаётся неслитой, и никто в схеме её не сливает. Для одиночной выданной карточки это
осознанный выбор (автор смотрит и сливает сам), но из карточки это не видно: нет ни поля
«слито», ни события.

### 5.3. Как это делают в изученных подходах

* **OAC** — слияния нет вообще; все агенты правят одно дерево, конфликт предотвращается
  запретом параллелить задачи по одному файлу (раздел 1.5).
* **C-компилятор Anthropic** — сливает **сам агент**: «pull from upstream, merges changes from
  other agents, pushes its changes, and removes the lock. Merge conflicts are frequent, but
  Claude is smart enough to figure that out». Это прямая противоположность здешнему правилу
  «конфликт разрешает автор».
* **Claude Code `/batch`** — «split the change across 5 to 30 subagents, with each subagent
  working in its own worktree and opening a pull request»: слияние вынесено в **PR-очередь**, то
  есть решает не агент и не оркестратор, а обычный процесс ревью и merge queue
  ([worktrees](https://code.claude.com/docs/en/worktrees)).
* **Agent teams** — файловой изоляции по умолчанию нет, и дока честно пишет: «Two teammates
  editing the same file leads to overwrites. Break the work so each teammate owns a different
  set of files».
* **Фреймворки без git** (LangGraph, Agents SDK, CrewAI, AutoGen) «сливают» не код, а данные:
  reducer состояния, `Task.context`, `SocietyOfMindAgent.response_preparer`, финальное сообщение
  субагента. К слиянию кода это не относится вовсе — значит, ответа на вопрос автора там нет, и
  искать его надо в git-схемах: PR-очередь, merge силами агента, либо merge силами оркестратора
  (как здесь).

### 5.4. Что стоит поменять по слиянию

Три вещи, в порядке цены:

1. **Зафиксировать факт слияния в карточке.** Сейчас «слито» живёт только в голове оркестратора
   и в истории git. Поле или событие `merged` (ветка, хеш merge-коммита) делает состояние
   наблюдаемым и убирает класс «забытое дерево».
2. **Реализовать слияние и уборку кодом** — `listik worktree <id> --finish`: `merge --no-ff`,
   `worktree remove` без `--force`, `branch -d`, с отказом и понятной ошибкой на конфликте и на
   грязном дереве. Сейчас это шесть абзацев прозы, которые модель обязана исполнить верно после
   `/clear`.
3. **Решить, кто разрешает конфликт.** Здесь — автор, в C-компиляторе — агент. Промежуточный
   вариант: конфликт разрешает **судья** (он и так читает дифф и коммитит), автор узнаёт из
   журнала. Это уменьшает поводы тревожить автора, но именно здесь риск тихой потери чужой
   правки самый высокий, так что вариант «оставить автору» — защитимый.

---

## 6. Предложение автору: четыре варианта

Формат результата (этот документ) и адресат внедрения выбраны так, как автор оставил на
усмотрение: адресат — **`plugins/feature-pipeline` + `listik/worktree.py` + один хук**, не
переписывание Listik под чужой фреймворк. Ниже четыре варианта, от дешёвого к дорогому.

### Вариант А. Тишина правилами: «дефолт вместо вопроса»

**Что делается.** Правка только текста `pipeline-core.md`, ни строки кода:

1. Инвертировать правило D: исполнитель этапов 1 и 3 **обязан** взять свой предпочтительный
   вариант и записать `решение: … основание: …`; отчёт `вопрос` как остановка отменяется.
2. Вопрос уходит не в `AskUserQuestion`, а в `listik needs-owner` — как уведомление, а не
   блокировка (протокол Listik это уже разрешает: «Questions never stall the pipeline»).
3. Перевести обязательные «стоп и вопрос автору» в решения по умолчанию там, где дефолт
   безопасен и обратим: `V = 1` (первое нарушение границ) → сразу `K+1`, как `V = 2`; «полный
   круг» → переписать ТЗ порции автоматически, останавливаться только на втором круге; занятое
   имя дерева/бумаг → брать следующий свободный суффикс и писать строку в журнал.
4. Оставить неприкосновенными ровно три остановки — те, где дефолт необратим: конфликт merge,
   незакоммиченное в дереве при уборке, `branch -D`.

**Сложность понимания: низкая.** Это те же приёмы, что в OAC («assumptions over questions») и в
Temporal («по таймауту решает код»), и они описаны в разделе 4.2.
**Сложность внедрения: низкая** — один файл, обратимо построчно.
**Что даёт по критерию.** Убирает подавляющую часть вопросов: все развилки этапов 1 и 3 плюс
четыре из десяти обязательных остановок.
**Что ломает.** Автор видит развилки постфактум в журнале. Плохой дефолт обойдётся прогоном
порции. `pipeline-core.md` перестаёт быть согласован с памятью «Вопросы: чат + Listik» — эту
запись придётся переписать.

### Вариант Б. А + автотесты воротами (рекомендуемый)

**Что делается.** Вариант А плюс два механических куска:

1. **Хук `Stop` / `SubagentStop`** в `plugins/feature-pipeline`: прогоняет
   `python3 -m unittest discover -s tests -t .` и, если тронут `web/`, `npm run typecheck`; при красном
   возвращает `continue: true` с выводом — исполнитель не может закончить на красных тестах
   ([hooks](https://code.claude.com/docs/en/hooks)). Плюс `PostToolUse` с `additionalContext`,
   чтобы вывод тестов приходил агенту сразу после правки.
2. **Правило «отчёт не доказательство»** из OAC `verification-before-completion`, внесённое в
   обязанности судьи этапа 4 буквально: «Agent completed → требуется VCS diff shows changes;
   недостаточно: agent reports success».
3. Бюджет на прогон: `maxTurns` и, где это внешний харнесс с деньгами, лимит попыток на порцию
   в маршруте — чтобы «без предела» не осталось нигде.

**Сложность понимания: средняя.** Нужно разобраться с контрактом хуков (exit code 2, `continue`,
`additionalContext`) — это одна страница документации.
**Сложность внедрения: средняя** — один shell-хук плюс запись в `hooks.json` плагина; тесты в
репозитории уже есть и быстрые (без сервера и Ollama).
**Что даёт по критерию.** Снимает главный оставшийся повод тревожить автора после варианта А:
«я не уверен, что готово». Истиной становится тест, а не мнение. Именно этот кусок делает
C-компилятор Anthropic работоспособным («the task verifier is nearly perfect»).
**Что ломает.** Порция с заведомо красным тестом (например, правка в середине рефакторинга)
перестаёт закрываться — потребуется способ помечать такие порции явно. И хук замедляет каждый
`Stop`.

### Вариант В. Б + слияние и уборка кодом

**Что делается.** Вариант Б плюс перенос шести абзацев прозы в код:

1. `listik worktree <id> --finish` — `merge --no-ff`, `worktree remove` (без `--force`),
   `branch -d`, с осмысленными ошибками `errors.*` на конфликте и грязном дереве.
2. Поле/событие `merged` в карточке (ветка + хеш merge-коммита), чтобы «слито» было видно на
   доске, а не только в git.
3. Для прямых маршрутов (`pi-glm`, `pi-deepseek`, `codex`, `grok`) — решить и записать в карточку, сливается их
   ветка или остаётся автору; сейчас это молчание.

**Сложность понимания: средняя** — предмет знакомый, `worktree.py` уже рядом.
**Сложность внедрения: средняя-высокая** — код плюс схема БД (`db.SCHEMA`/`SCHEMA_VERSION` **и**
alembic-ревизия, по правилам репозитория), плюс тесты.
**Что даёт по критерию.** Снимает остановки уборки и «забытое дерево», делает слияние
наблюдаемым. Но самих вопросов убирает мало — это вариант про надёжность, не про тишину.
**Что ломает.** Изменение схемы БД и новая команда CLI, которую надо держать в двух ветках
(`server.py` и `client.local_call`).

### Вариант Г. Заменить прозаический оркестратор программой

**Что делается.** `pipeline-core.md` перестаёт быть машиной состояний: переходы, счётчики
`M`/`K`/`V`, маршрутизация вердиктов и merge уезжают в код — либо в `Workflow`-скрипт Claude
Code (оркестрация вне контекста диалога), либо в свой `listik pipeline`-раннер, либо в
LangGraph/Temporal, где чекпойнт и retry даны из коробки. Промпт остаётся текстом роли, не
процессом.

**Сложность понимания: высокая.** Придётся освоить чужую модель исполнения — детерминизм
workflow-кода, реплей, идемпотентность шагов (Temporal), или графы и редьюсеры (LangGraph).
**Сложность внедрения: высокая** — это переписывание того, что сейчас работает, с риском
потерять накопленные в прозе правила (границы правки, запрет `amend`, имена бумаг).
**Что даёт по критерию.** Максимум: счётчик и порог перестают зависеть от того, помнит ли модель
после `/clear`, а «спросить» становится сигналом с таймаутом и дефолтом — ровно приём №4 из
раздела 2.8. Плюс появляется цена и лимит прогона как параметр, а не как терпение автора.
**Что ломает.** Всё, что сейчас держится на том, что оркестратор — это Claude: гибкие развилки,
свободные отчёты, откат на нового субагента, продолжение сессии харнесса по id. И появляется
зависимость (Temporal/LangGraph) в проекте, который сегодня — чистая stdlib-Python без
менеджера пакетов.

---

## 7. Рекомендация

**Делать вариант Б (= А + автотесты воротами), вариант В — следом, вариант Г — не делать сейчас.**

Основание:

1. «Наколхожено» оказалось не в архитектуре. По изоляции в worktree, зависимостям, независимой
   приёмке и счётчикам попыток эта схема совпадает с признанной практикой и сильнее OAC, который
   автор взял за образец: у OAC нет ни слияния, ни worktree, а автономность у него прямо
   запрещена правилом `approval_gate`. Менять архитектуру не на что.
2. Вопросы к автору берутся не из архитектуры, а из **двух строк правил** — «ты не отвечаешь за
   автора» и «вопрос этапа — это остановка». Оба приёма, которыми это лечат (дефолт вместо
   вопроса; отложенный вопрос, не блокирующий работу), в Listik уже есть в виде `needs-owner` и
   правила «Questions never stall the pipeline». Нужно только начать ими пользоваться в
   конвейере. Это самый большой эффект за самую малую цену.
3. Один механический кусок всё-таки обязателен: без автоматического верификатора автономность
   вырождается в «агент сказал, что готово». Все изученные автономные схемы стоят на тестах как
   истине, а C-компилятор Anthropic называет качество верификатора решающим условием. В этом
   репозитории тесты уже есть, быстрые и без внешних зависимостей — не хватает только хука
   `Stop`, который не даёт закончить на красном.
4. Вариант Г заманчив (счётчики в коде, сигналы с таймаутом), но его выигрыш — надёжность
   перехода, а не тишина; а цена — переписать работающую машину и завести первую внешнюю
   зависимость. Разумнее вернуться к нему, когда вариант Б покажет, что именно ломается на
   практике.

Что делать по порядку: (1) переписать раздел «Протокол вопросов» и обязательные остановки в
`pipeline-core.md` по варианту А; (2) добавить хук `Stop` с тестами и правило «отчёт не
доказательство» судье; (3) проверить на одной реальной карточке и посмотреть в журнал, какие
дефолты агент выбрал сам и сколько из них автор бы поменял; (4) после этого — вариант В.

Каждый пункт — отдельная карточка; эта, `listik-yf5z`, остаётся исследованием.
