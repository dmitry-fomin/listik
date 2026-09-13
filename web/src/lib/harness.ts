/**
 * Харнесс по ключу актора/держателя — те же подстроки и порядок, что на сервере
 * (`listik/actors.py AGENT_HINTS`), чтобы доска и сервер сходились в разборе.
 */
export type HarnessKey = 'claude' | 'dsh' | 'codex' | 'grok' | 'gemini' | 'human'

const HINTS: [string, HarnessKey][] = [
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
  human: 'человек',
}

export function harnessOf(actor: string | null | undefined): HarnessKey | null {
  if (!actor) return null
  const key = actor.trim().toLowerCase()
  if (!key) return null
  for (const [hint, harness] of HINTS) {
    if (key.includes(hint)) return harness
  }
  return 'human'
}
