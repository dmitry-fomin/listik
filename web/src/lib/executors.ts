/**
 * Исполнитель этапа по маршруту задачи (`launch_route` → запись `GET /api/routes`).
 * Держатель (`holder`) — тот, кто управляет карточкой; исполнитель — кто реально
 * делает этап: у конвейера это ячейка `roles` по этапу (обратная мапа
 * `STAGE_ROLE` в `lib/pipelines.ts`), у прямого маршрута — сам харнесс записи.
 * Исполнитель и держатель совпадают не всегда: оркестратор держит карточку,
 * а этап делает GLM-критик или DeepSeek через dsh — поэтому «кто делает» доска
 * берёт отсюда, а фактический держатель остаётся muted-подписью «держит …».
 */
import type { RouteDef, Task } from '@/api/types'
import { HARNESS_TITLES, type HarnessKey } from './harness'
import { STAGE_ROLE, type ProviderKey } from './pipelines'
import { routeByKey } from './routes'

export interface StageExecutor {
  kind: 'role' | 'direct'
  provider?: ProviderKey
  harness?: HarnessKey
  /** короткая подпись (label ячейки роли / имя харнесса) */
  label: string
  /** полная (title ячейки роли / title записи маршрута) */
  title: string
}

/**
 * Плановый исполнитель текущего этапа задачи. `null` — маршрута нет или запись
 * убрали из `routes.json`, у прямого маршрута исполнитель есть всегда; у
 * конвейера этапа без роли (`null`, `done`, неизвестный) или ячейки в `roles`
 * нет — тоже `null`, и карточка показывает держателя как раньше.
 */
export function stageExecutor(
  task: Pick<Task, 'launch_route' | 'stage'>,
  routes: RouteDef[],
): StageExecutor | null {
  const route = routeByKey(task.launch_route, routes)
  if (!route) return null
  if (route.kind === 'direct') {
    return {
      kind: 'direct',
      harness: route.harness,
      label: HARNESS_TITLES[route.harness],
      title: route.title || HARNESS_TITLES[route.harness],
    }
  }
  if (!task.stage || task.stage === 'done') return null
  const cell = route.roles[STAGE_ROLE[task.stage]]
  if (!cell) return null
  return { kind: 'role', provider: cell.provider, label: cell.label, title: cell.title || cell.label }
}
