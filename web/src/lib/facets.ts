/**
 * Значения фильтров. Тип, этап и статус — из справочника `dictionaries.ts`;
 * проекты и исполнители — из /api/meta.
 */
import type { Meta } from '@/api/types'
import { STAGES, STATUSES, TASK_TYPES } from './dictionaries'

/**
 * Заглушка «нет значения»: ею сервер (`store.facet_values`) обозначает пустое поле
 * в фасетах `/api/meta` — например, задачи без оркестратора.
 */
export const NO_VALUE = '—'

export interface FacetsOption {
  value: string
  label: string
}

function unique(values: (string | null | undefined)[]): string[] {
  return [...new Set(values.filter((value): value is string => Boolean(value)))].sort((a, b) =>
    a.localeCompare(b, 'ru'),
  )
}

export function projectOptions(meta: Meta | null): FacetsOption[] {
  const fromFacets = meta?.facets?.projects ?? []
  const fromProjects = (meta?.projects ?? []).map((project) => project.slug)
  return unique([...fromFacets, ...fromProjects]).map((value) => {
    const found = (meta?.projects ?? []).find((project) => project.slug === value)
    return { value, label: found?.title || value }
  })
}

export function statusOptions(): FacetsOption[] {
  return STATUSES.map(({ value, label }) => ({ value, label }))
}

export function stageOptions(): FacetsOption[] {
  return STAGES.map(({ value, label }) => ({ value, label }))
}

export function orchestratorOptions(meta: Meta | null): FacetsOption[] {
  const fromFacets = meta?.facets?.orchestrators ?? []
  return fromFacets.map((value) => {
    const actor = (meta?.actors ?? []).find((item) => item.key === value)
    return { value, label: actor?.title || value }
  })
}

/**
 * Типы задач доски: `meta.issue_types` в его порядке и составе (тип без записи в справочнике —
 * с подписью из meta, оформление у него от `task`); без `issue_types` (meta не загружена,
 * сервер старый) — справочник `TASK_TYPES`.
 */
export function issueTypes(meta: Meta | null | undefined): FacetsOption[] {
  const fromMeta = meta?.issue_types
  if (!fromMeta) return TASK_TYPES.map(({ value, label }) => ({ value, label }))
  return Object.entries(fromMeta).map(([value, label]) => ({ value, label }))
}

export function typeOptions(meta: Meta | null): FacetsOption[] {
  return issueTypes(meta)
}
