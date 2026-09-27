import { onBeforeUnmount, ref, type Ref } from 'vue'
import type { SaveStatusValue } from '@zoloto585/facet'

export interface AutosaveOptions<P extends object> {
  /** Диф черновика против принятого сервером: `null` — сохранять нельзя, `{}` — нечего. */
  diff: () => P | null
  /** Один PATCH; `true` — сервер принял. */
  send: (patch: P) => Promise<boolean>
  onSaved: (patch: P) => void
  onFailed: (patch: P) => void
  /** Дебаунс `schedule(false)`, мс. */
  delay?: number
}

export interface Autosave {
  status: Ref<SaveStatusValue>
  schedule: (immediate: boolean) => void
  flushNow: () => Promise<void>
  cancel: () => void
}

/**
 * Автосохранение карточки настроек: диф → один PATCH → `onSaved`/`onFailed`.
 * `diff()` вернул `null` (черновик с ошибкой, запись удалена) или объект без
 * ключей — ничего не шлётся и `status` не меняется. Пока запрос в полёте,
 * новый `flushNow` лишь помечает очередь; после ответа отправка повторяется
 * со свежим дифом — одновременно в полёте не больше одного `send`.
 * `schedule(false)` — дебаунс `delay` мс, `schedule(true)` — сразу.
 * Досохранение при размонтировании встроено (`onBeforeUnmount`), поэтому
 * вызывать только синхронно из `setup` компонента.
 */
export function useAutosave<P extends object>(options: AutosaveOptions<P>): Autosave {
  const delay = options.delay ?? 600
  const status = ref<SaveStatusValue>('idle')
  let inFlight = false
  let queued = false
  let timer: ReturnType<typeof setTimeout> | null = null

  function cancel(): void {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  async function flushNow(): Promise<void> {
    const patch = options.diff()
    if (!patch || Object.keys(patch).length === 0) return
    if (inFlight) {
      queued = true
      return
    }
    inFlight = true
    status.value = 'saving'
    let ok = false
    try {
      ok = await options.send(patch)
    } catch {
      ok = false
    } finally {
      inFlight = false
    }
    if (ok) {
      status.value = 'saved'
      options.onSaved(patch)
    } else {
      status.value = 'error'
      options.onFailed(patch)
    }
    if (queued) {
      queued = false
      await flushNow()
    }
  }

  function schedule(immediate: boolean): void {
    cancel()
    if (immediate) {
      void flushNow()
      return
    }
    timer = setTimeout(() => {
      timer = null
      void flushNow()
    }, delay)
  }

  onBeforeUnmount(() => {
    cancel()
    void flushNow()
  })

  return { status, schedule, flushNow, cancel }
}
