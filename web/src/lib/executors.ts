/**
 * Исполнитель этапа по маршруту задачи (`launch_route` → запись `GET /api/routes`).
 * Держатель (`holder`) — тот, кто управляет карточкой; исполнитель — кто реально
 * делает этап: у конвейера это ячейка `roles` по этапу (обратная мапа
 * `STAGE_ROLE` в `lib/pipelines.ts`), у роя — харнесс ячейки роли этапа.
 * Исполнитель и держатель совпадают не всегда: оркестратор держит карточку,
 * а этап делает GLM-критик или DeepSeek через dsh — поэтому «кто делает» доска
 * берёт отсюда, а фактический держатель остаётся muted-подписью «держит …».
 */
import type { RouteDef, Task } from '@/api/types'
import { harnessOf, harnessTitle, type HarnessKey } from './harness'
import { effortOf, isSwarmCell, STAGE_ROLE, type ProviderKey } from './pipelines'
import { routeByKey } from './routes'
import { DONE_STAGE } from './dictionaries'

export interface StageExecutor {
  kind: 'role' | 'swarm'
  provider?: ProviderKey
  /** Ключ харнесса — любой из каталога `harnesses`, не только встроенный. */
  harness?: HarnessKey | string
  /** короткая подпись (label ячейки роли / имя харнесса) */
  label: string
  /** полная (title ячейки роли / title записи маршрута) */
  title: string
  /**
   * Уточнение к `title` (effort ячейки роли) — `label`, если его нет словом в
   * `title`; иначе и у роя — `null` (listik-erjx).
   */
  detail: string | null
}

/**
 * Плановый исполнитель текущего этапа задачи. `null` — маршрута нет или запись
 * удалили из таблицы маршрутов; у
 * конвейера этапа без роли (`null`, `done`, неизвестный) или ячейки в `roles`
 * нет — тоже `null`, и карточка показывает держателя как раньше. У роя
 * исполнитель — харнесс ячейки роли этапа (listik-2gry).
 */
export function stageExecutor(
  task: Pick<Task, 'launch_route' | 'stage'>,
  routes: RouteDef[],
): StageExecutor | null {
  const route = routeByKey(task.launch_route, routes)
  if (!route) return null
  if (!task.stage || task.stage === DONE_STAGE.value) return null
  const cell = route.roles[STAGE_ROLE[task.stage]]
  if (!cell) return null
  if (isSwarmCell(cell)) {
    const name = harnessTitle(cell.harness)
    return { kind: 'swarm', harness: cell.harness, label: name, title: name, detail: null }
  }
  const title = cell.title || cell.label
  return { kind: 'role', provider: cell.provider, label: cell.label, title, detail: effortOf(cell.label, title) }
}

/** Харнессы держателя, которыми вендор ячейки роли исполняет этап. */
const PROVIDER_HARNESSES: Record<ProviderKey, readonly string[]> = {
  claude: ['claude'],
  grok: ['grok'],
  devin: ['devin'],
  openai: ['codex'],
  deepseek: ['dsh', 'pi-deepseek'],
  glm: ['pi-glm'],
}

/**
 * Этап делает сам держатель: ключ держателя (`harnessOf`) равен харнессу
 * роевой ячейки или входит в харнессы вендора ячейки роли. Без держателя и у
 * человека — не совпадает. Одно правило для строки «делает» панели и для
 * телефонного `holderStatusText`.
 */
export function executorIsHolder(executor: StageExecutor, holder: string | null | undefined): boolean {
  const key = harnessOf(holder)
  if (!key || key === 'human') return false
  if (executor.kind === 'swarm') return executor.harness === key
  return executor.provider ? (PROVIDER_HARNESSES[executor.provider] ?? []).includes(key) : false
}
