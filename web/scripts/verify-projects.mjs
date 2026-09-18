/**
 * Проверка сценария «добавить / скрыть / вернуть / убрать репозиторий» через
 * страницу настроек (`/settings/repos` — раздел «Репозитории»; модалки настроек
 * больше нет, поэтому скрипт заходит прямо по адресу, а не кликает кнопку в
 * шапке): скрытый проект возвращается на доску тумблером в разделе
 * «Скрыты с доски» и остаётся возвращённым после перезагрузки страницы
 * (возврат — это запись в базе, а не только вид; listik-54be).
 * Добавление и правка живут в одном окне (listik-zmos): его открывают кнопкой
 * «Добавить репозиторий» и действием правки у строки — инлайновых форм в списке
 * больше нет, поэтому поля скрипт ищет внутри формы окна, а не в списке.
 * Работает с живой страницей (dev или прод) и настоящим API Listik, поэтому
 * трогает базу: используйте временный каталог и slug вида `listik-check-*`.
 *
 * Запуск: node scripts/verify-projects.mjs "http://localhost:5173/?token=<токен>" /tmp/папка [slug]
 */
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { cdpTarget, connect, sleep, startChrome } from './lib/browser-harness.mjs'

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
const chrome = startChrome(chromePath, port, profile, '1600,1000')

const client = connect(await cdpTarget(port))
await client.ready
const { socket, send, events } = client
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
  return result.result.value
}

/** Ввод в поле формы окна: UiInput слушает input, значение ставим нативным сеттером. */
const typeInto = (index, value) => evaluate(`(() => {
  const input = document.querySelectorAll('.ui-form-modal__form input')[${index}];
  if (!input) return false;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, ${JSON.stringify(value)});
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()`)

/** Адрес раздела «Репозитории» с тем же токеном, что передали скрипту: у страницы
 *  настроек свой путь, открывать её кликом по шапке больше не нужно. */
const settingsUrl = (() => {
  const target = new URL(url)
  target.pathname = '/settings/repos'
  return target.toString()
})()

const report = {}
await send('Runtime.enable')
await send('Network.enable')
await send('Page.enable')
await send('Page.navigate', { url: settingsUrl })
await sleep(4000)

// раздел «Репозитории» открыт самим адресом
report.settingsOpen = await evaluate(`Boolean(document.querySelector('.listik-settings'))`)
report.rowsBefore = await evaluate(`document.querySelectorAll('.ui-entity-card').length`)

// добавить каталог: кнопка открывает общее окно, поля — внутри него
await evaluate(
  `[...document.querySelectorAll('button')]
    .find((b) => b.textContent.trim() === 'Добавить репозиторий')?.click()`,
)
await sleep(1200)
report.addFormFields = await evaluate(
  `document.querySelectorAll('.ui-form-modal__form input').length`,
)
await typeInto(0, folder)
await typeInto(1, slug)
await sleep(500)
/** Кнопка отправки окна — единственная `type=submit` с привязкой к форме. */
const submitForm = `document.querySelector('button[type=submit][form]')`
report.submitLabel = await evaluate(`${submitForm}?.textContent.trim()`)
await evaluate(`${submitForm}?.click()`)
await sleep(3000)
report.afterAdd = await evaluate(`JSON.stringify({
  rows: document.querySelectorAll('.ui-entity-card').length,
  hasSlug: [...document.querySelectorAll('.ui-entity-card__title')].some((el) => el.textContent.trim() === ${JSON.stringify(slug)}),
  error: document.querySelector('.ui-modal .ui-alert')?.textContent?.trim()?.slice(0, 120),
})`)

// правка открывает то же окно: два поля (название и путь), slug — только подпись
await evaluate(`(() => {
  const card = [...document.querySelectorAll('.ui-entity-card')]
    .find((el) => el.querySelector('.ui-entity-card__title')?.textContent.trim() === ${JSON.stringify(slug)});
  const btn = [...(card?.querySelectorAll('button') ?? [])].find((b) => (b.getAttribute('aria-label') || '').startsWith('Изменить проект'));
  btn?.click();
})()`)
await sleep(1200)
report.editForm = await evaluate(`JSON.stringify({
  fields: document.querySelectorAll('.ui-form-modal__form input').length,
  slugShown: [...document.querySelectorAll('.ui-form-modal__form code')].some((el) => el.textContent.trim() === ${JSON.stringify(slug)}),
  submit: document.querySelector('button[type=submit][form]')?.textContent.trim(),
})`)
await evaluate(
  `[...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Отмена')?.click()`,
)
await sleep(800)
// инлайновых форм в списке не осталось — поля есть только внутри окна
report.inlineInputs = await evaluate(`document.querySelectorAll('.listik-projects input').length`)

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
await send('Page.navigate', { url: settingsUrl })
await sleep(4000)
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

report.consoleErrors = events
  .filter((event) => event.method === 'Runtime.exceptionThrown')
  .map((event) => event.params?.exceptionDetails?.exception?.description ?? 'исключение')
report.failedRequests = events
  .filter((event) => event.method === 'Network.responseReceived' && event.params?.response?.status >= 400)
  .map((event) => `${event.params.response.status} ${event.params.response.url}`)
console.log(JSON.stringify(report, null, 2))

socket.close()
chrome.kill()
try {
  rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
} catch {
  /* временный профиль уберёт система */
}
