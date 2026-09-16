/**
 * Тонкий слой над `navigator.mediaDevices.getUserMedia` и `MediaRecorder`:
 * компонент не знает браузерных API, а скриптовая проверка подменяет их стабом.
 *
 * Импортов из стора/клиента/`.vue` здесь нет намеренно — файл автономен и
 * тестируем; допустимы только браузерные API и типы (`import type`).
 */

/** Кандидаты mime по убыванию предпочтения: Safari не умеет webm, Chrome — mp4. */
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'] as const

/** Машинный код ошибки записи; человеческий текст рисует UI, а не этот файл. */
export type VoiceRecorderErrorCode = 'denied' | 'unsupported' | 'failed'

/** Ошибка записи с машинным кодом: `denied` / `unsupported` / `failed`. */
export class VoiceRecorderError extends Error {
  readonly code: VoiceRecorderErrorCode

  constructor(code: VoiceRecorderErrorCode, message: string) {
    super(message)
    this.name = 'VoiceRecorderError'
    this.code = code
  }
}

/** Готовая запись: `blob` — аудио, `mime` — выбранный тип, `durationMs` — длительность. */
export interface VoiceRecordingResult {
  blob: Blob
  mime: string
  durationMs: number
}

/** Управление идущей записью (см. `startVoiceRecording`). */
export interface VoiceRecording {
  /** Момент старта (`Date.now()`) — таймер панели считает от него. */
  startedAt: number
  /** Остановить и отдать запись. После «отмены» промис не разрешается записью. */
  stop(): Promise<VoiceRecordingResult>
  /** Отменить: запись уничтожается, наружу ничего не отдаётся. */
  cancel(): void
  /** Текущий уровень звука 0…1; измерение недоступно — 0. */
  getLevel(): number
}

/** Отказ `getUserMedia`: нет разрешения — `denied`, всё прочее — `failed`. */
function classifyGetUserMediaError(error: unknown): VoiceRecorderErrorCode {
  const name = error instanceof Error ? error.name : (error as { name?: string } | null)?.name
  return name === 'NotAllowedError' || name === 'SecurityError' ? 'denied' : 'failed'
}

/** Первый mime, который умеет записывать браузер; `null` — ни одного поддержанного. */
function pickMime(): string | null {
  for (const candidate of MIME_CANDIDATES) {
    try {
      if (MediaRecorder.isTypeSupported(candidate)) return candidate
    } catch {
      /* кривой полифил — пробуем следующий кандидат */
    }
  }
  return null
}

/** Погасить все дорожки потока: и «стоп», и «отмена», и ошибка после получения. */
function stopTracks(stream: MediaStream | null): void {
  if (!stream) return
  for (const track of stream.getTracks()) {
    try {
      track.stop()
    } catch {
      /* дорожка уже погашена — не повод падать */
    }
  }
}

function createRecording(stream: MediaStream, mime: string): VoiceRecording {
  const startedAt = Date.now()
  const chunks: Blob[] = []
  let cancelled = false
  let stopPromise: Promise<VoiceRecordingResult> | null = null

  const recorder = new MediaRecorder(stream, { mimeType: mime })
  recorder.ondataavailable = (event: BlobEvent) => {
    if (!cancelled && event.data) chunks.push(event.data)
  }

  // Уровень — необязательное измерение: нет AudioContext/анализатора или он
  // бросил — запись продолжается, уровень остаётся 0 (этот путь и видит стаб).
  let audioContext: AudioContext | null = null
  let analyser: AnalyserNode | null = null
  let samples: Uint8Array<ArrayBuffer> | null = null
  try {
    const AudioCtor: typeof AudioContext | undefined =
      typeof window !== 'undefined'
        ? window.AudioContext ??
          (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
        : undefined
    if (AudioCtor) {
      audioContext = new AudioCtor()
      const source = audioContext.createMediaStreamSource(stream)
      analyser = audioContext.createAnalyser()
      source.connect(analyser)
      samples = new Uint8Array(analyser.fftSize)
    }
  } catch {
    audioContext = null
    analyser = null
    samples = null
  }

  function closeAudio(): void {
    analyser = null
    samples = null
    const context = audioContext
    audioContext = null
    if (!context) return
    try {
      void context.close().catch(() => undefined)
    } catch {
      /* AudioContext уже закрыт или не умеет close() — не важно */
    }
  }

  recorder.start()

  return {
    startedAt,
    stop(): Promise<VoiceRecordingResult> {
      // После отмены остановка ничего не отдаёт: промис остаётся вечным.
      if (cancelled) return new Promise<VoiceRecordingResult>(() => undefined)
      if (stopPromise) return stopPromise
      stopPromise = new Promise<VoiceRecordingResult>((resolve, reject) => {
        const finish = () => {
          if (cancelled) return
          closeAudio()
          stopTracks(stream)
          resolve({
            blob: new Blob(chunks, { type: mime }),
            mime,
            durationMs: Date.now() - startedAt,
          })
        }
        const fail = () => {
          if (cancelled) return
          closeAudio()
          stopTracks(stream)
          reject(new VoiceRecorderError('failed', 'запись прервалась'))
        }
        recorder.onstop = finish
        recorder.onerror = fail
        try {
          recorder.stop()
        } catch {
          finish()
        }
      })
      return stopPromise
    },
    cancel(): void {
      if (cancelled) return
      cancelled = true
      // Ссылка на куски сбрасывается: запись уничтожается, наружу не уходит.
      chunks.length = 0
      // Колбэки снимаются: остановка не должна ни отдать запись, ни разрешить промис.
      recorder.ondataavailable = null
      recorder.onstop = null
      recorder.onerror = null
      try {
        if (recorder.state !== 'inactive') recorder.stop()
      } catch {
        /* запись уже встала — важно лишь погасить дорожки */
      }
      closeAudio()
      stopTracks(stream)
    },
    getLevel(): number {
      if (!analyser || !samples) return 0
      try {
        analyser.getByteTimeDomainData(samples)
      } catch {
        return 0
      }
      let sum = 0
      for (const sample of samples) {
        const centered = (sample - 128) / 128
        sum += centered * centered
      }
      return Math.min(1, Math.sqrt(sum / samples.length))
    },
  }
}

/**
 * Начать запись. Бросает `VoiceRecorderError`: `unsupported` — нет браузерных
 * API или ни одного поддержанного mime, `denied`/`failed` — отказ `getUserMedia`.
 */
export async function startVoiceRecording(): Promise<VoiceRecording> {
  if (
    typeof navigator === 'undefined' ||
    !navigator.mediaDevices?.getUserMedia ||
    typeof MediaRecorder === 'undefined'
  ) {
    throw new VoiceRecorderError('unsupported', 'запись звука недоступна в этом браузере')
  }
  const mime = pickMime()
  if (!mime) {
    throw new VoiceRecorderError('unsupported', 'браузер не поддерживает ни один формат записи')
  }

  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch (error) {
    throw new VoiceRecorderError(classifyGetUserMediaError(error), 'не удалось получить доступ к микрофону')
  }

  // Дальше поток уже наш: любая ошибка обязана погасить дорожки микрофона.
  try {
    return createRecording(stream, mime)
  } catch (error) {
    stopTracks(stream)
    if (error instanceof VoiceRecorderError) throw error
    throw new VoiceRecorderError('failed', 'не удалось начать запись')
  }
}

/**
 * Перевести запись в base64 **без** префикса `data:…;base64,` — ровно то,
 * чего ждёт поле `audio_base64`.
 */
export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : ''
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(new VoiceRecorderError('failed', 'не удалось прочитать запись'))
    reader.readAsDataURL(blob)
  })
}
