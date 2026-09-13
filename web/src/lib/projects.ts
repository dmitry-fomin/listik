/**
 * Метка проекта (монограмма + цвет) для `ProjectMark.vue` и любых мест доски,
 * где нужен знак проекта. Ничего не запрашивает — список проектов передаётся
 * вызывающей стороной (`store.meta.value.projects`).
 */
import type { ProjectRow } from '@/api/types'

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
