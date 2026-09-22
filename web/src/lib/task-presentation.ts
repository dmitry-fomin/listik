import type { DepInfo, ProjectRow, RouteDef, Task, TaskComment, TaskDetail } from '@/api/types'
import { formatDateTime, humanAge } from './format'
import { HARNESS_TITLES, harnessOf } from './harness'
import { HEALTH_TITLES, healthReason, taskHealth } from './health'
import { worktreeState, worktreeValue } from './dictionaries'
import { stageExecutor } from './executors'

/** Общие представления задачи для настольной и телефонной карточек. */

export function projectOf(task: Pick<Task, 'project'>, projects?: ProjectRow[]): ProjectRow | null {
  return projects?.find((project) => project.slug === task.project) ?? null
}

export function sortedTasksByUpdatedDesc<T extends Pick<Task, 'updated_at'>>(tasks: T[]): T[] {
  return tasks.slice().sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))
}

/** Сервер использует «—» для отсутствующего держателя; пустая подпись означает то же. */
export function hasHolderTitle(title: string | null | undefined): title is string {
  return Boolean(title) && title !== '—'
}

/** Держатель/актор коротко: agent:dsh/dsh-flash → dsh, human me → я. */
export function actorShort(key: string | null | undefined): string {
  if (!key) return '—'
  const trimmed = key.trim()
  if (!trimmed) return '—'
  const harness = harnessOf(trimmed)
  if (harness && harness !== 'human') return HARNESS_TITLES[harness]
  return trimmed === 'me' ? 'я' : trimmed
}

/** Пилюля здоровья без дублирования подписи статуса задачи. */
export function healthPillText(task: TaskDetail): string {
  if (task.status === 'done' || task.status === 'cancelled') return 'закрыта'
  const health = taskHealth(task)
  if (health === 'dead' || health === 'unknown') return healthReason(task)
  return `${HEALTH_TITLES[health]} · ${healthReason(task)}`
}

/** Комментарии по времени по убыванию; при равенстве сохраняется поздний индекс. */
export function sortedCommentsDesc(comments: TaskComment[]): TaskComment[] {
  return comments
    .map((comment, index) => ({ comment, index }))
    .sort((a, b) => {
      const diff = Date.parse(b.comment.created_at) - Date.parse(a.comment.created_at)
      if (diff !== 0) return diff
      return b.index - a.index
    })
    .map((entry) => entry.comment)
}

export function depHolderHint(dep: DepInfo): string {
  return dep.holder ? `держит ${dep.holder_title}` : 'без держателя'
}

/**
 * Текст статуса держателя для компактного списка телефона. «Выдана, не взята»
 * важнее исполнителя (прогон ещё не подтвердился); иначе при известном
 * исполнителе этапа (`lib/executors.ts`) показываем его, а держатель уходит
 * в хвост «держит …» — держателя может и не быть вовсе.
 */
export function holderStatusText(task: TaskDetail, routes: RouteDef[] = []): string {
  if (task.not_taken && task.holder) {
    return `выдана ${task.holder_title}, не взята ${task.assigned_age}${task.holder_assigned_by_title ? ` · выдал ${task.holder_assigned_by_title}` : ''}`
  }
  const executor = stageExecutor(task, routes)
  if (executor) {
    const parts = [executor.title]
    if (hasHolderTitle(task.holder_title)) parts.push(`держит ${task.holder_title}`)
    if (task.holder_age) parts.push(task.holder_age)
    return parts.join(' · ')
  }
  if (!task.holder) return 'никто'
  return `${task.holder_title} · ${task.holder_age}`
}

export function heartbeatText(task: TaskDetail): string {
  return task.holder_at ? `${formatDateTime(task.holder_at)} · ${humanAge(task.holder_at)} назад` : '—'
}

export function stageStartedText(task: TaskDetail): string {
  return task.stage_at ? `${formatDateTime(task.stage_at)} · ${task.stage_age}` : '—'
}

export interface DesktopHolderPresentation {
  holder: string
  holderHasTitle: boolean
  holderAge: string | null
  notTaken: boolean
  assigned: string
  assignedBy: string | null
  heartbeat: string
  stageStarted: string
  assignee: string | null
  note: string
  /**
   * «Кто выполнял» — у закрытой карточки (done/cancelled) с непустым `worked_by`:
   * держателя у неё может уже не быть, а оставшийся ничего не держит. `null` —
   * блок показывает держателя как раньше.
   */
  workedBy: { label: 'выполнял' | 'выполняли'; title: string; actor: string } | null
}

/** Значения блока «Кто держит» в настольной карточке. Разметка остаётся в TaskDrawer. */
export function desktopHolderPresentation(task: TaskDetail): DesktopHolderPresentation {
  const hasTitle = hasHolderTitle(task.holder_title)
  const isClosed = task.status === 'done' || task.status === 'cancelled'
  const workedKeys = task.worked_by ?? []
  return {
    workedBy: isClosed && workedKeys.length > 0
      ? {
          label: workedKeys.length > 1 ? 'выполняли' : 'выполнял',
          title: task.worked_by_title,
          actor: workedKeys[0],
        }
      : null,
    holder: hasTitle ? task.holder_title : 'никто',
    holderHasTitle: hasTitle,
    holderAge: hasTitle ? task.holder_age : null,
    notTaken: task.not_taken,
    assigned: `выдана, не взята ${task.assigned_age}`,
    assignedBy: task.holder_assigned_by_title,
    heartbeat: heartbeatText(task),
    stageStarted: task.stage_at ? `${formatDateTime(task.stage_at)} · ${task.stage_age}` : `— · ${task.stage_age}`,
    assignee: task.assignee && task.assignee !== task.holder ? (task.assignee_title || task.assignee) : null,
    note: task.holder_note ? `«${task.holder_note}»` : '—',
  }
}

export function latestReview(comments: TaskComment[]): TaskComment | null {
  const candidates = comments.filter((comment) => comment.kind === 'review' || comment.kind === 'verdict')
  return sortedCommentsDesc(candidates)[0] ?? null
}


export interface ColdRow {
  key: string
  label: string
  ok: boolean
  value: string
  tone: 'success' | 'warning' | 'danger'
  color?: string
  hint?: string
  words?: boolean
}

/** Строки блока «Холодный старт», одинаковые для любого представления задачи. */
export function coldStartRows(task: TaskDetail, blockedBy: DepInfo[], waitingFor: DepInfo[]): ColdRow[] {
  const rows: ColdRow[] = []
  const simpleRow = (key: string, label: string, ok: boolean, value: string): ColdRow => ({
    key,
    label,
    ok,
    value,
    tone: ok ? 'success' : 'warning',
  })

  rows.push(simpleRow('spec_path', 'spec_path', Boolean(task.spec_path), task.spec_path || 'ТЗ не привязано'))
  const checklistPath = task.checklist_path ?? null
  const acceptanceText = task.acceptance?.trim() || ''
  rows.push(simpleRow(
    'acceptance',
    'acceptance',
    Boolean(checklistPath) || Boolean(acceptanceText),
    checklistPath || (acceptanceText ? acceptanceText.split('\n')[0] : 'чек-листа нет'),
  ))
  const journalRef = task.decision_path ?? task.journal_path
  rows.push(simpleRow('journal_path', 'journal_path', Boolean(journalRef), journalRef || 'журнала нет'))

  const worktree = worktreeState(task.worktree, task.branch)
  rows.push({
    key: 'worktree',
    label: 'worktree · branch',
    ok: worktree.filled,
    value: worktreeValue(task.worktree, task.branch),
    tone: worktree.tone,
    color: worktree.color,
    hint: worktree.hint,
    words: !worktree.mono,
  })

  const blockedIds = blockedBy.map((dep) => dep.id)
  const waitingIds = waitingFor.map((dep) => dep.id)
  let blocksValue = blockedIds.length ? `ждёт ${blockedIds.join(', ')}` : 'ничего не ждёт'
  if (waitingIds.length) blocksValue += ` · её ждут ${waitingIds.join(', ')}`
  rows.push(simpleRow('blocks', 'blocks', true, blocksValue))

  const journalComments = task.comments
    .filter((comment) => comment.kind === 'journal')
    .sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  const lastJournal = journalComments[journalComments.length - 1]
  rows.push(simpleRow(
    'comment_journal',
    'comment -k journal',
    Boolean(lastJournal),
    lastJournal ? lastJournal.text.slice(0, 80) : 'журнальных записей нет',
  ))

  const reviewComments = task.comments.filter((comment) => comment.kind === 'review')
  rows.push(simpleRow(
    'comment_review',
    'comment -k review',
    Boolean(task.review_path) || reviewComments.length > 0,
    task.review_path || (reviewComments.length ? `${reviewComments.length} замечаний` : 'ревью нет'),
  ))
  return rows
}

export function coldStartTone(rows: ColdRow[]): 'success' | 'warning' | 'danger' {
  if (rows.length && rows.every((row) => row.ok)) return 'success'
  return rows.some((row) => row.tone === 'danger') ? 'danger' : 'warning'
}
