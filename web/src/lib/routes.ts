/**
 * Правила маршрута «Новой задачи» — поверх данных `GET /api/routes` (записи
 * `routes.json`, тип `RouteDef`). Сами маршруты, их порядок, роли и иконки в коде
 * не зашиты: здесь только выбор по умолчанию, доступность по типу задачи и метки
 * для карточки. Метки `harness:<x>`/`process:<y>` сервер не читает — они для
 * человека и поиска; кто реально допущен до этапа, решает routing проекта.
 */
import type { PipelineRouteDef, RouteDef } from '@/api/types'

/** Эпику всегда нужно ТЗ (он режется на шаги через этап 1) — записи без `roles.spec` закрыты. */
export function pipelineAllowed(route: PipelineRouteDef, type: string): boolean {
  if (type !== 'epic') return true
  return Boolean(route.roles.spec)
}

export function directAllowed(type: string): boolean {
  return type !== 'epic'
}

/**
 * Пресет по умолчанию зависит от типа: эпик крупнее и рискованнее — `high-pipeline`,
 * задаче и багу хватает `low-pipeline`. Если нужного ключа нет среди видимых
 * разрешённых, берётся первый видимый разрешённый `pipeline` в порядке ответа;
 * если и его нет — маршрут не выбран (`null`).
 */
export function defaultPipelineFor(type: string, routes: RouteDef[]): PipelineRouteDef | null {
  const allowed = routes.filter(
    (route): route is PipelineRouteDef =>
      route.kind === 'pipeline' && route.visible && pipelineAllowed(route, type),
  )
  const preferred = type === 'epic' ? 'high-pipeline' : 'low-pipeline'
  return allowed.find((route) => route.key === preferred) ?? allowed[0] ?? null
}

/**
 * Метки выбранного маршрута: у `pipeline` роль исполнителя всегда claude,
 * у `direct` — харнесс из самой записи (зашитого соответствия ключ → харнесс нет).
 */
export function routeLabels(route: RouteDef): string[] {
  if (route.kind === 'direct') return [`harness:${route.harness}`, 'process:direct']
  return ['harness:claude', `process:${route.key}`]
}

/**
 * Запись маршрута по ключу задачи (`launch_route`): иконку уровня и подпись
 * карточка берёт из неё. Ключа нет или записи в списке нет (маршрут убрали из
 * `routes.json` после заведения задачи) — `null`, иконка не рисуется.
 */
export function routeByKey(key: string | null | undefined, routes: RouteDef[]): RouteDef | null {
  if (!key) return null
  return routes.find((route) => route.key === key) ?? null
}
