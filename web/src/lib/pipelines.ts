/**
 * Словарь ролей конвейера для таблицы маршрутов «Новой задачи». Сами маршруты
 * (пресеты конвейеров и прямые харнессы) в коде не зашиты: их отдаёт сервер —
 * `GET /api/routes`, файл `routes.json` (см. `RouteDef` в `api/types.ts`).
 * Здесь остаются только роли и вендоры, которыми размечены ячейки таблицы.
 */

export type RoleKey = 'spec' | 'critic' | 'impl' | 'judge'

export const ROLE_KEYS: RoleKey[] = ['spec', 'critic', 'impl', 'judge']

export const ROLE_TITLES: Record<RoleKey, string> = {
  spec: 'ТЗ',
  critic: 'Критик',
  impl: 'Исполнитель',
  judge: 'Судья',
}

/** Вендор роли — своя мини-таксономия, не `HarnessKey`: GLM никогда не держатель задачи на сервере. */
export type ProviderKey = 'claude' | 'glm' | 'openai' | 'grok' | 'deepseek'

/** Тот же набор списком — фолбэк селекта вендора, пока справочник сервера не загружен. */
export const PROVIDER_KEYS: ProviderKey[] = ['claude', 'glm', 'openai', 'grok', 'deepseek']

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
