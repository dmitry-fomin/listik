/**
 * Метка проекта (монограмма + цвет) для `ProjectMark.vue` и любых мест доски,
 * где нужен знак проекта. Ничего не запрашивает — список проектов передаётся
 * вызывающей стороной (`store.meta.value.projects`).
 */
import type { ProjectRow } from '@/api/types'

export function projectBySlug(projects: ProjectRow[] | null | undefined, slug: string | null | undefined): ProjectRow | null {
  if (!slug) return null
  return projects?.find((project) => project.slug === slug) ?? null
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

/**
 * Подпись строки репозитория. Slug уехал из заголовка в подпись, но не пропал:
 * им проект зовётся в CLI (`listik -p <slug>`), поэтому он остаётся видимым —
 * кроме случая, когда названия нет и slug уже стоит заголовком.
 */
export function projectMetaLabel(project: ProjectRow): string {
  const parts = project.title ? [project.slug] : []
  parts.push(projectTasksLabel(project), projectPathLabel(project))
  return parts.join(' · ')
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
