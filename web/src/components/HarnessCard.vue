<script setup lang="ts">
/**
 * HarnessCard — карточка выбранного харнесса раздела «Харнессы»
 * (`HarnessesSettings.vue`, правая панель). Раскладка — по макету
 * `docs/design/settings/Настройки · Харнессы-html/Harnesses.dc.html`.
 *
 * Сохранение автоматическое, как у карточек маршрутов: текстовые поля
 * уходят через 600 мс после последней правки и сразу по потере фокуса,
 * тумблер «В списках выбора» и иконка — сразу. `PATCH /api/harnesses/<key>`
 * уходит дифом с сохранённой записью одним запросом на серию правок; пока
 * один запрос в полёте, следующий ждёт в очереди (`inFlight/queued`).
 * Черновик с ошибкой (пустое имя, битая команда) на сервер не уходит и сам
 * не откатывается — причина стоит у поля. При размонтировании карточки
 * отложенная годная правка досохраняется.
 *
 * Поля: имя, подпись, иконка (пикер глифов — `bolt`, если «общий глиф»), тумблер
 * «В списках выбора», у `kind=exec` — команда по умолчанию (argv по строкам +
 * промпт последним аргументом) и предпросмотр. У `manual` командного блока нет.
 * Ниже — «Где используется»: записи `used_by` из `GET /api/harnesses`
 * (роль роя с кодом этапа).
 *
 * `:key="harness.key"` у вызывающей стороны — часть контракта: другая запись =
 * заново созданная карточка со свежим черновиком.
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  UiAlert,
  UiField,
  UiInput,
  UiRecordList,
  UiSaveStatus,
  UiSwitch,
  UiTextarea,
  type SaveStatusValue,
  type UiRecordListColumn,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import HarnessIcon from './marks/HarnessIcon.vue'
import RouteSubstitutions from './RouteSubstitutions.vue'
import store from '@/store/listik'
import type { Harness, HarnessPatch } from '@/api/types'
import { ROLE_STAGE, type RoleKey } from '@/lib/pipelines'
import { PIPELINE_STAGES } from '@/lib/dictionaries'
import { HARNESS_ICON_OPTIONS } from '@/lib/harness'
import { commandProblemText, previewCommand } from '@/lib/routes'

const props = defineProps<{ harness: Harness }>()

/* ── черновик ── */

interface ArgRow extends Record<string, unknown> {
  id: string
  value: string
}

let rowSeq = 0
function argRowsOf(values: string[]): ArgRow[] {
  return values.map((value) => ({ id: `arg-${(rowSeq += 1)}`, value }))
}

const label = ref(props.harness.label)
const hint = ref(props.harness.hint)
const icon = ref(props.harness.icon ?? '')
const enabled = ref(props.harness.enabled)
const argRows = ref<ArgRow[]>(argRowsOf(props.harness.argv ?? []))
const prompt = ref(props.harness.prompt ?? '')

const manual = computed(() => props.harness.kind === 'manual')

/* ── иконка: кнопки-глифы, как у карточек маршрутов; «общий глиф» — bolt ── */

const iconOptions: IconToggleOption<string>[] = HARNESS_ICON_OPTIONS

function onIcon(value: string): void {
  icon.value = value
  scheduleFlush(true)
}

/* ── проверки ── */

const labelError = computed(() => (label.value.trim() === '' ? 'имя не заполнено' : null))
const argProblems = computed(() => argRows.value.map((row) => commandProblemText(row.value)))
const promptProblem = computed(() => commandProblemText(prompt.value))
const commandError = computed<string | null>(() => {
  if (manual.value) return null
  if (argRows.value.every((row) => row.value.trim() === '')) {
    return 'команда пустая: нужен хотя бы один аргумент'
  }
  return argProblems.value.find((problem) => problem !== null) ?? promptProblem.value
})

const argv = computed<string[]>(() =>
  argRows.value.map((row) => row.value).filter((value) => value.trim() !== ''),
)

/* ── автосохранение: тот же порядок, что у карточек маршрутов ── */

/** Последнее известное серверу состояние: с ним сравнивается черновик. */
const baseline = reactive({
  label: props.harness.label,
  hint: props.harness.hint,
  icon: props.harness.icon ?? '',
  enabled: props.harness.enabled,
  argv: [...(props.harness.argv ?? [])],
  prompt: props.harness.prompt ?? '',
})

const status = ref<SaveStatusValue>('idle')
const serverError = ref<string | null>(null)

let inFlight = false
let queued = false
let debounceTimer: ReturnType<typeof setTimeout> | null = null

/** Диф черновика против сохранённой записи — прежние правила save(). */
function diffPatch(): HarnessPatch {
  const patch: HarnessPatch = {}
  if (label.value !== baseline.label) patch.label = label.value.trim()
  if (hint.value !== baseline.hint) patch.hint = hint.value
  if (icon.value !== baseline.icon) {
    patch.icon = icon.value.trim() === '' ? null : icon.value.trim()
  }
  if (enabled.value !== baseline.enabled) patch.enabled = enabled.value
  if (!manual.value) {
    if (JSON.stringify(argv.value) !== JSON.stringify(baseline.argv)) patch.argv = argv.value
    if (prompt.value !== baseline.prompt) {
      patch.prompt = prompt.value.trim() === '' ? null : prompt.value
    }
  }
  return patch
}

async function flush(): Promise<void> {
  // Черновик с ошибкой на сервер не уходит и не откатывается.
  if (labelError.value || commandError.value) return
  const patch = diffPatch()
  if (Object.keys(patch).length === 0) return
  if (inFlight) {
    queued = true
    return
  }
  inFlight = true
  status.value = 'saving'
  const updated = await store.patchHarness(props.harness.key, patch)
  inFlight = false
  if (updated) {
    if (patch.label !== undefined) baseline.label = patch.label
    if (patch.hint !== undefined) baseline.hint = patch.hint
    if (patch.icon !== undefined) baseline.icon = patch.icon ?? ''
    if (patch.enabled !== undefined) baseline.enabled = patch.enabled
    if (patch.argv !== undefined) baseline.argv = [...(patch.argv ?? [])]
    if (patch.prompt !== undefined) baseline.prompt = patch.prompt ?? ''
    serverError.value = null
    status.value = 'saved'
  } else {
    serverError.value = store.harnessesError.value ?? 'Сервер не принял правку'
    store.harnessesError.value = null
    status.value = 'error'
    // Тумблер возвращается к сохранённому значению — как `visible` у маршрутов.
    if (patch.enabled !== undefined) enabled.value = baseline.enabled
  }
  if (queued) {
    queued = false
    await flush()
  }
}

function scheduleFlush(immediate: boolean): void {
  if (debounceTimer) {
    clearTimeout(debounceTimer)
    debounceTimer = null
  }
  if (immediate) {
    void flush()
    return
  }
  debounceTimer = setTimeout(() => {
    debounceTimer = null
    void flush()
  }, 600)
}

function onLabel(value: string): void {
  label.value = value
  scheduleFlush(false)
}
function onHint(value: string): void {
  hint.value = value
  scheduleFlush(false)
}
function onPrompt(value: string): void {
  prompt.value = value
  scheduleFlush(false)
}
function onBlurText(): void {
  scheduleFlush(true)
}
function onEnabled(value: boolean): void {
  if (value === enabled.value) return
  enabled.value = value
  scheduleFlush(true)
}

/* Список аргументов (ввод в строке, добавление, удаление, перестановка) — в общий дебаунс. */
watch(argRows, () => scheduleFlush(false), { deep: true })

/* Карточку сняли (выбор другого харнесса, уход со страницы) — отложенную
   правку досохраняем; пустой диф и ошибки черновика flush() пропустит сам. */
onBeforeUnmount(() => {
  if (debounceTimer) {
    clearTimeout(debounceTimer)
    debounceTimer = null
  }
  void flush()
})

/* ── список аргументов и предпросмотр ── */

const argColumns: UiRecordListColumn[] = [{ key: 'value', label: 'Аргумент', type: 'custom' }]

function createArgRow(): ArgRow {
  return { id: `arg-${(rowSeq += 1)}`, value: '' }
}

function rowKeyOf(row: ArgRow): string {
  return row.id
}

const preview = computed(() => {
  const parts = prompt.value.trim() === '' ? argv.value : [...argv.value, prompt.value]
  return previewCommand(parts, props.harness.key)
})

/* ── «Где используется» ── */

/** Код этапа роли роя для строки использования (`s1`…`s4`). */
function roleCode(role: string | null): string {
  const stage = PIPELINE_STAGES.find((item) => item.value === ROLE_STAGE[role as RoleKey])
  return stage?.code ?? role ?? ''
}

interface UsageRow {
  key: string
  text: string
}

const usages = computed<UsageRow[]>(() =>
  (props.harness.used_by ?? []).map((entry) => ({
    key: `${entry.kind}:${entry.route}:${entry.role ?? ''}`,
    text: `рой ${entry.route} · роль ${roleCode(entry.role)}`,
  })),
)
</script>

<template>
  <div class="listik-harness-card">
    <header class="listik-harness-card__head">
      <span class="listik-harness-card__tile" aria-hidden="true">
        <HarnessIcon :harness="harness.key" size="md" />
      </span>
      <div class="listik-harness-card__head-main">
        <h2 class="listik-harness-card__name">{{ harness.label }}</h2>
        <p class="listik-harness-card__keyline">
          Харнесс · держатель <code class="listik-mono">agent:{{ harness.key }}</code>
          <template v-if="manual"> · ручная выдача, без команды</template>
          <template v-else> · команда — шаблон, маршруты и роли берут её и правят под себя</template>
        </p>
      </div>
      <UiSwitch :model-value="enabled" @update:model-value="onEnabled">В списках выбора</UiSwitch>
    </header>

    <UiAlert v-if="serverError" tone="danger" closable @close="serverError = null">
      <template #title>Сервер не принял правку</template>
      {{ serverError }}
    </UiAlert>

    <div class="listik-harness-card__fields">
      <UiField label="Имя" required :error="labelError">
        <UiInput
          :model-value="label"
          @update:model-value="onLabel"
          v-bind="{ onBlur: onBlurText }"
        />
      </UiField>
      <UiField label="Подпись">
        <UiInput
          :model-value="hint"
          placeholder="необязательно"
          @update:model-value="onHint"
          v-bind="{ onBlur: onBlurText }"
        />
      </UiField>
    </div>

    <section class="listik-harness-card__icon">
      <h4 class="listik-harness-card__label">Иконка в списках</h4>
      <IconToggle
        :model-value="icon"
        :options="iconOptions"
        ariaLabel="Иконка харнесса"
        @update:model-value="onIcon"
      >
        <template #icon="{ option }">
          <ListikIcon v-if="option.value === ''" name="bolt" size="sm" />
          <HarnessIcon v-else :icon="option.value" size="sm" />
        </template>
      </IconToggle>
    </section>

    <template v-if="!manual">
      <section class="listik-harness-card__section">
        <div class="listik-harness-card__section-head">
          <h4 class="listik-harness-card__section-title">Команда по умолчанию</h4>
          <span class="listik-harness-card__section-note">
            по одному аргументу в строке — кавычки не нужны
          </span>
        </div>
        <UiRecordList
          v-model="argRows"
          :columns="argColumns"
          :row-key="rowKeyOf"
          :create-row="createArgRow"
          add-label="Добавить аргумент"
          empty-title="Аргументов нет — только промпт"
        >
          <template #cell-value="{ row, index, update }">
            <div class="listik-harness-card__arg">
              <UiInput
                class="listik-mono"
                size="sm"
                :model-value="row.value"
                :invalid="Boolean(argProblems[index])"
                @update:model-value="(value: string) => update(value)"
                v-bind="{ onBlur: onBlurText }"
              />
              <p v-if="argProblems[index]" class="listik-harness-card__problem">
                {{ argProblems[index] }}
              </p>
            </div>
          </template>
        </UiRecordList>
      </section>

      <section class="listik-harness-card__section">
        <h4 class="listik-harness-card__section-title">Промпт по умолчанию — последний аргумент</h4>
        <UiTextarea
          :model-value="prompt"
          :rows="4"
          :invalid="Boolean(promptProblem)"
          @update:model-value="onPrompt"
          v-bind="{ onBlur: onBlurText }"
        />
        <p v-if="promptProblem" class="listik-harness-card__problem">{{ promptProblem }}</p>
        <RouteSubstitutions :route-key="harness.key" :command="argv" />
      </section>

      <section class="listik-harness-card__section">
        <h4 class="listik-harness-card__section-title">Что выполнится</h4>
        <p class="listik-mono listik-harness-card__preview">{{ preview || '—' }}</p>
      </section>
    </template>

    <section class="listik-harness-card__section">
      <h4 class="listik-harness-card__section-title">Где используется</h4>
      <ul v-if="usages.length > 0" class="listik-harness-card__usages">
        <li v-for="usage in usages" :key="usage.key" class="listik-harness-card__usage">
          <code class="listik-mono">{{ usage.text }}</code>
        </li>
      </ul>
      <p v-else class="listik-harness-card__hint">ни один маршрут пока не использует этот харнесс</p>
    </section>

    <div class="listik-harness-card__actions">
      <span class="listik-harness-card__actions-note">
        Правки применяются к следующему запуску. Уже запущенные задачи не трогаются.
      </span>
      <UiSaveStatus :status="status" @retry="() => scheduleFlush(true)" />
    </div>
  </div>
</template>

<style scoped>
.listik-harness-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.listik-harness-card__head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.listik-harness-card__tile {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: var(--space-8);
  height: var(--space-8);
  border-radius: var(--radius-md);
  background: var(--accent-50);
  color: var(--accent-600);
}

.listik-harness-card__head-main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.listik-harness-card__name {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-tight);
  color: var(--ink-1);
}

.listik-harness-card__keyline {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--ink-3);
}

.listik-harness-card__fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

/* ── иконка ── */

.listik-harness-card__icon {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.listik-harness-card__label {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-harness-card__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-harness-card__section-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.listik-harness-card__section-title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-harness-card__section-note {
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-harness-card__arg {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-1);
  width: 100%;
  min-width: 0;
}

.listik-harness-card__problem {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--danger-600);
}

.listik-harness-card__hint {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-harness-card__preview {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  font-size: var(--text-sm);
  max-height: 12em;
  overflow-y: auto;
  overflow-wrap: break-word;
  white-space: pre-wrap;
}

.listik-harness-card__usages {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.listik-harness-card__usage {
  font-size: var(--text-sm);
  color: var(--ink-2);
}

/* ── нижняя полоса: заметка + статус автосохранения ── */

.listik-harness-card__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-harness-card__actions-note {
  flex: 1 1 auto;
  font-size: var(--text-xs);
  color: var(--ink-3);
}
</style>
