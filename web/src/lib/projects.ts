/**
 * Метка проекта (монограмма + цвет) для `ProjectMark.vue` и любых мест доски,
 * где нужен знак проекта. Ничего не запрашивает — список проектов передаётся
 * вызывающей стороной (`store.meta.value.projects`).
 */
import type { Meta, ProjectRow, TaskStage } from '@/api/types'
import { PIPELINE_STAGE_KEYS, type Transition } from '@/lib/stages'
import { DONE_STAGE } from '@/lib/dictionaries'

export function projectBySlug(projects: ProjectRow[] | null | undefined, slug: string | null | undefined): ProjectRow | null {
  if (!slug) return null
  return projects?.find((project) => project.slug === slug) ?? null
}

/**
 * Таблица переходов для проекта `slug`: его `routing_effective` из `meta.projects`,
 * а если проекта или поля нет — общая `meta.routing`. `null` — meta не загружена
 * или сервер их не отдаёт; своей запасной таблицы у доски нет.
 */
export function projectTransitions(
  meta: Meta | null | undefined,
  slug: string | null | undefined,
): Record<string, Transition> | null {
  const project = projectBySlug(meta?.projects, slug)
  return project?.routing_effective?.transitions ?? meta?.routing?.transitions ?? null
}

/**
 * Вид перехода, которым этап `stage` закрывается в следующий этап конвейера
 * (s1 → s2 → s3 → s4 → done). `null` — таблицы нет или `stage` не s1…s4. Ключа
 * в таблице нет — `handoff`, как решает сервер.
 */
export function transitionOut(transitions: Record<string, Transition> | null, stage: TaskStage): Transition | null {
  const index = stage ? PIPELINE_STAGE_KEYS.indexOf(stage) : -1
  if (!transitions || index === -1) return null
  const next = PIPELINE_STAGE_KEYS[index + 1] ?? DONE_STAGE.value
  return transitions[`${stage}:${next}`] ?? 'handoff'
}

/** Сохраняет ли переход держателя: сервер снимает его только на `handoff`. */
export function keepsHolder(kind: Transition | null): boolean {
  return kind !== null && kind !== 'handoff'
}

export function projectTasksLabel(project: ProjectRow): string {
  const open = project.n_open ?? 0
  const total = project.n_tasks ?? 0
  if (!total) return 'задач нет'
  if (open === total) return `задач: ${total}`
  return `задач: ${total} · живых: ${open}`
}

/** Заголовок строки репозитория: человеку — название, и только без него — slug. */
export function projectTitleLabel(project: ProjectRow): string {
  return project.title || project.slug
}

export function projectPathLabel(project: ProjectRow): string {
  if (!project.path) return 'каталог не указан'
  return project.path_exists === false ? `${project.path} — каталога нет` : project.path
}

export function projectIsGit(project: ProjectRow): boolean {
  return Boolean(project.git_branch || project.git_remote)
}

export function projectGitHint(project: ProjectRow): string {
  const parts = ['git-репозиторий']
  if (project.git_branch) parts.push(`ветка ${project.git_branch}`)
  parts.push(project.git_remote ? `remote: ${project.git_remote}` : 'remote не задан')
  return parts.join(' · ')
}

export interface ProjectMarkResult {
  mark: string
  color: string
  title: string
}

const CHART_COLOR_RE = /^chart-([1-6])$/

/** Первые две буквы: первая заглавная, вторая строчная; одна буква — если строка короче двух символов. */
function monogram(source: string): string {
  const trimmed = source.trim()
  if (!trimmed) return '?'
  if (trimmed.length < 2) return trimmed.toUpperCase()
  return trimmed[0].toUpperCase() + trimmed[1].toLowerCase()
}

/** N = 1 + (сумма кодов символов slug mod 6) — детерминированно между перезагрузками. */
function fallbackChartIndex(slug: string): number {
  let sum = 0
  for (let i = 0; i < slug.length; i += 1) sum += slug.charCodeAt(i)
  return 1 + (sum % 6)
}

export function projectMark(
  project: Pick<ProjectRow, 'slug' | 'title' | 'color'> | null | undefined,
  slug?: string | null,
): ProjectMarkResult {
  const effectiveSlug = project?.slug ?? slug ?? ''
  const title = project?.title || effectiveSlug

  const markSource = title.includes('/') ? title.slice(title.lastIndexOf('/') + 1) : title
  const mark = monogram(markSource)

  const rawColor = project?.color
  let color: string
  if (rawColor) {
    const match = CHART_COLOR_RE.exec(rawColor)
    color = match ? `var(--chart-${match[1]})` : rawColor
  } else {
    color = `var(--chart-${fallbackChartIndex(effectiveSlug)})`
  }

  return { mark, color, title }
}
