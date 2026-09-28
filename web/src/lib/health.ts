/**
 * Здоровье задачи по heartbeat/держателю — вычисляемое состояние, не хранится
 * на сервере. Правила и пороги — легенда верха `CardStates.dc.html`.
 */
import type { Task } from '@/api/types'
import { isClosedStatus } from '@/lib/dictionaries'

export type Health = 'healthy' | 'at-risk' | 'dead' | 'unknown'

/** Heartbeat старше этого числа часов (15 мин) переводит здоровую задачу в at-risk. */
export const AT_RISK_IDLE_HOURS = 0.25

/** Молчание дольше этого (30 мин) красит пилюлю at-risk в оранжевый. */
export const IDLE_WARN_HOURS = 0.5

/** Молчание дольше этого (45 мин) красит пилюлю at-risk в красный. */
export const IDLE_DANGER_HOURS = 0.75

/** Тон пилюли кита: у at-risk он ступенчатый по времени молчания. */
export type PillTone = 'healthy' | 'at-risk' | 'dead' | 'unknown' | 'success' | 'warning' | 'danger'

/**
 * Цвет пилюли здоровья. `at-risk` — не одно состояние, а шкала: «молчит
 * 15 мин» и «молчит 3 часа» одинаково тревожными выглядеть не должны, поэтому
 * тон растёт ступенями AT_RISK_IDLE_HOURS → IDLE_WARN_HOURS → IDLE_DANGER_HOURS
 * (зелёный → оранжевый → красный). Ступени считаются только по молчанию: у
 * at-risk по `stage_warn` или «выдана, но не взята» своего возраста нет, и такая
 * задача остаётся на базовом тоне at-risk. Остальные состояния — как были.
 */
export function healthTone(task: Task): PillTone {
  const health = taskHealth(task)
  if (health !== 'at-risk') return health
  const idle = task.idle_hours
  if (idle === null || idle < AT_RISK_IDLE_HOURS) return 'at-risk'
  if (idle >= IDLE_DANGER_HOURS) return 'danger'
  if (idle >= IDLE_WARN_HOURS) return 'warning'
  return 'success'
}

export const HEALTH_TITLES: Record<Health, string> = {
  healthy: 'здорова',
  'at-risk': 'под угрозой',
  dead: 'брошена',
  unknown: 'без держателя',
}

/**
 * Здоровье и его подпись — одной цепочкой, первая сработавшая проверка решает:
 * 1. закрытая — healthy, «закрыта»: по heartbeat не оценивается (сервер не
 *    снимает holder_at при закрытии, иначе каждая закрытая со старым heartbeat
 *    стала бы at-risk);
 * 2–4. stale/abandoned — dead («брошена …»), раньше проверки держателя:
 *    abandoned на сервере — это в первую очередь «в работе без держателя»;
 * 5. нет держателя — unknown, «без держателя»;
 * 6. «выдана, но не взята» дольше порога — at-risk: держатель назначен, но агент
 *    так и не сделал claim, то есть прогон, похоже, не запустился;
 * 7. молчит дольше порога — at-risk, «молчит …»;
 * 8. превысила порог этапа — at-risk, «на этапе дольше порога»;
 * 9. выдана, не взята, но в пределах порога — healthy, «выдана, не взята …»;
 * 10. иначе healthy, «hb …».
 */
export function healthOf(task: Task): { health: Health; reason: string } {
  if (isClosedStatus(task.status)) return { health: 'healthy', reason: 'закрыта' }
  if (task.stale) return { health: 'dead', reason: `брошена ${task.idle_age}` }
  if (task.abandoned && !task.holder) return { health: 'dead', reason: 'брошена · без держателя' }
  if (task.abandoned) return { health: 'dead', reason: 'брошена' }
  if (!task.holder) return { health: 'unknown', reason: 'без держателя' }
  if (task.not_taken_warn) return { health: 'at-risk', reason: `выдана, не взята ${task.assigned_age}` }
  if (task.idle_hours !== null && task.idle_hours >= AT_RISK_IDLE_HOURS) {
    return { health: 'at-risk', reason: `молчит ${task.idle_age}` }
  }
  if (task.stage_warn) return { health: 'at-risk', reason: 'на этапе дольше порога' }
  if (task.not_taken) return { health: 'healthy', reason: `выдана, не взята ${task.assigned_age}` }
  return { health: 'healthy', reason: `hb ${task.holder_age}` }
}

/** См. healthOf. */
export function taskHealth(task: Task): Health {
  return healthOf(task).health
}

/** См. healthOf. */
export function healthReason(task: Task): string {
  return healthOf(task).reason
}
