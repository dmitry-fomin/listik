<script setup lang="ts">
/**
 * RouteName — название маршрута для показа на доске: уровень обычным цветом,
 * за ним семейство пресета (`full`/`cc`/`claude`) светло-серым `--ink-4`.
 * Разбор имени — `routeNameParts` (`lib/routes`): семейство берётся из поля
 * `plugin` записи, уровень — из `title` без ведущего «<семейство> ». У роя и
 * своих маршрутов плагина нет — выводится только `title`, как раньше.
 *
 * Для текстовых атрибутов (`title`, `aria-label`) вместо компонента —
 * `routeNameText`: `textContent` корня после схлопывания пробелов ей равен.
 * Название приходит с сервера — выводится только текстом, разметки в данных нет.
 */
import { computed } from 'vue'
import { routeNameParts } from '@/lib/routes'

const props = defineProps<{
  /** Запись маршрута (`GET /api/routes`) — нужны `title` и `plugin`. */
  route: { key: string; title: string; plugin?: string | null }
}>()

const parts = computed(() => routeNameParts(props.route))
</script>

<template>
  <!-- Пробел перед семейством — интерполяцией: whitespace-condense Vue снял бы
       голый пробел в начале `template`, а снаружи условия он оставил бы
       висячий хвост у маршрута без семейства. -->
  <span class="listik-route-name">{{ parts.level }}<template v-if="parts.family !== null">{{ ' ' }}<span class="listik-route-name__family">{{ parts.family }}</span></template></span>
</template>

<style scoped>
.listik-route-name__family {
  color: var(--ink-4);
  font-weight: normal;
}
</style>
