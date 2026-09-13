<script setup lang="ts">
/**
 * Окно «Новая задача» (референс: `docs/prototype/NewTask-html/NewTask.dc.html`,
 * `.modal`). Поля Тип/Приоритет/Проект/Заголовок/Описание·ТЗ/Критерии
 * приёмки/`spec_path` — как в форме прототипа, без блока оценки DeepSeek
 * (решение автора). Два отличия от прототипа: порядок первого ряда
 * Тип → Приоритет → Проект (в прототипе Проект второй) и подсказка приоритета
 * тултипом UiTooltip на контроле, а не строкой под ним. Блок «Маршрут» — свой (кит не знает такого
 * контрола): список пресетов конвейера из `lib/pipelines.ts` (роли ТЗ/критик/исполнитель/судья —
 * из `SKILL.md` плагина `feature-pipeline`, `~/Agents/Claude/homemade-skills-claude-code/plugins/
 * feature-pipeline`) плюс отдельный ряд «просто исполнитель» (dsh/grok/codex без ролей), логика
 * выбора — `lib/routes.ts`. Выбор уходит в метки `harness:<x>`/`process:<y>` на карточке — сервер их
 * не читает, полей `harness`/`skill` у задачи нет и после шага 04; кто реально допущен до этапа,
 * решает routing проекта на сервере (`ready --harness`, отказ в `claim`).
 */
import { computed, reactive, ref, watch } from 'vue'
import {
  UiButton,
  UiDrawer,
  UiField,
  UiInput,
  UiSelect,
  UiTextarea,
  UiTooltip,
  type UiSelectOption,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from '@/components/IconToggle.vue'
import ListikIcon from '@/components/ListikIcon.vue'
import HarnessIcon from '@/components/marks/HarnessIcon.vue'
import ProjectMark from '@/components/marks/ProjectMark.vue'
import ProviderIcon from '@/components/marks/ProviderIcon.vue'
import TaskGlyph from '@/components/marks/TaskGlyph.vue'
import { TASK_TYPES, priority } from '@/lib/dictionaries'
import type { ProjectRow } from '@/api/types'
import { HARNESS_TITLES, type HarnessKey } from '@/lib/harness'
import { ROLE_KEYS, ROLE_TITLES, type PipelineDef } from '@/lib/pipelines'
import {
  DEFAULT_ROUTE,
  defaultPipelineFor,
  DIRECT_HARNESSES,
  directAllowed,
  pipelineAllowed,
  PIPELINES,
  routeLabels,
  STRIP_PIPELINES,
  TABLE_PIPELINES,
} from '@/lib/routes'

const props = defineProps<{
  projects: ProjectRow[]
  pending: boolean
}>()

const emit = defineEmits<{
  submit: [
    body: {
      title: string
      project: string
      type: string
      priority: number
      description: string
      acceptance: string
      spec_path?: string
      labels: string[]
      actor: 'me'
    },
  ]
}>()

const isOpen = defineModel<boolean>({ default: false })

const TYPE_OPTIONS: IconToggleOption<string>[] = TASK_TYPES.map((item) => ({ value: item.value, label: item.hint }))

const CREATE_LABEL: Record<string, string> = Object.fromEntries(
  TASK_TYPES.map((item) => [item.value, item.createLabel]),
)

function defaults() {
  return {
    type: 'task',
    project: '' as string | null,
    priority: '2',
    title: '',
    description: '',
    acceptance: '',
    specPath: '',
    routeMode: DEFAULT_ROUTE.mode as 'pipeline' | 'direct',
    pipeline: defaultPipelineFor('task'),
    directHarness: 'dsh' as HarnessKey,
  }
}

const form = reactive(defaults())
const submitted = ref(false)
/** Пока false — маршрут следует умолчанию по типу (эпик → high, иначе low); ручной выбор это выключает. */
const routeTouched = ref(false)

function resetForm(): void {
  Object.assign(form, defaults())
  submitted.value = false
  routeTouched.value = false
}

watch(isOpen, (open) => {
  if (!open) resetForm()
})

const projectOptions = computed<UiSelectOption[]>(() =>
  props.projects.map((project) => ({ value: project.slug, label: project.title || project.slug })),
)

const selectedProject = computed(() => props.projects.find((project) => project.slug === form.project) ?? null)

const priorityOptions = computed<IconToggleOption<string>[]>(() => {
  const values = form.type === 'bug' ? ['0', '1', '2', '3'] : ['1', '2', '3', '4']
  return values.map((value) => ({ value, label: `P${value} · ${priority(value).label}` }))
})

// Текст тултипа поля «Приоритет» — прежняя видимая подсказка под контролем
// (заголовок выбранного приоритета + правило P0), теперь только по hover/focus.
const priorityHint = computed(() => {
  const title = priority(form.priority).label
  const rule = form.type === 'bug' ? 'для багов доступны P0–P3, P4 не назначается' : 'P0 — только для багов'
  return `${title} · ${rule}`
})

watch(
  () => form.type,
  (type, previous) => {
    if (previous === 'bug' && type !== 'bug' && form.priority === '0') form.priority = '1'
    if (type === 'bug' && previous !== 'bug' && form.priority === '4') form.priority = '3'

    if (!routeTouched.value) {
      // Маршрут ещё не трогали руками — держим умолчание своим для каждого типа.
      form.routeMode = DEFAULT_ROUTE.mode
      form.pipeline = defaultPipelineFor(type)
    } else if (type === 'epic') {
      const currentPipeline = PIPELINES.find((item) => item.key === form.pipeline)
      const stillAllowed = form.routeMode === 'pipeline' && currentPipeline && pipelineAllowed(currentPipeline, type)
      if (!stillAllowed) {
        form.routeMode = DEFAULT_ROUTE.mode
        form.pipeline = defaultPipelineFor(type)
      }
    }
  },
)

const titleError = computed(() => (submitted.value && !form.title.trim() ? 'нужен заголовок' : null))

const createDisabled = computed(() => props.pending || !form.title.trim() || !form.project)

// ── маршрут: пайплайн (ТЗ → критик → исполнитель → судья) или просто исполнитель ──

interface PipelineRow {
  pipeline: PipelineDef
  allowed: boolean
  selected: boolean
}

interface DirectItem {
  harness: HarnessKey
  allowed: boolean
  selected: boolean
}

function pipelineRowOf(pipeline: PipelineDef): PipelineRow {
  return {
    pipeline,
    allowed: pipelineAllowed(pipeline, form.type),
    selected: form.routeMode === 'pipeline' && form.pipeline === pipeline.key,
  }
}

const pipelineRows = computed<PipelineRow[]>(() => TABLE_PIPELINES.map(pipelineRowOf))

/** Пресеты без таблицы ролей (одна роль или роли из конфига) — в строке «Отдельно», не в таблице. */
const stripRows = computed<PipelineRow[]>(() => STRIP_PIPELINES.map(pipelineRowOf))

const directItems = computed<DirectItem[]>(() =>
  DIRECT_HARNESSES.map((harness) => ({
    harness,
    allowed: directAllowed(form.type),
    selected: form.routeMode === 'direct' && form.directHarness === harness,
  })),
)

type RouteOption = ({ kind: 'pipeline' } & PipelineRow) | ({ kind: 'direct' } & DirectItem)

const routeOptions = computed<RouteOption[]>(() => [
  ...pipelineRows.value.map((row): RouteOption => ({ kind: 'pipeline', ...row })),
  ...stripRows.value.map((row): RouteOption => ({ kind: 'pipeline', ...row })),
  ...directItems.value.map((item): RouteOption => ({ kind: 'direct', ...item })),
])

function selectPipeline(pipeline: PipelineDef): void {
  if (!pipelineAllowed(pipeline, form.type)) return
  routeTouched.value = true
  form.routeMode = 'pipeline'
  form.pipeline = pipeline.key
}

function selectDirect(harness: HarnessKey): void {
  if (!directAllowed(form.type)) return
  routeTouched.value = true
  form.routeMode = 'direct'
  form.directHarness = harness
}

function routeOptionKey(option: RouteOption): string {
  return option.kind === 'pipeline' ? `pipeline:${option.pipeline.key}` : `direct:${option.harness}`
}

function routeTabindex(option: RouteOption): number {
  if (!option.allowed) return -1
  if (option.selected) return 0
  const hasSelected = routeOptions.value.some((item) => item.allowed && item.selected)
  if (hasSelected) return -1
  // Ни одна опция не выбрана (не должно случаться — есть DEFAULT_ROUTE) —
  // фокусируемая первая доступная.
  const firstAllowed = routeOptions.value.find((item) => item.allowed)
  return firstAllowed && routeOptionKey(firstAllowed) === routeOptionKey(option) ? 0 : -1
}

const routeRefs = new Map<string, HTMLButtonElement>()

function setRouteRef(option: RouteOption, el: Element | null): void {
  const key = routeOptionKey(option)
  if (el) routeRefs.set(key, el as HTMLButtonElement)
  else routeRefs.delete(key)
}

function onRouteKeydown(event: KeyboardEvent, option: RouteOption): void {
  const allowedOptions = routeOptions.value.filter((item) => item.allowed)
  const at = allowedOptions.findIndex((item) => routeOptionKey(item) === routeOptionKey(option))
  if (at === -1) return
  let target = -1
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') target = (at + 1) % allowedOptions.length
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') target = (at - 1 + allowedOptions.length) % allowedOptions.length
  else if (event.key === 'Home') target = 0
  else if (event.key === 'End') target = allowedOptions.length - 1
  else return
  event.preventDefault()
  const next = allowedOptions[target]!
  if (next.kind === 'pipeline') selectPipeline(next.pipeline)
  else selectDirect(next.harness)
  routeRefs.get(routeOptionKey(next))?.focus()
}

function pipelineRowTooltip(row: PipelineRow): string {
  if (row.allowed) return row.pipeline.hint
  return 'Эпик всегда режется на шаги через ТЗ (s1) — пресеты без этапа ТЗ для эпиков закрыты.'
}

function directItemTooltip(item: DirectItem): string {
  if (item.allowed) return `${HARNESS_TITLES[item.harness]} делает задачу напрямую, без ТЗ, критики и приёмки`
  return 'Эпик всегда режется на шаги через ТЗ (s1) — прямой маршрут в обход разбивки на шаги и критики закрыт для эпиков.'
}

// ── отправка ──────────────────────────────────────────────────────────────

function submit(): void {
  submitted.value = true
  if (createDisabled.value) return
  emit('submit', {
    title: form.title.trim(),
    project: form.project as string,
    type: form.type,
    priority: Number(form.priority),
    description: form.description,
    acceptance: form.acceptance,
    ...(form.specPath.trim() ? { spec_path: form.specPath.trim() } : {}),
    labels: routeLabels(form.routeMode, form.pipeline, form.directHarness),
    actor: 'me',
  })
}

function cancel(): void {
  isOpen.value = false
}
</script>

<template>
  <UiDrawer v-model="isOpen" size="lg" side="right" title="Новая задача">
    <template #header>
      <div class="listik-stack" style="gap: var(--space-1)">
        <h2 class="ui-drawer__title">Новая задача</h2>
        <span class="listik-section__hint">что делаем, в каком проекте и как: кто исполняет и по какому процессу</span>
      </div>
    </template>

    <div class="listik-stack">
      <div class="listik-newtask-row">
        <UiField label="Тип">
          <IconToggle v-model="form.type" :options="TYPE_OPTIONS" ariaLabel="Тип задачи" size="md">
            <template #icon="{ option }">
              <TaskGlyph kind="type" :value="option.value" size="md" />
            </template>
          </IconToggle>
        </UiField>

        <UiField label="Приоритет">
          <UiTooltip :text="priorityHint" placement="bottom">
            <IconToggle v-model="form.priority" :options="priorityOptions" ariaLabel="Приоритет" size="md">
              <template #icon="{ option }">
                <TaskGlyph kind="priority" :value="Number(option.value)" />
              </template>
            </IconToggle>
          </UiTooltip>
        </UiField>

        <UiField label="Проект" required>
          <div class="listik-row" style="flex-wrap: nowrap">
            <ProjectMark :project="selectedProject" size="sm" />
            <UiSelect
              v-model="form.project"
              :options="projectOptions"
              placeholder="проект"
              style="min-width: 0; flex: 1"
            />
          </div>
        </UiField>
      </div>

      <UiField label="Заголовок" required :error="titleError">
        <UiInput v-model="form.title" placeholder="тема задачи" :error="titleError" />
      </UiField>

      <UiField
        label="Описание · ТЗ"
        hint="markdown · эпик режется на шаги и порции на этапе s1, здесь только суть"
      >
        <UiTextarea v-model="form.description" autosize :rows="5" placeholder="что делаем и зачем" />
      </UiField>

      <UiField label="Критерии приёмки">
        <UiTextarea v-model="form.acceptance" autosize :rows="3" placeholder="как проверить, что готово" />
      </UiField>

      <UiField
        label="Путь к ТЗ (spec_path)"
        hint="относительно корня репозитория проекта; можно заполнить позже"
      >
        <UiInput v-model="form.specPath" placeholder="docs/specs/….md" />
      </UiField>

      <section class="listik-stack" style="gap: var(--space-2)">
        <h4 class="listik-section__title" style="font-size: var(--text-md)">
          Маршрут · кто исполняет и по какому процессу
        </h4>

        <div class="listik-pipelines" role="radiogroup" aria-label="Маршрут: пайплайн">
          <div class="listik-pipelines__header">
            <span class="listik-pipelines__header-spacer" aria-hidden="true" />
            <span v-for="role in ROLE_KEYS" :key="role" class="listik-pipelines__col-title">
              {{ ROLE_TITLES[role] }}
            </span>
          </div>

          <button
            v-for="row in pipelineRows"
            :key="row.pipeline.key"
            :ref="(el) => setRouteRef({ kind: 'pipeline', ...row }, el as Element | null)"
            type="button"
            role="radio"
            class="listik-pipelines__row"
            :class="{ 'is-on': row.selected, 'is-off': !row.allowed }"
            :aria-checked="row.selected"
            :aria-disabled="!row.allowed || undefined"
            :disabled="!row.allowed"
            :tabindex="routeTabindex({ kind: 'pipeline', ...row })"
            :title="pipelineRowTooltip(row)"
            @click="selectPipeline(row.pipeline)"
            @keydown="onRouteKeydown($event, { kind: 'pipeline', ...row })"
          >
            <span class="listik-pipelines__row-title">
              <span class="listik-pipelines__row-name">{{ row.pipeline.title }}</span>
              <span class="listik-pipelines__row-hint">{{ row.pipeline.hint }}</span>
            </span>

            <span v-for="role in ROLE_KEYS" :key="role" class="listik-pipelines__cell">
              <template v-if="row.pipeline.roles[role]">
                <ProviderIcon :provider="row.pipeline.roles[role]!.provider" size="sm" />
                <span class="listik-pipelines__cell-label">{{ row.pipeline.roles[role]!.label }}</span>
              </template>
              <span v-else class="listik-pipelines__cell-empty" aria-hidden="true">—</span>
            </span>
          </button>
        </div>

        <div class="listik-direct">
          <span class="listik-direct__title">Отдельно · без таблицы ролей</span>
          <div class="listik-direct__items">
            <button
              v-for="item in directItems"
              :key="item.harness"
              :ref="(el) => setRouteRef({ kind: 'direct', ...item }, el as Element | null)"
              type="button"
              role="radio"
              class="listik-direct__item"
              :class="{ 'is-on': item.selected, 'is-off': !item.allowed }"
              :aria-checked="item.selected"
              :aria-disabled="!item.allowed || undefined"
              :disabled="!item.allowed"
              :tabindex="routeTabindex({ kind: 'direct', ...item })"
              :title="directItemTooltip(item)"
              @click="selectDirect(item.harness)"
              @keydown="onRouteKeydown($event, { kind: 'direct', ...item })"
            >
              <HarnessIcon :harness="item.harness" size="md" />
              {{ HARNESS_TITLES[item.harness] }}
            </button>

            <button
              v-for="row in stripRows"
              :key="row.pipeline.key"
              :ref="(el) => setRouteRef({ kind: 'pipeline', ...row }, el as Element | null)"
              type="button"
              role="radio"
              class="listik-direct__item"
              :class="{ 'is-on': row.selected, 'is-off': !row.allowed }"
              :aria-checked="row.selected"
              :aria-disabled="!row.allowed || undefined"
              :disabled="!row.allowed"
              :tabindex="routeTabindex({ kind: 'pipeline', ...row })"
              :title="pipelineRowTooltip(row)"
              @click="selectPipeline(row.pipeline)"
              @keydown="onRouteKeydown($event, { kind: 'pipeline', ...row })"
            >
              <ProviderIcon v-if="row.pipeline.strip?.provider" :provider="row.pipeline.strip.provider" size="md" />
              <ListikIcon v-else-if="row.pipeline.strip?.glyph" :name="row.pipeline.strip.glyph" size="md" />
              {{ row.pipeline.strip?.label }}
            </button>
          </div>
        </div>

        <p class="listik-section__hint">
          строка таблицы — пресет конвейера из плагина <span class="listik-mono">feature-pipeline</span>: кто
          пишет ТЗ, кто критикует, кто пишет код, кто принимает и коммитит. «Отдельно»: dsh/grok/codex делают
          задачу напрямую целиком; Opus — так же, без ТЗ и критики, коммитит сам; «по конфигу» —
          тот же конвейер из четырёх ролей, но кто есть кто, решает <span class="listik-mono"
            >.claude/feature-pipeline.yaml</span
          > проекта, а не эта таблица.
        </p>
        <p class="listik-section__hint">
          эпик всегда начинается с ТЗ, поэтому для него закрыто всё без этапа ТЗ — dsh/grok/codex,
          Opus и <span class="listik-mono">opus & sonnet</span>; «по конфигу» ТЗ пишет, поэтому
          остаётся открытым. Выбор
          сохраняется метками <span class="listik-mono">harness:&lt;…&gt;</span> и
          <span class="listik-mono">process:&lt;…&gt;</span> — их читает человек, автоматической раздачи
          задач по ним нет; кто допущен до этапа, решает сервер по routing проекта.
        </p>
      </section>
    </div>

    <template #footer>
      <UiButton variant="ghost" size="md" @click="cancel">Отмена</UiButton>
      <UiButton variant="primary" size="md" :loading="props.pending" :disabled="createDisabled" @click="submit">
        <template #icon><ListikIcon name="plus" size="xs" /></template>
        {{ CREATE_LABEL[form.type] ?? 'Создать задачу' }}
      </UiButton>
    </template>
  </UiDrawer>
</template>
