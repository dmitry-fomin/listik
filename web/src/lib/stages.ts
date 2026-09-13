/**
 * Конвейер шагов (s1…s4 → done) и тип перехода между этапами: sticky (держатель
 * остаётся) или handoff (снимается, задача уходит в ready). Значения — дефолт
 * сервера, не настройка. Сами этапы (ключи, коды, подписи) — в справочнике
 * `lib/dictionaries.ts` и переэкспортируются отсюда.
 */
import type { PipelineStage, TaskStage } from '@/api/types'
import { DONE_STAGE, PIPELINE_STAGES, PIPELINE_STAGE_KEYS, stageIndex, type PipelineCode } from './dictionaries'

export { PIPELINE_STAGE_KEYS, stageIndex, type PipelineCode }

export type Transition = 'sticky' | 'handoff'

export interface PipelineStep {
  key: PipelineStage
  code: PipelineCode
  title: string
}

export const PIPELINE: PipelineStep[] = PIPELINE_STAGES.map((step) => ({
  key: step.value,
  code: step.code,
  title: step.label,
}))

export function stageCode(stage: TaskStage): PipelineCode | null {
  return PIPELINE.find((step) => step.key === stage)?.code ?? null
}

export function stageTitle(stage: TaskStage): string | null {
  if (stage === DONE_STAGE.value) return DONE_STAGE.label
  return PIPELINE.find((step) => step.key === stage)?.title ?? null
}

export type TransitionKey = 's1-spec:s2-review' | 's2-review:s3-impl' | 's3-impl:s4-judge' | 's4-judge:done'

export const TRANSITIONS: Record<TransitionKey, Transition> = {
  's1-spec:s2-review': 'sticky',
  's2-review:s3-impl': 'handoff',
  's3-impl:s4-judge': 'sticky',
  's4-judge:done': 'handoff',
}

const NEXT_TRANSITION_KEY: Partial<Record<TaskStage & string, TransitionKey>> = {
  's1-spec': 's1-spec:s2-review',
  's2-review': 's2-review:s3-impl',
  's3-impl': 's3-impl:s4-judge',
  's4-judge': 's4-judge:done',
}

/** Тип перехода, которым текущий этап закрывается (в следующий этап конвейера). */
export function transitionOut(stage: TaskStage): Transition | null {
  if (!stage) return null
  const key = NEXT_TRANSITION_KEY[stage]
  return key ? TRANSITIONS[key] : null
}
