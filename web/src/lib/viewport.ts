/**
 * Порог «телефон» (шаг 06): один источник правды для CSS-медиа и для стора/
 * компонентов, которым нужно знать режим синхронно (до первого рендера), а не
 * только через CSS. Больше здесь ничего нет — общих «мобильных» помощников
 * (например, порог планшета 1023px) этот файл не собирает: он не появлялся в
 * коде до этого шага, а заводить его без нужды незачем.
 */
import { onScopeDispose, ref, type Ref } from 'vue'

export const PHONE_MAX_WIDTH = 767
export const PHONE_MEDIA = `(max-width: ${PHONE_MAX_WIDTH}px)`

export function useIsPhone(): Ref<boolean> {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return ref(false)
  }
  const query = window.matchMedia(PHONE_MEDIA)
  const isPhone = ref(query.matches)
  const onChange = (event: MediaQueryListEvent): void => {
    isPhone.value = event.matches
  }
  query.addEventListener('change', onChange)
  onScopeDispose(() => {
    query.removeEventListener('change', onChange)
  })
  return isPhone
}
