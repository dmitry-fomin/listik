/**
 * Локальные конвейеры реализации — из плагина `feature-pipeline`
 * (`~/Agents/Claude/homemade-skills-claude-code/plugins/feature-pipeline/skills/*`).
 * Роли и модели здесь зашиты вручную по `SKILL.md` каждого пресета: сервер их не
 * знает, это только подсказка человеку в «Новой задаче» и метка `process:<key>`
 * на карточке (см. `routes.ts`). Пресеты с `strip` в таблицу ролей не попадают —
 * они рисуются одной иконкой в строке «Отдельно», рядом с `DIRECT_HARNESSES`
 * (`routes.ts`): `opus-single-pipeline` потому что у него и так только одна роль,
 * `feature-pipeline` потому что его роли не зафиксированы, а берутся из конфига.
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

export interface RoleCell {
  provider: ProviderKey
  /** короткая подпись под иконкой ячейки */
  label: string
  /** полная расшифровка — в тултип */
  title: string
}

export interface PipelineDef {
  key: string
  title: string
  /** цена/квота/повод одной строкой — под названием пресета */
  hint: string
  roles: Partial<Record<RoleKey, RoleCell>>
  /** есть только у пресетов строки «Отдельно» — одна иконка и подпись вместо таблицы ролей */
  strip?: { provider?: ProviderKey; glyph?: string; label: string }
}

export const PIPELINES: PipelineDef[] = [
  {
    key: 'xhigh-pipeline',
    title: 'xhigh',
    hint: 'внешних ~$4.89 · ~37 мин · когда ошибка дороже прогона',
    roles: {
      spec: { provider: 'claude', label: 'xhigh', title: 'Fable · xhigh' },
      critic: { provider: 'glm', label: 'GLM', title: 'GLM 5.3 Flash по HTTP' },
      impl: { provider: 'openai', label: 'xhigh', title: 'GPT-6 Astra · xhigh, в Codex' },
      judge: { provider: 'grok', label: 'xhigh', title: 'Grok 4.6 · xhigh' },
    },
  },
  {
    key: 'high-pipeline',
    title: 'high · по умолчанию',
    hint: 'внешних ~$2 · подписка тратится на ТЗ и код',
    roles: {
      spec: { provider: 'claude', label: 'medium', title: 'Fable · medium' },
      critic: { provider: 'glm', label: 'GLM', title: 'GLM 5.3 Flash по HTTP' },
      impl: { provider: 'claude', label: 'medium', title: 'Opus · medium' },
      judge: { provider: 'grok', label: 'xhigh', title: 'Grok 4.6 · xhigh' },
    },
  },
  {
    key: 'medium-pipeline',
    title: 'medium',
    hint: 'внешних ~$1.75 · ~29 мин · работа понятная, разгон не нужен',
    roles: {
      spec: { provider: 'claude', label: 'low', title: 'Fable · low' },
      critic: { provider: 'glm', label: 'GLM', title: 'GLM 5.3 Flash по HTTP' },
      impl: { provider: 'claude', label: 'medium', title: 'Opus · medium' },
      judge: { provider: 'grok', label: 'medium', title: 'Grok 4.6 · medium' },
    },
  },
  {
    key: 'low-pipeline',
    title: 'low',
    hint: 'внешних ~$0.99 · ~21 мин · когда поджимает квота подписки',
    roles: {
      spec: { provider: 'claude', label: 'low', title: 'Opus · low' },
      critic: { provider: 'glm', label: 'GLM', title: 'GLM 5.3 Flash по HTTP' },
      impl: { provider: 'deepseek', label: 'dsh', title: 'DeepSeek Harness (dsh), своя модель' },
      judge: { provider: 'grok', label: 'low', title: 'Grok 4.6 · low' },
    },
  },
  {
    key: 'inherit-pipeline',
    title: 'inherit',
    hint: 'без внешних харнессов · effort наследуется от оркестратора · вопросы этапов приходят вам по ходу',
    roles: {
      spec: { provider: 'claude', label: 'Fable', title: 'Fable · effort наследует от оркестратора' },
      critic: { provider: 'claude', label: 'Opus', title: 'Opus (критик) · effort наследует от оркестратора' },
      impl: { provider: 'claude', label: 'Sonnet', title: 'Sonnet · effort наследует от оркестратора' },
      judge: { provider: 'claude', label: 'Opus', title: 'Opus (судья) · effort наследует от оркестратора' },
    },
  },
  {
    key: 'opus-single-pipeline',
    title: '«Один прогон»',
    hint: 'внешних $0 · ~6 мин · без ТЗ, критики и приёмки — Opus делает всё сам и коммитит',
    roles: {
      impl: { provider: 'claude', label: 'Opus', title: 'Opus · medium — сам и коммитит' },
    },
    strip: { provider: 'claude', label: 'Opus' },
  },
  {
    key: 'opus-sonnet-pipeline',
    title: 'opus & sonnet',
    hint: 'пачка задач из трекера · без ТЗ и критики',
    roles: {
      impl: { provider: 'claude', label: 'Opus', title: 'Opus, medium или high' },
      judge: { provider: 'claude', label: 'Sonnet', title: 'Sonnet-судья, коммитит' },
    },
  },
  {
    key: 'feature-pipeline',
    title: 'По конфигу проекта',
    hint: 'те же четыре роли, но вендор каждой — из .claude/feature-pipeline.yaml, по умолчанию grok',
    roles: {
      spec: { provider: 'claude', label: 'Fable', title: 'Fable, эффорт из конфига' },
      critic: { provider: 'grok', label: '2nd', title: 'second-opinion, провайдер по умолчанию grok' },
      impl: { provider: 'grok', label: 'grok', title: 'grok:grok-delegate → dsh → локально (фолбэк)' },
      judge: { provider: 'claude', label: 'judge', title: 'pipeline-judge, локально' },
    },
    strip: { glyph: 'gear', label: 'по конфигу' },
  },
]

/** Таблица ролей — пресеты с зафиксированным составом ТЗ/критик/исполнитель/судья. */
export const TABLE_PIPELINES: PipelineDef[] = PIPELINES.filter((pipeline) => !pipeline.strip)

/** Строка «Отдельно» — пресеты без таблицы ролей, одна иконка и подпись каждый. */
export const STRIP_PIPELINES: PipelineDef[] = PIPELINES.filter((pipeline) => pipeline.strip)
