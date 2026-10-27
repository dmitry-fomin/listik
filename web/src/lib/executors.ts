/**
 * Исполнитель этапа по маршруту задачи (`launch_route` → запись `GET /api/routes`).
 * Держатель (`holder`) — тот, кто управляет карточкой; исполнитель — кто реально
 * делает этап: у конвейера это исполнители роли v2 по этапу (`PIPELINE_STAGE_ROLE`
 * в `lib/pipelines.ts`), у роя — харнесс ячейки роли этапа (`STAGE_ROLE`).
 * Исполнитель и держатель совпадают не всегда: оркестратор держит карточку,
 * а этап делает GLM-критик или DeepSeek через dsh — поэтому «кто делает» доска
 * берёт отсюда, а фактический держатель остаётся muted-подписью «держит …».
 */
import type { RouteDef, Task } from '@/api/types'
import { harnessOf, harnessTitle, type HarnessKey } from './harness'
import {
  executorGlyph,
  executorName,
  executorsTitle,
  isSwarmCell,
  PIPELINE_STAGE_ROLE,
  roleExecutors,
  STAGE_ROLE,
} from './pipelines'
import { routeByKey } from './routes'
import { DONE_STAGE } from './dictionaries'

export interface StageExecutor {
  kind: 'role' | 'swarm'
  /** Ключ харнесса (глиф первого исполнителя роли) — любой из каталога `harnesses`. */
  harness?: HarnessKey | string
  /** Глифы всех исполнителей роли конвейера — по ним `executorIsHolder`; у роя пусто. */
  harnesses: string[]
  /** короткая подпись (имя первого исполнителя / имя харнесса) */
  label: string
  /** полная (имя единственного исполнителя или строка-тултип роли / имя харнесса) */
  title: string
  /** Усилие единственного исполнителя роли; у нескольких и у роя — `null`. */
  detail: string | null
}

/**
 * Плановый исполнитель текущего этапа задачи. `null` — маршрута нет или запись
 * удалили из таблицы маршрутов; у конвейера этапа без роли (`null`, `done`,
 * неизвестный), роли нет или значение не массив — тоже `null`, и карточка
 * показывает держателя как раньше. У роя исполнитель — харнесс ячейки роли
 * этапа (listik-2gry).
 */
export function stageExecutor(
  task: Pick<Task, 'launch_route' | 'stage'>,
  routes: RouteDef[],
): StageExecutor | null {
  const route = routeByKey(task.launch_route, routes)
  if (!route) return null
  if (!task.stage || task.stage === DONE_STAGE.value) return null
  if (route.kind === 'swarm') {
    const cell = route.roles[STAGE_ROLE[task.stage]]
    if (!isSwarmCell(cell)) return null
    const name = harnessTitle(cell.harness)
    return { kind: 'swarm', harness: cell.harness, harnesses: [], label: name, title: name, detail: null }
  }
  const executors = roleExecutors(route.roles[PIPELINE_STAGE_ROLE[task.stage]])
  const first = executors[0]
  if (!first) return null
  const single = executors.length === 1
  return {
    kind: 'role',
    harness: executorGlyph(first),
    harnesses: executors.map(executorGlyph),
    label: executorName(first),
    title: single ? executorName(first) : executorsTitle(executors),
    detail: single ? first.effort : null,
  }
}

/**
 * Этап делает сам держатель: ключ держателя (`harnessOf`) равен харнессу
 * роевой ячейки или глифу любого исполнителя роли конвейера. Без держателя и у
 * человека — не совпадает. Одно правило для строки «делает» панели и для
 * телефонного `holderStatusText`.
 */
export function executorIsHolder(executor: StageExecutor, holder: string | null | undefined): boolean {
  const key = harnessOf(holder)
  if (!key || key === 'human') return false
  return executor.harness === key || executor.harnesses.includes(key)
}
