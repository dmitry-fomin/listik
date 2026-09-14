/**
 * Значения фильтров. Тип, этап и статус — из справочника `dictionaries.ts`;
 * проекты и исполнители — из /api/meta.
 */
import type { Meta } from '@/api/types'
import { STAGES, STATUSES, TASK_TYPES } from './dictionaries'

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

export function assigneeOptions(meta: Meta | null): FacetsOption[] {
  const fromFacets = meta?.facets?.assignees ?? []
  return fromFacets.map((value) => {
    const actor = (meta?.actors ?? []).find((item) => item.key === value)
    return { value, label: actor?.title || value }
  })
}

export function typeOptions(): FacetsOption[] {
  return TASK_TYPES.map(({ value, label }) => ({ value, label }))
}

export interface ActorOption {
  key: string
  title: string | null
}

export function actorList(meta: Meta | null): ActorOption[] {
  const fromFacets = meta?.facets?.actors?.values
  if (fromFacets && fromFacets.length > 0) {
    return fromFacets.map((actor) => ({ key: actor.key, title: actor.title }))
  }
  return (meta?.actors ?? []).map((actor) => ({ key: actor.key, title: actor.title }))
}
