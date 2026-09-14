<script setup lang="ts">
/**
 * Ряд чипов «здоровье конвейера» (прототип: ряд под линией «Нужен
 * ты»). Чипы считают состояние — сами счётчики приходят из `store.counts`.
 * Проект и поиск задач переехали в шапку (AppHeader.vue): они фильтруют вообще
 * всё, а не только доску, поэтому логично, что они рядом с маркой приложения,
 * а не в тулбаре конкретного вида (см. ProjectPicker.vue).
 */
import { UiChip } from '@zoloto585/facet'
import store from '@/store/listik'

function toggleHealth(value: 'dead' | 'at-risk', next: boolean): void {
  store.filters.health = next ? value : ''
  void store.applyFilters()
}

function toggleDeps(value: 'blocked' | 'ready', next: boolean): void {
  store.filters.deps = next ? value : 'all'
  void store.applyFilters()
}
</script>

<template>
  <div class="listik-toolbar" aria-label="Фильтры конвейера">
    <div class="listik-toolbar__chips">
      <UiChip
        size="sm"
        :label="`${store.counts.value.dead} брошена`"
        hint="держатель молчит дольше суток или задача в работе без держателя"
        :selected="store.filters.health === 'dead'"
        @update:selected="(value) => toggleHealth('dead', value)"
      />
      <UiChip
        size="sm"
        :label="`${store.counts.value.atRisk} под угрозой`"
        hint="heartbeat старше 15 мин, дольше порога этапа или выдана, но не взята"
        :selected="store.filters.health === 'at-risk'"
        @update:selected="(value) => toggleHealth('at-risk', value)"
      />
      <UiChip
        size="sm"
        :label="`${store.counts.value.blocked} стоят из-за других`"
        hint="есть незакрытые жёсткие блокеры"
        :selected="store.filters.deps === 'blocked'"
        @update:selected="(value) => toggleDeps('blocked', value)"
      />
      <UiChip
        size="sm"
        :label="`${store.counts.value.ready} можно брать`"
        hint="нет блокеров и держателя"
        :selected="store.filters.deps === 'ready'"
        @update:selected="(value) => toggleDeps('ready', value)"
      />
      <UiChip size="sm" :label="`${store.counts.value.inPipeline} в конвейере`" hint="на этапах s1–s4" />
    </div>
  </div>
</template>

<style scoped>
.listik-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.listik-toolbar__chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}
</style>
