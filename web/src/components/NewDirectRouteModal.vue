<script setup lang="ts">
/**
 * NewDirectRouteModal — окно «Завести прямой маршрут» раздела настроек «Маршруты»
 * (`RoutesSettings.vue`). Единственный способ завести запись из доски: конвейеры
 * приходят из скилов, поэтому `kind` в теле всегда `"direct"`.
 *
 * Черновик целиком живёт здесь; открытие/закрытие держит вызывающая сторона
 * (`v-if`), поэтому повторное открытие — всегда пустая форма. Ключ подставляется
 * из названия, пока автор не тронул поле «Ключ» (после первой правки подстановка
 * прекращается); сервер ключ не собирает — он уходит явно.
 *
 * Окно — `UiFormModal` с дефолтным футером «Отмена/Завести маршрут». Отправка —
 * один `POST /api/routes` через `store.createRoute`; успех отдаётся наружу
 * событием `created` (список уже перечитан стором), ошибка остаётся в окне вместе
 * с введёнными значениями. При `409` к тексту сервера добавляется строка про
 * занятый ключ.
 *
 * Помощники проверки команды — из `lib/routes.ts` (`commandProblemText`,
 * `previewCommand`, `ROUTE_PLACEHOLDERS`), уровни — из `ROUTE_ICONS`,
 * держатели — из `HARNESS_TITLES`; списком в шаблоне ничего не дублируется.
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  UiAlert,
  UiField,
  UiFormModal,
  UiInput,
  UiRecordList,
  UiSelect,
  UiTextarea,
  type UiRecordListColumn,
  type UiSelectOption,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import store from '@/store/listik'
import type { DirectRouteCreate, DirectRouteDef, RouteIconKey } from '@/api/types'
import { ROUTE_ICONS } from '@/lib/dictionaries'
import { HARNESS_TITLES, type HarnessKey } from '@/lib/harness'
import { ROUTE_PLACEHOLDERS, braced, commandProblemText, previewCommand } from '@/lib/routes'

const emit = defineEmits<{ created: [route: DirectRouteDef]; close: [] }>()

/** Окно закрывается «Отменой», крестиком или Escape — сообщаем наружу, чтобы снять `v-if`. */
const open = ref(true)
watch(open, (value) => {
  if (!value) emit('close')
})

/* ── черновик ── */

const title = ref('')
const key = ref('')
/** Автор тронул «Ключ» — подстановка из названия больше не действует. */
const keyTouched = ref(false)
const harness = ref<HarnessKey | null>(null)
const hint = ref('')
const icon = ref<RouteIconKey | null>('direct')
const prompt = ref('')

interface ArgRow extends Record<string, unknown> {
  id: string
  value: string
}

let rowSeq = 0
function argRowsOf(values: string[]): ArgRow[] {
  return values.map((value) => ({ id: `arg-${(rowSeq += 1)}`, value }))
}

/** Аргументы команды: строка — один аргумент, кавычки не нужны. */
const argRows = ref<ArgRow[]>(argRowsOf(['']))

/** Тот же ключ, что проверяет сервер (`listik/routes.py KEY_RE`). */
const KEY_RE = /^[a-z0-9][a-z0-9-]*$/

/**
 * Черновой ключ по шагам: нижний регистр → латиница/цифры сохраняются → прочее
 * дефисом → подряд дефисы схлопываются → края обрезаются.
 */
function draftKeyFromTitle(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

watch(title, (value) => {
  if (keyTouched.value) return
  const draft = draftKeyFromTitle(value)
  key.value = KEY_RE.test(draft) ? draft : ''
})

function onKeyInput(value: string): void {
  keyTouched.value = true
  key.value = value
}

/* ── проверки до отправки ── */

const titleError = computed(() => (title.value.trim() === '' ? 'название не заполнено' : null))

const keyError = computed(() => {
  const value = key.value
  if (!value) return 'ключ не заполнен'
  if (!KEY_RE.test(value)) {
    return 'ключ: строчные латинские буквы, цифры и дефис, начинается с буквы или цифры'
  }
  return null
})

const harnessError = computed(() => (harness.value === null ? 'держатель не выбран' : null))

const argProblems = computed(() => argRows.value.map((row) => commandProblemText(row.value)))
const promptProblem = computed(() => commandProblemText(prompt.value))
const firstProblem = computed(
  () => argProblems.value.find((problem) => problem !== null) ?? promptProblem.value ?? null,
)

/** Что уйдёт в `command`: непустые аргументы по порядку, промпт — последним элементом. */
const command = computed<string[]>(() => {
  const args = argRows.value.map((row) => row.value).filter((value) => value.trim() !== '')
  return prompt.value.trim() === '' ? args : [...args, prompt.value]
})

const commandError = computed(() => {
  if (command.value.length === 0) return 'команда пустая: нужен хотя бы один аргумент'
  return firstProblem.value
})

const submitting = ref(false)
const serverError = ref<string | null>(null)

const canSubmit = computed(
  () =>
    !submitting.value &&
    !titleError.value &&
    !keyError.value &&
    !harnessError.value &&
    !commandError.value,
)

/**
 * Кнопка отправки неактивна, пока форма негодна. Кит 1.1.0 выключать её атрибутом
 * ещё не умеет (задача кита uikit-6mtl), поэтому `submit-disabled` проставляем
 * кнопке сами: у формы окна берём её id и находим связанную кнопку `form="…"`.
 * Когда кит научится — атрибут заработает сам, без правки окна; до тех пор запрос
 * гасит `submit()`.
 */
const submitDisabled = computed(() => !canSubmit.value)

function syncSubmitDisabled(): void {
  const anchor = document.querySelector('.listik-new-direct__anchor')
  const form = anchor?.closest('form') ?? null
  if (!form) return
  const button = document.querySelector<HTMLButtonElement>(`button[form="${form.id}"]`)
  if (!button) return
  if (submitDisabled.value) button.setAttribute('submit-disabled', '')
  else button.removeAttribute('submit-disabled')
}

onMounted(syncSubmitDisabled)
watch(submitDisabled, syncSubmitDisabled, { flush: 'post' })

/* ── выборы: держатель и иконка ── */

/** Держатели — ключи `HARNESS_TITLES` без `human` (сервер принимает пять харнессов). */
const harnessOptions = computed<UiSelectOption<HarnessKey>[]>(() =>
  (Object.keys(HARNESS_TITLES) as HarnessKey[])
    .filter((value) => value !== 'human')
    .map((value) => ({ value, label: HARNESS_TITLES[value] })),
)

const iconOptions = computed<IconToggleOption<string>[]>(() => [
  ...ROUTE_ICONS.map((item) => ({ value: item.value as string, label: item.hint })),
  { value: '', label: 'Без иконки' },
])
const iconValue = computed(() => icon.value ?? '')

function glyphFor(value: string): string | null {
  return ROUTE_ICONS.find((item) => item.value === value)?.icon ?? null
}

function onIcon(value: string): void {
  icon.value = value === '' ? null : (value as RouteIconKey)
}

/* ── список аргументов ── */

const argColumns: UiRecordListColumn[] = [{ key: 'value', label: 'Аргумент', type: 'custom' }]

function createArgRow(): ArgRow {
  return { id: `arg-${(rowSeq += 1)}`, value: '' }
}

function rowKeyOf(row: ArgRow): string {
  return row.id
}

/* ── предпросмотр ── */

const preview = computed(() => previewCommand(command.value, key.value))

/* ── отправка ── */

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  submitting.value = true
  serverError.value = null
  const payload: DirectRouteCreate = {
    kind: 'direct',
    key: key.value,
    title: title.value.trim(),
    hint: hint.value,
    icon: icon.value,
    harness: harness.value as HarnessKey,
    command: command.value,
  }
  const created = await store.createRoute(payload)
  submitting.value = false
  if (!created) {
    const message = store.routesSettingsError.value ?? 'Не получилось завести маршрут'
    store.routesSettingsError.value = null
    serverError.value = store.routeCreateConflict.value
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
    title="Завести прямой маршрут"
    submit-label="Завести маршрут"
    cancel-label="Отмена"
    :loading="submitting"
    @submit="submit"
  >
    <p class="listik-new-direct__lead listik-new-direct__anchor">
      Прямой маршрут — одна команда, которую Listik запускает сам. Конвейеры так не заводятся:
      они приходят из скилов.
    </p>

    <UiAlert v-if="serverError" tone="danger">
      <template #title>Сервер не принял</template>
      <span class="listik-new-direct__error">{{ serverError }}</span>
    </UiAlert>

    <div class="listik-new-direct__row">
      <UiField label="Название в меню" required :error="titleError">
        <UiInput class="listik-new-direct__title" v-model="title" placeholder="например, opencode DeepSeek" />
      </UiField>

      <UiField
        label="Ключ"
        required
        hint="Потом его не переименовать: на него ссылаются уже запущенные задачи"
        :error="keyError"
      >
        <UiInput
          class="listik-new-direct__key listik-mono"
          :model-value="key"
          placeholder="opencode-deepseek"
          @update:model-value="onKeyInput"
        />
      </UiField>
    </div>

    <div class="listik-new-direct__row">
      <UiField label="Держатель карточки" required :error="harnessError">
        <UiSelect
          v-model="harness"
          :options="harnessOptions"
          placeholder="Выбрать…"
          ariaLabel="Держатель карточки"
        />
      </UiField>

      <UiField label="Подпись под названием">
        <UiInput class="listik-new-direct__hint" v-model="hint" placeholder="необязательно" />
      </UiField>
    </div>

    <UiField label="Иконка">
      <IconToggle
        :model-value="iconValue"
        :options="iconOptions"
        ariaLabel="Иконка маршрута"
        @update:model-value="onIcon"
      >
        <template #icon="{ option }">
          <span v-if="option.value === ''" class="listik-new-direct__icon-none">Без иконки</span>
          <ListikIcon v-else :name="glyphFor(option.value) ?? ''" size="sm" />
        </template>
      </IconToggle>
    </UiField>

    <UiField
      label="Команда"
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
          <div class="listik-new-direct__arg">
            <UiInput
              class="listik-new-direct__arg-input listik-mono"
              size="sm"
              :model-value="row.value"
              :invalid="Boolean(argProblems[index])"
              @update:model-value="(value: string) => update(value)"
            />
            <p v-if="argProblems[index]" class="listik-new-direct__problem">
              {{ argProblems[index] }}
            </p>
          </div>
        </template>
      </UiRecordList>
    </UiField>

    <UiField label="Промпт — последний аргумент">
      <UiTextarea
        class="listik-new-direct__prompt"
        v-model="prompt"
        :rows="4"
        :invalid="Boolean(promptProblem)"
      />
      <p v-if="promptProblem" class="listik-new-direct__problem">{{ promptProblem }}</p>
      <p class="listik-new-direct__placeholders">
        Подстановки:
        <code v-for="name in ROUTE_PLACEHOLDERS" :key="name" class="listik-mono">
          {{ braced(name) }}
        </code>
      </p>
    </UiField>

    <section class="listik-new-direct__preview">
      <h4 class="listik-new-direct__preview-title">Что выполнится</h4>
      <p class="listik-mono listik-new-direct__preview-code">{{ preview || '—' }}</p>
    </section>
  </UiFormModal>
</template>

<style scoped>
.listik-new-direct__lead {
  margin: 0;
  font-size: var(--text-sm);
  line-height: 1.5;
  color: var(--ink-3);
}

.listik-new-direct__error {
  white-space: pre-line;
}

.listik-new-direct__row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

@media (max-width: 560px) {
  .listik-new-direct__row {
    grid-template-columns: minmax(0, 1fr);
  }
}

.listik-new-direct__icon-none {
  font-size: var(--text-sm);
  color: var(--ink-3);
}

.listik-new-direct__arg {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-1);
  width: 100%;
  min-width: 0;
}

.listik-new-direct__arg :deep(.ui-input) {
  min-width: 0;
}

.listik-new-direct__problem {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--danger-600);
}

.listik-new-direct__placeholders {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-new-direct__placeholders .listik-mono {
  margin-right: var(--space-1);
}

.listik-new-direct__preview {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-new-direct__preview-title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-new-direct__preview-code {
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
