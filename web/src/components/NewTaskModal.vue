<script setup lang="ts">
/**
 * Окно «Новая задача» (референс: `docs/prototype/NewTask-html/NewTask.dc.html`,
 * `.modal`). Поля Тип/Приоритет/Проект/Заголовок/Описание·ТЗ/Критерии
 * приёмки/`spec_path` — как в форме прототипа; вместо блока `.est`/`.scale`
 * прототипа оценку и маршрут предлагает помощник DeepSeek — полупрозрачной
 * кнопкой у каждого текстового поля (`AssistantField`), и применяется только по
 * подтверждению. Два отличия от прототипа: порядок первого ряда
 * Тип → Приоритет → Проект (в прототипе Проект второй) и подсказка приоритета
 * тултипом UiTooltip на контроле, а не строкой под ним. Блок «Маршрут» —
 * общий `RoutePicker` (кит такого контрола не знает): записи (пресеты
 * конвейера и прямые харнессы) отдаёт сервер — `GET /api/routes`, файл
 * `routes.json`; грузятся один раз за сессию доски, логика выбора —
 * `lib/routes.ts`. Выбранный ключ уходит на сервер полем `route`; метки
 * `harness:<x>`/`process:<y>` сервер ставит по нему сам (`routes.labels_for`)
 * — доска их не считает, они для человека и поиска.
 */
import { computed, reactive, ref, watch } from 'vue'
import {
  UiAlert,
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
import AssistantField from '@/components/AssistantField.vue'
import ProjectMark from '@/components/marks/ProjectMark.vue'
import RoutePicker from '@/components/RoutePicker.vue'
import TaskGlyph from '@/components/marks/TaskGlyph.vue'
import { TASK_TYPES, priority } from '@/lib/dictionaries'
import type {
  AssistantContext,
  AssistantField as AssistantFieldKey,
  ProjectRow,
  RouteDef,
  VoiceDraft,
} from '@/api/types'
import {
  defaultPipelineFor,
  routeAllowedForType,
  routesAlertText,
  visibleRoutesOf,
} from '@/lib/routes'
import { projectBySlug } from '@/lib/projects'
import store from '@/store/listik'

const props = defineProps<{
  projects: ProjectRow[]
  pending: boolean
  /**
   * Черновик голосового ввода (порция b): предзаполняет форму в момент открытия
   * окна. `null`/не передан — поведение формы не меняется ни в чём.
   */
  draft?: VoiceDraft | null
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
      /** Ключ маршрута из routes.json; нет — задача создаётся без маршрута. */
      route?: string
      /**
       * Владелец задачи (серверный режим): ключ есть, только если он отличается
       * от представившегося — иначе владельца проставит сервер по заголовку.
       */
      owner?: string
      autostart: boolean
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
    /** Ключ выбранной записи `routes.json`; null — маршрут не выбран. */
    routeKey: null as string | null,
    /** Владелец задачи в серверном режиме; по умолчанию — тот, кто представился. */
    owner: store.owner.value,
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
  applyDefaultRoute()
}

watch(isOpen, (open) => {
  if (!open) {
    resetForm()
    return
  }
  // Открытие начинается с умолчаний: поверх них ложится черновик голосового ввода.
  resetForm()
  // Имя в шапке могли сменить, пока форма была закрыта — подставляем текущее
  // (после resetForm, иначе умолчания затрут подстановку).
  form.owner = store.owner.value
  applyDraft(props.draft)
  // Первое открытие формы — единственный запрос маршрутов за сессию доски.
  store.ensureRoutes()
  // И единственный запрос статуса помощника: без ключа кнопок у полей не будет.
  store.ensureAssistant()
  if (!routeTouched.value) applyDefaultRoute()
})

const projectOptions = computed<UiSelectOption[]>(() =>
  props.projects.map((project) => ({ value: project.slug, label: project.title || project.slug })),
)

const selectedProject = computed(() => projectBySlug(props.projects, form.project))

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
      applyDefaultRoute()
      return
    }
    // Выбранный вручную маршрут мог стать недоступным (эпику нужен этап ТЗ) —
    // тогда возвращаемся к умолчанию по типу.
    if (!selectedRoute.value) {
      routeTouched.value = false
      applyDefaultRoute()
    }
  },
)

const titleError = computed(() => (submitted.value && !form.title.trim() ? 'нужен заголовок' : null))

/**
 * Владелец задачи — только серверный режим: сервер ставит его сам по заголовку
 * «я — …», поэтому пункта «без владельца» тут нет. Никто не представился и
 * владелец не выбран — сервер откажет (400), не отправляем.
 */
const ownerOptions = computed<UiSelectOption[]>(() =>
  store.users.value.map((user) => ({ value: user, label: user })),
)

const ownerMissing = computed(() => store.isServerMode.value && !form.owner && !store.owner.value)

const ownerError = computed(() =>
  submitted.value && ownerMissing.value ? 'В серверном режиме у задачи должен быть владелец' : null,
)

const createDisabled = computed(() => props.pending || !form.title.trim() || !form.project)

// ── помощник DeepSeek у полей: кнопка на поле, применение — только по подтверждению ──

/** Контекст карточки для помощника: то, что уже введено в форму, без пустых полей. */
const assistantContext = computed<AssistantContext>(() => ({
  type: form.type,
  priority: Number(form.priority),
  project: form.project ?? undefined,
  title: form.title.trim() || undefined,
  description: form.description.trim() || undefined,
  acceptance: form.acceptance.trim() || undefined,
  spec_path: form.specPath.trim() || undefined,
}))

/** Переписанный текст прилетает в своё поле — какое спросили, то и меняем. */
function applyAssistantText(field: AssistantFieldKey, text: string): void {
  if (field === 'title') form.title = text
  else if (field === 'description') form.description = text
  else if (field === 'acceptance') form.acceptance = text
  else form.specPath = text
}

/** Дописать критерии приёмки к уже написанному, по одному в строке. */
function appendAcceptance(criteria: string[]): void {
  const clean = criteria.map((item) => item.trim()).filter(Boolean)
  if (!clean.length) return
  const body = clean.join('\n')
  form.acceptance = form.acceptance.trim() ? `${form.acceptance.trimEnd()}\n${body}` : body
}

/** Маршрут от помощника проходит те же правила, что и клик по матрице. */
function applyAssistantRoute(key: string): void {
  onPickRoute(key)
}

// ── маршрут: записи из routes.json (`GET /api/routes`), правила — lib/routes.ts ──

/** Ошибка файла (`ok:false`) или самого запроса — таблицу и ряд «Отдельно» не рисуем. */
const routesFailed = computed(() => store.routesRequestFailed.value || !store.routesOk.value)

const visibleRoutes = computed<RouteDef[]>(() =>
  routesFailed.value ? [] : visibleRoutesOf(store.routes.value),
)

function routeAllowed(route: RouteDef): boolean {
  return routeAllowedForType(route, form.type)
}

/** Выбранная запись: видимая и разрешённая текущему типу. */
const selectedRoute = computed<RouteDef | null>(() => {
  const route = visibleRoutes.value.find((item) => item.key === form.routeKey)
  return route && routeAllowed(route) ? route : null
})

function applyDefaultRoute(): void {
  form.routeKey = defaultPipelineFor(form.type, visibleRoutes.value)?.key ?? null
}

// Маршруты приехали после первого открытия формы — подставляем умолчание по типу.
watch(
  () => store.routes.value,
  () => {
    if (!routeTouched.value) applyDefaultRoute()
  },
)

/**
 * Черновик голосового ввода поверх умолчаний: пустые/нераспознанные поля не
 * трогаются, чтобы форма честно потребовала их выбрать. Тип — только из
 * `epic|task|bug`; проект — только если такой slug есть на доске; маршрут —
 * только видимый и допустимый итоговому типу (применённый помечается
 * `routeTouched`, иначе смена типа в форме затрёт его умолчанием).
 */
function applyDraft(draft: VoiceDraft | null | undefined): void {
  if (!draft) return
  if (draft.title) form.title = draft.title
  if (draft.description) form.description = draft.description
  if (draft.acceptance?.length) form.acceptance = draft.acceptance.join('\n')
  if (draft.type === 'epic' || draft.type === 'task' || draft.type === 'bug') form.type = draft.type
  if (draft.project && props.projects.some((project) => project.slug === draft.project)) {
    form.project = draft.project
  }
  const key = draft.route?.key
  if (!key) return
  const route = visibleRoutes.value.find((item) => item.key === key)
  if (route && routeAllowedForType(route, form.type)) {
    form.routeKey = route.key
    routeTouched.value = true
  }
}

function selectRoute(route: RouteDef): void {
  if (!routeAllowed(route)) return
  routeTouched.value = true
  form.routeKey = route.key
}

/** Клик по матрице — тот же путь, что и маршрут от помощника. */
function onPickRoute(key: string): void {
  const route = visibleRoutes.value.find((item) => item.key === key)
  if (route) selectRoute(route)
}

const routesAlert = computed(() => routesAlertText(store.routesRequestFailed.value, store.routesError.value))

/**
 * Замечания проверки файла (`warnings` ответа) — неизвестный `icon` записи.
 * Маршруты при этом работают: у записи посчитан фолбэк по ключу. Это не ошибка,
 * а предупреждение автору `routes.json`; запись без фолбэка доска помечает
 * серым кружком с крестиком (`RouteIcon`).
 */
const routesWarnings = computed(() => store.routesWarnings.value)

/** Кнопка «повторить» — ровно один запрос, кеш до этого не трогаем. */
function retryRoutes(): void {
  void store.loadRoutes()
}

// ── отправка ──────────────────────────────────────────────────────────────

function submit(): void {
  submitted.value = true
  if (createDisabled.value || ownerMissing.value) return
  const route = selectedRoute.value
  // Совпал с представившимся — ключа в теле нет: владельца проставит сервер по
  // заголовку. Отличается — это «завести на другого».
  const owner = store.isServerMode.value && form.owner && form.owner !== store.owner.value ? form.owner : ''
  emit('submit', {
    title: form.title.trim(),
    project: form.project as string,
    type: form.type,
    priority: Number(form.priority),
    description: form.description,
    acceptance: form.acceptance,
    ...(form.specPath.trim() ? { spec_path: form.specPath.trim() } : {}),
    // Метки маршрута ставит сервер (`routes.labels_for`); без маршрута ключа нет.
    // Процесс поднимает рой, не галочка на этой форме.
    ...(route ? { route: route.key } : {}),
    ...(owner ? { owner } : {}),
    autostart: false,
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
        <h2 class="listik-drawer__title">Новая задача</h2>
        <span class="listik-section__hint">что делаем, в каком проекте и как: кто исполняет и по какому процессу</span>
      </div>
    </template>

    <div class="listik-stack listik-newtask">
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

      <UiField v-if="store.isServerMode.value" label="Владелец" required :error="ownerError">
        <UiSelect
          :model-value="form.owner"
          :options="ownerOptions"
          @update:model-value="form.owner = $event ?? ''"
          placeholder="кому принадлежит задача"
          :invalid="Boolean(ownerError)"
        />
      </UiField>

      <UiField label="Заголовок" required :error="titleError">
        <AssistantField
          field="title"
          label="Заголовок"
          :text="form.title"
          :context="assistantContext"
          :selected-route-key="form.routeKey"
          @apply-text="(text) => applyAssistantText('title', text)"
          @apply-acceptance="appendAcceptance"
          @apply-route="applyAssistantRoute"
        >
          <UiInput v-model="form.title" placeholder="тема задачи" :error="titleError" />
        </AssistantField>
      </UiField>

      <UiField
        label="Описание · ТЗ"
        hint="markdown · эпик режется на шаги и порции на этапе s1, здесь только суть"
      >
        <AssistantField
          field="description"
          label="Описание · ТЗ"
          :text="form.description"
          :context="assistantContext"
          :selected-route-key="form.routeKey"
          @apply-text="(text) => applyAssistantText('description', text)"
          @apply-acceptance="appendAcceptance"
          @apply-route="applyAssistantRoute"
        >
          <UiTextarea v-model="form.description" autosize :rows="5" placeholder="что делаем и зачем" />
        </AssistantField>
      </UiField>

      <UiField label="Критерии приёмки">
        <AssistantField
          field="acceptance"
          label="Критерии приёмки"
          :text="form.acceptance"
          :context="assistantContext"
          :selected-route-key="form.routeKey"
          @apply-text="(text) => applyAssistantText('acceptance', text)"
          @apply-acceptance="appendAcceptance"
          @apply-route="applyAssistantRoute"
        >
          <UiTextarea v-model="form.acceptance" autosize :rows="3" placeholder="как проверить, что готово" />
        </AssistantField>
      </UiField>

      <UiField
        label="Путь к ТЗ (spec_path)"
        hint="относительно корня репозитория проекта; можно заполнить позже"
      >
        <AssistantField
          field="spec_path"
          label="Путь к ТЗ"
          :text="form.specPath"
          :context="assistantContext"
          :selected-route-key="form.routeKey"
          @apply-text="(text) => applyAssistantText('spec_path', text)"
          @apply-acceptance="appendAcceptance"
          @apply-route="applyAssistantRoute"
        >
          <UiInput v-model="form.specPath" placeholder="docs/specs/….md" />
        </AssistantField>
      </UiField>

      <section class="listik-stack" style="gap: var(--space-2)">
        <h4 class="listik-section__title">Как делать</h4>
        <p class="listik-newtask__route-caption">
          Маршрут · кто исполняет и по какому процессу
        </p>

        <template v-if="routesFailed">
          <UiAlert tone="warning">
            {{ routesAlert }}
            <div class="listik-row" style="margin-top: var(--space-3)">
              <UiButton size="sm" variant="secondary" :loading="store.routesLoading.value" @click="retryRoutes">
                Повторить
              </UiButton>
            </div>
          </UiAlert>
        </template>

        <template v-else>
          <UiAlert v-if="routesWarnings.length" tone="warning">
            <template #title>routes.json: предупреждения</template>
            <div v-for="warning in routesWarnings" :key="warning" class="listik-mono">
              {{ warning }}
            </div>
          </UiAlert>

          <RoutePicker
            :routes="store.routes.value"
            :selected-key="form.routeKey"
            :issue-type="form.type"
            @select="onPickRoute"
          />
        </template>

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

<style scoped>
/* Подпись блока маршрутов под «Как делать» — мелкий капс, как в макете. */
.listik-newtask__route-caption {
  margin: 0;
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-3);
}
</style>
