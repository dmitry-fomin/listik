<script setup lang="ts" generic="V extends string | number">
/**
 * Переключатель одного значения из набора, где вариант рисуется иконкой/кружком,
 * а не текстом (UiSegmented кита умеет только текстовый label). Своя разметка —
 * radiogroup той же механики, что и UiSegmented (roving tabindex, стрелки/Home/End),
 * опция передаётся вызывающему через слот #icon.
 */
import { ref } from 'vue'

export interface IconToggleOption<V> {
  value: V
  label: string
}

const props = withDefaults(
  defineProps<{
    modelValue: V
    options: IconToggleOption<V>[]
    ariaLabel?: string
    size?: 'sm' | 'md'
  }>(),
  { size: 'sm', ariaLabel: undefined },
)

const emit = defineEmits<{ 'update:modelValue': [value: V] }>()

const groupRef = ref<HTMLElement | null>(null)

function indexOf(value: V): number {
  return props.options.findIndex((option) => option.value === value)
}

function select(option: IconToggleOption<V>): void {
  emit('update:modelValue', option.value)
}

function focusAt(index: number): void {
  groupRef.value?.querySelectorAll<HTMLButtonElement>('[role="radio"]')[index]?.focus()
}

function onKeydown(event: KeyboardEvent, index: number): void {
  const count = props.options.length
  let target = -1
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') target = (index + 1) % count
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') target = (index - 1 + count) % count
  else if (event.key === 'Home') target = 0
  else if (event.key === 'End') target = count - 1
  else return
  event.preventDefault()
  select(props.options[target]!)
  focusAt(target)
}
</script>

<template>
  <div
    ref="groupRef"
    class="listik-icon-toggle"
    :class="`listik-icon-toggle--${size}`"
    role="radiogroup"
    :aria-label="ariaLabel"
  >
    <button
      v-for="(option, index) in options"
      :key="String(option.value)"
      type="button"
      role="radio"
      class="listik-icon-toggle__item"
      :class="{ 'is-on': option.value === modelValue }"
      :aria-checked="option.value === modelValue"
      :aria-label="option.label"
      :title="option.label"
      :tabindex="option.value === modelValue || (indexOf(modelValue) === -1 && index === 0) ? 0 : -1"
      @click="select(option)"
      @keydown="onKeydown($event, index)"
    >
      <slot name="icon" :option="option" />
    </button>
  </div>
</template>
