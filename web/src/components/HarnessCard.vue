<script setup lang="ts">
/**
 * HarnessCard — карточка выбранного харнесса раздела «Харнессы»
 * (`HarnessesSettings.vue`, правая панель). Раскладка — по макету
 * `docs/design/settings/Настройки · Харнессы-html/Harnesses.dc.html`.
 *
 * В отличие от карточек маршрутов здесь нет автосохранения: внизу кнопки
 * «Отмена/Сохранить» (макет), а `PATCH /api/harnesses/<key>` уходит дифом по
 * клику. Причина — у харнесса команда по умолчанию делится между прямым
 * маршрутом и ролями роя, и полуготовое состояние лучше не уносить на сервер.
 *
 * Поля: имя, подпись, иконка (ключ глифа — `bolt`, если пусто), тумблер
 * «В списках выбора», у `kind=exec` — команда по умолчанию (argv по строкам +
 * промпт последним аргументом) и предпросмотр. У `manual` командного блока нет.
 * Ниже — «Где используется»: записи `used_by` из `GET /api/harnesses`
 * (прямой маршрут или роль роя с кодом этапа).
 *
 * `:key="harness.key"` у вызывающей стороны — часть контракта: другая запись =
 * заново созданная карточка со свежим черновиком.
 */
import { computed, ref } from 'vue'
import {
  UiAlert,
  UiButton,
  UiField,
  UiInput,
  UiRecordList,
  UiSwitch,
  UiTextarea,
  type UiRecordListColumn,
} from '@zoloto585/facet'
import ListikIcon from './ListikIcon.vue'
import HarnessIcon from './marks/HarnessIcon.vue'
import RouteSubstitutions from './RouteSubstitutions.vue'
import store from '@/store/listik'
import type { Harness, HarnessPatch } from '@/api/types'
import { ROLE_STAGE, type RoleKey } from '@/lib/pipelines'
import { PIPELINE_STAGES } from '@/lib/dictionaries'
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

/** Годный ли черновик и отличается ли он от записи — кнопка «Сохранить». */
const dirty = computed(() => {
  if (label.value !== props.harness.label) return true
  if (hint.value !== props.harness.hint) return true
  if (icon.value !== (props.harness.icon ?? '')) return true
  if (enabled.value !== props.harness.enabled) return true
  if (!manual.value) {
    const savedArgv = props.harness.argv ?? []
    if (JSON.stringify(argv.value) !== JSON.stringify(savedArgv)) return true
    if (prompt.value !== (props.harness.prompt ?? '')) return true
  }
  return false
})

const canSave = computed(() => dirty.value && !labelError.value && !commandError.value && !saving.value)

/* ── отправка ── */

const saving = ref(false)
const serverError = ref<string | null>(null)
const savedFlash = ref(false)

function resetDraft(): void {
  label.value = props.harness.label
  hint.value = props.harness.hint
  icon.value = props.harness.icon ?? ''
  enabled.value = props.harness.enabled
  argRows.value = argRowsOf(props.harness.argv ?? [])
  prompt.value = props.harness.prompt ?? ''
  serverError.value = null
}

async function save(): Promise<void> {
  if (!canSave.value) return
  saving.value = true
  serverError.value = null
  const patch: HarnessPatch = {}
  if (label.value !== props.harness.label) patch.label = label.value.trim()
  if (hint.value !== props.harness.hint) patch.hint = hint.value
  if (icon.value !== (props.harness.icon ?? '')) {
    patch.icon = icon.value.trim() === '' ? null : icon.value.trim()
  }
  if (enabled.value !== props.harness.enabled) patch.enabled = enabled.value
  if (!manual.value) {
    const savedArgv = props.harness.argv ?? []
    if (JSON.stringify(argv.value) !== JSON.stringify(savedArgv)) patch.argv = argv.value
    if (prompt.value !== (props.harness.prompt ?? '')) {
      patch.prompt = prompt.value.trim() === '' ? null : prompt.value
    }
  }
  const updated = await store.patchHarness(props.harness.key, patch)
  saving.value = false
  if (!updated) {
    serverError.value = store.harnessesError.value ?? 'Сервер не принял правку'
    store.harnessesError.value = null
    return
  }
  savedFlash.value = true
  setTimeout(() => (savedFlash.value = false), 2000)
}

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
    text:
      entry.kind === 'direct'
        ? `прямой маршрут ${entry.route}`
        : `рой ${entry.route} · роль ${roleCode(entry.role)}`,
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
      <UiSwitch v-model="enabled">В списках выбора</UiSwitch>
    </header>

    <UiAlert v-if="serverError" tone="danger" closable @close="serverError = null">
      <template #title>Сервер не принял правку</template>
      {{ serverError }}
    </UiAlert>

    <div class="listik-harness-card__fields">
      <UiField label="Имя" required :error="labelError">
        <UiInput v-model="label" />
      </UiField>
      <UiField label="Подпись">
        <UiInput v-model="hint" placeholder="необязательно" />
      </UiField>
      <UiField
        label="Иконка в списках"
        hint="ключ глифа: claude, dsh, codex, grok, gemini, devin, pi, user — пусто: общий глиф"
      >
        <UiInput class="listik-mono" v-model="icon" placeholder="bolt" />
      </UiField>
    </div>

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
        <UiTextarea v-model="prompt" :rows="4" :invalid="Boolean(promptProblem)" />
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
      <span v-if="savedFlash" class="listik-harness-card__saved">
        <ListikIcon name="check" size="sm" /> сохранено
      </span>
      <UiButton variant="secondary" :disabled="!dirty" @click="resetDraft">Отмена</UiButton>
      <UiButton variant="primary" :disabled="!canSave" :loading="saving" @click="save">
        Сохранить
      </UiButton>
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

.listik-harness-card__saved {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-sm);
  color: var(--success-600);
}
</style>
