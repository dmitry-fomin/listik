<script setup lang="ts">
/**
 * RouteSubstitutions — панель «Подстановки»: шесть имён, допустимых в `command`
 * записи маршрута (`listik/routes.py`, `PLACEHOLDERS`), значение для примера и
 * счётчик, сколько раз имя встречается в текущей команде записи. Только чтение —
 * используется картой конвейера (`RouteCard.vue`), картой роя и карточкой
 * харнесса, поэтому вынесена отдельным компонентом. Значения для примера
 * живут в `lib/routes.ts` (`placeholderExample`): те же значения подставляет
 * предпросмотр команды.
 */
import { computed } from 'vue'
import { ROUTE_PLACEHOLDERS, countPlaceholders, placeholderExample } from '@/lib/routes'

const props = defineProps<{
  /** Ключ выбранного маршрута — значение примера для `{route}`. */
  routeKey: string
  /** Команда записи (argv); `null` — подстановки не встречаются нигде. */
  command: string[] | null
}>()

/** `{name}` — вынесено функцией: буквальные `{}` внутри `{{ }}` шаблона путают парсер Vue. */
function braced(name: string): string {
  return '{' + name + '}'
}

const rows = computed(() =>
  ROUTE_PLACEHOLDERS.map((name) => ({
    name,
    example: placeholderExample(name, props.routeKey),
    count: countPlaceholders(props.command, name),
  })),
)
</script>

<template>
  <div class="listik-route-substitutions">
    <h4 class="listik-route-substitutions__title">Подстановки</h4>
    <ul class="listik-route-substitutions__list">
      <li v-for="row in rows" :key="row.name" class="listik-route-substitutions__row">
        <code class="listik-mono listik-route-substitutions__name">{{ braced(row.name) }}</code>
        <span class="listik-route-substitutions__example">{{ row.example }}</span>
        <span class="listik-route-substitutions__count">×{{ row.count }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.listik-route-substitutions {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.listik-route-substitutions__title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-route-substitutions__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.listik-route-substitutions__row {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.listik-route-substitutions__name {
  flex: 0 0 auto;
  color: var(--accent-600);
}

.listik-route-substitutions__example {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink-3);
}

.listik-route-substitutions__count {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
  color: var(--ink-2);
}
</style>
