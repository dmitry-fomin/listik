<script setup lang="ts">
/**
 * Вид «Доска»: колонки этапов конвейера из /api/board. Других группировок нет —
 * все задачи в любых срезах ищутся во вкладке «Список». Никакого drag&drop —
 * этап меняется только из панели задачи (`Следующий этап`), доска только показывает.
 *
 * Ряд колонок и рельса переходов над ним — два grid с одинаковым набором
 * дорожек (`boardTracks`) внутри одного прокручиваемого контейнера, чтобы
 * подписи переходов ехали вместе с колонками. Колонки резиновые: развёрнутая
 * берёт долю `1fr` (не уже `--listik-col-min`), свёрнутая — узкую рельсу
 * `--listik-rail-w`, поэтому когда колонку сворачивают, остальные расширяются.
 * Какие колонки свёрнуты — помнит браузер (localStorage).
 */
import { computed, ref, watch } from 'vue'
import { UiBadge, UiButton } from '@zoloto585/facet'
import BoardColumn from '@/components/board/BoardColumn.vue'
import ListikIcon from '@/components/ListikIcon.vue'
import VoiceCapture from '@/components/VoiceCapture.vue'
import store from '@/store/listik'
import type { ProjectRow, TaskStage, VoiceDraft } from '@/api/types'
import { PIPELINE_STAGE_KEYS, transitionOut } from '@/lib/stages'
import { INTAKE_COLUMN_KEY } from '@/lib/dictionaries'

const props = defineProps<{
  projects?: ProjectRow[]
  /** id задачи, созданной голосом: подтверждение в панели записи (см. App.vue). */
  voiceCreatedId?: string | null
  /** Ошибка создания голосом — текст под черновиком, доска её общим алертом не показывает. */
  voiceCreateError?: string | null
}>()

const emit = defineEmits<{
  create: []
  /** «Создать задачу» из панели записи: тело уходит в App.vue (store.createTask). */
  voiceCreate: [body: Record<string, unknown>]
  /** «Открыть форму» из панели записи: черновик предзаполняет «Новую задачу». */
  voiceOpenForm: [draft: VoiceDraft]
}>()

const COLLAPSED_KEY = 'listik.board.collapsed'

/** По умолчанию на узком окне свёрнута «Заведена» — как было до резиновых колонок. */
function readCollapsed(): string[] {
  try {
    const raw = window.localStorage.getItem(COLLAPSED_KEY)
    if (raw) {
      const parsed: unknown = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed.filter((key): key is string => typeof key === 'string')
    }
  } catch {
    /* приватный режим — берём умолчание */
  }
  return window.matchMedia('(max-width: 1279px)').matches ? [INTAKE_COLUMN_KEY] : []
}

const collapsed = ref<string[]>(readCollapsed())

watch(collapsed, (value) => {
  try {
    window.localStorage.setItem(COLLAPSED_KEY, JSON.stringify(value))
  } catch {
    /* приватный режим — не переживёт перезагрузку */
  }
})

function isCollapsed(key: string): boolean {
  return collapsed.value.includes(key)
}

function toggleColumn(key: string): void {
  collapsed.value = isCollapsed(key) ? collapsed.value.filter((item) => item !== key) : [...collapsed.value, key]
}

/** Колонки ряда («Готово» — всегда рельса, не колонка). */
const renderColumns = computed(() => store.columns.value.filter((column) => column.key !== 'done'))

/** Дорожки `grid-template-columns`, общие для рельсы и ряда колонок; последняя — рельса «Готово». */
const boardTracks = computed<string>(() => {
  const tracks = renderColumns.value.map((column) =>
    isCollapsed(column.key) ? 'var(--listik-rail-w)' : 'minmax(var(--listik-col-min), 1fr)',
  )
  tracks.push('var(--listik-rail-w)')
  return tracks.join(' ')
})

interface RailCell {
  key: string
  transition: { stage: TaskStage; kind: 'sticky' | 'handoff' } | null
}

/** Ячейки рельсы переходов — линия и подпись только в ячейках s1–s4. */
const railCells = computed<RailCell[]>(() => {
  const cells: RailCell[] = renderColumns.value.map((column) => {
    if (!PIPELINE_STAGE_KEYS.includes(column.key)) return { key: column.key, transition: null }
    const stage = column.key as TaskStage
    const kind = transitionOut(stage)
    return { key: column.key, transition: kind ? { stage, kind } : null }
  })
  cells.push({ key: '__done-rail', transition: null })
  return cells
})

const gridStyle = computed(() => ({ gridTemplateColumns: boardTracks.value }))
</script>

<template>
  <section class="listik-section" aria-label="Доска задач">
    <div class="listik-section__head">
      <div class="listik-row">
        <span class="listik-section__hint">
          колонки — этапы конвейера; этап меняет агент через <span class="listik-mono">stage</span>, доска только
          показывает · клик по заголовку сворачивает колонку
        </span>
      </div>
      <div class="listik-row">
        <span class="listik-section__hint tnum">{{ store.board.value?.total ?? 0 }} задач в выборке</span>
        <VoiceCapture
          :created-id="props.voiceCreatedId"
          :create-error="props.voiceCreateError"
          @create="(body) => emit('voiceCreate', body)"
          @open-form="(draft) => emit('voiceOpenForm', draft)"
        />
        <UiButton size="sm" variant="secondary" @click="emit('create')">
          <template #icon><ListikIcon name="plus" size="xs" /></template>
          Новая задача
        </UiButton>
      </div>
    </div>

    <div class="listik-board-scroll">
      <div class="listik-rail" :style="gridStyle" aria-label="Переходы конвейера">
        <div v-for="cell in railCells" :key="cell.key" class="listik-rail__cell">
          <template v-if="cell.transition">
            <span class="listik-rail__line" />
            <span class="listik-rail__tr" :class="`is-${cell.transition.kind}`">
              <ListikIcon :name="cell.transition.kind === 'sticky' ? 'refresh' : 'expand'" size="xs" />
              {{ cell.transition.kind }}
            </span>
          </template>
        </div>
      </div>

      <div class="listik-board listik-board--grid" :style="gridStyle">
        <BoardColumn
          v-for="column in renderColumns"
          :key="column.key"
          :column="column"
          :collapsed="isCollapsed(column.key)"
          :loading="store.loading.value"
          :deps-summary="store.depsSummary.value"
          :projects="props.projects"
          @open="store.openTask"
          @toggle="toggleColumn(column.key)"
        />

        <aside
          class="listik-done-rail"
          data-stage="done"
          role="button"
          tabindex="0"
          :aria-label="`Готово за 7 дней, ${store.doneWeekCount.value} задач — открыть список готовых`"
          @click="store.openDoneList()"
          @keydown.enter="store.openDoneList()"
          @keydown.space.prevent="store.openDoneList()"
        >
          <ListikIcon name="check" size="md" />
          <span class="listik-done-rail__title">Готово · 7 дн</span>
          <UiBadge tone="success" size="sm">{{ store.doneWeekCount.value }}</UiBadge>
        </aside>
      </div>
    </div>
  </section>
</template>
