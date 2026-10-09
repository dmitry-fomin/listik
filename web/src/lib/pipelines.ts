/**
 * Словарь ролей конвейера для таблицы маршрутов «Новой задачи». Сами маршруты
 * (пресеты конвейеров и маршруты роя) в коде не зашиты: их отдаёт сервер —
 * `GET /api/routes` (таблица `routes`; см. `RouteDef` в `api/types.ts`).
 * Здесь остаются только роли и вендоры, которыми размечены ячейки таблицы.
 */
import type { PipelineStage, SwarmRoleCell } from '@/api/types'
import { PIPELINE_STAGES, type PipelineStep } from '@/lib/dictionaries'

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

/**
 * Этап конвейера (код `s1`…, подпись) для роли; `null` — `role` не ключ `ROLE_STAGE`.
 */
export function roleStage(role: string | null | undefined): PipelineStep | null {
  if (!role || !Object.prototype.hasOwnProperty.call(ROLE_STAGE, role)) return null
  const stage = ROLE_STAGE[role as RoleKey]
  return PIPELINE_STAGES.find((item) => item.value === stage) ?? null
}

/**
 * Обратное соответствие этап → роль — собрано из `ROLE_STAGE`, чтобы литералы
 * этапов и ролей не дублировались в двух местах. По нему `lib/executors.ts`
 * находит ячейку `roles` записи маршрута для текущего этапа задачи.
 */
export const STAGE_ROLE: Record<PipelineStage, RoleKey> = Object.fromEntries(
  (Object.entries(ROLE_STAGE) as [RoleKey, PipelineStage][]).map(([role, stage]) => [stage, role]),
) as Record<PipelineStage, RoleKey>

/** Вендор роли — своя мини-таксономия, не `HarnessKey`: GLM никогда не держатель задачи на сервере. */
export type ProviderKey = 'claude' | 'glm' | 'openai' | 'grok' | 'deepseek' | 'devin'

/** Значение параметра запускатора: плоский скаляр, как и на сервере. */
export type RoleParamValue = string | number | boolean

export interface RoleCell {
  provider: ProviderKey
  /**
   * усилие одиночной модели (`xhigh`, `max`); если моделей несколько — короткая
   * подпись вроде `S+DS+GLM`
   */
  label: string
  /** модель или модели по договорённости `roleModels`: `Модель · усилие + Модель …` */
  title: string
  /** скил-запускатор роли (`плагин:скил`); нет — роль описана без запускателя */
  skill?: string
  /** параметры запускателя; без `skill` сервер их не принимает */
  params?: Record<string, RoleParamValue>
}

/** `label`, если его нет целым словом в `title` (без учёта регистра), иначе `null`. */
export function effortOf(label: string, title: string): string | null {
  if (!label) return null
  const needle = label.toLowerCase()
  const words = title.toLowerCase().split(/[\s·—\-:,]+/)
  return words.includes(needle) ? null : label
}

/** Строка модели ячейки роли: имя и усилие (`null` — усилия нет). */
export interface RoleModel {
  name: string
  effort: string | null
  /** модель стоит после ` → `: зовётся, только если предыдущие что-то нашли (линзы → судья) */
  escalation: boolean
}

/**
 * Модели ячейки роли из `title` по договорённости: модели через ` + `
 * (параллельно) или ` → ` (следующая зовётся только при находке), усилие
 * модели — после последнего ` · ` в её части. У единственной части без ` · `
 * усилием считается `label` (`effortOf`: только если его нет словом в имени);
 * у нескольких частей `label` — сокращение и не используется. Пустой `title`
 * даёт одну строку `{ name: label, effort: null }`. Названий моделей функция
 * не знает и текст не переписывает — только делит.
 *
 * Пример: `title` «Sonnet 5.5 · high + DeepSeek V4.1 Flash + GLM 5.3 Flash» →
 * Sonnet 5.5/high, DeepSeek V4.1 Flash/null, GLM 5.3 Flash/null; `label` «xhigh» и `title`
 * «Opus 5.5» → Opus 5.5/xhigh; «GLM 5.3 Flash · 3 линзы → Grok 4.7 · xhigh» →
 * GLM 5.3 Flash/3 линзы, Grok 4.7/xhigh с `escalation`.
 */
export function roleModels(cell: Pick<RoleCell, 'label' | 'title'>): RoleModel[] {
  const parts = (cell.title ?? '')
    .split(/( \+ | → )/)
    .reduce<{ text: string; escalation: boolean }[]>((acc, chunk, at, all) => {
      if (at % 2 === 0 && chunk.trim()) acc.push({ text: chunk.trim(), escalation: all[at - 1] === ' → ' })
      return acc
    }, [])
  if (parts.length === 0) return [{ name: cell.label, effort: null, escalation: false }]
  return parts.map(({ text, escalation }) => {
    const at = text.lastIndexOf(' · ')
    if (at !== -1) {
      return { name: text.slice(0, at).trim(), effort: text.slice(at + 3).trim() || null, escalation }
    }
    return { name: text, effort: parts.length === 1 ? effortOf(cell.label, text) : null, escalation }
  })
}

/** Имя параметра — то же правило, что у сервера (`listik/routes.py`). */
export const PARAM_KEY_RE = /^[a-z][a-z0-9_]*$/

/**
 * Ячейка роли роевого исполнения (ячейка маршрута `kind=swarm`,
 * listik-2gry): исполнитель — харнесс из каталога, а не
 * вендор с подписью. Тип объявлен в `api/types.ts` (`SwarmRoleCell`),
 * предикат здесь — рядом с `RoleCell`, которому он альтернатива.
 */

/** Ячейка роевой формы: есть `harness`, нет `provider`. */
export function isSwarmCell(cell: unknown): cell is SwarmRoleCell {
  return (
    typeof cell === 'object' &&
    cell !== null &&
    'harness' in cell &&
    typeof (cell as { harness?: unknown }).harness === 'string'
  )
}

/** Ячейка скиловой формы (`{provider,label,title}`) — обратный предикат. */
export function isProviderCell(cell: unknown): cell is RoleCell {
  return typeof cell === 'object' && cell !== null && 'provider' in cell
}
