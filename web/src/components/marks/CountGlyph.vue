<script setup lang="ts">
/** CountGlyph — счётчик связи: иконка + число (прототип: `.cnt`). Ничего не рендерит при count <= 0. */
import { computed } from 'vue'
import ListikIcon from '@/components/ListikIcon.vue'

const props = defineProps<{
  icon: 'lock' | 'key' | 'branch'
  count: number
  title: string
  tone: 'warning' | 'accent' | 'neutral'
}>()

const visible = computed(() => props.count > 0)
</script>

<template>
  <span
    v-if="visible"
    class="listik-count-glyph"
    :class="`listik-count-glyph--${tone}`"
    :title="title"
  >
    <ListikIcon :name="icon" size="xs" />
    <span class="tnum">{{ count }}</span>
  </span>
</template>
