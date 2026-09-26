<script setup lang="ts">
/**
 * RouteCommandText — строка команды маршрута с подсвеченными подстановками:
 * допустимая `{имя}` — акцентом, всё, чего сервер не примет (`{чужое}` и голая
 * скобка вроде `{{`, `{`, `{foo`), — красным с волной. Разбор — `splitCommandChunks`
 * из `lib/routes.ts`, то есть то же правило, по которому карточка роя не
 * пускает команду на сервер (`RouteSwarmCard.vue`).
 *
 * Отдельный компонент, потому что показывается дважды в одной карточке: под
 * полем аргумента и под полем промпта. Только чтение — правит автор сами поля,
 * а это их разбор.
 */
import { braced, splitCommandChunks } from '@/lib/routes'

defineProps<{ text: string }>()
</script>

<template>
  <span class="listik-route-command-text">
    <template v-for="(chunk, index) in splitCommandChunks(text)" :key="index">
      <span v-if="chunk.type === 'text'">{{ chunk.value }}</span>
      <span v-else-if="chunk.type === 'placeholder'" class="listik-route-command-text__placeholder">{{
        braced(chunk.value)
      }}</span>
      <span
        v-else-if="chunk.type === 'brace'"
        class="listik-route-command-text__placeholder listik-route-command-text__placeholder--bad"
        :title="'фигурные скобки допустимы только в подстановках'"
        >{{ chunk.value }}</span
      >
      <span
        v-else
        class="listik-route-command-text__placeholder listik-route-command-text__placeholder--bad"
        :title="`неизвестная подстановка ${braced(chunk.value)}`"
        >{{ braced(chunk.value) }}</span
      >
    </template>
  </span>
</template>

<style scoped>
.listik-route-command-text {
  overflow-wrap: break-word;
  white-space: pre-wrap;
}

.listik-route-command-text__placeholder {
  color: var(--accent-600);
  font-weight: var(--weight-medium);
}

.listik-route-command-text__placeholder--bad {
  color: var(--danger-600);
  text-decoration: underline wavy;
}
</style>
