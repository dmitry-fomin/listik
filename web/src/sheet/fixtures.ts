/**
 * Фиктивные данные dev-витрины (`Sheet.vue`). В API витрина не ходит — тут
 * ровно то, что нужно показать примитивы кита и свои марки живьём.
 */
import type { ProjectRow } from '@/api/types'
import type { UiTabItem } from '@zoloto585/facet'
import type { HarnessKey } from '@/lib/harness'
import type { Health } from '@/lib/health'

export const FIXTURE_PROJECTS: ProjectRow[] = [
  { slug: 'zoloto585-symfony', title: 'Symfony', kind: 'git', color: 'chart-2' },
  { slug: 'zoloto585-orders', title: 'Orders', kind: 'git', color: '' },
  { slug: 'PremiumTackleFinal', title: 'PremiumTackle', kind: 'git', color: '#CD68CB' },
  { slug: 'VastAISmartClient', title: 'VastAI', kind: 'git', color: 'chart-3' },
  { slug: 'dicms', title: 'dicms', kind: 'git', color: 'chart-6' },
  { slug: 'listik', title: 'Listik', kind: 'git', color: null },
]

export const FIXTURE_HARNESSES: HarnessKey[] = ['claude', 'dsh', 'codex', 'grok', 'gemini', 'human']

export const FIXTURE_HEALTH_LEGEND: { health: Health; hint: string }[] = [
  { health: 'healthy', hint: 'hb < 15 мин' },
  { health: 'at-risk', hint: 'молчит или дольше порога' },
  { health: 'dead', hint: 'брошена, hb > 24 ч' },
  { health: 'unknown', hint: 'без держателя' },
]

export const FIXTURE_VIEW_TABS: UiTabItem[] = [
  { key: 'board', label: 'Доска (12)' },
  { key: 'list', label: 'Список' },
  { key: 'metrics', label: 'Метрики' },
]

export const FIXTURE_STEPS = [
  { label: 's1 · ТЗ и чек-лист', description: 'claude · 34 мин · sticky →', status: 'done' as const },
  { label: 's2 · Второе мнение', description: 'dsh · 12 мин · handoff →', status: 'done' as const },
  { label: 's3 · Реализация', description: 'claude · 1.2 ч · sticky →', status: 'current' as const },
  { label: 's4 · Проверка и коммит', description: 'ожидает handoff', status: 'upcoming' as const },
  { label: 'done', status: 'upcoming' as const },
]

export const FIXTURE_TIMELINE = [
  { id: 1, title: 'Взял в работу', description: 'claude', timestamp: '12.03.2026 09:14', tone: 'accent' as const },
  { id: 2, title: 'Этап s2 → s3', description: 'handoff', timestamp: '12.03.2026 08:40', tone: 'info' as const },
  { id: 3, title: 'Второе мнение: одобрено', description: 'dsh', timestamp: '12.03.2026 08:12', tone: 'success' as const },
  { id: 4, title: 'Красный вердикт', description: 'нужны правки', timestamp: '11.03.2026 22:03', tone: 'danger' as const },
  { id: 5, title: 'Заведена', description: 'me', timestamp: '11.03.2026 20:00', tone: 'neutral' as const },
]

export const FIXTURE_SELECT_OPTIONS = FIXTURE_PROJECTS.map((project) => ({
  value: project.slug,
  label: project.title ?? project.slug,
}))
