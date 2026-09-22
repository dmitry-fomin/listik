<script setup lang="ts">
/**
 * RouteDirectCard — карточка маршрута `kind=direct` во вкладке «Маршруты»
 * настроек (`RoutesSettings.vue`, правая панель). Раскладка — по макету
 * `docs/design/settings/Настройки · Маршруты · прямая выдача-html/RoutesDirect.dc.html`.
 *
 * Шапка — своя, а не `UiEntityHeader`: у кита в subtitle нет слота, а ключ
 * маршрута в строке «Прямая выдача · ключ <key>» обязан быть моноширинным.
 * Держатель карточки — только показ, одной строкой: подпись, замочек с
 * тултипом «держателя не сменить», глиф и имя харнесса (треб. 11).
 *
 * Сохранение автоматическое, как у карточки конвейера: текст — через 600мс
 * после последней клавиши и сразу по потере фокуса, тумблер и иконка — сразу,
 * состояние показывает `UiSaveStatus` нижней полосой. Пустого `PATCH` не
 * бывает: шлётся диф черновика против `baseline` (сервер на `{}` отвечает 400
 * «нечего менять»). Команда входит в диф только целой и валидной — пока в ней
 * ошибка, уходит одна шапка, а причина стоит в нижней полосе.
 *
 * На сервер уходит массив argv: аргументы — строки списка `UiRecordList`,
 * промпт — последний элемент. Склейка в строку никому не показывается — она
 * только под кнопкой «Команда для выполнения» нижней полосы.
 * Незнакомая подстановка не уходит вовсе (`unknownPlaceholders` /
 * `commandProblemText`): кнопка выключена, поле помечено `invalid`, рядом —
 * причина словами сервера.
 *
 * `:key="route.key"` у вызывающей стороны — часть контракта: другой маршрут =
 * заново созданная карточка со свежим черновиком. Несохранённые правки
 * (`update:dirty`) сторожит вызывающая сторона.
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiCopyButton,
  UiField,
  UiInput,
  UiRecordList,
  UiSaveStatus,
  UiSwitch,
  UiTextarea,
  UiTooltip,
  type SaveStatusValue,
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
import { harnessTitle } from '@/lib/harness'
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

const status = ref<SaveStatusValue>('idle')
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
  status.value = 'idle'
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

/** `visible` сюда не входит: тумблер сохраняется сразу своим `PATCH` (чек-лист d, п. 17). */
const headerChanged = computed(
  () =>
    draft.title !== baseline.title ||
    draft.hint !== baseline.hint ||
    draft.icon !== baseline.icon,
)

const dirty = computed(() => headerChanged.value || commandChanged.value)

/** Причина отказа по каждому аргументу (`null` — аргумент годится) — она же метит поле. */
const argProblems = computed(() => argRows.value.map((row) => commandProblemText(row.value)))
const promptProblem = computed(() => commandProblemText(prompt.value))
/** Порядок — серверный: элементы argv слева направо, промпт последний. */
const firstProblem = computed(
  () => argProblems.value.find((problem) => problem !== null) ?? promptProblem.value ?? null,
)

const emptyArg = computed(() => argRows.value.some((row) => row.value.trim() === ''))

/**
 * Почему команда не уходит на сервер — текст под секцией промпта. Ровно то,
 * что сервер отверг бы: пустая команда, пустой элемент, незнакомая подстановка.
 * «Изменений нет» здесь больше не причина: сохранение автоматическое, а не по
 * кнопке, и отсутствие правок объяснять автору незачем.
 */
const commandBlock = computed<string | null>(() => {
  if (commandEmpty.value) return 'команда пустая: нужен хотя бы промпт'
  if (prompt.value.trim() === '') return 'промпт пустой — так команда не сохранится'
  if (emptyArg.value) return 'пустой аргумент не сохранится: заполни строку или удали её'
  if (firstProblem.value) return firstProblem.value
  return null
})

/**
 * Что действительно потеряется при уходе с карточки — только команда с ошибкой:
 * она на сервер не уходит вовсе, а всё остальное досохранит автосохранение (в
 * том числе недоспавший дебаунс — `onBeforeUnmount` ниже). Именно этим, а не
 * `dirty`, кормится сторож ухода в `RoutesSettings.vue`: иначе диалог «потеряешь
 * правки» выскакивал бы на каждые 600мс дебаунса, когда терять нечего.
 */
const unsaved = computed(() => commandChanged.value && commandBlock.value !== null)
watch(unsaved, (value) => emit('update:dirty', value), { immediate: true })

/* ── автосохранение: тот же порядок, что у карточки конвейера ──
 *
 * Текст (название, подпись, аргументы, промпт) уходит через 600мс после
 * последней клавиши и сразу по потере фокуса; тумблер и иконка — сразу.
 * Команда попадает в `PATCH` только целой и валидной: пока `commandBlock` не
 * пуст, шапка сохраняется, а `command` ждёт — иначе каждый промежуточный
 * символ ловил бы 400 сервера.
 */

let inFlight = false
let queued = false
let debounceTimer: ReturnType<typeof setTimeout> | null = null

function diffPatch(): RoutePatch {
  const patch: RoutePatch = {}
  if (draft.title !== baseline.title) patch.title = draft.title
  if (draft.hint !== baseline.hint) patch.hint = draft.hint
  if (draft.visible !== baseline.visible) patch.visible = draft.visible
  if (draft.icon !== baseline.icon) patch.icon = draft.icon
  if (commandChanged.value && commandBlock.value === null) patch.command = commandDraft.value.slice()
  return patch
}

async function flush(): Promise<void> {
  const patch = diffPatch()
  if (Object.keys(patch).length === 0) return
  if (inFlight) {
    queued = true
    return
  }
  inFlight = true
  status.value = 'saving'
  const result = await store.patchRoute(props.route.key, patch)
  inFlight = false
  if (result) {
    if (patch.title !== undefined) baseline.title = patch.title
    if (patch.hint !== undefined) baseline.hint = patch.hint
    if (patch.visible !== undefined) baseline.visible = patch.visible
    if (patch.icon !== undefined) baseline.icon = patch.icon
    if (patch.command !== undefined) baseline.command = patch.command ? patch.command.slice() : null
    saveError.value = null
    status.value = 'saved'
  } else {
    // текст отказа (в том числе 400 сервера на команду) читаем сразу после await:
    // раньше другого действия вкладки его никто не перезапишет
    saveError.value = store.routesSettingsError.value
    status.value = 'error'
    // тумблер не сохранился — возвращаем его к серверному значению; текстовые
    // поля остаются как есть, там автор ещё может поправить ввод
    if (patch.visible !== undefined) draft.visible = baseline.visible
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

function onTitle(value: string): void {
  draft.title = value
  scheduleFlush(false)
}
function onHint(value: string): void {
  draft.hint = value
  scheduleFlush(false)
}
function onBlurText(): void {
  scheduleFlush(true)
}

/** Тумблер «В меню «Запустить»» — без задержки, как и иконка. */
function onVisible(value: boolean): void {
  if (value === draft.visible) return
  draft.visible = value
  scheduleFlush(true)
}

/*
 * Аргументы и промпт правятся несколькими путями (ввод, добавление, удаление,
 * перестановка строк), поэтому их ловит один watcher, а не обработчики полей.
 */
watch(commandDraft, () => scheduleFlush(false), { deep: true })

/*
 * Карточку сняли (выбрали другой маршрут, ушли со страницы) — досохраняем то,
 * что ещё лежит в дебаунсе.
 */
onBeforeUnmount(() => {
  if (debounceTimer) {
    clearTimeout(debounceTimer)
    debounceTimer = null
  }
  // `flush()` без условия на таймер: правка могла прийти и пока летел
  // предыдущий запрос (`queued`), и тогда таймера уже нет, а диф есть.
  // Пустой диф `flush` отбрасывает сам.
  void flush()
})

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

/* ── иконка: те же семь кнопок-глифов, что у конвейера, и так же сразу ── */

const iconOptions = computed<IconToggleOption<string>[]>(() => [
  ...ROUTE_ICONS.map((item) => ({ value: item.value as string, label: item.hint })),
  { value: '', label: 'Без иконки' },
])
const iconValue = computed(() => draft.icon ?? '')

function glyphFor(value: string): string | null {
  return ROUTE_ICONS.find((item) => item.value === value)?.icon ?? null
}

function onIcon(value: string): void {
  draft.icon = value === '' ? null : (value as RouteIconKey)
  scheduleFlush(true)
}

/* ── предпросмотр ── */

const preview = computed(() => previewCommand(commandDraft.value, props.route.key))
</script>

<template>
  <div class="listik-route-direct">
    <header class="listik-route-direct__head">
      <span class="listik-route-direct__tile" aria-hidden="true">
        <RouteIcon :route="route" size="md" />
      </span>
      <div class="listik-route-direct__head-main">
        <h2 class="listik-route-direct__name">{{ route.title }}</h2>
        <p class="listik-route-direct__keyline">
          Прямая выдача · ключ <code class="listik-mono">{{ route.key }}</code>
        </p>
      </div>
      <UiSwitch :model-value="draft.visible" @update:model-value="onVisible">В меню «Запустить»</UiSwitch>
    </header>

    <div class="listik-route-direct__fields">
      <UiField label="Название в меню">
        <UiInput
          :model-value="draft.title"
          @update:model-value="onTitle"
          v-bind="{ onBlur: onBlurText }"
        />
      </UiField>
      <UiField label="Подпись под названием">
        <UiInput
          :model-value="draft.hint"
          @update:model-value="onHint"
          v-bind="{ onBlur: onBlurText }"
        />
      </UiField>
    </div>

    <div class="listik-route-direct__row">
      <section class="listik-route-direct__icon">
        <h4 class="listik-route-direct__label">Иконка в списках и на карточке</h4>
        <IconToggle
          :model-value="iconValue"
          :options="iconOptions"
          ariaLabel="Иконка маршрута"
          @update:model-value="onIcon"
        >
          <template #icon="{ option }">
            <span v-if="option.value === ''" class="listik-route-direct__icon-none">Без иконки</span>
            <ListikIcon v-else :name="glyphFor(option.value) ?? ''" size="sm" />
          </template>
        </IconToggle>
      </section>

      <section class="listik-route-direct__holder-field">
        <div class="listik-route-direct__holder">
          <span class="listik-route-direct__label">Держатель карточки</span>
          <UiTooltip text="держателя не сменить" placement="top">
            <ListikIcon
              class="listik-route-direct__holder-lock"
              name="lock"
              size="sm"
            />
          </UiTooltip>
          <HarnessIcon :harness="route.harness" size="sm" />
          <code class="listik-mono">{{ harnessTitle(route.harness) }}</code>
        </div>
      </section>
    </div>

    <UiAlert v-if="saveError" tone="danger" closable @close="saveError = null">
      <template #title>Сервер не принял правку</template>
      {{ saveError }}
    </UiAlert>

    <section class="listik-route-direct__section listik-route-direct__args">
      <div class="listik-route-direct__section-head">
        <h4 class="listik-route-direct__section-title">Команда запуска</h4>
        <span class="listik-route-direct__section-note">
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
      <p v-if="promptProblem" class="listik-route-direct__problem">{{ promptProblem }}</p>
      <RouteSubstitutions :route-key="route.key" :command="commandDraft" />
    </section>

    <!-- Нижняя полоса: команда для выполнения — кнопкой копирования, а не
         простынёй текста: целиком её всё равно не читают, а скопировать в
         терминал нужно. Рядом — состояние автосохранения и причина, по которой
         команда пока не ушла на сервер. -->
    <div class="listik-route-direct__actions">
      <span class="listik-route-direct__actions-note">
        Правки применятся к следующему запуску. Уже запущенные задачи не трогаются.
      </span>
      <UiCopyButton :value="preview" label="Команда для выполнения">
        <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
        Команда для выполнения
      </UiCopyButton>
      <UiSaveStatus :status="status" @retry="() => scheduleFlush(true)" />
      <p v-if="commandBlock" class="listik-route-direct__reason is-problem">{{ commandBlock }}</p>
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

/* ── шапка: плитка с глифом, название с ключом, тумблер видимости ── */

.listik-route-direct__head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.listik-route-direct__tile {
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

.listik-route-direct__head-main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.listik-route-direct__name {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-tight);
  color: var(--ink-1);
}

.listik-route-direct__keyline {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--ink-3);
}

.listik-route-direct__fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

/* ── иконка и держатель в одной строке ── */

/* Колонка держателя — по содержимому: строка «подпись + замочек + глиф +
   имя» шире бывших 14rem и резать её нельзя. */
.listik-route-direct__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-4);
  align-items: end;
}

.listik-route-direct__icon,
.listik-route-direct__holder-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.listik-route-direct__label {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-route-direct__icon-none {
  font-size: var(--text-sm);
  color: var(--ink-3);
}

/* Держатель — показ одной строкой: подпись, замочек «не сменить» (тултип),
   глиф и имя харнесса. Бокса-поля нет: внутри нечего вводить. */
.listik-route-direct__holder {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control-h-md);
  min-width: 0;
  font-size: var(--text-sm);
}

.listik-route-direct__holder-lock {
  color: var(--ink-3);
}

/* ── секции ── */

.listik-route-direct__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
  min-width: 0;
}

.listik-route-direct__section-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.listik-route-direct__section-title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-route-direct__section-note {
  margin-left: auto;
  font-size: var(--text-xs);
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

.listik-route-direct__problem {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--danger-600);
}

/* ── нижняя полоса ── */

.listik-route-direct__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-route-direct__actions-note {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--text-xs);
  color: var(--ink-3);
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
</style>
