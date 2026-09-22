<script setup lang="ts">
/**
 * Помощник DeepSeek у поля формы «Новая задача»: полупрозрачная кнопка поверх поля
 * появляется при наведении на поле (и по фокусу внутри), открывает поповер с
 * разбором — переписанный текст, дописанные критерии приёмки, оценка когнитивной
 * сложности и предложенный маршрут.
 *
 * Ничего не применяется само: у текста, критериев и маршрута свои кнопки
 * подтверждения, а меняет форму родитель (`NewTaskModal`) по событиям. Запрос
 * уходит в `store.askAssistant` → `POST /api/assistant/suggest`; ключ DeepSeek
 * читает сервер Listik, в браузер он не попадает. Если сервер сказал
 * `enabled=false` (в `[assistant]` нет `api_key`), кнопки нет вовсе; ошибку
 * запроса панель показывает понятным текстом и не роняет доску.
 *
 * Применение не закрывает поповер: применённая секция скрывается целиком (до
 * следующего запроса — `ask`), а видимые соседи остаются на месте — можно
 * применить и текст, и критерии, и маршрут за один заход.
 */
import { computed, ref, watch } from 'vue'
import { UiAlert, UiBadge, UiButton, UiPopover, UiSpinner } from '@zoloto585/facet'
import ListikIcon from '@/components/ListikIcon.vue'
import type {
  AssistantComplexityLevel,
  AssistantContext,
  AssistantField,
  AssistantSuggestion,
} from '@/api/types'
import { complexity as complexityItem } from '@/lib/dictionaries'
import { routeAllowedForType } from '@/lib/routes'
import store, { errorMessage } from '@/store/listik'

const props = defineProps<{
  /** Какое поле обслуживаем — сервер принимает только четыре ключа. */
  field: AssistantField
  /** Подпись поля для доступного имени кнопки. */
  label: string
  /** Текущий текст поля: уходит в DeepSeek как есть и сравнивается с ответом. */
  text: string
  /** Срез карточки: тип, приоритет, проект и остальные поля формы. */
  context: AssistantContext
  /** Ключ уже выбранного маршрута — предложенный повторно не применяется. */
  selectedRouteKey?: string | null
}>()

const emit = defineEmits<{
  /** Применить переписанный текст к своему полю. */
  applyText: [text: string]
  /** Дописать критерии в поле «Критерии приёмки». */
  applyAcceptance: [criteria: string[]]
  /** Выбрать предложенный маршрут; доступность проверяет родитель. */
  applyRoute: [key: string]
}>()

const open = ref(false)
const loading = ref(false)
const error = ref<string | null>(null)
const suggestion = ref<AssistantSuggestion | null>(null)

/** Что уже применили: секция прячется целиком и живёт так до следующего запроса. */
interface AppliedMarks {
  text: boolean
  acceptance: boolean
  route: boolean
}

const applied = ref<AppliedMarks>({ text: false, acceptance: false, route: false })

/** Сброс отметок — в начале `ask()`: и новое открытие, и «Повторить» показывают ответ целиком. */
function resetApplied(): void {
  applied.value = { text: false, acceptance: false, route: false }
}

/** Пока сервер не подтвердил `enabled`, кнопки нет — как и без ключа в конфиге. */
const enabled = computed(() => store.assistantEnabled.value)
const modelTitle = computed(() => store.assistantModel.value || 'DeepSeek')

const complexity = computed(() => {
  const level: AssistantComplexityLevel | undefined = suggestion.value?.complexity?.level
  return level ? complexityItem(level) : null
})

/** Текст ответа отличается от текущего — иначе кнопка «применить» бессмысленна. */
const textChanged = computed(() => {
  const next = suggestion.value?.text.trim() ?? ''
  return Boolean(next) && next !== props.text.trim()
})

/** Запись `routes.json`, которую предложил DeepSeek: только видимая и знакомая доске. */
const suggestedRoute = computed(() => {
  const key = suggestion.value?.route?.key
  if (!key) return null
  return store.routes.value.find((route) => route.visible && route.key === key) ?? null
})

const routeSelected = computed(
  () => Boolean(suggestion.value?.route?.key) && suggestion.value?.route?.key === props.selectedRouteKey,
)

/** Почему предложенный маршрут нельзя выбрать: нет в файле или закрыт для эпика. */
const routeBlockedReason = computed(() => {
  const route = suggestion.value?.route
  if (!route) return null
  const record = suggestedRoute.value
  if (!record) return 'этого маршрута нет среди видимых записей routes.json'
  const type = props.context.type ?? 'task'
  const allowed = routeAllowedForType(record, type)
  if (!allowed) return 'эпик идёт только через пресеты с этапом ТЗ — этот маршрут ему недоступен'
  return null
})

// Секции, которые уже применили, не показываем — иначе «Маршрут» воскрес бы
// веткой «Уже выбран», а текст — кнопкой «Текст уже такой».

/** «Переписанный текст»: пустого предложения не рисуем, применённого — тоже. */
const textBlockShown = computed(() => Boolean(suggestion.value?.text) && !applied.value.text)

/** «Дописать в приёмку»: у самого поля приёмки секции нет, пустой список её не рисует. */
const acceptanceBlockShown = computed(
  () =>
    props.field !== 'acceptance' &&
    Boolean(suggestion.value?.acceptance.length) &&
    !applied.value.acceptance,
)

/** «Маршрут»: применённая секция скрыта целиком — вместе с обеими ветками. */
const routeBlockShown = computed(() => !applied.value.route)

/** Кнопка применения есть только у ветки с предложенным маршрутом. */
const routeButtonShown = computed(() => routeBlockShown.value && Boolean(suggestion.value?.route))

/** Видимых секций с кнопкой применения не осталось — все предложения применены. */
const allApplied = computed(
  () =>
    Boolean(suggestion.value) &&
    !textBlockShown.value &&
    !acceptanceBlockShown.value &&
    !routeButtonShown.value,
)

async function ask(): Promise<void> {
  loading.value = true
  error.value = null
  suggestion.value = null
  resetApplied()
  try {
    const response = await store.askAssistant({
      field: props.field,
      text: props.text,
      context: props.context,
    })
    suggestion.value = response.suggestion
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

// Каждое открытие — новый запрос: текст поля мог измениться, а кеш предложения
// «протух» бы молча.
watch(open, (isOpen) => {
  if (isOpen) void ask()
})

function applyText(): void {
  if (!suggestion.value) return
  emit('applyText', suggestion.value.text)
  applied.value.text = true
}

function applyAcceptance(): void {
  if (!suggestion.value?.acceptance.length) return
  emit('applyAcceptance', suggestion.value.acceptance)
  applied.value.acceptance = true
}

function applyRoute(): void {
  const key = suggestion.value?.route?.key
  if (!key || routeBlockedReason.value || routeSelected.value) return
  emit('applyRoute', key)
  applied.value.route = true
}
</script>

<template>
  <!-- Оборачивает контрол целиком: кнопка лежит поверх поля, панель — в поповере кита. -->
  <div v-if="enabled" class="listik-assist">
    <slot />
    <span class="listik-assist__anchor">
      <UiPopover v-model="open" placement="bottom" :label="`Помощник: ${label}`">
        <template #trigger>
          <UiButton
            class="listik-assist__trigger"
            variant="ghost"
            size="sm"
            :ariaLabel="`Помощник: ${label}`"
          >
            <template #icon><ListikIcon name="magic" size="sm" /></template>
          </UiButton>
        </template>

        <div class="listik-assist__panel">
          <div class="listik-assist__head">
            <span class="listik-assist__head-title">
              <ListikIcon name="magic" size="sm" />
              Помощник · <span class="listik-mono">{{ modelTitle }}</span>
            </span>
            <UiBadge v-if="complexity" :tone="complexity.tone" size="sm">
              сложность: {{ complexity.label }}
            </UiBadge>
          </div>

          <div v-if="loading" class="listik-assist__state">
            <UiSpinner size="sm" label="Спрашиваю DeepSeek" />
            <span class="listik-section__hint">DeepSeek думает над полем «{{ label }}»…</span>
          </div>

          <UiAlert v-else-if="error" tone="danger">
            {{ error }}
            <div class="listik-row" style="margin-top: var(--space-2)">
              <UiButton size="sm" variant="secondary" @click="ask">Повторить</UiButton>
            </div>
          </UiAlert>

          <template v-else-if="suggestion">
            <p v-if="suggestion.complexity?.reason" class="listik-section__hint">
              {{ suggestion.complexity.reason }}
            </p>

            <section v-if="textBlockShown" class="listik-assist__block">
              <span class="listik-assist__block-title">Переписанный текст</span>
              <p class="listik-assist__text">{{ suggestion.text }}</p>
              <UiButton size="sm" variant="secondary" :disabled="!textChanged" @click="applyText">
                {{ textChanged ? 'Применить текст' : 'Текст уже такой' }}
              </UiButton>
            </section>

            <!-- Для самого поля «приёмка» переписанный текст уже содержит критерии:
                 отдельный список «дописать» здесь только путал бы. -->
            <section
              v-if="acceptanceBlockShown"
              class="listik-assist__block"
            >
              <span class="listik-assist__block-title">Дописать в приёмку</span>
              <ul class="listik-assist__list">
                <li v-for="item in suggestion.acceptance" :key="item">{{ item }}</li>
              </ul>
              <UiButton size="sm" variant="secondary" @click="applyAcceptance">
                Добавить критерии ({{ suggestion.acceptance.length }})
              </UiButton>
            </section>

            <section v-if="routeBlockShown" class="listik-assist__block">
              <span class="listik-assist__block-title">Маршрут</span>
              <template v-if="suggestion.route">
                <div class="listik-row">
                  <span class="listik-assist__route">
                    {{ suggestion.route.title || suggestion.route.key }}
                  </span>
                  <UiBadge tone="neutral" size="sm">{{ suggestion.route.key }}</UiBadge>
                </div>
                <p v-if="suggestion.route.reason" class="listik-section__hint">
                  {{ suggestion.route.reason }}
                </p>
                <p v-if="routeBlockedReason" class="listik-section__hint">{{ routeBlockedReason }}</p>
                <UiButton
                  size="sm"
                  variant="secondary"
                  :disabled="Boolean(routeBlockedReason) || routeSelected"
                  @click="applyRoute"
                >
                  {{ routeSelected ? 'Уже выбран' : 'Выбрать маршрут' }}
                </UiButton>
              </template>
              <span v-else class="listik-section__hint">DeepSeek не выбрал маршрут</span>
            </section>

            <p v-if="allApplied" class="listik-section__hint">Все предложения применены.</p>

            <p class="listik-assist__footnote">Ничего не меняется без подтверждения.</p>
          </template>
        </div>
      </UiPopover>
    </span>
  </div>
  <slot v-else />
</template>
