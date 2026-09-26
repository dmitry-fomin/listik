<script setup lang="ts">
/**
 * NewHarnessModal — окно «Завести харнесс» раздела настроек «Харнессы»
 * (`HarnessesSettings.vue`). Запись каталога `harnesses` (`POST /api/harnesses`):
 * исполнитель роли роя. Держатель на сервере — `agent:<ключ>`: ключ уходит явно и потом не
 * переименовывается (подстановка из имени живёт, пока поле не тронуто).
 *
 * `kind`: по умолчанию `exec` — Listik поднимает процесс по argv; переключатель
 * «Ручная выдача» заводит `manual` (как `me` — человек): команды у записи нет,
 * поля argv/промпта прячутся и на сервер не уходят.
 *
 * Отправка — один `POST` через `store.createHarness`; успех отдаётся `created`
 * (список уже перечитан стором), ошибка остаётся в окне. При `409` к тексту
 * добавляется строка про занятый ключ.
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  UiAlert,
  UiField,
  UiFormModal,
  UiInput,
  UiRecordList,
  UiSwitch,
  UiTextarea,
  type UiRecordListColumn,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import HarnessIcon from './marks/HarnessIcon.vue'
import store from '@/store/listik'
import type { Harness, HarnessCreate } from '@/api/types'
import { HARNESS_ICON_OPTIONS } from '@/lib/harness'
import { ROUTE_PLACEHOLDERS, braced, commandProblemText, previewCommand } from '@/lib/routes'

const emit = defineEmits<{ created: [harness: Harness]; close: [] }>()

/** Окно закрывается «Отменой», крестиком или Escape — сообщаем наружу, чтобы снять `v-if`. */
const open = ref(true)
watch(open, (value) => {
  if (!value) emit('close')
})

/* ── черновик ── */

const label = ref('')
const key = ref('')
/** Автор тронул «Ключ» — подстановка из имени больше не действует. */
const keyTouched = ref(false)
const hint = ref('')
const icon = ref('')
const iconOptions: IconToggleOption<string>[] = HARNESS_ICON_OPTIONS
const manual = ref(false)
const prompt = ref('')

interface ArgRow extends Record<string, unknown> {
  id: string
  value: string
}

let rowSeq = 0
function argRowsOf(values: string[]): ArgRow[] {
  return values.map((value) => ({ id: `arg-${(rowSeq += 1)}`, value }))
}

/** Аргументы команды по умолчанию: строка — один аргумент, кавычки не нужны. */
const argRows = ref<ArgRow[]>(argRowsOf(['']))

/** Тот же ключ, что проверяет сервер (`listik/routes.py KEY_RE`). */
const KEY_RE = /^[a-z0-9][a-z0-9-]*$/

function draftKeyFromLabel(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

watch(label, (value) => {
  if (keyTouched.value) return
  const draft = draftKeyFromLabel(value)
  key.value = KEY_RE.test(draft) ? draft : ''
})

function onKeyInput(value: string): void {
  keyTouched.value = true
  key.value = value
}

/* ── проверки до отправки ── */

const labelError = computed(() => (label.value.trim() === '' ? 'имя не заполнено' : null))

const keyError = computed(() => {
  const value = key.value
  if (!value) return 'ключ не заполнен'
  if (!KEY_RE.test(value)) {
    return 'ключ: строчные латинские буквы, цифры и дефис, начинается с буквы или цифры'
  }
  return null
})

const argProblems = computed(() => argRows.value.map((row) => commandProblemText(row.value)))
const promptProblem = computed(() => commandProblemText(prompt.value))
const firstProblem = computed(
  () => argProblems.value.find((problem) => problem !== null) ?? promptProblem.value ?? null,
)

/** Аргументы по порядку без пустых; промпт — последним элементом команды. */
const argv = computed<string[]>(() =>
  argRows.value.map((row) => row.value).filter((value) => value.trim() !== ''),
)

const commandError = computed<string | null>(() => {
  if (manual.value) return null
  if (argv.value.length === 0) return 'команда пустая: нужен хотя бы один аргумент'
  return firstProblem.value
})

const submitting = ref(false)
const serverError = ref<string | null>(null)

const canSubmit = computed(
  () => !submitting.value && !labelError.value && !keyError.value && !commandError.value,
)

/**
 * Кнопка отправки неактивна, пока форма негодна — тот же приём, что у окна
 * маршрута роя (`submit-disabled` на кнопке `form="…"`).
 */
const submitDisabled = computed(() => !canSubmit.value)

function syncSubmitDisabled(): void {
  const anchor = document.querySelector('.listik-new-harness__anchor')
  const form = anchor?.closest('form') ?? null
  if (!form) return
  const button = document.querySelector<HTMLButtonElement>(`button[form="${form.id}"]`)
  if (!button) return
  if (submitDisabled.value) button.setAttribute('submit-disabled', '')
  else button.removeAttribute('submit-disabled')
}

onMounted(syncSubmitDisabled)
watch(submitDisabled, syncSubmitDisabled, { flush: 'post' })

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
  return previewCommand(parts, key.value || 'route')
})

/* ── отправка ── */

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  submitting.value = true
  serverError.value = null
  const payload: HarnessCreate = {
    key: key.value,
    label: label.value.trim(),
    hint: hint.value,
    icon: icon.value.trim() === '' ? null : icon.value.trim(),
    kind: manual.value ? 'manual' : 'exec',
  }
  if (!manual.value) {
    payload.argv = argv.value
    payload.prompt = prompt.value.trim() === '' ? null : prompt.value
  }
  const created = await store.createHarness(payload)
  submitting.value = false
  if (!created) {
    const message = store.harnessesError.value ?? 'Не получилось завести харнесс'
    store.harnessesError.value = null
    serverError.value = store.harnessCreateConflict.value
      ? `${message}\nКлюч занят — измените поле «Ключ»`
      : message
    return
  }
  emit('created', created)
}
</script>

<template>
  <UiFormModal
    v-model="open"
    title="Завести харнесс"
    submit-label="Завести харнесс"
    cancel-label="Отмена"
    :loading="submitting"
    @submit="submit"
  >
    <p class="listik-new-harness__lead listik-new-harness__anchor">
      Харнесс — исполнитель роли роя.
      Держателем на сервере станет <code class="listik-mono">agent:{{ key || 'ключ' }}</code>.
    </p>

    <UiAlert v-if="serverError" tone="danger">
      <template #title>Сервер не принял</template>
      <span class="listik-new-harness__error">{{ serverError }}</span>
    </UiAlert>

    <div class="listik-new-harness__row">
      <UiField label="Имя в списках" required :error="labelError">
        <UiInput v-model="label" placeholder="например, opencode" />
      </UiField>

      <UiField
        label="Ключ"
        required
        hint="Потом его не переименовать: на него ссылаются маршруты"
        :error="keyError"
      >
        <UiInput
          class="listik-mono"
          :model-value="key"
          placeholder="opencode"
          @update:model-value="onKeyInput"
        />
      </UiField>
    </div>

    <div class="listik-new-harness__row">
      <UiField label="Подпись">
        <UiInput v-model="hint" placeholder="необязательно" />
      </UiField>
    </div>

    <UiField label="Иконка в списках">
      <IconToggle
        :model-value="icon"
        :options="iconOptions"
        ariaLabel="Иконка харнесса"
        @update:model-value="(value: string) => (icon = value)"
      >
        <template #icon="{ option }">
          <ListikIcon v-if="option.value === ''" name="bolt" size="sm" />
          <HarnessIcon v-else :icon="option.value" size="sm" />
        </template>
      </IconToggle>
    </UiField>

    <UiSwitch v-model="manual">Ручная выдача — команды нет, карточку берёт человек</UiSwitch>

    <template v-if="!manual">
      <UiField
        label="Команда по умолчанию"
        required
        hint="по одному аргументу в строке — кавычки не нужны"
        :error="commandError"
      >
        <UiRecordList
          v-model="argRows"
          :columns="argColumns"
          :row-key="rowKeyOf"
          :create-row="createArgRow"
          add-label="Добавить аргумент"
          empty-title="Аргументов нет — только промпт"
        >
          <template #cell-value="{ row, index, update }">
            <div class="listik-new-harness__arg">
              <UiInput
                class="listik-new-harness__arg-input listik-mono"
                size="sm"
                :model-value="row.value"
                :invalid="Boolean(argProblems[index])"
                @update:model-value="(value: string) => update(value)"
              />
              <p v-if="argProblems[index]" class="listik-new-harness__problem">
                {{ argProblems[index] }}
              </p>
            </div>
          </template>
        </UiRecordList>
      </UiField>

      <UiField label="Промпт по умолчанию — последний аргумент">
        <UiTextarea v-model="prompt" :rows="4" :invalid="Boolean(promptProblem)" />
        <p v-if="promptProblem" class="listik-new-harness__problem">{{ promptProblem }}</p>
        <p class="listik-new-harness__placeholders">
          Подстановки:
          <code v-for="name in ROUTE_PLACEHOLDERS" :key="name" class="listik-mono">
            {{ braced(name) }}
          </code>
        </p>
      </UiField>

      <section class="listik-new-harness__preview">
        <h4 class="listik-new-harness__preview-title">Что выполнится</h4>
        <p class="listik-mono listik-new-harness__preview-code">{{ preview || '—' }}</p>
      </section>
    </template>
  </UiFormModal>
</template>

<style scoped>
.listik-new-harness__lead {
  margin: 0;
  font-size: var(--text-sm);
  line-height: 1.5;
  color: var(--ink-3);
}

.listik-new-harness__error {
  white-space: pre-line;
}

.listik-new-harness__row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

@media (max-width: 560px) {
  .listik-new-harness__row {
    grid-template-columns: minmax(0, 1fr);
  }
}

.listik-new-harness__arg {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-1);
  width: 100%;
  min-width: 0;
}

.listik-new-harness__arg :deep(.ui-input) {
  min-width: 0;
}

.listik-new-harness__problem {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--danger-600);
}

.listik-new-harness__placeholders {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-new-harness__placeholders .listik-mono {
  margin-right: var(--space-1);
}

.listik-new-harness__preview {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-new-harness__preview-title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-new-harness__preview-code {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  max-height: 12em;
  overflow-y: auto;
  overflow-wrap: break-word;
  white-space: pre-wrap;
}
</style>
