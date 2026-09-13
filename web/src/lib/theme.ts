/**
 * Тёмная тема по умолчанию + переключатель. Цветовая гамма — из кита
 * (`useColorScheme`), она живёт в своём ключе localStorage; здесь только ось
 * светлая/тёмная (`data-theme` на <html>), которую кит читает сам.
 */
import { ref, type Ref } from 'vue'

export type ThemeValue = 'dark' | 'light'

const THEME_STORAGE_KEY = 'listik.theme'
const ATTR = 'data-theme'

function readStored(): ThemeValue | null {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY)
    return value === 'dark' || value === 'light' ? value : null
  } catch {
    return null
  }
}

function apply(value: ThemeValue): void {
  document.documentElement.setAttribute(ATTR, value)
}

const theme: Ref<ThemeValue> = ref<ThemeValue>('dark')

/** Ставит атрибут темы сразу (до монтирования приложения), чтобы не было вспышки светлой темы. */
export function initTheme(): void {
  theme.value = readStored() ?? 'dark'
  apply(theme.value)
}

export function useTheme(): {
  theme: Ref<ThemeValue>
  setTheme: (value: ThemeValue) => void
  toggleTheme: () => void
} {
  function setTheme(value: ThemeValue): void {
    theme.value = value
    apply(value)
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, value)
    } catch {
      /* приватный режим — тема не переживёт перезагрузку */
    }
  }

  return {
    theme,
    setTheme,
    toggleTheme: () => setTheme(theme.value === 'dark' ? 'light' : 'dark'),
  }
}
