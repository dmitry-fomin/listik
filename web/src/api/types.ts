/**
 * Типы ответов API Listik. Источник правды — docs/API.md и listik/store.py.
 */
import type { RoleCell, RoleKey } from '@/lib/pipelines'

export type TaskStatus = 'open' | 'in_progress' | 'blocked' | 'review' | 'done' | 'cancelled'
export type PipelineStage = 's1-spec' | 's2-review' | 's3-impl' | 's4-judge'
export type TaskStage = PipelineStage | 'done' | null

/** Доска показывает только этапы конвейера; другие группировки сервера клиент не использует. */
export type GroupBy = 'stage'
export type SearchMode = 'hybrid' | 'text' | 'vector'

export interface Task {
  id: string
  project: string | null
  title: string
  description: string
  acceptance: string
  design: string
  notes: string
  result: string
  status: TaskStatus
  status_title: string
  stage: TaskStage
  stage_title: string | null
  priority: number
  priority_title: string
  issue_type: string
  assignee: string | null
  assignee_title: string
  holder: string | null
  holder_title: string
  /** Владелец задачи (`server.users`): чьи задачи видно и кому их можно брать; null — без владельца. */
  owner: string | null
  holder_note: string | null
  holder_at: string | null
  holder_age: string
  holder_hours: number | null
  idle_hours: number | null
  idle_age: string
  stage_at: string | null
  stage_age: string
  stage_hours: number | null
  stage_warn: boolean
  needs_owner: boolean
  labels: string[]
  spec_path: string | null
  journal_path: string | null
  /** Путь к чек-листу приёмки (`ready`/`checklist_path` из карточки, см. docs/API.md). */
  checklist_path?: string | null
  /** Путь к файлу ревью (`comment -k review` дублируется файлом на диске). */
  review_path?: string | null
  /** Путь к решению/вердикту (используется вместо `journal_path`, если задан). */
  decision_path?: string | null
  worktree: string | null
  branch: string | null
  blocked_by: string[]
  source: string
  external_ref: string | null
  created_at: string
  created_by: string | null
  updated_at: string
  updated_age: string
  started_at: string | null
  closed_at: string | null
  close_reason: string | null
  archived: boolean
  stale: boolean
  abandoned: boolean
  /**
   * Держатель подтвердил работу сам (`claim`/`heartbeat` от своего имени).
   * `false` при живом держателе — карточку выдали (`stage --holder`), а агент
   * ещё не запустился: доска показывает «выдана, не взята» вместо «держит».
   */
  holder_taken: boolean
  /** Кто поставил держателя: `agent:claude` у выдачи оркестратором, null у старых записей. */
  holder_assigned_by: string | null
  holder_assigned_by_title: string | null
  /**
   * Кто подтвердил работу сам: акторы событий `claim`/`heartbeat`, сделанных от
   * их имени. Выдача оркестратором и heartbeat чужой рукой не считаются; у
   * закрытой карточки это «кто выполнял». Никого — пустой массив.
   */
  worked_by: string[]
  /** Подписи `worked_by` через запятую; `''` при пустом списке. */
  worked_by_title: string
  assigned_at: string | null
  /** Сколько задача «выдана, но не взята» — от события выдачи, не от heartbeat. */
  assigned_age: string
  assigned_hours: number | null
  not_taken: boolean
  /** «Выдана, но не взята» дольше `board.assign_warn_minutes` (15 мин). */
  not_taken_warn: boolean
  // ── запуск процесса (см. docs/API.md, «Маршруты запуска»): пишет только сервер,
  // поля есть у каждой задачи, у незапущенных — null/false
  autostart: boolean
  launch_route: string | null
  /**
   * Можно ли ещё менять маршрут (`launch_route`): сервер разрешает только пока
   * задача заведена — без этапа, держателя и запущенного процесса.
   */
  route_editable: boolean
  launched_by: string | null
  launch_pid: number | null
  launched_at: string | null
  launch_log: string | null
  launch_exit_code: number | null
  launch_finished_at: string | null
  launch_error: string | null
}

export interface TaskComment {
  id: number
  author: string | null
  kind: string
  text: string
  created_at: string
}

export interface TaskDep {
  depends_on: string
  dep_type: string
  created_at?: string
}

export interface TaskDependent {
  issue_id: string
  dep_type: string
}

export interface TaskEvent {
  ts: string
  kind: string
  from_value: string | null
  to_value: string | null
  actor: string | null
  harness: string | null
  note: string | null
  duration_s: number | null
}

export interface TaskDetail extends Task {
  comments: TaskComment[]
  dependencies: TaskDep[]
  dependents: TaskDependent[]
  events: TaskEvent[]
  /**
   * Вердикт по зависимостям. Сервер отдаёт его с `GET /api/tasks/{id}`;
   * `null` — сервер не смог посчитать граф (в старых версиях поля нет вовсе).
   */
  deps_state?: DepsState | null
  /**
   * Файлы задачи (ТЗ/чек-лист/ревью/решение) — см. docs/API.md, «Модель задачи».
   * Мок API их не отдаёт, поэтому в клиенте всё построенное на этом поле
   * обязано работать и при `undefined`.
   */
  documents?: TaskDocument[]
  /**
   * Все дочерние карточки, включая закрытых (`GET /api/tasks/{id}` → `children[]`,
   * `listik/store.py: child_cards`). Поля может не быть — мок API его не отдаёт,
   * тогда дети берутся из `deps_state.children_open` (только незакрытые).
   */
  children?: TaskChild[]
}

/**
 * Дочерняя карточка из `TaskDetail.children` — та же форма, что у строки
 * `_CARD_LINK_KEYS` в `listik/store.py`; обязательны только id, название и статус.
 */
export interface TaskChild {
  id: string
  title: string
  status: TaskStatus
  project?: string | null
  status_title?: string
  stage?: TaskStage
  stage_title?: string | null
  priority?: number
  priority_title?: string
  holder?: string | null
  holder_title?: string
  spec_path?: string | null
  checklist_path?: string | null
  review_path?: string | null
  decision_path?: string | null
  created_at?: string
  updated_at?: string
}

/** Файл задачи (spec/checklist/review/decision) — GET /api/tasks/{id} → documents[]. */
export interface TaskDocument {
  id: number
  kind: 'spec' | 'checklist' | 'review' | 'decision' | string
  path: string
  revision: number
  content_hash: string | null
  title: string | null
  updated_at: string | null
  status: 'ok' | 'missing' | string
  error: string | null
  chunk_count: number
}

export interface ReadyResponse {
  tasks: ReadyTask[]
  cycles: string[][]
  generated_at: string
}

export interface BlockedResponse {
  tasks: BlockedTask[]
  generated_at: string
}

export interface BoardColumn {
  key: string
  title: string
  count: number
  wip: number
  needs_owner: number
  stale: number
  /** Сколько задач колонки выданы, но не взяты дольше `board.assign_warn_minutes`. */
  not_taken?: number
  /** Сколько задач колонки стоят из-за других (считается по графу на клиенте). */
  blocked?: number
  tasks: Task[]
}

/**
 * Справка о связанной задаче (блокер, ожидающий, ребёнок эпика, мягкая связь).
 * Форма одна и та же во всех местах, где сервер разбирает связи (deps._info).
 */
export interface DepInfo {
  id: string
  dep_type: string
  /** Человеческое имя типа связи: «блокирует», «родитель» и т.п. */
  dep_title?: string
  title: string
  status: string
  closed?: boolean
  project?: string | null
  stage?: TaskStage
  stage_title?: string | null
  holder?: string | null
  holder_title?: string
  holder_age?: string
  idle_age?: string
  stale?: boolean
  /** Связь ссылается на несуществующую задачу. */
  missing?: boolean
  /**
   * Связь пришла с противоположной стороны: карточка-источник так видит задачи,
   * найденные при работе над ней (`discovered-from`, «найдена при»).
   */
  incoming?: boolean
}

/** Вердикт «можно ли брать эту задачу» — POST /api/tasks/{id}/ready, deps_state в карточке. */
export interface DepsState {
  task_id: string
  title?: string
  status?: TaskStatus
  stage?: TaskStage
  ready: boolean
  claimable: boolean
  can_finish: boolean
  verdict: string
  reasons: string[]
  blocked_by: DepInfo[]
  waiting_for: DepInfo[]
  children_open: DepInfo[]
  parent: DepInfo | null
  soft_links: DepInfo[]
  holder?: string | null
  holder_title?: string
  holder_age?: string
  stale_holder?: boolean
}

/** Дерево связей: POST /api/tasks/{id}/deps без depends_on. */
export interface DepTreeEntry extends DepInfo {
  up?: DepTreeEntry[]
  down?: DepTreeEntry[]
}

export interface DepTree {
  task: DepInfo
  waits_for: DepTreeEntry[]
  waited_by: DepTreeEntry[]
  soft_links: DepInfo[]
}

/** Задача из /api/ready: можно взять прямо сейчас. */
export interface ReadyTask extends Task {
  /** Сколько других задач ждёт её завершения. */
  waiting_for_count: number
}

/** Задача из /api/blocked: стоит из-за других, с разбором по каждому блокеру. */
export interface BlockedTask extends Task {
  blockers: DepInfo[]
  /** Все блокеры стоят без движения — ждать молча бессмысленно. */
  blocked_by_stale: boolean
  /** Кто-то из блокеров занят (имя держателя). */
  blocked_by_holder: string | null
}

export interface Board {
  group_by: GroupBy
  columns: BoardColumn[]
  total: number
  needs_you: Task[]
  /** Что можно взять прямо сейчас: нет незакрытых блокеров и нет держателя. */
  ready: ReadyTask[]
  /** Сколько задач в выборке стоят из-за других. */
  blocked_count: number
  /** Циклы в зависимостях; нормально — пустой массив. */
  cycles: string[][]
  generated_at: string
}

export interface HealthEmbed {
  ok: boolean
  models?: string[] | string
  model?: string
  detail?: string
  error?: string
}

export interface Health {
  status: string
  version: string
  db: string
  counts: Record<string, number>
  embed: HealthEmbed | null
  now: string
  /** Режим сервера: `server` — есть список пользователей и владелец у задач, `local` — нет. */
  mode?: 'local' | 'server'
  /** Имена из `server.users`; в локальном режиме пусто, у старого сервера поля нет. */
  users?: string[]
  /** Кем представился этот запрос (заголовок `X-Listik-Owner`), null — никем. */
  owner?: string | null
}

export interface ProjectRow {
  slug: string
  title: string | null
  kind: string
  /** Каталог репозитория на диске (если известен). */
  path?: string | null
  /** Существует ли каталог прямо сейчас — считает сервер в `/api/projects`. */
  path_exists?: boolean
  git_remote?: string | null
  git_branch?: string | null
  color?: string | null
  n_tasks?: number
  n_open?: number
  n_wip?: number
  archived?: number
  /** Ответ `POST /api/projects`: проект создан именно этим запросом, а не обновлён. */
  created?: boolean
  /** Ответ `POST /api/projects`: каталог оказался git-репозиторием. */
  git?: boolean
  /**
   * Ответ `POST /api/projects`: исходный путь, если каталог лежал внутри
   * git-репозитория и его привели к корню (`git rev-parse --show-toplevel`).
   * `null` — путь уже был корнем (или это не git-репозиторий).
   */
  path_adjusted_from?: string | null
}

/**
 * Поля, которые правит доска через `PATCH /api/projects/<slug>`.
 *
 * Сервер принимает ещё `color`, `kind`, `archived` и `routing`, но у них на
 * доске свои места: скрытие — тумблер в списке репозиториев, маршрутизация —
 * `listik projects <slug> --routing`. Slug здесь нет намеренно: это ключ
 * проекта (по нему лежат задачи), и переименования у API не существует.
 */
export interface ProjectPatch {
  title?: string
  path?: string
}

export interface ProjectsResponse {
  projects: ProjectRow[]
  /** Корень, внутри которого ищутся проекты (`[import] projects_root`). */
  root: string
}

export interface ProjectRemoved {
  slug: string
  removed: boolean
  removed_tasks: number
}

// ── маршруты запуска: routes.json, GET /api/routes (см. docs/API.md) ─────────────

/**
 * Уровень маршрута — значение поля `icon` записи `routes.json`. Набор уровней и
 * правила фолбэка по ключу — `listik/routes.py` (`ROUTE_ICONS`, `fallback_icon`),
 * подписи и иконки — `lib/dictionaries.ts` (`ROUTE_ICONS`).
 */
export type RouteIconKey = 'xhigh' | 'high' | 'medium' | 'low' | 'xlow' | 'direct'

/** Способ исполнения маршрута (`routes.driver`): `skill` — один процесс на карточку, `swarm` — этапы отдельными процессами (listik-2gry). */
export type RouteDriver = 'skill' | 'swarm'

interface RouteBase {
  key: string
  title: string
  /** цена/квота/повод одной строкой — под названием пресета */
  hint: string
  /** показывать ли запись на доске; скрытая не выбирается и не ловится стрелками */
  visible: boolean
  /**
   * Уровень маршрута для иконки; `null` — уровня нет (сервер не вывел его из
   * `key`/`kind`). Необязательно: сервер до появления поля его не отдаёт.
   */
  icon?: RouteIconKey | null
  /**
   * Явный `icon` записи не принят сервером (`listik/routes.py`): уровень взят по
   * ключу, а здесь — причина. Поле есть только у таких записей; `icon: null`
   * вместе с ним значит «иконки нет» — доска рисует серый кружок с крестиком.
   */
  icon_error?: string | null
  /** Порядок записи (сквозной по всей таблице `routes`), серверный; из доски не меняется. */
  position: number
  /** Argv команды маршрута; `null` — команды нет (роли ещё не переввезены и т.п.). */
  command: string[] | null
  /** Способ исполнения; у `direct` всегда `skill` (роя без ролей нет). */
  driver?: RouteDriver
}

/**
 * Пресет конвейера: роли ТЗ/критик/исполнитель/судья. При `driver='swarm'`
 * (роевой конвейер, listik-2gry) ячейки — `SwarmRoleCell`, а не `RoleCell`:
 * различают их `isSwarmCell`/`isProviderCell` из `lib/pipelines.ts`.
 */
export interface PipelineRouteDef extends RouteBase {
  kind: 'pipeline'
  roles: Partial<Record<RoleKey, RoleCell | SwarmRoleCell | null>>
  /** Путь к `SKILL.md` относительно корня Listik; каталога скила нет — `null`. */
  skill_path?: string | null
  /** Каталог скила пропал (переименовали/удалили) — запись отдаётся скрытой (`visible:false`). */
  skill_missing?: boolean
}

/** Прямой маршрут: харнесс делает задачу целиком, без ролей. */
export interface DirectRouteDef extends RouteBase {
  kind: 'direct'
  /** Ключ харнесса из каталога (`agent:<key>`) — любой, не только встроенный. */
  harness: string
}

/**
 * Ячейка роли маршрута роя: исполнитель (`harness` — ключ каталога `harnesses`)
 * и его команда. Без `argv` этап запускается командой харнесса по умолчанию;
 * `null` у ключа роли — этап пропускается (listik-2gry).
 */
export interface SwarmRoleCell {
  harness: string
  argv?: string[]
  prompt?: string
}

export type SwarmRoles = Partial<Record<RoleKey, SwarmRoleCell | null>>

/** Маршрут роя: Listik водит карточку по этапам, на каждый — свой харнесс. */
export interface SwarmRouteDef extends RouteBase {
  kind: 'swarm'
  driver: 'swarm'
  roles: SwarmRoles
}

export type RouteDef = PipelineRouteDef | DirectRouteDef | SwarmRouteDef

/**
 * Маршрут, исполняемый роем: `kind='swarm'` или конвейер с `driver='swarm'`
 * (в группу «Рой» попадают оба — `swarmRoutesOf` в `lib/routes.ts`).
 */
export type SwarmLikeRoute = SwarmRouteDef | PipelineRouteDef

/** GET /api/routes: ошибка файла — `ok:false` с текстом, а не HTTP-ошибкой. */
export interface RoutesResponse {
  ok: boolean
  error: string | null
  path: string
  /**
   * Замечания, которые файл не отменяют (неизвестный `icon` записи): маршрут
   * работает, у записи посчитан фолбэк по ключу. Необязательно: сервер до
   * появления поля его не отдаёт.
   */
  warnings?: string[]
  routes: RouteDef[]
}

/**
 * Тело `POST /api/routes` для прямого маршрута (`kind="direct"`, listik-sjx3 порция `a`).
 * `key`, `title`, `harness`, `command` обязательны; `hint` по умолчанию `""`,
 * `icon: null` — запись без иконки. `visible` не передаётся: новый маршрут заводится
 * выключенным. Ключ собирает клиент — сервер его не выводит.
 */
export interface DirectRouteCreate {
  kind: 'direct'
  key: string
  title: string
  hint: string
  icon: RouteIconKey | null
  harness: string
  command: string[]
}

/**
 * Тело `POST /api/routes` для маршрута роя (`kind="swarm"`, listik-2gry):
 * `key`, `title`, `roles` обязательны (хотя бы одна роль с харнессом и
 * командой); `command` и `harness` сервер не примет — исполнитель живёт в
 * ячейках ролей.
 */
export interface SwarmRouteCreate {
  kind: 'swarm'
  key: string
  title: string
  hint: string
  icon: RouteIconKey | null
  roles: SwarmRoles
  visible?: boolean
}

/** Правка записи — PATCH /api/routes/{key}; `command` только у `kind=direct`. */
export interface RoutePatch {
  title?: string
  hint?: string
  icon?: RouteIconKey | null
  visible?: boolean
  command?: string[] | null
  /**
   * Расклад ролей целиком: у `kind=pipeline` ячейки скилового вида
   * (`{provider,label,title}`), у `kind=swarm` — `{harness,argv?,prompt?}`
   * или `null` (пропуск этапа).
   */
  roles?: Partial<Record<RoleKey, RoleCell | SwarmRoleCell | null>>
  /** Способ исполнения конвейера: `skill` (умолчание) или `swarm`. */
  driver?: RouteDriver
}

// ── каталог харнессов: GET/POST /api/harnesses, GET/PATCH /api/harnesses/{key} ──
// (см. docs/API.md «Харнессы», listik-2gry)

/** Вид харнесса: `exec` — процесс с командой, `manual` — ручная выдача без команды. */
export type HarnessKind = 'exec' | 'manual'

/** Харнесс каталога: исполнитель прямого маршрута или роли роя. */
export interface Harness {
  /** Ключ `^[a-z0-9][a-z0-9-]*$`; держатель на сервере — `agent:<key>`. */
  key: string
  label: string
  hint: string
  icon: string | null
  /** Команда по умолчанию (argv); `null` — команды нет (manual). */
  argv: string[] | null
  /** Промпт по умолчанию — последний аргумент команды; `null` — не задан. */
  prompt: string | null
  kind: HarnessKind
  /** Встроенный сид — не пользовательский. */
  builtin: boolean
  /** Показывать в списках выбора; выключенный остаётся в базе. */
  enabled: boolean
  position: number
  /** Где задействован: прямые маршруты и роли маршрутов роя (есть у GET). */
  used_by?: { route: string; kind: 'direct' | 'swarm'; role: string | null }[]
  created_at?: string | null
  updated_at?: string | null
}

export interface HarnessesResponse {
  harnesses: Harness[]
  /** Протокол роли роя — лаунчер ставит его последним аргументом при пустом `prompt` ячейки. */
  swarm_prompt?: string
}

/** Тело `POST /api/harnesses`. `key` обязателен; у `kind="manual"` команды нет. */
export interface HarnessCreate {
  key: string
  label?: string
  hint?: string
  icon?: string | null
  argv?: string[] | null
  prompt?: string | null
  kind?: HarnessKind
  enabled?: boolean
}

/** Правка харнесса — `PATCH /api/harnesses/{key}`; `key`/`kind`/`builtin` не правятся. */
export interface HarnessPatch {
  label?: string
  hint?: string
  icon?: string | null
  argv?: string[] | null
  prompt?: string | null
  enabled?: boolean
  position?: number
}

// ── помощник DeepSeek при создании задачи: GET /api/assistant/status,
//    POST /api/assistant/suggest (см. docs/API.md «Помощник DeepSeek») ─────────────

/** Поля формы, у которых есть кнопка помощника (белый список `assistant.FIELDS`). */
export type AssistantField = 'title' | 'description' | 'acceptance' | 'spec_path'

/** Уровни когнитивной сложности — ключи сервера, подписи — в lib/dictionaries.ts. */
export type AssistantComplexityLevel = 'low' | 'medium' | 'high'

/** GET /api/assistant/status: ключ DeepSeek наружу не отдаётся — только факт наличия. */
export interface AssistantStatus {
  enabled: boolean
  model: string
  base_url: string
  /**
   * Голосовой ввод (`[deepgram]` + `[assistant]` настроены). Необязательно:
   * сервер до появления голоса поля не отдавал, `undefined` — «выключено».
   */
  voice?: boolean
}

/** Контекст карточки, который уходит на сервер вместе с текстом поля. */
export interface AssistantContext {
  type?: string
  priority?: number
  project?: string
  title?: string
  description?: string
  acceptance?: string
  spec_path?: string
}

/** Предложенный маршрут: только из видимых записей `routes.json` (проверяет сервер). */
export interface AssistantRoute {
  key: string
  kind: RouteDef['kind'] | null
  title: string | null
  hint: string
  reason: string
}

export interface AssistantComplexity {
  level: AssistantComplexityLevel
  reason: string
}

/** Разобранное предложение модели: `null` — уровень/маршрут не приняты сервером. */
export interface AssistantSuggestion {
  /** Переписанный текст того поля, которое спросили. */
  text: string
  /** Критерии приёмки, которых нет в текущем тексте приёмки. */
  acceptance: string[]
  complexity: AssistantComplexity | null
  route: AssistantRoute | null
}

export interface AssistantSuggestResponse {
  field: AssistantField
  model: string
  suggestion: AssistantSuggestion
}

export interface AssistantSuggestRequest {
  field: AssistantField
  text: string
  context: AssistantContext
}

// ── голосовой ввод: POST /api/assistant/transcribe, POST /api/assistant/draft
//    (см. docs/API.md «Помощник DeepSeek» → «Голосом») ──────────────────────────

/** Тело `POST /api/assistant/transcribe`: запись целиком в base64, без `data:…;base64,`. */
export interface VoiceTranscribeRequest {
  audio_base64: string
  mime: string
}

/** Ответ `transcribe`: тишина — не ошибка, `transcript` пустой. */
export interface VoiceTranscribeResponse {
  transcript: string
  model: string
}

/** Тело `POST /api/assistant/draft`: рассказ человека словами. */
export interface VoiceDraftRequest {
  text: string
}

/** Маршрут черновика: только видимая запись `routes.json` (проверяет сервер). */
export interface VoiceDraftRoute {
  key: string
  kind: string | null
  title: string | null
  hint: string
  reason: string
}

/**
 * Черновик задачи. Все шесть полей есть всегда; поле, которое модель не
 * вывела или которое не прошло проверку, — `null`, поэтому UI обязан
 * показать ветку «не распознано».
 */
export interface VoiceDraft {
  project: string | null
  type: 'epic' | 'task' | 'bug' | null
  title: string | null
  description: string | null
  acceptance: string[] | null
  route: VoiceDraftRoute | null
}

/** Ответ `POST /api/assistant/draft`: черновик ничего не создаёт. */
export interface VoiceDraftResponse {
  model: string
  draft: VoiceDraft
}

export interface ActorRow {
  key: string
  title: string | null
  kind?: string
  note?: string | null
  n_tasks?: number
  n_held?: number
  harness?: string | null
  last_seen?: string | null
}

export interface FacetActors {
  key: string
  values: { key: string; title: string | null; kind?: string }[]
}

export interface MetaFacets {
  projects: string[]
  assignees: string[]
  holders: string[]
  statuses: string[]
  stages: string[]
  types: string[]
  actors?: FacetActors
}

export interface Meta {
  projects: ProjectRow[]
  actors: ActorRow[]
  facets: MetaFacets
  statuses: Record<string, string>
  stages: Record<string, string>
  priorities: Record<string, string>
}

export interface StatsProject {
  project: string
  total: number
  in_progress: number
  waiting: number
}

export interface StatsHolder {
  holder: string
  title: string
  count: number
}

export interface StatsActor {
  actor: string
  title: string
  count: number
}

export interface Stats {
  by_status: Record<string, number>
  by_stage: Record<string, number>
  by_project: StatsProject[]
  by_holder: StatsHolder[]
  by_actor: StatsActor[]
  stale: number
  needs_owner: number
  /** Закрыто за последние 7 суток и за 7 суток до них. */
  closed_7d: number
  closed_prev_7d: number
  closed_delta: number
  /** Закрытия по дням за 14 суток, от старых к свежим. */
  closed_by_day: { date: string; count: number }[]
  /** В работе/на проверке дольше 8 ч на текущем этапе. */
  long_stage: number
  running: Task[]
  generated_at: string
}

export interface SearchHit {
  kind: string
  doc_id: string | number
  rrf: number
  snippet: string
  author: string | null
}

export interface SearchResult {
  id: string
  project: string | null
  title: string
  status: TaskStatus
  stage: TaskStage
  holder: string | null
  actor: string | null
  actor_name: string | null
  priority: number
  issue_type: string
  updated_at: string
  labels: string[]
  snippet: string
  score: number
  hits: SearchHit[]
  needs_owner: boolean
}

export interface SearchResponse {
  query: string
  mode: SearchMode
  took_ms: number
  lexical_docs: number
  vector_docs: number
  count: number
  results: SearchResult[]
}

export interface TimelineItem {
  ts: string
  kind: string
  from_value: string | null
  to_value: string | null
  actor: string | null
  actor_title: string | null
  harness: string | null
  note: string | null
  duration_s: number | null
  task_id: string | null
  title: string | null
  project: string | null
  stage: TaskStage
  status: TaskStatus | null
  age: string
}

export interface TasksPage {
  total: number
  limit: number
  offset: number
  tasks: Task[]
}

export interface TaskQuery {
  project?: string
  status?: string
  stage?: string
  assignee?: string
  holder?: string
  needs_owner?: boolean
  type?: string
  label?: string
  text?: string
  include_closed?: boolean
  include_archived?: boolean
  limit?: number
  offset?: number
  order?: 'updated' | 'created' | 'priority' | 'stage'
}

/** Поля, которые принимает PATCH /api/tasks/{id} (store.UPDATABLE). */
export interface TaskPatch {
  title?: string
  description?: string
  acceptance?: string
  design?: string
  notes?: string
  result?: string
  status?: TaskStatus
  stage?: string
  priority?: number
  issue_type?: string
  assignee?: string
  holder?: string
  holder_note?: string
  /** Смена владельца задачи; пустая строка снимает владельца. */
  owner?: string
  project?: string
  labels?: string[]
  spec_path?: string
  journal_path?: string
  worktree?: string
  branch?: string
  close_reason?: string
  needs_owner?: boolean
  external_ref?: string
  archived?: boolean
  /**
   * Маршрут запуска («тип запуска»): алиас колонки `launch_route`, как и в
   * POST /api/tasks. Сервер принимает смену, только пока задача не начата.
   */
  route?: string | null
  launch_route?: string | null
  actor?: string
  harness?: string
  note?: string
}

export type CommentKind = 'comment' | 'journal' | 'question' | 'answer' | 'review' | 'verdict'

export interface StreamEvent {
  kind: string
  at?: string
  payload?: { id?: string; action?: string }
}
