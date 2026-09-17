<script setup lang="ts">
/**
 * RouteRolesEditor — редактор состава ролей маршрута `kind=pipeline` во вкладке
 * «Маршруты» настроек (внутри `RouteCard.vue`). До listik-syu8 состав был только
 * для чтения и менялся ввозом файла; теперь расклад — данные маршрута, и смена
 * исполнителя роли здесь меняет то, кем задача реально делается.
 *
 * Сохранение — явной кнопкой, а не автосейвом как у шапки карточки: расклад уходит
 * на сервер целиком, и половина отправленного состава хуже, чем несохранённая.
 * Кнопка активна, только когда черновик отличается от серверного значения, в нём
 * есть хотя бы одна роль и параметры всех ролей разобрались.
 *
 * Параметры роли вводятся построчно (`ключ=значение`) — форма, которую можно
 * напечатать, не собирая мышью таблицу: ключ проверяется тем же правилом, что на
 * сервере (`PARAM_KEY_RE`), значение приводится к boolean/number/строке.
 *
 * `:key="route.key"` у вызывающей стороны — часть контракта (как и у `RouteCard`):
 * при выборе другого маршрута компонент пересоздаётся со свежим черновиком.
 */
import { computed, onMounted, reactive, ref } from 'vue'
import {
  UiAlert,
  UiButton,
  UiField,
  UiInput,
  UiSaveStatus,
  UiSelect,
  UiSwitch,
  UiTextarea,
  type SaveStatusValue,
} from '@zoloto585/facet'
import ProviderIcon from './marks/ProviderIcon.vue'
import store from '@/store/listik'
import type { PipelineRouteDef } from '@/api/types'
import {
  PARAM_KEY_RE,
  PROVIDER_KEYS,
  ROLE_KEYS,
  ROLE_TITLES,
  type ProviderKey,
  type RoleCell,
  type RoleKey,
  type RoleParamValue,
} from '@/lib/pipelines'

const props = defineProps<{ route: PipelineRouteDef }>()

/** Строка роли в черновике: `on` — роль есть в маршруте. */
interface RoleDraft {
  on: boolean
  provider: ProviderKey
  label: string
  title: string
  skill: string
  params: string
  /** вендор меняли руками — выбор запускателя его больше не перетирает */
  providerTouched: boolean
}

/** `{"channel": "glm", "web": false}` → «channel=glm\nweb=false». */
function paramsToText(params: Record<string, RoleParamValue> | undefined): string {
  if (!params) return ''
  return Object.entries(params)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join('\n')
}

function draftOfRole(cell: RoleCell | undefined): RoleDraft {
  return {
    on: Boolean(cell),
    provider: cell?.provider ?? 'claude',
    label: cell?.label ?? '',
    title: cell?.title ?? '',
    skill: cell?.skill ?? '',
    params: paramsToText(cell?.params),
    providerTouched: false,
  }
}

function draftOfRoute(route: PipelineRouteDef): Record<RoleKey, RoleDraft> {
  const out = {} as Record<RoleKey, RoleDraft>
  for (const role of ROLE_KEYS) out[role] = draftOfRole(route.roles[role])
  return out
}

const draft = reactive<Record<RoleKey, RoleDraft>>(draftOfRoute(props.route))
/** Последнее известное серверу состояние — для «есть ли что сохранять». */
const baseline = ref(JSON.stringify(props.route.roles ?? {}))

const status = ref<SaveStatusValue>('idle')
const saveError = ref<string | null>(null)

onMounted(() => {
  void store.loadRouteLaunchers()
})

/* ── справочник запускаторов ── */

const launchers = computed(() => store.routeLaunchers.value?.launchers ?? [])
const catalogueReady = computed(() => store.routeLaunchers.value !== null)
const skillsAvailable = computed(() => store.routeLaunchers.value?.skills_available === true)
/** Селект запускателя выключен, пока справочника нет или запускаторов у установки нет. */
const skillSelectDisabled = computed(() => !catalogueReady.value || !skillsAvailable.value)

const skillOptions = computed(() => [
  { value: '', label: 'без запускателя' },
  ...launchers.value.map((item) => ({
    value: item.key,
    label: item.hint ? `${item.key} — ${item.hint}` : item.key,
  })),
])

const providerOptions = computed(() =>
  (store.routeLaunchers.value?.providers ?? PROVIDER_KEYS).map((value) => ({
    value,
    label: value,
  })),
)

/* ── разбор параметров ── */

/** `true`/`false` → boolean, целое или дробное число → number, остальное — строка. */
function paramValue(raw: string): RoleParamValue {
  if (raw === 'true') return true
  if (raw === 'false') return false
  if (raw !== '' && Number.isFinite(Number(raw))) return Number(raw)
  return raw
}

interface ParsedParams {
  params: Record<string, RoleParamValue>
  error: string | null
}

function parseParams(text: string): ParsedParams {
  const params: Record<string, RoleParamValue> = {}
  for (const line of text.split('\n')) {
    const trimmed = line.trim()
    if (trimmed === '') continue
    const eq = trimmed.indexOf('=')
    if (eq <= 0) {
      return { params, error: `строка «${trimmed}»: нужен вид ключ=значение` }
    }
    const key = trimmed.slice(0, eq).trim()
    if (!PARAM_KEY_RE.test(key)) {
      return {
        params,
        error: `ключ «${key}»: строчные латинские буквы, цифры и подчёркивание, начиная с буквы`,
      }
    }
    params[key] = paramValue(trimmed.slice(eq + 1).trim())
  }
  return { params, error: null }
}

const paramsErrors = computed<Partial<Record<RoleKey, string>>>(() => {
  const out: Partial<Record<RoleKey, string>> = {}
  for (const role of ROLE_KEYS) {
    const cell = draft[role]
    if (!cell.on || cell.skill === '') continue
    const parsed = parseParams(cell.params)
    if (parsed.error) out[role] = parsed.error
  }
  return out
})

const hasParamsError = computed(() => Object.keys(paramsErrors.value).length > 0)

/* ── сборка расклада и состояние кнопки ── */

const rolesPayload = computed<Partial<Record<RoleKey, RoleCell>>>(() => {
  const out: Partial<Record<RoleKey, RoleCell>> = {}
  for (const role of ROLE_KEYS) {
    const cell = draft[role]
    if (!cell.on) continue
    const value: RoleCell = { provider: cell.provider, label: cell.label, title: cell.title }
    if (cell.skill !== '') {
      value.skill = cell.skill
      const parsed = parseParams(cell.params)
      if (Object.keys(parsed.params).length > 0) value.params = parsed.params
    }
    out[role] = value
  }
  return out
})

const enabledCount = computed(() => Object.keys(rolesPayload.value).length)
const changed = computed(() => JSON.stringify(rolesPayload.value) !== baseline.value)

const saveHint = computed(() => {
  if (hasParamsError.value) return 'поправь параметры роли'
  if (enabledCount.value === 0) return 'нужна хотя бы одна роль'
  if (!changed.value) return 'расклад совпадает с сохранённым'
  return null
})
const canSave = computed(() => saveHint.value === null)

/* ── ввод ── */

function onSkill(role: RoleKey, value: string | null): void {
  const cell = draft[role]
  cell.skill = value ?? ''
  if (cell.skill === '' || cell.providerTouched) return
  const info = launchers.value.find((item) => item.key === cell.skill)
  if (info?.provider) cell.provider = info.provider
}

function onProvider(role: RoleKey, value: string | null): void {
  if (value === null) return
  draft[role].provider = value as ProviderKey
  draft[role].providerTouched = true
}

function onToggle(role: RoleKey, value: boolean): void {
  draft[role].on = value
  if (!value) draft[role].providerTouched = false
}

async function save(): Promise<void> {
  if (!canSave.value) return
  status.value = 'saving'
  const result = await store.patchRoute(props.route.key, { roles: rolesPayload.value })
  if (result && result.kind === 'pipeline') {
    baseline.value = JSON.stringify(result.roles ?? {})
    for (const role of ROLE_KEYS) draft[role].providerTouched = false
    saveError.value = null
    status.value = 'saved'
  } else {
    saveError.value = store.routesSettingsError.value
    status.value = 'error'
  }
}
</script>

<template>
  <div class="listik-roles-editor">
    <UiAlert v-if="catalogueReady && !skillsAvailable" tone="info">
      <template #title>Запускаторов нет</template>
      у этой установки нет скилов-запускаторов — роль можно описать без запускателя.
    </UiAlert>
    <UiAlert v-else-if="!catalogueReady" tone="info">
      справочник запускаторов недоступен — роль можно описать без запускателя.
    </UiAlert>

    <div v-for="role in ROLE_KEYS" :key="role" class="listik-roles-editor__role">
      <div class="listik-roles-editor__role-head">
        <span class="listik-roles-editor__role-name">{{ ROLE_TITLES[role] }}</span>
        <ProviderIcon v-if="draft[role].on" :provider="draft[role].provider" size="sm" />
        <UiSwitch
          :model-value="draft[role].on"
          @update:model-value="(value: boolean) => onToggle(role, value)"
        />
      </div>

      <div v-if="draft[role].on" class="listik-roles-editor__role-body">
        <UiField label="Запускатор">
          <UiSelect
            :model-value="draft[role].skill"
            :options="skillOptions"
            :disabled="skillSelectDisabled"
            :ariaLabel="`Запускатор роли ${ROLE_TITLES[role]}`"
            @update:model-value="(value: string | null) => onSkill(role, value)"
          />
        </UiField>
        <UiField label="Вендор">
          <UiSelect
            :model-value="draft[role].provider"
            :options="providerOptions"
            :ariaLabel="`Вендор роли ${ROLE_TITLES[role]}`"
            @update:model-value="(value: string | null) => onProvider(role, value)"
          />
        </UiField>
        <UiField label="Подпись">
          <UiInput
            :model-value="draft[role].label"
            @update:model-value="(value: string) => (draft[role].label = value)"
          />
        </UiField>
        <UiField label="Расшифровка">
          <UiInput
            :model-value="draft[role].title"
            @update:model-value="(value: string) => (draft[role].title = value)"
          />
        </UiField>
        <UiField
          v-if="draft[role].skill !== ''"
          label="Параметры (ключ=значение построчно)"
          :error="paramsErrors[role]"
        >
          <UiTextarea
            :model-value="draft[role].params"
            :rows="2"
            placeholder="channel=glm"
            :invalid="Boolean(paramsErrors[role])"
            @update:model-value="(value: string) => (draft[role].params = value)"
          />
        </UiField>
      </div>
    </div>

    <UiAlert v-if="status === 'error' && saveError" tone="danger">
      <template #title>Расклад не сохранён</template>
      {{ saveError }}
    </UiAlert>

    <div class="listik-roles-editor__actions">
      <UiButton :disabled="!canSave" @click="save">Сохранить расклад</UiButton>
      <span v-if="saveHint" class="listik-roles-editor__hint">{{ saveHint }}</span>
      <UiSaveStatus :status="status" @retry="save" />
    </div>
  </div>
</template>

<style scoped>
.listik-roles-editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}

.listik-roles-editor__role {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--color-border-subtle, rgba(0, 0, 0, 0.08));
}

.listik-roles-editor__role-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.listik-roles-editor__role-name {
  flex: 1 1 auto;
  font-weight: 600;
}

.listik-roles-editor__role-body {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-2);
}

.listik-roles-editor__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.listik-roles-editor__hint {
  color: var(--color-text-muted, #6b7280);
  font-size: var(--font-size-sm, 0.875rem);
}
</style>
