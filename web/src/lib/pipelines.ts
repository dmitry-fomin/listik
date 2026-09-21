/**
 * Словарь ролей конвейера для таблицы маршрутов «Новой задачи». Сами маршруты
 * (пресеты конвейеров и прямые харнессы) в коде не зашиты: их отдаёт сервер —
 * `GET /api/routes`, файл `routes.json` (см. `RouteDef` в `api/types.ts`).
 * Здесь остаются только роли и вендоры, которыми размечены ячейки таблицы.
 */
import type { PipelineStage } from '@/api/types'

export type RoleKey = 'spec' | 'critic' | 'impl' | 'judge'

export const ROLE_KEYS: RoleKey[] = ['spec', 'critic', 'impl', 'judge']

export const ROLE_TITLES: Record<RoleKey, string> = {
  spec: 'ТЗ',
  critic: 'Критик',
  impl: 'Исполнитель',
  judge: 'Судья',
}

/**
 * Роль конвейера ↔ этап задачи — единственное место этого соответствия.
 * Подписи этапов (`s1` / «ТЗ и чек-лист» …) берутся уже из `PIPELINE_STAGES`
 * (`lib/dictionaries.ts`) по этому значению, чтобы в шаблонах не заводились
 * свои литералы этапов.
 */
export const ROLE_STAGE: Record<RoleKey, PipelineStage> = {
  spec: 's1-spec',
  critic: 's2-review',
  impl: 's3-impl',
  judge: 's4-judge',
}

/** Вендор роли — своя мини-таксономия, не `HarnessKey`: GLM никогда не держатель задачи на сервере. */
export type ProviderKey = 'claude' | 'glm' | 'openai' | 'grok' | 'deepseek' | 'devin'

/** Тот же набор списком — фолбэк селекта вендора, пока справочник сервера не загружен. */
export const PROVIDER_KEYS: ProviderKey[] = ['claude', 'glm', 'openai', 'grok', 'deepseek', 'devin']

/** Значение параметра запускатора: плоский скаляр, как и на сервере. */
export type RoleParamValue = string | number | boolean

export interface RoleCell {
  provider: ProviderKey
  /** короткая подпись под иконкой ячейки */
  label: string
  /** полная расшифровка — в тултип */
  title: string
  /** скил-запускатор роли (`плагин:скил`); нет — роль описана без запускателя */
  skill?: string
  /** параметры запускателя; без `skill` сервер их не принимает */
  params?: Record<string, RoleParamValue>
}

/** Имя параметра — то же правило, что у сервера (`listik/routes.py`). */
export const PARAM_KEY_RE = /^[a-z][a-z0-9_]*$/
