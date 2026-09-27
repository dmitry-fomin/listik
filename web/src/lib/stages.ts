/**
 * Конвейер шагов (s1…s4 → done) и тип перехода между этапами: sticky (держатель
 * остаётся), handoff (снимается, задача уходит в ready) или sticky-return (возврат
 * s4 → s3 после красного вердикта, держатель остаётся). Сами виды переходов приходят
 * с сервера — `projects[].routing_effective` и `meta.routing` из `/api/meta`; выбор
 * таблицы и вида перехода — в `lib/projects.ts`. Сами этапы (ключи, коды, подписи) —
 * в справочнике `lib/dictionaries.ts` и переэкспортируются отсюда.
 */
import type { PipelineStage, TaskStage } from '@/api/types'
import { DONE_STAGE, PIPELINE_STAGES, PIPELINE_STAGE_KEYS, stageIndex, type PipelineCode } from './dictionaries'

export { PIPELINE_STAGE_KEYS, stageIndex, type PipelineCode }

export type Transition = 'sticky' | 'handoff' | 'sticky-return'

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
