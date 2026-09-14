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
 * Разрешён ли маршрут типу задачи — одни и те же правила у «Новой задачи» и у
 * смены маршрута в карточке: эпику нужен этап ТЗ, прямой маршрут ему закрыт.
 */
export function routeAllowedForType(route: RouteDef, type: string): boolean {
  return route.kind === 'direct' ? directAllowed(type) : pipelineAllowed(route, type)
}

/**
 * Текст алерта «маршруты недоступны»: отказ запроса и ошибка самого файла
 * (`ok:false`) звучат по-разному, потому что причины разные.
 */
export function routesAlertText(requestFailed: boolean, error: string | null): string {
  const reason = error ?? 'неизвестная ошибка'
  if (requestFailed) return `${reason} — нужен ты`
  return `routes.json с ошибкой: ${reason} — нужен ты`
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
