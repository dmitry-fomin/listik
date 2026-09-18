<script setup lang="ts">
/**
 * RouteDirectCard — карточка маршрута `kind=direct` во вкладке «Маршруты»
 * настроек (`RoutesSettings.vue`, правая панель; артборд «Direct» из
 * `docs/specs/routes-settings-ui.md`). Здесь автор правит командную строку,
 * которую сервер потом сам запускает, поэтому карточка устроена не как
 * карточка конвейера (`RouteCard.vue`, там автосохранение):
 *
 *  * **сохранение явное** — «Отменить»/«Сохранить» нижней полосой карточки,
 *    последним действием после всех правок, один `PATCH` со
 *    всеми изменёнными полями сразу. Пустого `PATCH` не бывает: без изменений
 *    кнопка выключена (сервер на `{}` отвечает 400 «нечего менять»);
 *  * **на сервер уходит массив** argv: аргументы — строки списка, промпт —
 *    последний элемент. Склейка в строку живёт только в предпросмотре;
 *  * **незнакомая подстановка не уходит вовсе**: правило — `unknownPlaceholders`
 *    / `commandProblemText` из `lib/routes.ts`, повторяющие серверный
 *    `routes.validate_command` (сначала голая скобка, потом незнакомое имя).
 *    Кнопка выключена, поле помечено `invalid`, рядом — причина словами сервера.
 *    Ответ 400 всё равно показывается: правило могло разойтись с сервером;
 *  * пустой аргумент, пустой промпт и пустая команда не сохраняются — маршрут
 *    не должен оказаться с невыполнимой командой;
 *  * `harness` — только чтение (глиф `HarnessIcon`): сервер его и не примет,
 *    в теле `PATCH` только `title|hint|icon|visible|command`.
 *
 * Порядок аргументов, добавление и удаление — `UiRecordList` кита (ручка,
 * ▲/▼, ✕, кнопка добавления): своего списка и своего drag&drop тут нет.
 * В отличие от `RoutesSettings.vue`, мутацию модели китом откатывать не надо —
 * список аргументов до «Сохранить» и так живёт только в карточке.
 *
 * `:key="route.key"` у вызывающей стороны — часть контракта: другой маршрут =
 * заново созданная карточка со свежим черновиком. Несохранённые правки
 * (`update:dirty`) сторожит вызывающая сторона: она спрашивает подтверждение
 * до того, как сменить выбор.
 */
import { computed, reactive, ref, watch } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiEntityHeader,
  UiField,
  UiInput,
  UiRecordList,
  UiSwitch,
  UiTextarea,
  type UiRecordListColumn,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import HarnessIcon from './marks/HarnessIcon.vue'
import RouteIcon from './marks/RouteIcon.vue'
import RouteCommandText from './RouteCommandText.vue'
import RouteSubstitutions from './RouteSubstitutions.vue'
import store from '@/store/listik'
import type { DirectRouteDef, RouteIconKey, RoutePatch } from '@/api/types'
import { ROUTE_ICONS } from '@/lib/dictionaries'
import { HARNESS_TITLES } from '@/lib/harness'
import { commandProblemText, isDangerousArg, previewCommand } from '@/lib/routes'

const props = defineProps<{ route: DirectRouteDef }>()
const emit = defineEmits<{ 'update:dirty': [value: boolean] }>()

/* ── черновик: шапка + argv ── */

interface HeaderDraft {
  title: string
  hint: string
  visible: boolean
  icon: RouteIconKey | null
}

/**
 * Строка списка аргументов. `id` — устойчивый ключ для `UiRecordList`
 * (перестановка анимируется только при стабильном `rowKey`, а сам аргумент
 * значением быть ключом не может: пустых и одинаковых строк сколько угодно).
 */
interface ArgRow extends Record<string, unknown> {
  id: string
  value: string
}

let rowSeq = 0
function argRowsOf(values: string[]): ArgRow[] {
  return values.map((value) => ({ id: `arg-${(rowSeq += 1)}`, value }))
}

function headerOf(route: DirectRouteDef): HeaderDraft {
  return { title: route.title, hint: route.hint, visible: route.visible, icon: route.icon ?? null }
}

/** Промпт — последний элемент argv, остальное — аргументы списка. */
function splitCommand(route: DirectRouteDef): { args: string[]; prompt: string } {
  const command = route.command ?? []
  if (command.length === 0) return { args: [], prompt: '' }
  return { args: command.slice(0, -1), prompt: command[command.length - 1] ?? '' }
}

const draft = reactive<HeaderDraft>(headerOf(props.route))
const argRows = ref<ArgRow[]>(argRowsOf(splitCommand(props.route).args))
const prompt = ref(splitCommand(props.route).prompt)

/** Последнее известное серверу состояние: с ним сравнивается черновик. */
const baseline = reactive<HeaderDraft & { command: string[] | null }>({
  ...headerOf(props.route),
  command: props.route.command ? [...props.route.command] : null,
})

const saving = ref(false)
const saveError = ref<string | null>(null)

function resetDraft(route: DirectRouteDef): void {
  const header = headerOf(route)
  draft.title = header.title
  draft.hint = header.hint
  draft.visible = header.visible
  draft.icon = header.icon
  const parts = splitCommand(route)
  argRows.value = argRowsOf(parts.args)
  prompt.value = parts.prompt
  baseline.title = header.title
  baseline.hint = header.hint
  baseline.visible = header.visible
  baseline.icon = header.icon
  baseline.command = route.command ? [...route.command] : null
  saveError.value = null
}

/**
 * Запись пришла заново (`store.reloadRoutes` после любой правки вкладки) —
 * забираем серверные значения, но только пока автор ничего не набрал: иначе
 * перезагрузка списка стирала бы недопечатанную команду.
 */
watch(
  () => props.route,
  (route) => {
    if (!dirty.value) resetDraft(route)
  },
)

/* ── что получится в argv и что с этим не так ── */

const commandDraft = computed<string[]>(() => [
  ...argRows.value.map((row) => row.value),
  prompt.value,
])

const commandEmpty = computed(
  () => argRows.value.length === 0 && prompt.value.trim() === '',
)

const commandChanged = computed(() => {
  if (baseline.command === null) return !commandEmpty.value
  return JSON.stringify(commandDraft.value) !== JSON.stringify(baseline.command)
})

const headerChanged = computed(
  () =>
    draft.title !== baseline.title ||
    draft.hint !== baseline.hint ||
    draft.visible !== baseline.visible ||
    draft.icon !== baseline.icon,
)

const dirty = computed(() => headerChanged.value || commandChanged.value)
watch(dirty, (value) => emit('update:dirty', value), { immediate: true })

/** Причина отказа по каждому аргументу (`null` — аргумент годится) — она же метит поле. */
const argProblems = computed(() => argRows.value.map((row) => commandProblemText(row.value)))
const promptProblem = computed(() => commandProblemText(prompt.value))
/** Порядок — серверный: элементы argv слева направо, промпт последний. */
const firstProblem = computed(
  () => argProblems.value.find((problem) => problem !== null) ?? promptProblem.value ?? null,
)

const emptyArg = computed(() => argRows.value.some((row) => row.value.trim() === ''))

/**
 * Почему «Сохранить» выключена — текст рядом с кнопкой. Сначала то, что сервер
 * отверг бы (пустая команда, пустой элемент, незнакомая подстановка), и только
 * потом «изменений нет»: у команды с ошибкой причина важнее, чем факт правки.
 */
const blockReason = computed<string | null>(() => {
  if (commandEmpty.value) return 'команда пустая: нужен хотя бы промпт'
  if (prompt.value.trim() === '') return 'промпт пустой — так команда не сохранится'
  if (emptyArg.value) return 'пустой аргумент не сохранится: заполни строку или удали её'
  if (firstProblem.value) return firstProblem.value
  if (!dirty.value) return 'изменений нет'
  return null
})

/** «изменений нет» — это не ошибка ввода, поэтому и выглядит иначе. */
const reasonIsProblem = computed(() => blockReason.value !== null && dirty.value)

const canSave = computed(() => blockReason.value === null && !saving.value)

/* ── сохранение: один PATCH со всеми изменёнными полями ── */

async function save(): Promise<void> {
  if (!canSave.value) return
  const patch: RoutePatch = {}
  if (draft.title !== baseline.title) patch.title = draft.title
  if (draft.hint !== baseline.hint) patch.hint = draft.hint
  if (draft.visible !== baseline.visible) patch.visible = draft.visible
  if (draft.icon !== baseline.icon) patch.icon = draft.icon
  const command = commandChanged.value ? commandDraft.value.slice() : null
  if (command) patch.command = command
  if (Object.keys(patch).length === 0) return

  saving.value = true
  const result = await store.patchRoute(props.route.key, patch)
  saving.value = false
  if (!result) {
    // текст отказа (в том числе 400 сервера на команду) читаем сразу после await:
    // раньше другого действия вкладки его никто не перезапишет
    saveError.value = store.routesSettingsError.value
    return
  }
  saveError.value = null
  baseline.title = draft.title
  baseline.hint = draft.hint
  baseline.visible = draft.visible
  baseline.icon = draft.icon
  if (command) baseline.command = command
}

function cancel(): void {
  resetDraft(props.route)
}

/* ── список аргументов ── */

const argColumns: UiRecordListColumn[] = [{ key: 'value', label: 'Аргумент', type: 'custom' }]

function createArgRow(): ArgRow {
  return { id: `arg-${(rowSeq += 1)}`, value: '' }
}

function rowKeyOf(row: ArgRow): string {
  return row.id
}

/** Показывать разбор строки под полем стоит только там, где есть что разбирать. */
function hasBraces(value: string): boolean {
  return value.includes('{') || value.includes('}')
}

/* ── уровень: те же семь кнопок-глифов, что у конвейера, но без автосохранения ── */

const levelOptions = computed<IconToggleOption<string>[]>(() => [
  ...ROUTE_ICONS.map((item) => ({ value: item.value as string, label: item.hint })),
  { value: '', label: 'нет' },
])
const levelValue = computed(() => draft.icon ?? '')

function glyphFor(value: string): string | null {
  return ROUTE_ICONS.find((item) => item.value === value)?.icon ?? null
}

function onLevel(value: string): void {
  draft.icon = value === '' ? null : (value as RouteIconKey)
}

/* ── предпросмотр ── */

const preview = computed(() => previewCommand(commandDraft.value, props.route.key))
</script>

<template>
  <div class="listik-route-direct">
    <UiEntityHeader :title="route.title" eyebrow="Прямая выдача" :subtitle="route.key">
      <template #avatar><RouteIcon :route="route" size="md" /></template>
    </UiEntityHeader>

    <div class="listik-route-direct__header">
      <UiField label="Название кнопки">
        <UiInput v-model="draft.title" />
      </UiField>
      <UiField label="Подпись">
        <UiInput v-model="draft.hint" />
      </UiField>
      <UiField label="Показывать автору">
        <UiSwitch v-model="draft.visible" />
      </UiField>

      <div class="listik-route-direct__holder">
        <span class="listik-route-direct__holder-label">Держатель карточки</span>
        <HarnessIcon :harness="route.harness" size="sm" />
        <span class="listik-mono">{{ HARNESS_TITLES[route.harness] }}</span>
        <span class="listik-route-direct__holder-note">
          <ListikIcon name="lock" size="sm" />
          не правится
        </span>
      </div>
    </div>

    <UiAlert v-if="saveError" tone="danger" closable @close="saveError = null">
      <template #title>Сервер не принял правку</template>
      {{ saveError }}
    </UiAlert>

    <section class="listik-route-direct__section">
      <h4 class="listik-route-direct__section-title">Уровень</h4>
      <IconToggle
        :model-value="levelValue"
        :options="levelOptions"
        ariaLabel="Уровень маршрута"
        @update:model-value="onLevel"
      >
        <template #icon="{ option }">
          <span v-if="option.value === ''" class="listik-route-direct__level-none" aria-hidden="true">—</span>
          <ListikIcon v-else :name="glyphFor(option.value) ?? ''" size="sm" />
        </template>
      </IconToggle>
    </section>

    <section class="listik-route-direct__section listik-route-direct__args">
      <h4 class="listik-route-direct__section-title">Аргументы</h4>
      <p class="listik-prose">
        По одному аргументу в поле: кавычки не нужны, ничего экранировать не надо — argv
        уходит списком строк. Промпт — последний аргумент, он ниже отдельным полем.
      </p>
      <UiRecordList
        v-model="argRows"
        :columns="argColumns"
        :row-key="rowKeyOf"
        :create-row="createArgRow"
        add-label="Добавить аргумент"
        empty-title="Аргументов нет — только промпт"
      >
        <template #cell-value="{ row, index, update }">
          <div class="listik-route-direct__arg">
            <UiInput
              class="listik-route-direct__arg-input"
              size="sm"
              :model-value="row.value"
              :invalid="Boolean(argProblems[index])"
              @update:model-value="(value: string) => update(value)"
            />
            <UiBadge v-if="isDangerousArg(row.value)" tone="warning" size="sm">
              полный доступ к репозиторию
            </UiBadge>
            <p v-if="hasBraces(row.value)" class="listik-mono listik-route-direct__echo">
              <RouteCommandText :text="row.value" />
            </p>
            <p v-if="argProblems[index]" class="listik-route-direct__problem">
              {{ argProblems[index] }}
            </p>
          </div>
        </template>
      </UiRecordList>
    </section>

    <section class="listik-route-direct__section">
      <h4 class="listik-route-direct__section-title">Промпт — последний аргумент</h4>
      <UiTextarea
        v-model="prompt"
        class="listik-route-direct__prompt"
        :rows="8"
        :invalid="Boolean(promptProblem)"
      />
      <p class="listik-mono listik-route-direct__echo listik-route-direct__echo--prompt">
        <RouteCommandText :text="prompt" />
      </p>
      <p v-if="promptProblem" class="listik-route-direct__problem">{{ promptProblem }}</p>
    </section>

    <section class="listik-route-direct__section">
      <RouteSubstitutions :route-key="route.key" :command="commandDraft" />
    </section>

    <section class="listik-route-direct__section">
      <h4 class="listik-route-direct__section-title">Предпросмотр</h4>
      <p class="listik-mono listik-route-direct__preview">{{ preview }}</p>
      <p class="listik-prose">
        Так команда выглядела бы строкой: подстановки заменены примерными значениями, аргумент
        с пробелом внутри показан в кавычках. Запускается она не так — shell не участвует, argv
        передаётся списком строк, и кавычки в него не попадают.
      </p>
    </section>

    <!-- Нижняя полоса: «Сохранить» — последнее действие карточки, а не первое.
         Правки идут сверху вниз (шапка → уровень → аргументы → промпт), кнопка
         стоит там, где автор заканчивает, и причина отказа рядом с ней. -->
    <div class="listik-route-direct__actions">
      <UiButton variant="ghost" :disabled="!dirty || saving" @click="cancel">Отменить</UiButton>
      <UiButton
        class="listik-route-direct__save"
        :disabled="!canSave"
        :loading="saving"
        @click="save"
      >
        Сохранить
      </UiButton>
      <p
        v-if="blockReason"
        class="listik-route-direct__reason"
        :class="{ 'is-problem': reasonIsProblem }"
      >
        {{ blockReason }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.listik-route-direct {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.listik-route-direct__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.listik-route-direct__holder {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.listik-route-direct__holder-label {
  color: var(--ink-3);
}

.listik-route-direct__holder-note {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--ink-3);
}

.listik-route-direct__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-route-direct__reason {
  flex: 1 1 100%;
  margin: 0;
  font-size: var(--text-sm);
  color: var(--ink-3);
}

.listik-route-direct__reason.is-problem {
  color: var(--danger-600);
}

.listik-route-direct__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
  min-width: 0;
}

.listik-route-direct__section-title {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-route-direct__level-none {
  font-size: var(--text-sm);
  color: var(--ink-3);
}

.listik-route-direct__arg {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-1);
  width: 100%;
  min-width: 0;
}

/* Колонка аргумента обязана ужиматься под узкую панель карточки: нативный input
   шире своего содержимого (по умолчанию `size=20`) и не сжимается как flex-элемент,
   поэтому колонка распирала таблицу и поле вместе с ✕ строки уезжало под
   горизонтальный скролл. `min-width: 0` — ровно на контрол кита, ширину колонок
   таблица по-прежнему считает сама (у ручки и ✕ она своя). */
.listik-route-direct__arg :deep(.ui-input) {
  min-width: 0;
}

/* Бейдж кита не переносится по умолчанию (`white-space: nowrap`), а колонка
   аргумента узкая: без переноса его min-content распирал таблицу шире панели и
   поле аргумента уезжало под горизонтальный скролл. */
.listik-route-direct__arg .ui-badge {
  white-space: normal;
  text-align: left;
  overflow-wrap: anywhere;
}

.listik-route-direct__echo {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--ink-3);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.listik-route-direct__echo--prompt {
  max-height: 10em;
  overflow-y: auto;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}

.listik-route-direct__problem {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--danger-600);
}

.listik-route-direct__preview {
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
