/**
 * Справочники значений задачи — тип, приоритет, этап, статус. Единственное место,
 * где перечислены ключи, подписи, иконки и цвета: фильтры, модалка создания,
 * глифы, доска и витрина берут значения отсюда, а не держат свои копии.
 */
import type { AssistantComplexityLevel, PipelineStage, TaskStage, TaskStatus } from '@/api/types'

export interface DictionaryItem<T extends string | number> {
  value: T
  label: string
}

// ── Тип ─────────────────────────────────────────────────────────────────────

export type IssueType = 'epic' | 'task' | 'bug'

export interface TaskTypeItem extends DictionaryItem<IssueType> {
  /** Подпись с пояснением — в глифе и переключателе типа модалки. */
  hint: string
  createLabel: string
  icon: string
  color: string
}

export const TASK_TYPES: TaskTypeItem[] = [
  { value: 'epic', label: 'эпик', hint: 'эпик · ТЗ', createLabel: 'Создать эпик', icon: 'epic', color: 'var(--chart-3)' },
  { value: 'task', label: 'задача', hint: 'задача', createLabel: 'Создать задачу', icon: 'task', color: 'var(--info-500)' },
  { value: 'bug', label: 'баг', hint: 'баг', createLabel: 'Создать баг', icon: 'bug', color: 'var(--danger-500)' },
]

/** Незнакомый тип (старые импорты: feature, chore…) показываем как обычную задачу. */
export function taskType(value: string | null | undefined): TaskTypeItem {
  return TASK_TYPES.find((item) => item.value === value) ?? TASK_TYPES[1]
}

// ── Приоритет ───────────────────────────────────────────────────────────────

export type Priority = 0 | 1 | 2 | 3 | 4

export interface PriorityItem extends DictionaryItem<Priority> {
  icon: string
  color: string
}

export const PRIORITIES: PriorityItem[] = [
  { value: 0, label: 'блокер', icon: 'p0', color: 'var(--danger-500)' },
  { value: 1, label: 'критичный', icon: 'p1', color: 'var(--warning-600)' },
  { value: 2, label: 'обычный', icon: 'p2', color: 'var(--ink-3)' },
  { value: 3, label: 'низкий', icon: 'p3', color: 'var(--info-500)' },
  { value: 4, label: 'потом', icon: 'p4', color: 'var(--ink-4)' },
]

export const DEFAULT_PRIORITY: Priority = 2

/** Незнакомое значение — обычный приоритет. */
export function priority(value: number | string | null | undefined): PriorityItem {
  return PRIORITIES.find((item) => item.value === Number(value)) ?? PRIORITIES[DEFAULT_PRIORITY]
}

// ── Этап ────────────────────────────────────────────────────────────────────

export type PipelineCode = 's1' | 's2' | 's3' | 's4'

export interface PipelineStep extends DictionaryItem<PipelineStage> {
  code: PipelineCode
}

/** Этапы конвейера s1…s4 в порядке прохождения. */
export const PIPELINE_STAGES: PipelineStep[] = [
  { value: 's1-spec', code: 's1', label: 'ТЗ и чек-лист' },
  { value: 's2-review', code: 's2', label: 'Второе мнение' },
  { value: 's3-impl', code: 's3', label: 'Реализация' },
  { value: 's4-judge', code: 's4', label: 'Проверка' },
]

export const DONE_STAGE: DictionaryItem<'done'> = { value: 'done', label: 'Готово' }

/** Все этапы, которые может иметь задача: s1…s4 и «Готово». */
export const STAGES: DictionaryItem<PipelineStage | 'done'>[] = [...PIPELINE_STAGES, DONE_STAGE]

export const PIPELINE_STAGE_KEYS: string[] = PIPELINE_STAGES.map((step) => step.value)

/** Колонка доски для задач без этапа. */
export const INTAKE_COLUMN_KEY = 'none'

export function stageIndex(stage: TaskStage): number {
  const at = STAGES.findIndex((item) => item.value === stage)
  return at === -1 ? 0 : at
}

// ── Статус ──────────────────────────────────────────────────────────────────

export const STATUSES: DictionaryItem<TaskStatus>[] = [
  { value: 'open', label: 'открыта' },
  { value: 'in_progress', label: 'в работе' },
  { value: 'blocked', label: 'заблокирована' },
  { value: 'review', label: 'на проверке' },
  { value: 'done', label: 'готова' },
  { value: 'cancelled', label: 'отменена' },
]

export function statusTitle(value: TaskStatus): string {
  return STATUSES.find((item) => item.value === value)?.label ?? value
}

// ── Когнитивная сложность (оценка помощника DeepSeek) ───────────────────────

export interface ComplexityItem extends DictionaryItem<AssistantComplexityLevel> {
  /** Пояснение уровня — в подсказке и в панели помощника. */
  hint: string
  /** Тон бейджа: чем сложнее, тем громче. */
  tone: 'success' | 'info' | 'warning'
}

/** Уровни — ключи сервера (`assistant.COMPLEXITY_LEVELS`), подписи — здесь. */
export const COMPLEXITY_LEVELS: ComplexityItem[] = [
  { value: 'low', label: 'низкая', hint: 'механическая работа', tone: 'success' },
  { value: 'medium', label: 'средняя', hint: 'несколько шагов', tone: 'info' },
  { value: 'high', label: 'высокая', hint: 'много контекста и развилок', tone: 'warning' },
]

/** Незнакомый или пустой уровень — `null`: бейдж не рисуем. */
export function complexity(value: string | null | undefined): ComplexityItem | null {
  return COMPLEXITY_LEVELS.find((item) => item.value === value) ?? null
}
