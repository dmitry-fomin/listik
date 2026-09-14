/**
 * Здоровье задачи по heartbeat/держателю — вычисляемое состояние, не хранится
 * на сервере. Правила и пороги — легенда верха `CardStates.dc.html`.
 */
import type { Task } from '@/api/types'

export type Health = 'healthy' | 'at-risk' | 'dead' | 'unknown'

/** Heartbeat старше этого числа часов (15 мин) переводит здоровую задачу в at-risk. */
export const AT_RISK_IDLE_HOURS = 0.25

export const HEALTH_TITLES: Record<Health, string> = {
  healthy: 'здорова',
  'at-risk': 'под угрозой',
  dead: 'брошена',
  unknown: 'без держателя',
}

/**
 * Порядок проверок фиксирован — первая сработавшая возвращает результат:
 * 1. закрытая задача по heartbeat не оценивается (сервер не снимает holder_at
 *    при закрытии, иначе каждая закрытая задача со старым heartbeat стала бы at-risk);
 * 2. stale/abandoned — раньше проверки держателя: abandoned на сервере — это
 *    в первую очередь «в работе без держателя»;
 * 3. нет держателя — unknown;
 * 4. «выдана, но не взята» дольше порога — at-risk: держатель назначен, но
 *    агент так и не сделал claim, то есть прогон, похоже, не запустился;
 * 5. молчит дольше порога или превысила порог этапа — at-risk;
 * 6. иначе healthy.
 */
export function taskHealth(task: Task): Health {
  if (task.status === 'done' || task.status === 'cancelled') return 'healthy'
  if (task.stale || task.abandoned) return 'dead'
  if (!task.holder) return 'unknown'
  if (task.not_taken_warn) return 'at-risk'
  if (task.stage_warn || (task.idle_hours !== null && task.idle_hours >= AT_RISK_IDLE_HOURS)) {
    return 'at-risk'
  }
  return 'healthy'
}

/** Короткая подпись для тултипа/бейджа — тем же порядком проверок, что taskHealth. */
export function healthReason(task: Task): string {
  if (task.status === 'done' || task.status === 'cancelled') return 'закрыта'
  if (task.stale) return `брошена ${task.idle_age}`
  if (task.abandoned && !task.holder) return 'брошена · без держателя'
  if (task.abandoned) return 'брошена'
  if (!task.holder) return 'без держателя'
  if (task.not_taken) return `выдана, не взята ${task.assigned_age}`
  if (task.idle_hours !== null && task.idle_hours >= AT_RISK_IDLE_HOURS) return `молчит ${task.idle_age}`
  if (task.stage_warn) return 'на этапе дольше порога'
  return `hb ${task.holder_age}`
}
