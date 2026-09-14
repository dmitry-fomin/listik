/**
 * Проверка сценария «добавить / скрыть / вернуть / убрать репозиторий» через
 * интерфейс настроек: скрытый проект возвращается на доску тумблером в разделе
 * «Скрыты с доски» и остаётся возвращённым после перезагрузки страницы
 * (возврат — это запись в базе, а не только вид; listik-54be).
 * Работает с живой страницей (dev или прод) и настоящим API Listik, поэтому
 * трогает базу: используйте временный каталог и slug вида `listik-check-*`.
 *
 * Запуск: node scripts/verify-projects.mjs "http://localhost:5173/?token=<токен>" /tmp/папка [slug]
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const url = process.argv[2]
const folder = process.argv[3]
const slug = process.argv[4] ?? 'listik-check-repo'
if (!url || !folder) {
  console.error('нужно: node scripts/verify-projects.mjs "<url с токеном>" <каталог> [slug]')
  process.exit(2)
}

const chromePath =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const port = 9900 + Math.floor(Math.random() * 90)
const profile = mkdtempSync(join(tmpdir(), 'listik-projects-'))
const chrome = spawn(
  chromePath,
  [
    '--headless=old',
    '--disable-gpu',
    '--window-size=1600,1000',
    '--no-sandbox',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    'about:blank',
  ],
  { stdio: 'ignore' },
)

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function target() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`)
      const page = (await response.json()).find((item) => item.type === 'page')
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl
    } catch {
      /* chrome ещё поднимается */
    }
    await sleep(250)
  }
  throw new Error('не дождались CDP-таргета Chrome')
}

const socket = new WebSocket(await target())
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true })
  socket.addEventListener('error', reject, { once: true })
})

let nextId = 1
const pending = new Map()
const consoleErrors = []
const failed = []
socket.addEventListener('message', (message) => {
  const data = JSON.parse(message.data)
  if (data.id && pending.has(data.id)) {
    const { resolve, reject } = pending.get(data.id)
    pending.delete(data.id)
    data.error ? reject(new Error(JSON.stringify(data.error))) : resolve(data.result)
    return
  }
  if (data.method === 'Runtime.exceptionThrown') {
    consoleErrors.push(data.params?.exceptionDetails?.exception?.description ?? 'исключение')
  }
  if (data.method === 'Network.responseReceived' && data.params?.response?.status >= 400) {
    failed.push(`${data.params.response.status} ${data.params.response.url}`)
  }
})

const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = nextId++
    pending.set(id, { resolve, reject })
    socket.send(JSON.stringify({ id, method, params }))
  })

async function evaluate(expression) {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
  return result.result.value
}

/** Ввод в поле кита: UiInput слушает input, значение ставим нативным сеттером. */
const typeInto = (index, value) => evaluate(`(() => {
  const input = document.querySelectorAll('.ui-modal input')[${index}];
  if (!input) return false;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, ${JSON.stringify(value)});
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()`)

/** Открыть настройки: кнопка в шапке подписана «Настройки» (тултип «Репозитории, …» —
 *  не текст кнопки, искать по нему нельзя). Вкладка «Репозитории» открыта по умолчанию. */
const openSettings = `[...document.querySelectorAll('button')]
  .find((b) => b.textContent.trim() === 'Настройки')?.click()`

const report = {}
await send('Runtime.enable')
await send('Network.enable')
await send('Page.enable')
await send('Page.navigate', { url })
await sleep(4000)

// открыть настройки репозиториев
await evaluate(openSettings)
await sleep(2000)
report.settingsOpen = await evaluate(`Boolean(document.querySelector('.ui-modal'))`)
report.rowsBefore = await evaluate(`document.querySelectorAll('.ui-entity-card').length`)

// добавить каталог
await typeInto(0, folder)
await typeInto(1, slug)
await sleep(500)
report.addDisabledBefore = await evaluate(
  `[...document.querySelectorAll('.ui-modal button')].find((b) => b.textContent.includes('Добавить'))?.disabled`,
)
await evaluate(
  `[...document.querySelectorAll('.ui-modal button')].find((b) => b.textContent.includes('Добавить'))?.click()`,
)
await sleep(3000)
report.afterAdd = await evaluate(`JSON.stringify({
  rows: document.querySelectorAll('.ui-entity-card').length,
  hasSlug: [...document.querySelectorAll('.ui-entity-card__title')].some((el) => el.textContent.trim() === ${JSON.stringify(slug)}),
  error: document.querySelector('.ui-modal .ui-alert')?.textContent?.trim()?.slice(0, 120),
})`)

// скрыть добавленный проект тумблером
await evaluate(`(() => {
  const card = [...document.querySelectorAll('.ui-entity-card')]
    .find((el) => el.querySelector('.ui-entity-card__title')?.textContent.trim() === ${JSON.stringify(slug)});
  card?.querySelector('[role=switch]')?.click();
})()`)
await sleep(3000)
report.afterHide = await evaluate(`JSON.stringify({
  visible: [...document.querySelectorAll('.ui-entity-card__title')].map((el) => el.textContent.trim()).includes(${JSON.stringify(slug)}),
  hiddenSection: [...document.querySelectorAll('.listik-section__title')].map((el) => el.textContent.trim()),
})`)

// вернуть скрытый проект на доску тумблером в разделе «Скрыты с доски»
report.hiddenSwitch = await evaluate(`(() => {
  const card = [...document.querySelectorAll('.ui-entity-card')]
    .find((el) => el.querySelector('.ui-entity-card__title')?.textContent.trim() === ${JSON.stringify(slug)});
  const toggle = card?.querySelector('[role=switch]');
  return JSON.stringify({ found: Boolean(toggle), checked: toggle?.getAttribute('aria-checked') });
})()`)
await evaluate(`(() => {
  const card = [...document.querySelectorAll('.ui-entity-card')]
    .find((el) => el.querySelector('.ui-entity-card__title')?.textContent.trim() === ${JSON.stringify(slug)});
  card?.querySelector('[role=switch]')?.click();
})()`)
await sleep(3000)
const boardState = `(() => {
  const slug = ${JSON.stringify(slug)};
  const inSection = (needle) => {
    const section = [...document.querySelectorAll('.listik-projects__list')]
      .find((el) => (el.querySelector('.listik-section__title')?.textContent || '').includes(needle));
    if (!section) return false;
    return [...section.querySelectorAll('.ui-entity-card__title')].some((el) => el.textContent.trim() === slug);
  };
  return JSON.stringify({ onBoard: inSection('На доске'), inHidden: inSection('Скрыты') });
})()`
report.afterRestore = await evaluate(boardState)

// состояние возврата должно пережить перезагрузку: возврат — не только вид, но и база
await send('Page.navigate', { url })
await sleep(4000)
await evaluate(openSettings)
await sleep(2000)
report.afterReload = await evaluate(boardState)

// удалить: подтверждение → убрать
await evaluate(`(() => {
  const card = [...document.querySelectorAll('.ui-entity-card')]
    .find((el) => el.querySelector('.ui-entity-card__title')?.textContent.trim() === ${JSON.stringify(slug)});
  const btn = [...(card?.querySelectorAll('button') ?? [])].find((b) => (b.getAttribute('aria-label') || '').startsWith('Удалить проект'));
  btn?.click();
})()`)
await sleep(1200)
report.confirmShown = await evaluate(
  `[...document.querySelectorAll('[role=alertdialog], .ui-modal')].some((el) => el.textContent.includes('Убрать репозиторий'))`,
)
await evaluate(
  `[...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Убрать')?.click()`,
)
await sleep(3000)
report.afterRemove = await evaluate(`JSON.stringify({
  stillThere: [...document.querySelectorAll('.ui-entity-card__title')].some((el) => el.textContent.trim() === ${JSON.stringify(slug)}),
  rows: document.querySelectorAll('.ui-entity-card').length,
})`)

report.consoleErrors = consoleErrors
report.failedRequests = failed
console.log(JSON.stringify(report, null, 2))

socket.close()
chrome.kill()
try {
  rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
} catch {
  /* временный профиль уберёт система */
}
