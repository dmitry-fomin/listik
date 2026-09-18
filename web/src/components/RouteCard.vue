<script setup lang="ts">
/**
 * RouteCard — карточка выбранного маршрута во вкладке «Маршруты» настроек
 * (`RoutesSettings.vue`, правая панель). Общая шапка (заголовок/подпись/
 * «Показывать автору») работает для обеих разновидностей записи и сохраняется
 * сама; уровень, состав конвейера (только чтение) и «чем запускается» —
 * только у `kind=pipeline` (у `kind=direct` в этой порции показывается только
 * шапка — свою карточку с редактором `argv` делает порция `f`).
 *
 * Автосохранение шапки: текстовые поля — debounce 600мс после последней
 * клавиши плюс сброс по потере фокуса, переключатель и уровень — сразу.
 * `flush()` шлёт диф (`draft` против `baseline`, обновлённого только по
 * ключам последнего успешного PATCH) одним `PATCH /api/routes/<key>`; запрос
 * уже в пути — новый вызов лишь помечает `queued`, а не летит вторым сразу,
 * и после ответа первого перезапускает себя тем же `flush()`, забирая самое
 * свежее значение полей. `store.patchRoute` — из этого же файла (порция `d`),
 * его отказ кладёт текст в `store.routesSettingsError`, читаем сразу после
 * `await` (тот же тик, раньше другого действия вкладки его не перезапишет).
 *
 * `:key="route.key"` у вызывающей стороны — часть контракта: при выборе
 * другого маршрута компонент должен пересоздаться заново (свежие `draft`/
 * `baseline`), а не переиспользоваться с патчем пропа поверх недопечатанного
 * ввода.
 */
import { computed, reactive, ref } from 'vue'
import {
  UiAlert,
  UiCopyButton,
  UiEntityHeader,
  UiField,
  UiInput,
  UiSaveStatus,
  UiSwitch,
  type SaveStatusValue,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import ProviderIcon from './marks/ProviderIcon.vue'
import RouteIcon from './marks/RouteIcon.vue'
import RouteSubstitutions from './RouteSubstitutions.vue'
import store from '@/store/listik'
import type { RouteDef, RouteIconKey, RoutePatch } from '@/api/types'
import { PIPELINE_STAGES, ROUTE_ICONS, type PipelineStep } from '@/lib/dictionaries'
import { ROLE_KEYS, ROLE_STAGE, ROLE_TITLES, type RoleCell, type RoleKey } from '@/lib/pipelines'
import { splitPlaceholders, unknownPlaceholders } from '@/lib/routes'

const props = defineProps<{ route: RouteDef }>()

/* ── шапка: title/hint/visible/icon, автосохранение ── */

interface HeaderDraft {
  title: string
  hint: string
  visible: boolean
  icon: RouteIconKey | null
}

function draftOf(route: RouteDef): HeaderDraft {
  return { title: route.title, hint: route.hint, visible: route.visible, icon: route.icon ?? null }
}

/** Последнее известное серверу состояние — только по ключам успешного PATCH. */
const baseline = reactive<HeaderDraft>(draftOf(props.route))
/** Текущий ввод автора — то, что показывают поля. */
const draft = reactive<HeaderDraft>(draftOf(props.route))

const status = ref<SaveStatusValue>('idle')
const saveError = ref<string | null>(null)
const errorFields = ref<string[]>([])

let inFlight = false
let queued = false
let debounceTimer: ReturnType<typeof setTimeout> | null = null

function diffPatch(): RoutePatch {
  const patch: RoutePatch = {}
  if (draft.title !== baseline.title) patch.title = draft.title
  if (draft.hint !== baseline.hint) patch.hint = draft.hint
  if (draft.visible !== baseline.visible) patch.visible = draft.visible
  if (draft.icon !== baseline.icon) patch.icon = draft.icon
  return patch
}

async function flush(): Promise<void> {
  const patch = diffPatch()
  const keys = Object.keys(patch) as (keyof RoutePatch)[]
  if (keys.length === 0) return
  if (inFlight) {
    queued = true
    return
  }
  inFlight = true
  status.value = 'saving'
  const result = await store.patchRoute(props.route.key, patch)
  inFlight = false
  if (result) {
    for (const key of keys) (baseline as Record<string, unknown>)[key] = patch[key]
    saveError.value = null
    errorFields.value = []
    status.value = 'saved'
  } else {
    saveError.value = store.routesSettingsError.value
    errorFields.value = keys
    status.value = 'error'
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
function onVisible(value: boolean): void {
  draft.visible = value
  scheduleFlush(true)
}

function errorFor(field: string): string | undefined {
  return status.value === 'error' && errorFields.value.includes(field) ? (saveError.value ?? undefined) : undefined
}

/* ── уровень: семь кнопок-глифов (шесть ROUTE_ICONS + «нет») ── */

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
  scheduleFlush(true)
}

/* ── состав конвейера: только чтение ── */

const hasRoles = computed(() => props.route.kind === 'pipeline' && Object.keys(props.route.roles).length > 0)

/** Этап роли — через `ROLE_STAGE`, подписи только из словаря (своих литералов этапов тут нет). */
function stageOf(role: RoleKey): PipelineStep {
  return PIPELINE_STAGES.find((item) => item.value === ROLE_STAGE[role]) ?? PIPELINE_STAGES[0]
}

interface RoleTile {
  role: RoleKey
  step: PipelineStep
  title: string
  cell: RoleCell | null
}

/**
 * Плитки состава — всегда четыре, по одной на этап конвейера: у роли, которой в
 * пресете нет (и у незаполненного состава целиком), плитка та же, только
 * приглушённая. Так видно, что этапов ровно четыре, а пресет закрывает не все.
 */
const roleTiles = computed<RoleTile[]>(() =>
  ROLE_KEYS.map((role) => ({
    role,
    step: stageOf(role),
    title: ROLE_TITLES[role],
    cell: props.route.kind === 'pipeline' ? (props.route.roles[role] ?? null) : null,
  })),
)

/* ── чем запускается + подстановки ── */

/** Команда построчно: элемент argv — строка блока, подсветка считается внутри элемента. */
const commandLines = computed(() => (props.route.command ?? []).map((element) => splitPlaceholders(element)))
/** Та же команда одной строкой — для кнопки копирования рядом с заголовком секции. */
const commandText = computed(() => (props.route.command ?? []).join(' '))
const unknownNames = computed(() => unknownPlaceholders(props.route.command))

/** `{name}` — вынесено функцией: буквальные `{}` внутри `{{ }}` шаблона путают парсер Vue. */
function braced(name: string): string {
  return '{' + name + '}'
}
</script>

<template>
  <div class="listik-route-card">
    <UiEntityHeader :title="route.title" eyebrow="Конвейер" :subtitle="route.key">
      <template #avatar><RouteIcon :route="route" size="md" /></template>
    </UiEntityHeader>

    <div class="listik-route-card__header">
      <UiField label="Заголовок" :error="errorFor('title')">
        <UiInput
          :model-value="draft.title"
          @update:model-value="onTitle"
          v-bind="{ onBlur: onBlurText }"
        />
      </UiField>
      <UiField label="Подпись" :error="errorFor('hint')">
        <UiInput
          :model-value="draft.hint"
          @update:model-value="onHint"
          v-bind="{ onBlur: onBlurText }"
        />
      </UiField>
      <UiField label="Показывать автору" :error="errorFor('visible')">
        <UiSwitch :model-value="draft.visible" @update:model-value="onVisible" />
      </UiField>
      <UiSaveStatus :status="status" @retry="() => scheduleFlush(true)" />
    </div>

    <UiAlert v-if="route.kind === 'pipeline' && route.skill_missing" tone="warning">
      <template #title>Расхождение со скилом</template>
      скила <code class="listik-mono">/feature-pipeline:{{ route.key }}</code> нет, маршрут скрыт от автора.
    </UiAlert>

    <template v-if="route.kind === 'pipeline'">
      <section class="listik-route-card__section">
        <h4 class="listik-route-card__section-title">Уровень</h4>
        <IconToggle
          :model-value="levelValue"
          :options="levelOptions"
          ariaLabel="Уровень маршрута"
          @update:model-value="onLevel"
        >
          <template #icon="{ option }">
            <span v-if="option.value === ''" class="listik-route-card__level-none" aria-hidden="true">—</span>
            <ListikIcon v-else :name="glyphFor(option.value) ?? ''" size="sm" />
          </template>
        </IconToggle>
      </section>

      <section class="listik-route-card__section">
        <h4 class="listik-route-card__section-title">
          <ListikIcon name="lock" size="sm" />
          Состав конвейера
        </h4>
        <div class="listik-route-card__roles">
          <div
            v-for="tile in roleTiles"
            :key="tile.role"
            class="listik-route-card__role"
            :class="{ 'is-empty': !tile.cell }"
          >
            <span class="listik-route-card__role-stage">
              <span class="listik-route-card__role-code">{{ tile.step.code }}</span>
              <span class="listik-route-card__role-label">{{ tile.step.label }}</span>
            </span>
            <span v-if="tile.cell" class="listik-route-card__role-vendor">
              <ProviderIcon :provider="tile.cell.provider" size="sm" />
              <span class="listik-route-card__role-title">{{ tile.cell.title }}</span>
            </span>
            <span v-else class="listik-route-card__role-empty">этапа нет в этом пресете</span>
            <span class="listik-route-card__role-role">{{ tile.title }}</span>
          </div>
        </div>
        <p v-if="!hasRoles" class="listik-prose">
          состав не заполнен: роли берутся из базы, их заполняет ввоз
          <code class="listik-mono">listik routes import</code>
        </p>
        <p class="listik-route-card__skill-line">
          из скила <code class="listik-mono">/feature-pipeline:{{ route.key }}</code>
        </p>
        <div v-if="route.skill_path" class="listik-route-card__skill-path">
          <code class="listik-mono">{{ route.skill_path }}</code>
          <UiCopyButton :value="route.skill_path" label="Путь к скилу">
            <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
          </UiCopyButton>
        </div>
      </section>

      <section class="listik-route-card__section">
        <h4 class="listik-route-card__section-title">
          Чем запускается
          <UiCopyButton v-if="route.command" :value="commandText" label="Команда запуска">
            <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
          </UiCopyButton>
        </h4>
        <p v-if="!route.command" class="listik-prose">маршрут не запускается автоматически</p>
        <template v-else>
          <!-- Внутри `pre` компилятор Vue сохраняет пробелы как есть, поэтому тут нет
               ни одного переноса строки между узлами: перенос даёт сама плитка строки
               (`display: block`), а лишний отступ шаблона утёк бы в команду на экране. -->
          <pre
            class="listik-route-card__command"
          ><span v-for="(line, index) in commandLines" :key="index" class="listik-route-card__command-line"><template
            v-for="(chunk, at) in line"
            :key="at"
          ><span v-if="chunk.type === 'text'">{{ chunk.value }}</span><span
            v-else-if="chunk.type === 'placeholder'"
            class="listik-route-card__placeholder"
          >{{ braced(chunk.value) }}</span><span
            v-else
            class="listik-route-card__placeholder listik-route-card__placeholder--unknown"
            :title="`неизвестная подстановка: ${chunk.value}`"
          >{{ braced(chunk.value) }}</span></template></span></pre>
          <p v-if="unknownNames.length > 0" class="listik-route-card__unknown">
            неизвестная подстановка: {{ unknownNames.join(', ') }}
          </p>
        </template>
        <RouteSubstitutions :route-key="route.key" :command="route.command" />
      </section>
    </template>
  </div>
</template>

<style scoped>
.listik-route-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.listik-route-card__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.listik-route-card__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-route-card__section-title {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-route-card__level-none {
  font-size: var(--text-sm);
  color: var(--ink-3);
}

/* Четыре плитки по этапам конвейера: на всю ширину карточки — в строку, на узкой
   панели — в две колонки (плитка с вендором и подписью в одну колонку не влезает). */
.listik-route-card__roles {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-2);
}

@media (max-width: 720px) {
  .listik-route-card__roles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.listik-route-card__role {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  padding: var(--space-2);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  background: var(--surface-2);
  font-size: var(--text-sm);
}

/* Этапа в пресете нет — плитка остаётся на месте, но гаснет: состав читается
   как четыре этапа, из которых закрыты не все. */
.listik-route-card__role.is-empty {
  color: var(--ink-3);
}

.listik-route-card__role-stage {
  display: flex;
  align-items: baseline;
  gap: var(--space-1);
  min-width: 0;
}

.listik-route-card__role-code {
  font-family: var(--font-mono);
  font-weight: var(--weight-medium);
}

.listik-route-card__role-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-route-card__role-vendor {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  min-width: 0;
}

.listik-route-card__role-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.listik-route-card__role-role {
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-route-card__role-empty {
  color: var(--ink-3);
  font-style: italic;
}

.listik-route-card__skill-line {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--ink-3);
}

.listik-route-card__skill-path {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.listik-route-card__skill-path code {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Команда — блок, а не строка: каждый элемент argv на своей строке, длинный
   элемент переносится внутри себя, а не уезжает под горизонтальный скролл. */
.listik-route-card__command {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  background: var(--surface-2);
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.listik-route-card__command-line {
  display: block;
}

.listik-route-card__placeholder {
  color: var(--accent-600);
  font-weight: var(--weight-medium);
}

.listik-route-card__placeholder--unknown {
  color: var(--danger-600);
  text-decoration: underline wavy;
}

.listik-route-card__unknown {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--danger-600);
}
</style>
