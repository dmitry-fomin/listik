/**
 * Харнесс по ключу актора/держателя — те же подстроки и порядок, что на сервере
 * (`listik/actors.py AGENT_HINTS`), чтобы доска и сервер сходились в разборе.
 * Держатель может быть любым ключом каталога `harnesses` (listik-2gry), не
 * только встроенным: `agent:<key>` и голый ключ каталога возвращаются как есть,
 * а глиф к ним ищет `HarnessIcon` (точный ключ, потом голова до дефиса, иначе
 * общий `bolt` — агент без фирменного знака, но не человек).
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
 * `pi-glm`/`pi-deepseek` глифа не имеют: до них дойдёт голова `pi`.
 */
export const HARNESS_GLYPHS: readonly string[] = [
  'claude',
  'dsh',
  'codex',
  'grok',
  'gemini',
  'devin',
  'pi',
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

const KEY_RE = /^[a-z0-9][a-z0-9-]*$/

/**
 * Ключ харнесса держателя для глифа и подписи; `null` — держателя нет,
 * `'human'` — человек. Зеркалит `actors.resolve`: `agent:<key>` — агент с
 * ключом каталога (неизвестному — общий `bolt`, не «человек»); голое имя —
 * сначала подсказки `AGENT_HINTS`, потом ключ с фирменным глифом (`devin`,
 * `pi-glm`) или ключ уже загруженного каталога (`catalog`); остальное —
 * человек. `catalog` — ключи `GET /api/harnesses`, когда они уже есть в сторе.
 */
export function harnessOf(
  actor: string | null | undefined,
  catalog?: Iterable<string>,
): string | null {
  if (!actor) return null
  const key = actor.trim().toLowerCase()
  if (!key) return null
  if (key === 'me' || key === 'human') return 'human'
  if (key.startsWith('agent:')) {
    const bare = key.slice(6)
    return bare && bare !== 'me' ? bare : 'human'
  }
  for (const [hint, harness] of HINTS) {
    if (key.includes(hint)) return harness
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
 * Харнесс, способный исполнить этап роя или прямой маршрут: включён и у него
 * есть команда `argv`. Ручная выдача (`kind=manual`, `argv=null`) этап не
 * запустит — в выбор исполнителя она не входит.
 */
export function runnableHarness(item: { enabled: boolean; argv: string[] | null }): boolean {
  return item.enabled && Array.isArray(item.argv) && item.argv.length > 0
}
