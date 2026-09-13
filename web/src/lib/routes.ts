/**
 * Маршрут «Новой задачи»: либо один из локальных конвейеров `PIPELINES`
 * (`pipelines.ts`, четыре роли — ТЗ/критик/исполнитель/судья), либо «просто
 * исполнитель» — харнесс делает задачу напрямую, без ролей. Зашито в UI по
 * `SKILL.md` плагина `feature-pipeline`; сервер этого
 * не знает — метки `harness:<x>`/`process:<y>`, которые получаются отсюда,
 * никто из бэкенда не читает (полей `harness`/`skill` на карточке нет), они
 * только для человека и поиска. Кто реально допущен до этапа, решает сервер
 * по `routing` проекта.
 */
import type { HarnessKey } from './harness'
import type { PipelineDef } from './pipelines'

export type RouteMode = 'pipeline' | 'direct'

/** Харнессы «просто исполнитель» — без ТЗ, критики и приёмки, весь конвейер сам. */
export const DIRECT_HARNESSES: HarnessKey[] = ['dsh', 'grok', 'codex']

export const DEFAULT_ROUTE: { mode: 'pipeline'; harness: 'claude' } = {
  mode: 'pipeline',
  harness: 'claude',
}

/** Пресет по умолчанию зависит от типа: эпик крупнее и рискованнее — `high`, задаче и багу хватает `low`. */
export function defaultPipelineFor(type: string): string {
  return type === 'epic' ? 'high-pipeline' : 'low-pipeline'
}

/** Эпику всегда нужно ТЗ (он режется на шаги через этап 1) — пресеты и «прямая задача» без него закрыты. */
export function pipelineAllowed(pipeline: PipelineDef, type: string): boolean {
  if (type !== 'epic') return true
  return Boolean(pipeline.roles.spec)
}

export function directAllowed(type: string): boolean {
  return type !== 'epic'
}

export function routeLabels(mode: RouteMode, pipeline: string, directHarness: HarnessKey): string[] {
  if (mode === 'direct') return [`harness:${directHarness}`, 'process:direct']
  return ['harness:claude', `process:${pipeline}`]
}

export { PIPELINES, TABLE_PIPELINES, STRIP_PIPELINES, type PipelineDef } from './pipelines'
