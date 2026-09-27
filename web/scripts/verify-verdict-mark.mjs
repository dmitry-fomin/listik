/**
 * Проверка цвета маркера вердикта в ленте панели задачи — `verdictMark`
 * (`web/src/lib/dictionaries.ts`), шаг listik-m88l, порция a.
 *
 * Поле `verdict` от сервера (`pass`/`fail`) главнее текста; без поля или при `null`
 * работает прежний фолбэк по префиксу текста (`красн`/`red`/`fail` → danger).
 * Без браузера, мока и `web/dist`: функция импортируется прямо из исходника.
 *
 * Запуск (из `web/`): node scripts/verify-verdict-mark.mjs
 *
 * Печатает JSON-отчёт `{ cases: [{ name, ok, want, got }] }`. Код возврата 1, если хоть
 * один кейс не прошёл (включая исключение при вызове) или скрипт упал.
 */
import { verdictMark } from '../src/lib/dictionaries.ts'

const DANGER = { icon: 'close', bg: 'var(--danger-50)', color: 'var(--danger-700)' }
const SUCCESS = { icon: 'check', bg: 'var(--success-50)', color: 'var(--success-700)' }

const CASES = [
  ['поле fail', { text: 'VERDICT: FAIL\n1. тест x падает', verdict: 'fail' }, DANGER],
  ['поле pass', { text: 'VERDICT: PASS', verdict: 'pass' }, SUCCESS],
  ['поле pass главнее текста «красный»', { text: 'красный', verdict: 'pass' }, SUCCESS],
  ['поле fail главнее текста «зелёный»', { text: 'зелёный', verdict: 'fail' }, DANGER],
  ['фолбэк при null: «красный: тест падает»', { text: 'красный: тест падает', verdict: null }, DANGER],
  ['фолбэк при null: «ок, собирай»', { text: 'ок, собирай', verdict: null }, SUCCESS],
  ['фолбэк без поля: «  RED: сборка»', { text: '  RED: сборка' }, DANGER],
  ['фолбэк без поля: «fail»', { text: 'fail' }, DANGER],
  ['фолбэк без поля: «зелёный»', { text: 'зелёный' }, SUCCESS],
  ['фолбэк — префикс, не подстрока: «сборка красная»', { text: 'сборка красная' }, SUCCESS],
]

const cases = CASES.map(([name, comment, want]) => {
  let got
  try {
    got = verdictMark(comment)
  } catch (error) {
    return { name, ok: false, want, got: { error: String(error) } }
  }
  const ok = ['icon', 'bg', 'color'].every((key) => got?.[key] === want[key])
  return { name, ok, want, got }
})

console.log(JSON.stringify({ cases }, null, 2))
if (cases.some((c) => !c.ok)) process.exitCode = 1
