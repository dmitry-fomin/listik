import type { HarnessChannel } from '@/api/types'

/**
 * Харнесс по ключу актора/держателя — те же подстроки и порядок, что на сервере
 * (`listik/actors.py AGENT_HINTS`), чтобы доска и сервер сходились в разборе.
 * Держатель может быть любым ключом каталога `harnesses` (listik-2gry), не
 * только встроенным: `agent:<key>` и голый ключ каталога возвращаются как есть,
 * лишь когда в ключе не нашлась ни одна подсказка `HINTS` — подсказки ищутся
 * первыми по любому ключу. Глиф к ним ищет `HarnessIcon` (точный ключ, потом
 * голова до дефиса, иначе общий `bolt` — агент без фирменного знака, но не
 * человек).
 */
export type HarnessKey =
  | 'claude'
  | 'dsh'
  | 'codex'
  | 'grok'
  | 'gemini'
  | 'pi'
  | 'pi-glm'
  | 'pi-deepseek'
  | 'devin'
  | 'me'
  | 'human'

/**
 * Ключи с фирменным глифом — совпадают с ветками шаблона `HarnessIcon`.
 * У `pi-glm`/`pi-deepseek` — свои глифы (π с точкой цвета модели); прочие
 * `pi-*` ключи по-прежнему складываются в голову `pi`.
 */
export const HARNESS_GLYPHS: readonly string[] = [
  'claude',
  'dsh',
  'codex',
  'grok',
  'devin',
  'pi',
  'pi-glm',
  'pi-deepseek',
]

const HINTS: [string, HarnessKey][] = [
  // Порядок — как в `actors.AGENT_HINTS`: pi-* раньше «deepseek», иначе
  // pi-deepseek уйдёт в dsh.
  ['pi-glm', 'pi-glm'],
  ['pi-deepseek', 'pi-deepseek'],
  ['claude', 'claude'],
  ['opus', 'claude'],
  ['sonnet', 'claude'],
  ['fable', 'claude'],
  ['dsh', 'dsh'],
  ['deepseek', 'dsh'],
  ['grok', 'grok'],
  ['codex', 'codex'],
  ['gemini', 'gemini'],
]

export const HARNESS_TITLES: Record<HarnessKey, string> = {
  claude: 'claude',
  dsh: 'dsh',
  codex: 'codex',
  grok: 'grok',
  gemini: 'gemini',
  pi: 'pi',
  'pi-glm': 'pi · GLM',
  'pi-deepseek': 'pi · DeepSeek',
  devin: 'devin',
  me: 'человек',
  human: 'человек',
}

/**
 * Варианты пикера иконки харнесса: все фирменные глифы, `user` («человек»)
 * и пустое значение — общий `bolt`. Порядок — порядок кнопок пикера.
 * Объявление обязано стоять ниже `HARNESS_TITLES`: метки считаются через
 * `harnessTitle` при инициализации модуля, выше — TDZ.
 */
export const HARNESS_ICON_OPTIONS: { value: string; label: string }[] = [
  ...HARNESS_GLYPHS.map((key) => ({ value: key, label: harnessTitle(key) })),
  { value: 'user', label: 'человек' },
  { value: '', label: 'общий глиф' },
]

/** Тот же ключ, что проверяет сервер (`listik/routes.py KEY_RE`). */
export const KEY_RE = /^[a-z0-9][a-z0-9-]*$/

/** Черновик ключа из имени/названия: годный по `KEY_RE` или `''`. */
export function slugifyKey(value: string): string {
  const draft = value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return KEY_RE.test(draft) ? draft : ''
}

/** Причина, по которой ключ не годится; `null` — годится. */
export function keyProblem(value: string): string | null {
  if (!value) return 'ключ не заполнен'
  if (!KEY_RE.test(value)) {
    return 'ключ: строчные латинские буквы, цифры и дефис, начинается с буквы или цифры'
  }
  return null
}

/**
 * Ключ харнесса держателя для глифа и подписи; `null` — держателя нет,
 * `'human'` — человек. Зеркалит `actors.resolve`, порядок разбора тот же:
 * `me`/`human` — человек; затем подсказки `HINTS` (состав и порядок совпадают
 * с серверным `AGENT_HINTS`) по любому ключу, включая `agent:<key>`; затем
 * ветка `agent:<key>` — возвращает остаток как ключ каталога (пустой остаток
 * или `me` — человек; неизвестному ключу — общий `bolt`, не «человек»);
 * затем ключ с фирменным глифом (`devin`, `pi-glm`) или ключ уже загруженного
 * каталога (`catalog`); остальное — человек. `catalog` — ключи
 * `GET /api/harnesses`, когда они уже есть в сторе.
 */
export function harnessOf(
  actor: string | null | undefined,
  catalog?: Iterable<string>,
): string | null {
  if (!actor) return null
  const key = actor.trim().toLowerCase()
  if (!key) return null
  if (key === 'me' || key === 'human') return 'human'
  for (const [hint, harness] of HINTS) {
    if (key.includes(hint)) return harness
  }
  if (key.startsWith('agent:')) {
    const bare = key.slice(6)
    return bare && bare !== 'me' ? bare : 'human'
  }
  const head = key.split('-', 1)[0]
  if (
    KEY_RE.test(key) &&
    (HARNESS_GLYPHS.includes(key) ||
      HARNESS_GLYPHS.includes(head) ||
      (catalog !== undefined && [...catalog].includes(key)))
  ) {
    return key
  }
  return 'human'
}

/**
 * Подпись харнесса по ключу каталога: встроенные ключи знают свои имена,
 * пользовательский ключ (`opencode`, `mini`…) показывается как есть — каталог
 * `GET /api/harnesses` теперь принимает любой ключ (listik-2gry).
 */
export function harnessTitle(key: string | null | undefined): string {
  if (!key) return ''
  return HARNESS_TITLES[key as HarnessKey] ?? key
}

/**
 * Харнесс, способный исполнить этап роя: включён и у него
 * есть команда `argv`. Ручная выдача (`kind=manual`, `argv=null`) этап не
 * запустит — в выбор исполнителя она не входит.
 */
export function runnableHarness(item: { enabled: boolean; argv: string[] | null }): boolean {
  return item.enabled && Array.isArray(item.argv) && item.argv.length > 0
}

/* ── каналы харнесса (listik-46q6): строки редактора и проверки по контракту API ── */

/** Каналов у харнесса не больше — то же ограничение, что на сервере. */
export const MAX_CHANNELS = 20

/** Строка редактора каналов; `fresh` — добавлена в карточке и ещё не сохранена. */
export interface ChannelRow extends Record<string, unknown> {
  id: string
  name: string
  model: string
  effort: string
  fresh: boolean
}

let channelSeq = 0

export function channelRowsOf(channels: HarnessChannel[]): ChannelRow[] {
  return channels.map((channel) => ({
    id: `channel-${(channelSeq += 1)}`,
    name: channel.name,
    model: channel.model,
    effort: channel.effort ?? '',
    fresh: false,
  }))
}

export function createChannelRow(): ChannelRow {
  return { id: `channel-${(channelSeq += 1)}`, name: '', model: '', effort: '', fresh: true }
}

/** Пустая новая строка (имя и модель пусты) — не ошибка, в патч не идёт. */
export function channelRowBlank(row: ChannelRow): boolean {
  return row.fresh && row.name.trim() === '' && row.model.trim() === ''
}

export interface ChannelProblems {
  name: string | null
  model: string | null
  effort: string | null
}

/** Ошибки полей по строкам (у пустой новой строки — никаких); порядок — порядок `rows`. */
export function channelProblems(rows: ChannelRow[]): ChannelProblems[] {
  const seen = new Map<string, number>()
  for (const row of rows) {
    if (channelRowBlank(row)) continue
    const name = row.name.trim()
    seen.set(name, (seen.get(name) ?? 0) + 1)
  }
  return rows.map((row) => {
    if (channelRowBlank(row)) return { name: null, model: null, effort: null }
    const name = row.name.trim()
    const model = row.model.trim()
    const effort = row.effort.trim()
    return {
      name:
        name === ''
          ? 'имя не заполнено'
          : !KEY_RE.test(name)
            ? 'имя: строчные латинские буквы, цифры и дефис, начинается с буквы или цифры'
            : (seen.get(name) ?? 0) > 1
              ? 'имя повторяется'
              : null,
      model:
        model === ''
          ? 'модель не заполнена'
          : /\s/.test(model)
            ? 'модель без пробелов'
            : model.length > 200
              ? 'модель длиннее 200 символов'
              : null,
      effort:
        effort !== '' && !KEY_RE.test(effort)
          ? 'усилие: строчные латинские буквы, цифры и дефис'
          : null,
    }
  })
}

/** Тело `channels` из черновика: непустые строки по порядку, `default` — у строки `defaultId`. */
export function channelsOf(rows: ChannelRow[], defaultId: string | null): HarnessChannel[] {
  return rows
    .filter((row) => !channelRowBlank(row))
    .map((row) => ({
      name: row.name.trim(),
      model: row.model.trim(),
      effort: row.effort.trim() === '' ? null : row.effort.trim(),
      default: row.id === defaultId,
    }))
}
