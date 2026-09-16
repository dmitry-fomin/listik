<script setup lang="ts">
/**
 * Голосовой ввод задачи (шаг listik-8hrq). Десктоп (порция b): кнопка «Голосом»
 * рядом с «Новая задача», панель записи на `UiPopover`. Телефон (порция c):
 * круглая FAB над очередью и лист `UiDrawer side="bottom"`. Режим — проп
 * `variant`; состояние, поток `transcribe → draft` и ветки ошибок общие.
 *
 * Поток: старт записи → стоп даёт `{blob, mime}` → base64 → `store.transcribeVoice`
 * (`POST /api/assistant/transcribe`) → пустой `transcript` — ветка «Ничего не
 * расслышал», непустой → `store.draftVoice` (`POST /api/assistant/draft`) →
 * черновик. Компонент сам в `fetch`/`api` не ходит — только через стор; ошибки
 * ловит и рисует в панели (`errorMessage`), в общий `handleError`/`lastError`
 * не отдаёт, чтобы отказ Deepgram/DeepSeek не выглядел как «сервер Listik
 * недоступен». Создание задачи и открытие формы — события наружу (App.vue),
 * потому что это то же создание, что у «Новой задачи».
 *
 * Кнопка рисуется только при `store.voiceEnabled` (статус тянется из
 * `ensureAssistant` один раз за сессию); пока флага нет — в DOM ничего.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { UiAlert, UiButton, UiDrawer, UiPopover, UiSpinner } from '@zoloto585/facet'
import ListikIcon from '@/components/ListikIcon.vue'
import ProjectMark from '@/components/marks/ProjectMark.vue'
import TaskGlyph from '@/components/marks/TaskGlyph.vue'
import type { VoiceDraft } from '@/api/types'
import { taskType } from '@/lib/dictionaries'
import { projectBySlug } from '@/lib/projects'
import { routeAllowedForType, visibleRoutesOf } from '@/lib/routes'
import {
  blobToBase64,
  startVoiceRecording,
  type VoiceRecording,
} from '@/lib/voice-recorder'
import store, { errorMessage } from '@/store/listik'

const props = withDefaults(
  defineProps<{
    /** id созданной голосом задачи для подтверждения панели (см. App.vue). */
    createdId?: string | null
    /** Ошибка создания — текстом под черновиком, доска общим алертом её не показывает. */
    createError?: string | null
    /** Вид: десктоп — кнопка + поповер, телефон — кружок + лист снизу. */
    variant?: 'desktop' | 'phone'
  }>(),
  { createdId: null, createError: null, variant: 'desktop' },
)

const emit = defineEmits<{
  /** «Создать задачу» из панели: тело создания, App.vue зовёт store.createTask. */
  create: [body: Record<string, unknown>]
  /** «Открыть форму»: черновик (или один текст расшифровки) для «Новой задачи». */
  openForm: [draft: VoiceDraft]
}>()

type Stage = 'idle' | 'recording' | 'transcribing' | 'drafting' | 'draft' | 'created' | 'error'
type ErrorBranch = 'mic' | 'silence' | 'transcribe' | 'draft'

/** Амплитудные множители столбиков волны — «живость» при общем уровне записи. */
const WAVE_BARS = [0.5, 0.85, 1, 0.7, 0.9]

/** Порог телефонного жеста: короче — короткое нажатие, дольше — удержание. */
const HOLD_MS = 350

const open = ref(false)
const stage = ref<Stage>('idle')
const branch = ref<ErrorBranch | null>(null)
const serverError = ref<string | null>(null)
const transcript = ref('')
const draft = ref<VoiceDraft | null>(null)
const level = ref(0)
const elapsed = ref(0)
const creating = ref(false)
const localCreatedId = ref<string>('')

/** Запись-обёртка; null — ещё не получена или уже остановлена. */
const recording = ref<VoiceRecording | null>(null)
/** Последнее аудио: «Повторить» в ветке расшифровки шлёт его же, без новой записи. */
let lastAudio: { blob: Blob; mime: string } | null = null
/** Номер запроса: «Отмена»/новый старт делают ответы «догоняющих» запросов неслышимыми. */
let requestSeq = 0
let tickTimer: ReturnType<typeof setInterval> | null = null
let rafId: number | null = null
let voiceKeyHeld = false
/**
 * «Закончить» пришло, пока микрофон ещё не выдан (диалог разрешения): пользователь
 * отпустил V/кликнул «Готово» раньше `getUserMedia`. Ставим флаг и заканчиваем
 * сразу, как только поток придёт, — иначе панель навсегда остаётся в «Слушаю».
 */
let finishRequested = false
/** Телефонный жест: момент `pointerdown` и признак «этот тап уже обработан». */
let pressStartedAt = 0
let pressHandled = false

const voiceEnabled = computed(() => store.voiceEnabled.value)
const isPhone = computed(() => props.variant === 'phone')
/** Кнопки панели: на телефоне крупнее и во всю ширину (`block`). */
const actionSize = computed<'sm' | 'md'>(() => (isPhone.value ? 'md' : 'sm'))
/** Пропсы обёртки панели: поповер на десктопе, лист снизу на телефоне. */
const wrapperProps = computed(() =>
  isPhone.value
    ? ({ side: 'bottom', title: 'Голосом' } as const)
    : ({ placement: 'bottom', label: 'Голосовой ввод задачи' } as const),
)

const timerText = computed(() => {
  const total = Math.max(0, Math.floor(elapsed.value / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = String(total % 60).padStart(2, '0')
  return `${minutes}:${seconds}`
})

const projectRow = computed(() => projectBySlug(store.meta.value?.projects ?? [], draft.value?.project ?? null))
const draftType = computed(() => (draft.value?.type ? taskType(draft.value.type) : null))
const acceptanceItems = computed(() => draft.value?.acceptance ?? [])
/** Slug известен доске: «первый попавшийся» проект не подставляем, чужой не создаём. */
const knownProject = computed(() => {
  const slug = draft.value?.project
  if (!slug) return false
  return (store.meta.value?.projects ?? []).some((item) => item.slug === slug)
})
const canCreate = computed(() => knownProject.value && Boolean(draft.value?.title))
/** Чего не хватает для создания одной кнопкой — «Открыть форму» доступна всегда. */
const createHint = computed(() => {
  if (!draft.value) return null
  if (!draft.value.project || !knownProject.value) return 'проект не распознан — откройте форму'
  if (!draft.value.title) return 'заголовок не распознан — откройте форму'
  return null
})

const BRANCH_TEXT: Record<ErrorBranch, { title: string; text: string }> = {
  mic: {
    title: 'Микрофон не разрешён',
    text: 'Браузер отказал в доступе. Разреши микрофон для этой вкладки и нажми ещё раз.',
  },
  silence: {
    title: 'Ничего не расслышал',
    text: 'Расшифровка пустая — тишина или слишком далеко от микрофона. Запись сохранена, можно наговорить заново.',
  },
  transcribe: {
    title: 'Расшифровка не удалась',
    text: 'Deepgram не ответил. Текст не потерян только если он уже пришёл — иначе запись придётся повторить.',
  },
  draft: {
    title: 'Черновик не собрался',
    text: 'Расшифровка есть, а DeepSeek не ответил. Открою форму с текстом в описании — заголовок допиши сам.',
  },
}

const branchInfo = computed(() => (branch.value ? BRANCH_TEXT[branch.value] : null))

/** Кнопки ветки: второстепенная и главная — ровно как в макете. */
const branchActions = computed<{
  secondary: { label: string; run: () => void }
  primary: { label: string; run: () => void }
} | null>(() => {
  switch (branch.value) {
    case 'mic':
      return {
        secondary: { label: 'Закрыть', run: resetToIdle },
        primary: { label: 'Повторить', run: () => void beginRecording() },
      }
    case 'silence':
      return {
        secondary: { label: 'Открыть форму', run: openForm },
        primary: { label: 'Записать заново', run: () => void beginRecording() },
      }
    case 'transcribe':
      return {
        secondary: { label: 'Отмена', run: resetToIdle },
        primary: { label: 'Повторить', run: () => void retryTranscribe() },
      }
    case 'draft':
      return {
        secondary: { label: 'Повторить', run: () => void retryDraft() },
        primary: { label: 'Открыть форму с текстом', run: openForm },
      }
    default:
      return null
  }
})

function stopMeters(): void {
  if (tickTimer) {
    clearInterval(tickTimer)
    tickTimer = null
  }
  if (rafId !== null) {
    cancelAnimationFrame(rafId)
    rafId = null
  }
  level.value = 0
}

function startMeters(startedAt: number): void {
  stopMeters()
  elapsed.value = 0
  tickTimer = setInterval(() => {
    elapsed.value = Date.now() - startedAt
  }, 200)
  const loop = (): void => {
    level.value = recording.value?.getLevel() ?? 0
    rafId = requestAnimationFrame(loop)
  }
  rafId = requestAnimationFrame(loop)
}

/** Амплитуда столбика: тихий минимум плюс уровень; ограничена единицей. */
function barTransform(index: number): string {
  const amplitude = 0.25 + level.value * 0.75 * (WAVE_BARS[index] ?? 0.6)
  return `scaleY(${Math.min(1, amplitude).toFixed(2)})`
}

function discardRecording(): void {
  const active = recording.value
  recording.value = null
  finishRequested = false
  stopMeters()
  active?.cancel()
}

function resetVoiceResult(): void {
  transcript.value = ''
  draft.value = null
  branch.value = null
  serverError.value = null
  creating.value = false
  localCreatedId.value = ''
}

/** В покой: бросить запись и незавершённый результат, панель закрыть. */
function resetToIdle(): void {
  requestSeq += 1
  discardRecording()
  resetVoiceResult()
  stage.value = 'idle'
  open.value = false
}

function closePanel(): void {
  resetToIdle()
}

async function beginRecording(): Promise<void> {
  requestSeq += 1
  const seq = requestSeq
  discardRecording()
  resetVoiceResult()
  lastAudio = null
  stage.value = 'recording'
  open.value = true
  startMeters(Date.now())
  try {
    const active = await startVoiceRecording()
    // За время запроса разрешения запись отменили/перезапустили — поток гасим.
    if (seq !== requestSeq || stage.value !== 'recording') {
      active.cancel()
      return
    }
    recording.value = active
    // Закончить просили ещё до выдачи потока — доводим запись до конца сразу.
    if (finishRequested) {
      finishRequested = false
      void finishRecording()
    }
  } catch (error) {
    if (seq !== requestSeq) return
    stopMeters()
    recording.value = null
    branch.value = 'mic'
    serverError.value = errorMessage(error)
    stage.value = 'error'
  }
}

async function finishRecording(): Promise<void> {
  const active = recording.value
  if (!active) {
    // Микрофон ещё не выдан (диалог разрешения) — дослушаем и закончим сразу после.
    if (stage.value === 'recording') finishRequested = true
    return
  }
  if (stage.value !== 'recording') return
  finishRequested = false
  recording.value = null
  stopMeters()
  let result: Awaited<ReturnType<VoiceRecording['stop']>>
  try {
    result = await active.stop()
  } catch (error) {
    branch.value = 'mic'
    serverError.value = errorMessage(error)
    stage.value = 'error'
    return
  }
  lastAudio = { blob: result.blob, mime: result.mime }
  await runTranscribe()
}

async function runTranscribe(): Promise<void> {
  if (!lastAudio) return
  const seq = (requestSeq += 1)
  stage.value = 'transcribing'
  open.value = true
  branch.value = null
  serverError.value = null
  transcript.value = ''
  try {
    const audioBase64 = await blobToBase64(lastAudio.blob)
    if (seq !== requestSeq) return
    const response = await store.transcribeVoice({ audio_base64: audioBase64, mime: lastAudio.mime })
    if (seq !== requestSeq) return
    transcript.value = response.transcript
  } catch (error) {
    if (seq !== requestSeq) return
    branch.value = 'transcribe'
    serverError.value = errorMessage(error)
    stage.value = 'error'
    return
  }
  if (!transcript.value.trim()) {
    branch.value = 'silence'
    serverError.value = null
    stage.value = 'error'
    return
  }
  await runDraft()
}

async function runDraft(): Promise<void> {
  const text = transcript.value
  if (!text.trim()) return
  const seq = (requestSeq += 1)
  stage.value = 'drafting'
  open.value = true
  branch.value = null
  serverError.value = null
  try {
    const response = await store.draftVoice({ text })
    if (seq !== requestSeq) return
    draft.value = response.draft
    stage.value = 'draft'
  } catch (error) {
    if (seq !== requestSeq) return
    branch.value = 'draft'
    serverError.value = errorMessage(error)
    stage.value = 'error'
  }
}

/** «Повторить» в ветке расшифровки: тот же blob, новой записи нет. */
async function retryTranscribe(): Promise<void> {
  await runTranscribe()
}

/** «Повторить» в ветке черновика: та же расшифровка. */
async function retryDraft(): Promise<void> {
  await runDraft()
}

/** Тело создания по черновику. `autostart` — литерал false: голос харнесс не запускает. */
function buildCreateBody(): Record<string, unknown> {
  const source = draft.value
  // Неизвестный доске slug — как пустой: одной кнопкой такую задачу не создаём.
  if (!source || !source.title || !knownProject.value) return {}
  const type = source.type === 'epic' || source.type === 'task' || source.type === 'bug' ? source.type : 'task'
  const body: Record<string, unknown> = {
    title: source.title,
    project: source.project,
    type,
    priority: 2,
    description: source.description ?? '',
    acceptance: (source.acceptance ?? []).join('\n'),
    autostart: false,
    actor: 'me',
  }
  const route = visibleRoutesOf(store.routes.value).find((item) => item.key === source.route?.key)
  if (route && routeAllowedForType(route, type)) body.route = route.key
  return body
}

/** Отправка запрещена и атрибутом `disabled`, и обработчиком: пустой проект не додумываем. */
function submitCreate(): void {
  if (!canCreate.value || creating.value || stage.value === 'created') return
  const body = buildCreateBody()
  if (!body.title || !body.project) return
  creating.value = true
  emit('create', body)
}

/** Форма получает черновик; без него — только текст расшифровки в описании. */
function openForm(): void {
  const seed: VoiceDraft =
    draft.value ?? {
      project: null,
      type: null,
      title: null,
      description: transcript.value,
      acceptance: null,
      route: null,
    }
  emit('openForm', seed)
  resetToIdle()
}

function onTrigger(): void {
  if (stage.value === 'recording') {
    void finishRecording()
    return
  }
  if (stage.value === 'idle') {
    void beginRecording()
    return
  }
  if (stage.value === 'transcribing' || stage.value === 'drafting') return
  open.value = !open.value
}

// ── телефонный жест: короткое нажатие открывает лист, удержание завершает ────

/**
 * `pointerdown` на кружке: короткое нажатие и удержание начинаются одинаково —
 * запись и лист. Повторный тап во время записи завершает её (как «Готово»).
 */
function onPhonePointerDown(): void {
  pressStartedAt = Date.now()
  pressHandled = false
  if (stage.value === 'recording') {
    pressHandled = true
    void finishRecording()
    return
  }
  if (stage.value === 'idle') {
    void beginRecording()
    return
  }
  if (stage.value === 'transcribing' || stage.value === 'drafting') return
  open.value = !open.value
}

/** `pointerup`: удержание дольше порога заканчивает запись, короткое — продолжает. */
function onPhonePointerUp(): void {
  if (pressHandled) {
    pressHandled = false
    return
  }
  if (stage.value !== 'recording') return
  if (Date.now() - pressStartedAt < HOLD_MS) return
  void finishRecording()
}

/** `pointercancel` (палец ушёл, системный жест) — запись выбрасывается. */
function onPhonePointerCancel(): void {
  pressHandled = false
  if (stage.value === 'recording') resetToIdle()
}

/**
 * Слушатели жеста на FAB. Отдаются через `v-bind`, а не `@pointerdown`: у `UiButton`
 * объявлен только `click`, и строгая проверка шаблонов не пропускает нативные
 * слушатели как пропсы.
 */
const fabGestures = {
  onPointerdown: (event: PointerEvent) => {
    event.preventDefault()
    onPhonePointerDown()
  },
  onPointerup: () => onPhonePointerUp(),
  onPointercancel: () => onPhonePointerCancel(),
}

// Результат создания из App.vue: id — подтверждение, ошибка — строкой в черновике.
watch(
  () => props.createdId,
  (id) => {
    if (id === null || id === undefined) return
    creating.value = false
    localCreatedId.value = id
    stage.value = 'created'
  },
)

watch(
  () => props.createError,
  (message) => {
    if (!message) return
    creating.value = false
    serverError.value = message
  },
)

// Панель закрыли (внешний клик, Escape, повторный клик) — запись гасим, состояние в покой.
watch(open, (isOpen) => {
  if (!isOpen) resetToIdle()
})

// ── клавиатура: удержание V, Enter/Esc во время записи ───────────────────────

/** Фокус в поле/редактируемом блоке или внутри открытого диалога — не наш случай. */
function isTypingOrDialog(): boolean {
  const active = document.activeElement as HTMLElement | null
  if (!active) return false
  if (active.isContentEditable) return true
  const tag = active.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (active.closest('[contenteditable]')) return true
  if (active.closest('[role="dialog"]')) return true
  return false
}

/** Открытая «Новая задача» (дровер) — глобальный V не должен писать поверх формы. */
function formOpen(): boolean {
  for (const drawer of document.querySelectorAll('.ui-drawer')) {
    const backdrop = drawer.closest('.ui-drawer-backdrop')
    const closing =
      drawer.classList.toString().includes('leave') ||
      Boolean(backdrop && backdrop.classList.toString().includes('leave'))
    if (!closing) return true
  }
  return false
}

function onKeydown(event: KeyboardEvent): void {
  if (event.code === 'KeyV') {
    if (event.repeat) return
    if (event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return
    if (!voiceEnabled.value || isTypingOrDialog() || formOpen()) return
    if (voiceKeyHeld) return
    voiceKeyHeld = true
    event.preventDefault()
    void beginRecording()
    return
  }
  if (stage.value !== 'recording') return
  if (event.code === 'Enter') {
    event.preventDefault()
    void finishRecording()
  } else if (event.code === 'Escape') {
    event.preventDefault()
    resetToIdle()
  }
}

function onKeyup(event: KeyboardEvent): void {
  if (event.code !== 'KeyV') return
  if (!voiceKeyHeld) return
  voiceKeyHeld = false
  if (stage.value === 'recording') void finishRecording()
}

onMounted(() => {
  // Кнопка живёт в шапке и не должна ждать открытия формы: статус — один запрос за сессию.
  store.ensureAssistant()
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('keyup', onKeyup)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('keyup', onKeyup)
  discardRecording()
})
</script>

<template>
  <div v-if="voiceEnabled" class="listik-voice">
    <!-- Телефон: кружок-триггер (FAB) поверх очереди; жест — pointer-события. -->
    <UiButton
      v-if="isPhone"
      class="listik-voice__fab"
      size="lg"
      variant="primary"
      ariaLabel="Голосовой ввод задачи"
      :loading="stage === 'transcribing' || stage === 'drafting'"
      v-bind="fabGestures"
    >
      <template #icon><ListikIcon name="mic" size="md" /></template>
    </UiButton>

    <!-- Обёртка панели: десктоп — поповер с кнопкой-триггером, телефон — лист снизу. -->
    <component :is="isPhone ? UiDrawer : UiPopover" v-model="open" v-bind="wrapperProps">
      <template #trigger>
        <UiButton
          v-if="!isPhone"
          size="sm"
          variant="secondary"
          ariaLabel="Голосовой ввод задачи"
          :loading="stage === 'transcribing' || stage === 'drafting'"
          @click.stop="onTrigger"
        >
          <template #icon><ListikIcon name="mic" size="xs" /></template>
          <template v-if="stage === 'recording'">
            <span class="listik-voice__wave" aria-hidden="true">
              <span
                v-for="(_bar, index) in WAVE_BARS"
                :key="index"
                class="listik-voice__bar"
                :style="{ transform: barTransform(index) }"
              />
            </span>
            <span class="listik-voice__timer tnum">{{ timerText }}</span>
          </template>
          <template v-else-if="stage === 'transcribing' || stage === 'drafting'">Разбираю</template>
          <template v-else>Голосом</template>
        </UiButton>
      </template>

      <div
        class="listik-voice__panel"
        :class="{ 'listik-voice__panel--phone': isPhone }"
        :data-stage="stage"
        :data-branch="branch ?? undefined"
      >
        <!-- 1. Слушаю -->
        <template v-if="stage === 'recording'">
          <span class="listik-voice__title">Слушаю</span>
          <span class="listik-section__hint">расскажи задачу словами — разберу сам</span>
          <span class="listik-voice__timer tnum">{{ timerText }}</span>
          <span class="listik-voice__wave" aria-hidden="true">
            <span
              v-for="(_bar, index) in WAVE_BARS"
              :key="index"
              class="listik-voice__bar"
              :style="{ transform: barTransform(index) }"
            />
          </span>
          <p v-if="!isPhone" class="listik-section__hint">Enter — закончить · Esc — отменить</p>
          <div class="listik-row">
            <UiButton :size="actionSize" :block="isPhone" variant="ghost" @click="resetToIdle">Отмена</UiButton>
            <UiButton :size="actionSize" :block="isPhone" variant="primary" @click="finishRecording">Готово</UiButton>
          </div>
          <p v-if="isPhone" class="listik-section__hint">
            на телефоне голос — основной ввод: держи кнопку и говори
          </p>
        </template>

        <!-- 2. Разбираю -->
        <template v-else-if="stage === 'transcribing' || stage === 'drafting'">
          <div class="listik-voice__processing">
            <UiSpinner size="sm" :label="stage === 'transcribing' ? 'Расшифровываю запись' : 'Собираю черновик'" />
            <span class="listik-voice__title">
              {{ stage === 'transcribing' ? 'Расшифровываю запись…' : 'Собираю черновик…' }}
            </span>
          </div>
          <div class="listik-row">
            <UiButton :size="actionSize" :block="isPhone" variant="ghost" @click="resetToIdle">Отмена</UiButton>
          </div>
        </template>

        <!-- 3. Черновик -->
        <template v-else-if="stage === 'draft' && draft">
          <div class="listik-voice__block">
            <span class="listik-voice__block-title">расшифровка</span>
            <p class="listik-voice__transcript">{{ transcript }}</p>
          </div>

          <div class="listik-row">
            <span v-if="draft.project" class="listik-voice__chip">
              <ProjectMark :project="projectRow" :slug="draft.project" size="sm" />
              <span>{{ projectRow?.title || draft.project }}</span>
            </span>
            <span v-if="draftType" class="listik-voice__chip">
              <TaskGlyph kind="type" :value="draft.type ?? 'task'" />
              <span>{{ draftType.label }}</span>
            </span>
          </div>

          <div class="listik-voice__block">
            <span class="listik-voice__block-title">Заголовок</span>
            <span class="listik-voice__draft-title">{{ draft.title || '—' }}</span>
          </div>

          <div class="listik-voice__block">
            <span class="listik-voice__block-title">Описание</span>
            <p class="listik-voice__draft-description">{{ draft.description || '—' }}</p>
          </div>

          <div class="listik-voice__block">
            <span class="listik-voice__block-title">Критерии приёмки</span>
            <ul v-if="acceptanceItems.length" class="listik-voice__draft-list">
              <li v-for="item in acceptanceItems" :key="item">{{ item }}</li>
            </ul>
            <span v-else class="listik-section__hint">—</span>
          </div>

          <p v-if="createHint" class="listik-voice__warn">{{ createHint }}</p>
          <p v-if="serverError" class="listik-voice__server-error">{{ serverError }}</p>

          <div class="listik-row">
            <UiButton v-if="!isPhone" :size="actionSize" variant="ghost" @click="beginRecording">Переписать</UiButton>
            <UiButton :size="actionSize" :block="isPhone" variant="secondary" @click="openForm">Открыть форму</UiButton>
            <UiButton
              :size="actionSize"
              :block="isPhone"
              variant="primary"
              :disabled="!canCreate"
              :loading="creating"
              @click="submitCreate"
            >
              Создать задачу
            </UiButton>
          </div>
        </template>

        <!-- 4. Создана -->
        <template v-else-if="stage === 'created'">
          <span class="listik-voice__title">Задача создана</span>
          <span v-if="localCreatedId" class="listik-voice__created-id listik-mono">{{ localCreatedId }}</span>
          <div class="listik-row">
            <UiButton :size="actionSize" :block="isPhone" variant="secondary" @click="closePanel">Закрыть</UiButton>
          </div>
        </template>

        <!-- Ветки ошибок -->
        <template v-else-if="stage === 'error' && branchInfo && branchActions">
          <UiAlert tone="danger">
            <template #title>{{ branchInfo.title }}</template>
            {{ branchInfo.text }}
            <div class="listik-row" style="margin-top: var(--space-2)">
              <UiButton :size="actionSize" :block="isPhone" variant="ghost" @click="branchActions.secondary.run">
                {{ branchActions.secondary.label }}
              </UiButton>
              <UiButton :size="actionSize" :block="isPhone" variant="primary" @click="branchActions.primary.run">
                {{ branchActions.primary.label }}
              </UiButton>
            </div>
          </UiAlert>
          <p v-if="serverError" class="listik-voice__server-error">{{ serverError }}</p>
        </template>
      </div>
    </component>
  </div>
</template>
