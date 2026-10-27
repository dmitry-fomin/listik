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
 * один запрос в полёте, следующий ждёт в очереди (`useAutosave`, `@/lib/autosave`).
 * Черновик с ошибкой (пустое имя, битая команда) на сервер не уходит и сам
 * не откатывается — причина стоит у поля. При размонтировании карточки
 * отложенная годная правка досохраняется.
 *
 * Поля: имя, подпись, иконка (пикер глифов — `bolt`, если «общий глиф»), тумблер
 * «В списках выбора», у `kind=exec` — команда по умолчанию (argv по строкам +
 * промпт последним аргументом) и предпросмотр. У `manual` командного блока нет.
 * Ниже — «Где используется»: записи `used_by` из `GET /api/harnesses`
 * (роль роя с кодом этапа; участник роли конвейера — с кодом этапа и каналом).
 *
 * Каналы (listik-46q6, только `kind=exec`, над командой и вне блока
 * `__fields`): `UiRecordList` строк «Имя / Модель / Усилие» и `UiRadioGroup`
 * «Канал по умолчанию». `channels` уходит в PATCH целым списком непустых строк
 * и только при отличии от сохранённого; пустая новая строка молча отбрасывается,
 * строка с ошибкой (имя, повтор, модель, усилие, больше 20) держит весь патч.
 * Ввод текста — в общий дебаунс, выбор канала по умолчанию и удаление строки —
 * сразу. Ответ сервера кладётся в `baseline.channels`, а в черновик — только
 * если тот всё ещё равен отправленному (ввод во время запроса не теряется).
 *
 * Харнесс без команды на сервере (`argv: null`, например `pi`/`opencode`):
 * пока строки аргументов пусты, пустая команда — не ошибка и `argv` в патч не
 * идёт; после сохранения непустого `argv` действует обычное правило.
 *
 * `:key="harness.key"` у вызывающей стороны — часть контракта: другая запись =
 * заново созданная карточка со свежим черновиком.
 */
import { computed, reactive, ref, watch } from 'vue'
import {
  UiAlert,
  UiField,
  UiInput,
  UiRadioGroup,
  UiRecordList,
  UiSaveStatus,
  UiSwitch,
  UiTextarea,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import HarnessIcon from './marks/HarnessIcon.vue'
import RouteSubstitutions from './RouteSubstitutions.vue'
import store from '@/store/listik'
import { useAutosave } from '@/lib/autosave'
import type { Harness, HarnessChannel, HarnessPatch } from '@/api/types'
import type { UiRecordListColumn } from '@zoloto585/facet'
import { roleStage } from '@/lib/pipelines'
import {
  type ChannelRow,
  HARNESS_ICON_OPTIONS,
  MAX_CHANNELS,
  channelProblems,
  channelRowBlank,
  channelRowsOf,
  channelsOf,
  createChannelRow,
} from '@/lib/harness'
import {
  ARG_COLUMNS,
  type ArgRow,
  argRowsOf,
  commandProblemText,
  createArgRow,
  previewCommand,
  rowKeyOf,
  withPrompt,
} from '@/lib/routes'

const props = defineProps<{ harness: Harness }>()

/* ── черновик ── */

const label = ref(props.harness.label)
const hint = ref(props.harness.hint)
const icon = ref(props.harness.icon ?? '')
const enabled = ref(props.harness.enabled)
const argRows = ref<ArgRow[]>(argRowsOf(props.harness.argv ?? []))
const prompt = ref(props.harness.prompt ?? '')
const channelRows = ref<ChannelRow[]>(channelRowsOf(props.harness.channels ?? []))
/** Строка канала по умолчанию (id строки, не имя — имя правится). */
const defaultChannelId = ref<string | null>(
  channelRows.value[(props.harness.channels ?? []).findIndex((channel) => channel.default)]?.id ?? null,
)
/** Команды нет на сервере (`argv: null`) — снимается, когда сохранён непустой `argv`. */
const noServerCommand = ref(props.harness.argv === null)

const manual = computed(() => props.harness.kind === 'manual')

/* ── иконка: кнопки-глифы, как у карточек маршрутов; «общий глиф» — bolt ── */

const iconOptions: IconToggleOption<string>[] = HARNESS_ICON_OPTIONS

function onIcon(value: string): void {
  icon.value = value
  schedule(true)
}

/* ── проверки ── */

const labelError = computed(() => (label.value.trim() === '' ? 'имя не заполнено' : null))
const argProblems = computed(() => argRows.value.map((row) => commandProblemText(row.value)))
const promptProblem = computed(() => commandProblemText(prompt.value))
const commandError = computed<string | null>(() => {
  if (manual.value) return null
  if (argRows.value.every((row) => row.value.trim() === '')) {
    return noServerCommand.value ? null : 'команда пустая: нужен хотя бы один аргумент'
  }
  return argProblems.value.find((problem) => problem !== null) ?? promptProblem.value
})

const argv = computed<string[]>(() =>
  argRows.value.map((row) => row.value).filter((value) => value.trim() !== ''),
)

/* ── каналы ── */

const CHANNEL_COLUMNS: UiRecordListColumn[] = [
  { key: 'name', label: 'Имя', type: 'custom' },
  { key: 'model', label: 'Модель', type: 'custom' },
  { key: 'effort', label: 'Усилие', type: 'custom' },
]

const channelFieldProblems = computed(() => channelProblems(channelRows.value))
const channelsFull = computed(() => channelRows.value.length >= MAX_CHANNELS)
const channelsCountError = computed(() =>
  channelRows.value.filter((row) => !channelRowBlank(row)).length > MAX_CHANNELS
    ? `каналов больше ${MAX_CHANNELS}`
    : null,
)
const channelsError = computed(
  () =>
    channelsCountError.value !== null ||
    channelFieldProblems.value.some((p) => p.name !== null || p.model !== null || p.effort !== null),
)
const draftChannels = computed<HarnessChannel[]>(() =>
  channelsOf(channelRows.value, defaultChannelId.value),
)

/** Варианты «Канала по умолчанию» — годные непустые строки. */
const defaultOptions = computed(() =>
  channelRows.value
    .filter((row, index) => {
      const problem = channelFieldProblems.value[index]
      return !channelRowBlank(row) && !problem?.name && !problem?.model && !problem?.effort
    })
    .map((row) => ({ value: row.id, label: row.name.trim() })),
)

/** Строк не больше `MAX_CHANNELS`: при полном списке «Добавить канал» спрятана, лишняя строка не встаёт. */
function onChannelRows(rows: ChannelRow[]): void {
  if (rows.length > MAX_CHANNELS && rows.length > channelRows.value.length) return
  channelRows.value = rows
}

function onDefaultChannel(id: string | null): void {
  if (id === null || id === defaultChannelId.value) return
  defaultChannelId.value = id
  schedule(true)
}

function onChannelRemoved(): void {
  schedule(true)
}

/** Ответ сервера → черновик: те же строки (id, фокус), значения и отметка — из ответа. */
function applyChannels(channels: HarnessChannel[]): void {
  const rows = channelRows.value.filter((row) => !channelRowBlank(row))
  if (rows.length !== channels.length) return
  rows.forEach((row, index) => {
    const channel = channels[index]
    if (!channel) return
    if (row.name !== channel.name) row.name = channel.name
    if (row.model !== channel.model) row.model = channel.model
    if (row.effort !== (channel.effort ?? '')) row.effort = channel.effort ?? ''
    row.fresh = false
    if (channel.default) defaultChannelId.value = row.id
  })
}

/* ── автосохранение: тот же порядок, что у карточек маршрутов ── */

/** Последнее известное серверу состояние: с ним сравнивается черновик. */
const baseline = reactive({
  label: props.harness.label,
  hint: props.harness.hint,
  icon: props.harness.icon ?? '',
  enabled: props.harness.enabled,
  argv: [...(props.harness.argv ?? [])],
  prompt: props.harness.prompt ?? '',
  channels: (props.harness.channels ?? []).map((channel) => ({ ...channel })),
})

const serverError = ref<string | null>(null)

/** Диф черновика против сохранённой записи; `null` — черновик с ошибкой не уходит и не откатывается. */
function diffPatch(): HarnessPatch | null {
  if (labelError.value || commandError.value) return null
  if (!manual.value && channelsError.value) return null
  const patch: HarnessPatch = {}
  if (label.value !== baseline.label) patch.label = label.value.trim()
  if (hint.value !== baseline.hint) patch.hint = hint.value
  if (icon.value !== baseline.icon) {
    patch.icon = icon.value.trim() === '' ? null : icon.value.trim()
  }
  if (enabled.value !== baseline.enabled) patch.enabled = enabled.value
  if (!manual.value) {
    const skipArgv = noServerCommand.value && argv.value.length === 0
    if (!skipArgv && JSON.stringify(argv.value) !== JSON.stringify(baseline.argv)) {
      patch.argv = argv.value
    }
    if (prompt.value !== baseline.prompt) {
      patch.prompt = prompt.value.trim() === '' ? null : prompt.value
    }
    if (JSON.stringify(draftChannels.value) !== JSON.stringify(baseline.channels)) {
      patch.channels = draftChannels.value
    }
  }
  return patch
}

/** Ответ последнего успешного PATCH — `onSaved` берёт из него нормализованные каналы. */
let savedHarness: Harness | null = null

const { status, schedule } = useAutosave<HarnessPatch>({
  diff: diffPatch,
  send: async (patch) => {
    savedHarness = await store.patchHarness(props.harness.key, patch)
    return savedHarness !== null
  },
  onSaved: (patch) => {
    if (patch.label !== undefined) baseline.label = patch.label
    if (patch.hint !== undefined) baseline.hint = patch.hint
    if (patch.icon !== undefined) baseline.icon = patch.icon ?? ''
    if (patch.enabled !== undefined) baseline.enabled = patch.enabled
    if (patch.argv !== undefined) baseline.argv = [...(patch.argv ?? [])]
    if (patch.argv && patch.argv.length > 0) noServerCommand.value = false
    if (patch.prompt !== undefined) baseline.prompt = patch.prompt ?? ''
    if (patch.channels !== undefined) {
      const sent = JSON.stringify(patch.channels)
      const saved = (savedHarness?.channels ?? patch.channels).map((channel) => ({ ...channel }))
      baseline.channels = saved
      // Черновик заменяется ответом, только если пользователь не правил его, пока запрос летел.
      if (JSON.stringify(draftChannels.value) === sent) applyChannels(saved)
    }
    serverError.value = null
  },
  onFailed: (patch) => {
    serverError.value = store.harnessesError.value ?? 'Сервер не принял правку'
    store.harnessesError.value = null
    // Тумблер возвращается к сохранённому значению — как `visible` у маршрутов.
    if (patch.enabled !== undefined) enabled.value = baseline.enabled
  },
})

function onLabel(value: string): void {
  label.value = value
  schedule(false)
}
function onHint(value: string): void {
  hint.value = value
  schedule(false)
}
function onPrompt(value: string): void {
  prompt.value = value
  schedule(false)
}
function onBlurText(): void {
  schedule(true)
}
function onEnabled(value: boolean): void {
  if (value === enabled.value) return
  enabled.value = value
  schedule(true)
}

/* Список аргументов (ввод в строке, добавление, удаление, перестановка) — в общий дебаунс. */
watch(argRows, () => schedule(false), { deep: true })
/* Каналы — так же; удаление строки и выбор по умолчанию дополнительно шлют сразу. */
watch(channelRows, () => schedule(false), { deep: true })

/* ── список аргументов и предпросмотр ── */

const preview = computed(() => previewCommand(withPrompt(argv.value, prompt.value), props.harness.key))

/* ── «Где используется» ── */

/** Код этапа роли для строки использования (`s1`…`s4`). */
function roleCode(role: string | null): string {
  return roleStage(role)?.code ?? role ?? ''
}

interface UsageRow {
  key: string
  text: string
}

const usages = computed<UsageRow[]>(() =>
  (props.harness.used_by ?? []).map((entry, index) => ({
    key: `${index}:${entry.kind}:${entry.route}:${entry.role ?? ''}`,
    text:
      entry.kind === 'pipeline'
        ? `конвейер ${entry.route} · роль ${roleCode(entry.role)}${entry.channel ? ` · канал ${entry.channel}` : ''}`
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
      <section class="listik-harness-card__section listik-harness-card__channels">
        <div class="listik-harness-card__section-head">
          <h4 class="listik-harness-card__section-title">Каналы</h4>
          <span v-if="channelsCountError" class="listik-harness-card__problem">
            {{ channelsCountError }}
          </span>
        </div>
        <p class="listik-harness-card__hint">
          Модели каналов читают мосты pi и opencode (харнессы <code class="listik-mono">pi</code> и
          <code class="listik-mono">opencode</code>); у других харнессов каналы пока никто не читает.
          Правка действует со следующего запуска. Удаление или переименование канала, на который
          ссылаются роли (см. «Где используется»), остановит роль, которая его просит.
        </p>
        <UiRecordList
          :class="{ 'listik-harness-card__channels-list--full': channelsFull }"
          :model-value="channelRows"
          :columns="CHANNEL_COLUMNS"
          :row-key="(row: ChannelRow) => row.id"
          :create-row="createChannelRow"
          add-label="Добавить канал"
          empty-title="Каналов нет"
          @update:model-value="onChannelRows"
          @remove="onChannelRemoved"
        >
          <template #cell-name="{ row, index, update }">
            <div class="listik-harness-card__arg">
              <UiInput
                size="sm"
                :model-value="row.name"
                :invalid="Boolean(channelFieldProblems[index]?.name)"
                @update:model-value="(value: string) => update(value)"
                v-bind="{ onBlur: onBlurText }"
              />
              <p v-if="channelFieldProblems[index]?.name" class="listik-harness-card__problem">
                {{ channelFieldProblems[index]?.name }}
              </p>
            </div>
          </template>
          <template #cell-model="{ row, index, update }">
            <div class="listik-harness-card__arg">
              <UiInput
                class="listik-mono"
                size="sm"
                placeholder="provider/model"
                :model-value="row.model"
                :invalid="Boolean(channelFieldProblems[index]?.model)"
                @update:model-value="(value: string) => update(value)"
                v-bind="{ onBlur: onBlurText }"
              />
              <p v-if="channelFieldProblems[index]?.model" class="listik-harness-card__problem">
                {{ channelFieldProblems[index]?.model }}
              </p>
            </div>
          </template>
          <template #cell-effort="{ row, index, update }">
            <div class="listik-harness-card__arg">
              <UiInput
                size="sm"
                placeholder="необязательно"
                :model-value="row.effort"
                :invalid="Boolean(channelFieldProblems[index]?.effort)"
                @update:model-value="(value: string) => update(value)"
                v-bind="{ onBlur: onBlurText }"
              />
              <p v-if="channelFieldProblems[index]?.effort" class="listik-harness-card__problem">
                {{ channelFieldProblems[index]?.effort }}
              </p>
            </div>
          </template>
        </UiRecordList>
        <p v-if="channelsFull" class="listik-harness-card__hint">
          не больше {{ MAX_CHANNELS }} каналов
        </p>
        <UiField v-if="defaultOptions.length > 0" label="Канал по умолчанию">
          <UiRadioGroup
            :model-value="defaultChannelId"
            :options="defaultOptions"
            @update:model-value="onDefaultChannel"
          />
        </UiField>
      </section>

      <section class="listik-harness-card__section">
        <div class="listik-harness-card__section-head">
          <h4 class="listik-harness-card__section-title">Команда по умолчанию</h4>
          <span class="listik-harness-card__section-note">
            по одному аргументу в строке — кавычки не нужны
          </span>
        </div>
        <UiRecordList
          v-model="argRows"
          :columns="ARG_COLUMNS"
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
        <!-- Ошибки строк стоят у строк; пустая команда — одна на весь список. -->
        <p v-if="commandError && argv.length === 0" class="listik-harness-card__problem">
          {{ commandError }}
        </p>
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
      <UiSaveStatus :status="status" @retry="() => schedule(true)" />
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

/* При 20 строках кнопка кита «Добавить канал» недоступна — отдельного пропа у UiRecordList нет. */
.listik-harness-card__channels-list--full :deep(.ui-record-list__add) {
  display: none;
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
