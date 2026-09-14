import type { Task } from '@/api/types'
import { COMMENT_KINDS } from './dictionaries'

/** Человекочитаемый возраст из ISO-строки: «3 ч», «2 дн», «5 мин». */
export function humanAge(iso: string | null | undefined): string {
  if (!iso) return '—'
  const time = Date.parse(iso)
  if (Number.isNaN(time)) return '—'
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60000))
  if (minutes < 1) return 'только что'
  if (minutes < 60) return `${minutes} мин`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} ч`
  const days = Math.round(hours / 24)
  if (days < 31) return `${days} дн`
  const months = Math.round(days / 30)
  return `${months} мес`
}

/** Дата и время для подписи под событием: 12.03.2026 09:14. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** Значение для <time datetime="…">. */
export function datetimeAttr(iso: string | null | undefined): string | undefined {
  if (!iso) return undefined
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return undefined
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—'
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total} с`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes} мин`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (hours < 24) return rest ? `${hours} ч ${rest} мин` : `${hours} ч`
  const days = Math.floor(hours / 24)
  return `${days} дн ${hours % 24} ч`
}

/** Часы → «2 ч», «3 дн». Для stage_hours/holder_hours из API. */
export function formatHours(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || !Number.isFinite(hours)) return '—'
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} мин`
  if (hours < 24) return `${Math.round(hours * 10) / 10} ч`
  return `${Math.round((hours / 24) * 10) / 10} дн`
}


/** Подпись вида записи ленты — единственное место подписей: `dictionaries.ts`, COMMENT_KINDS. */
export function commentKindTitle(kind: string): string {
  return COMMENT_KINDS.find((item) => item.value === kind)?.label ?? kind
}

const EVENT_KIND_TITLES: Record<string, string> = {
  create: 'создана',
  created: 'создана',
  status: 'статус',
  stage: 'этап',
  claim: 'взял в работу',
  release: 'освободил',
  heartbeat: 'heartbeat',
  comment: 'комментарий',
  question: 'нужен человек',
  answer: 'ответ автора',
  note: 'заметка',
  done: 'закрыта',
  dep: 'связь',
  route: 'маршрут',
  embed: 'вектор',
}

export function eventKindTitle(kind: string): string {
  return EVENT_KIND_TITLES[kind] ?? kind
}

/** Строка-подпись статуса/этапа задачи для компактных мест интерфейса. */
export function taskStageLabel(task: Task): string {
  return task.stage_title || task.status_title
}

export function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10
  const mod100 = count % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few
  return many
}

export function tasksCountLabel(count: number): string {
  return `${count} ${plural(count, 'задача', 'задачи', 'задач')}`
}
