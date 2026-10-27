/**
 * Словари ролей маршрутов: роя (`spec/critic/impl/judge`) и конвейера v2
 * (`writer/critic/implementer/lens/judge`). Сами маршруты в коде не зашиты: их
 * отдаёт сервер — `GET /api/routes` (таблица `routes`; см. `RouteDef` в `api/types.ts`).
 */
import type { PipelineExecutor, PipelineStage, SwarmRoleCell } from '@/api/types'
import { PIPELINE_STAGES, type PipelineStep } from '@/lib/dictionaries'

export type RoleKey = 'spec' | 'critic' | 'impl' | 'judge'

export const ROLE_KEYS: RoleKey[] = ['spec', 'critic', 'impl', 'judge']

export const ROLE_TITLES: Record<RoleKey, string> = {
  spec: 'ТЗ',
  critic: 'Критик',
  impl: 'Исполнитель',
  judge: 'Судья',
}

/**
 * Роль роя ↔ этап задачи — единственное место этого соответствия.
 * Подписи этапов (`s1` / «ТЗ и чек-лист» …) берутся уже из `PIPELINE_STAGES`
 * (`lib/dictionaries.ts`) по этому значению, чтобы в шаблонах не заводились
 * свои литералы этапов.
 */
export const ROLE_STAGE: Record<RoleKey, PipelineStage> = {
  spec: 's1-spec',
  critic: 's2-review',
  impl: 's3-impl',
  judge: 's4-judge',
}

/**
 * Этап (код `s1`…, подпись) для роли роя или конвейера v2; `null` — `role` не
 * ключ ни `ROLE_STAGE`, ни `PIPELINE_ROLE_STAGE`.
 */
export function roleStage(role: string | null | undefined): PipelineStep | null {
  if (!role) return null
  const own = (map: object) => Object.prototype.hasOwnProperty.call(map, role)
  const stage = own(ROLE_STAGE)
    ? ROLE_STAGE[role as RoleKey]
    : own(PIPELINE_ROLE_STAGE)
      ? PIPELINE_ROLE_STAGE[role as PipelineRoleKey]
      : null
  return stage ? (PIPELINE_STAGES.find((item) => item.value === stage) ?? null) : null
}

/**
 * Обратное соответствие этап → роль роя — собрано из `ROLE_STAGE`, чтобы литералы
 * этапов и ролей не дублировались в двух местах. По нему `lib/executors.ts`
 * находит ячейку `roles` маршрута роя для текущего этапа задачи.
 */
export const STAGE_ROLE: Record<PipelineStage, RoleKey> = Object.fromEntries(
  (Object.entries(ROLE_STAGE) as [RoleKey, PipelineStage][]).map(([role, stage]) => [stage, role]),
) as Record<PipelineStage, RoleKey>

// ── роли конвейера формата v2 (`kind=pipeline`, плагин `listik-pipeline`) ──

export type PipelineRoleKey = 'writer' | 'critic' | 'implementer' | 'lens' | 'judge'

export const PIPELINE_ROLE_KEYS: PipelineRoleKey[] = ['writer', 'critic', 'implementer', 'lens', 'judge']

export const PIPELINE_ROLE_TITLES: Record<PipelineRoleKey, string> = {
  writer: 'ТЗ',
  critic: 'Критика',
  implementer: 'Код',
  lens: 'Линза',
  judge: 'Судья',
}

/** Глиф роли конвейера v2 (`lib/icons.ts`) — в схеме «Состав конвейера». */
export const PIPELINE_ROLE_ICONS: Record<PipelineRoleKey, string> = {
  writer: 'edit',
  critic: 'review',
  implementer: 'branch',
  lens: 'search',
  judge: 'check',
}

/** Роль конвейера v2 → этап задачи; у линзы и судьи этап общий — `s4-judge`. */
export const PIPELINE_ROLE_STAGE: Record<PipelineRoleKey, PipelineStage> = {
  writer: 's1-spec',
  critic: 's2-review',
  implementer: 's3-impl',
  lens: 's4-judge',
  judge: 's4-judge',
}

/** Этап → роль конвейера v2 для строки «делает»: на `s4-judge` — судья, не линза. */
export const PIPELINE_STAGE_ROLE: Record<PipelineStage, PipelineRoleKey> = {
  's1-spec': 'writer',
  's2-review': 'critic',
  's3-impl': 'implementer',
  's4-judge': 'judge',
}

/**
 * Исполнители роли конвейера; значение не массив (старая ячейка-объект в базе
 * до `--reimport`) — роли нет, пустой список, без исключения.
 */
export function roleExecutors(value: unknown): PipelineExecutor[] {
  return Array.isArray(value) ? (value as PipelineExecutor[]) : []
}

/** Имя исполнителя для показа: модель, иначе канал, иначе харнесс. */
export function executorName(executor: PipelineExecutor): string {
  return executor.model ?? executor.channel ?? executor.harness
}

/** Глиф `HarnessIcon` исполнителя: у pi с каналом — `pi-<канал>`. */
export function executorGlyph(executor: PipelineExecutor): string {
  return executor.harness === 'pi' && executor.channel ? `pi-${executor.channel}` : executor.harness
}

/** Строка-тултип роли: `имя · усилие` через ` + `. */
export function executorsTitle(executors: PipelineExecutor[]): string {
  return executors.map((executor) => `${executorName(executor)} · ${executor.effort}`).join(' + ')
}

/** Ячейка роевой формы: есть `harness`, нет `provider`. */
export function isSwarmCell(cell: unknown): cell is SwarmRoleCell {
  return (
    typeof cell === 'object' &&
    cell !== null &&
    'harness' in cell &&
    typeof (cell as { harness?: unknown }).harness === 'string'
  )
}

