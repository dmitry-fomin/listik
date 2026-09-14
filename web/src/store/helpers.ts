/** Общие обёртки для асинхронных действий стора. */

export interface LoadingState {
  value: boolean
}

export type ErrorHandler = (error: unknown) => void

/** Выполняет запрос, возвращая null после обработки ошибки. */
export async function tryRequest<T>(
  action: () => Promise<T>,
  onError: ErrorHandler,
): Promise<T | null> {
  try {
    return await action()
  } catch (error) {
    onError(error)
    return null
  }
}

/** Гарантирует сброс флага загрузки даже при исключении. */
export async function withLoading<T>(
  loading: LoadingState,
  action: () => Promise<T>,
): Promise<T> {
  loading.value = true
  try {
    return await action()
  } finally {
    loading.value = false
  }
}
