import { onMounted, watch, type ComputedRef, type Ref } from 'vue'

/**
 * Кнопка отправки окна неактивна, пока форма негодна: по якорю внутри формы
 * находит её кнопку `form="…"` и ставит/снимает `submit-disabled`. Звать из
 * `setup` — сам вешает `onMounted` и `watch`; нет якоря, формы или кнопки — молча выходит.
 */
export function bindSubmitDisabled(
  anchorSelector: string,
  disabled: Ref<boolean> | ComputedRef<boolean>,
): void {
  function sync(): void {
    const anchor = document.querySelector(anchorSelector)
    const form = anchor?.closest('form') ?? null
    if (!form) return
    const button = document.querySelector<HTMLButtonElement>(`button[form="${form.id}"]`)
    if (!button) return
    if (disabled.value) button.setAttribute('submit-disabled', '')
    else button.removeAttribute('submit-disabled')
  }
  onMounted(sync)
  watch(disabled, sync, { flush: 'post' })
}
