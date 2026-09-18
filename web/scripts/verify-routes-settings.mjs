/**
 * Проверка раздела «Маршруты» страницы настроек (`/settings/routes` →
 * `RoutesSettings.vue`; страница открывается прямым адресом, вкладок и модалки
 * настроек больше нет):
 * список не пуст и в нём есть обе группы («Конвейеры»/«Прямая выдача»), клик по
 * строке выбирает маршрут, перестановка кнопкой ▲ (кит `UiRecordList`) пишется
 * в базу — порядок переживает перезагрузку страницы, а не живёт только в
 * памяти вкладки. Заодно проверяет тело запроса `POST /api/routes/reorder`
 * (перехват `window.fetch` на странице): в `keys` приходят ключи ВСЕХ
 * маршрутов, а не только той группы, где случилась перестановка.
 *
 * Порция `e` (карточка выбранного маршрута) добавляет проверку автосохранения
 * шапки: серия «нажатий» в поле «Подпись» без потери фокуса даёт ровно один
 * `PATCH /api/routes/<key>` — с единственным ключом `hint` (не все четыре поля
 * «на всякий случай»), — статус в карточке показывает «Сохранено»; переключатель
 * «Показывать автору» и уровень (семь кнопок-глифов) сохраняются сразу, тоже
 * по одному ключу за раз (`visible`/`icon`). Все три поля возвращаются к
 * исходному значению по ходу сценария тем же путём (ввод/клик), а не только
 * прямым PATCH — тем самым заодно проверяется round-trip.
 *
 * Работает с живой страницей (dev или прод) и настоящим API Listik, поэтому
 * трогает базу маршрутов: исходный порядок возвращается и при успехе сценария
 * (кнопкой ▼ по ходу проверки), и при падении посреди него (прямым
 * `POST /api/routes/reorder` на подобранный ранее исходный порядок) — этот
 * откат живёт в `finalize()`, общей для обычного `finally` и для `SIGINT`/
 * `SIGTERM`: голый `try/finally` не сработал бы на Ctrl-C (Node завершает
 * процесс по умолчанию раньше, чем размотался бы стек), поэтому оба сигнала
 * перехвачены отдельно и вызывают тот же откат перед выходом. Та же схема —
 * `hintRestoreNeeded`/`visibleRestoreNeeded`/`iconRestoreNeeded` и прямой
 * `PATCH` в `finalize()` — страхует карточку маршрута.
 *
 * Порция `f` (карточка прямой выдачи, `RouteDirectCard.vue`) добавляет проверку
 * редактора argv на маршруте `dsh`: аргументы и промпт на экране совпадают с
 * `GET /api/routes`, держатель показан только на чтение, без правок «Сохранить»
 * выключена; правка промпта включает её, `{foo}` в промпте и отдельно в
 * аргументе — снова выключает, называет подстановку рядом с кнопкой и метит
 * поле `aria-invalid`; уход с несохранённой карточки спрашивает подтверждение;
 * сохранение шлёт ровно один `PATCH` с единственным ключом `command` — массивом,
 * новый промпт переживает перезагрузку страницы, а переставленный кнопкой ▼
 * аргумент — сохранение (порядок сверяется по ответу сервера). Исходная команда
 * возвращается прямым `PATCH` в `finalize()` и на успехе, и на падении.
 *
 * Запуск: node scripts/verify-routes-settings.mjs "http://localhost:5173/?token=<токен>"
 * Печатает JSON-отчёт и «ок»/«ошибка: …» последней строкой; код выхода
 * ненулевой, если сценарий не прошёл (или прерван сигналом).
 */
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { cdpTarget, connect, sleep, startChrome } from './lib/browser-harness.mjs'

/** Откат по сигналу не должен виснуть на неотвечающей странице/сети. */
function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`таймаут ${ms}мс`)), ms)),
  ])
}

const url = process.argv[2]
if (!url) {
  console.error('нужно: node scripts/verify-routes-settings.mjs "<url с токеном>"')
  process.exit(2)
}

const chromePath =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const port = 9990 + Math.floor(Math.random() * 90)
const profile = mkdtempSync(join(tmpdir(), 'listik-routes-settings-'))
const chrome = startChrome(chromePath, port, profile, '1600,1100')

const client = connect(await cdpTarget(port))
await client.ready
const { send, evaluate, consoleErrors } = client

/* ── перехват fetch на странице: копится в window.__routesReorderCalls и
 * window.__routesPatchCalls (последний — тела PATCH /api/routes/<key>,
 * автосохранение карточки, порция `e`), добавлен как «скрипт на новый
 * документ» — переживает Page.navigate сам, повторно вставлять после
 * перезагрузки не нужно. */
const REORDER_PATCH = `(() => {
  window.__routesReorderCalls = window.__routesReorderCalls ?? [];
  window.__routesPatchCalls = window.__routesPatchCalls ?? [];
  if (window.__routesFetchPatched) return;
  window.__routesFetchPatched = true;
  const original = window.fetch.bind(window);
  window.fetch = function (input, init) {
    try {
      const reqUrl = typeof input === 'string' ? input : (input && input.url) || '';
      if (reqUrl.includes('/api/routes/reorder') && init && typeof init.body === 'string') {
        window.__routesReorderCalls.push(JSON.parse(init.body));
      }
      const patchMatch = reqUrl.match(/\\/api\\/routes\\/([^/?]+)$/);
      if (patchMatch && init && init.method === 'PATCH' && typeof init.body === 'string') {
        window.__routesPatchCalls.push({ key: patchMatch[1], body: JSON.parse(init.body) });
      }
    } catch (_e) { /* тело не JSON — не мешаем запросу */ }
    return original(input, init);
  };
})()`

/** Адрес раздела «Маршруты» с тем же токеном, что передали скрипту. */
const routesUrl = (() => {
  const target = new URL(url)
  target.pathname = '/settings/routes'
  return target.toString()
})()

const groupRows = (title) => `(() => {
  const group = [...document.querySelectorAll('.listik-routes-settings__group')]
    .find((el) => el.querySelector('.listik-section__title')?.textContent.trim() === ${JSON.stringify(title)});
  if (!group) return null;
  return [...group.querySelectorAll('.ui-record-list__row')].map((row) => ({
    key: row.querySelector('.listik-routes-row .listik-mono')?.textContent.trim() ?? null,
    title: row.querySelector('.listik-routes-row__title')?.textContent.trim() ?? null,
  }));
})()`

const clickRow = (title, index) => `(() => {
  const group = [...document.querySelectorAll('.listik-routes-settings__group')]
    .find((el) => el.querySelector('.listik-section__title')?.textContent.trim() === ${JSON.stringify(title)});
  const rows = group ? [...group.querySelectorAll('.ui-record-list__row')] : [];
  const btn = rows[${index}]?.querySelector('.listik-routes-row');
  if (!btn) return false;
  btn.click();
  return true;
})()`

const clickStep = (title, index, dir) => `(() => {
  const group = [...document.querySelectorAll('.listik-routes-settings__group')]
    .find((el) => el.querySelector('.listik-section__title')?.textContent.trim() === ${JSON.stringify(title)});
  const rows = group ? [...group.querySelectorAll('.ui-record-list__row')] : [];
  const row = rows[${index}];
  const label = ${JSON.stringify(dir === 'up' ? 'Переместить выше' : 'Переместить ниже')};
  const btn = row ? [...row.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label) : null;
  if (!btn || btn.disabled) return false;
  btn.click();
  return true;
})()`

const detailTitle = `document.querySelector('.listik-routes-settings__card input')?.value ?? null`

/** Открыть раздел «Маршруты»: у него свой адрес, кликать нечего. */
const openTab = async () => {
  await send('Page.navigate', { url: routesUrl })
  await sleep(4000)
}

/** Прямой POST в обход UI — страховка `finally`, когда сценарий упал раньше клика ▼. */
const forceReorder = (keys) => `(async () => {
  try {
    const token = localStorage.getItem('listik.token') ?? ''
    const response = await fetch('/api/routes/reorder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify({ keys: ${JSON.stringify(keys)} }),
    })
    return response.ok
  } catch (_e) {
    return false
  }
})()`

/** Прямой PATCH записи в обход UI — для той же цели у карточки автосохранения (порция `e`). */
const forcePatch = (key, body) => `(async () => {
  try {
    const token = localStorage.getItem('listik.token') ?? ''
    const response = await fetch('/api/routes/' + ${JSON.stringify(key)}, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify(${JSON.stringify(body)}),
    })
    return response.ok
  } catch (_e) {
    return false
  }
})()`

/** Текущая запись маршрута по ключу — читает `GET /api/routes` прямо со страницы (свой токен). */
const fetchRoute = (key) => `(async () => {
  try {
    const token = localStorage.getItem('listik.token') ?? ''
    const response = await fetch('/api/routes', { headers: { Authorization: 'Bearer ' + token } })
    if (!response.ok) return null
    const payload = await response.json()
    const data = payload.data ?? payload
    return (data.routes || []).find((r) => r.key === ${JSON.stringify(key)}) ?? null
  } catch (_e) {
    return null
  }
})()`

/**
 * Печатает символы `value` в поле `input[index]` карточки одним нативным
 * сеттером на символ + событие `input` — без `blur`, чтобы проверить именно
 * debounce (600мс после последней «клавиши»), а не сохранение по потере
 * фокуса. Серия быстрых вызовов должна дать один `PATCH`, а не по символу.
 */
const typeCardField = (index, value) => `(async () => {
  const input = document.querySelectorAll('.listik-routes-settings__card input')[${index}]
  if (!input) return false
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  setter.call(input, '')
  input.dispatchEvent(new Event('input', { bubbles: true }))
  let acc = ''
  for (const ch of ${JSON.stringify(value)}) {
    acc += ch
    setter.call(input, acc)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise((r) => setTimeout(r, 25))
  }
  return true
})()`

const clickSwitch = `(() => {
  const btn = document.querySelector('.listik-routes-settings__card .ui-switch__track')
  if (!btn) return false
  btn.click()
  return true
})()`

const levelState = `(() => {
  const buttons = [...document.querySelectorAll('.listik-routes-settings__card [role="radiogroup"] [role="radio"]')]
  return { count: buttons.length, checked: buttons.findIndex((b) => b.getAttribute('aria-checked') === 'true') }
})()`

const clickLevel = (index) => `(() => {
  const buttons = [...document.querySelectorAll('.listik-routes-settings__card [role="radiogroup"] [role="radio"]')]
  const btn = buttons[${index}]
  if (!btn) return false
  btn.click()
  return true
})()`

/* ── карточка прямой выдачи (порция `f`): argv, промпт, явное «Сохранить» ── */

/** Состояние кнопки «Сохранить» и текста причины рядом с ней. */
const directSaveState = `(() => {
  const button = document.querySelector('.listik-route-direct__save')
  const reason = document.querySelector('.listik-route-direct__reason')
  return {
    present: Boolean(button),
    disabled: button ? button.disabled : null,
    reason: reason ? reason.textContent.trim() : '',
  }
})()`

const clickDirectSave = `(() => {
  const button = document.querySelector('.listik-route-direct__save')
  if (!button || button.disabled) return false
  button.click()
  return true
})()`

/** Значение поля целиком через нативный сеттер: v-model слушает событие `input`. */
const setFieldValue = (selector, value) => `(() => {
  const el = document.querySelector(${JSON.stringify(selector)})
  if (!el) return false
  const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, ${JSON.stringify(value)})
  el.dispatchEvent(new Event('input', { bubbles: true }))
  return true
})()`

const fieldValue = (selector) => `document.querySelector(${JSON.stringify(selector)})?.value ?? null`

const PROMPT_SELECTOR = 'textarea.listik-route-direct__prompt'
const ARG_INPUTS = '.listik-route-direct__args .listik-route-direct__arg-input input'

const directArgValues = `[...document.querySelectorAll('${ARG_INPUTS}')].map((el) => el.value)`

const setDirectArg = (index, value) => `(() => {
  const el = [...document.querySelectorAll('${ARG_INPUTS}')][${index}]
  if (!el) return false
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(el, ${JSON.stringify(value)})
  el.dispatchEvent(new Event('input', { bubbles: true }))
  return true
})()`

/** Перестановка аргумента кнопкой ▲/▼ кита — внутри списка аргументов карточки. */
const clickArgStep = (index, dir) => `(() => {
  const rows = [...document.querySelectorAll('.listik-route-direct__args .ui-record-list__row')]
  const row = rows[${index}]
  const label = ${JSON.stringify(dir === 'up' ? 'Переместить выше' : 'Переместить ниже')}
  const button = row ? [...row.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label) : null
  if (!button || button.disabled) return false
  button.click()
  return true
})()`

const ariaInvalid = (selector) =>
  `document.querySelector(${JSON.stringify(selector)})?.getAttribute('aria-invalid') === 'true'`

const argAriaInvalid = (index) =>
  `[...document.querySelectorAll('${ARG_INPUTS}')][${index}]?.getAttribute('aria-invalid') === 'true'`

const leaveDialogShown = `[...document.querySelectorAll('.ui-modal')].some((el) => el.textContent.includes('Уйти и потерять правки?'))`

const clickByText = (text) => `(() => {
  const button = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === ${JSON.stringify(text)})
  if (!button) return false
  button.click()
  return true
})()`

const patchCallsSince = (from) => `window.__routesPatchCalls.slice(${from})`
const patchCallsCount = `window.__routesPatchCalls.length`
const saveStatusText = `document.querySelector('.listik-routes-settings__card .ui-save-status')?.textContent ?? ''`

const report = { ok: false }
let originalPipelineKeys = null
let restoreNeeded = false
let cardKey = null
let cardOriginal = null
let hintRestoreNeeded = false
let visibleRestoreNeeded = false
let iconRestoreNeeded = false
/* Команда прямого маршрута: тронули — возвращаем в finalize() и на успехе, и на падении. */
let directKey = null
let directOriginal = null
let directRestoreNeeded = false
let finalized = false

/**
 * Откат и уборка — общая точка выхода что для обычного завершения (`finally`),
 * что для `SIGINT`/`SIGTERM`: перехват сигналов ниже вызывает эту же функцию,
 * потому что голый `try/finally` на Ctrl-C не сработал бы (Node завершает
 * процесс раньше, чем размотался бы стек вызовов).
 */
async function finalize() {
  if (finalized) return
  finalized = true
  if (restoreNeeded && originalPipelineKeys) {
    try {
      await withTimeout(
        (async () => {
          await openTab()
          const direct = (await evaluate(groupRows('Прямая выдача'))) ?? []
          const keys = [...originalPipelineKeys, ...direct.map((row) => row.key)]
          report.restoreFallbackOk = await evaluate(forceReorder(keys))
        })(),
        10000,
      )
    } catch (restoreError) {
      report.restoreFallbackOk = false
      report.restoreError = String(restoreError?.stack ?? restoreError)
    }
  }
  if ((hintRestoreNeeded || visibleRestoreNeeded || iconRestoreNeeded) && cardKey && cardOriginal) {
    try {
      await withTimeout(
        (async () => {
          const body = {}
          if (hintRestoreNeeded) body.hint = cardOriginal.hint
          if (visibleRestoreNeeded) body.visible = cardOriginal.visible
          if (iconRestoreNeeded) body.icon = cardOriginal.icon ?? null
          report.cardRestoreFallbackOk = await evaluate(forcePatch(cardKey, body))
        })(),
        10000,
      )
    } catch (restoreError) {
      report.cardRestoreFallbackOk = false
      report.cardRestoreError = String(restoreError?.stack ?? restoreError)
    }
  }
  if (directRestoreNeeded && directKey && directOriginal?.command) {
    try {
      await withTimeout(
        (async () => {
          report.directRestoreOk = await evaluate(forcePatch(directKey, { command: directOriginal.command }))
        })(),
        10000,
      )
    } catch (restoreError) {
      report.directRestoreOk = false
      report.directRestoreError = String(restoreError?.stack ?? restoreError)
    }
  }
  try {
    client.socket.close()
  } catch {
    /* сокет мог уже закрыться сам */
  }
  try {
    chrome.kill()
  } catch {
    /* процесс мог уже завершиться сам */
  }
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  } catch {
    /* временный профиль уберёт система */
  }
}

/** Ctrl-C/kill посреди сценария — тот же откат, что и в finally, до выхода. */
async function handleSignal(signal) {
  report.interrupted = signal
  await finalize()
  console.log(JSON.stringify(report, null, 2))
  console.error(`прервано сигналом ${signal} — база возвращена к исходному порядку, если он был тронут`)
  process.exit(130)
}
process.on('SIGINT', () => { void handleSignal('SIGINT') })
process.on('SIGTERM', () => { void handleSignal('SIGTERM') })

try {
  await send('Runtime.enable')
  await send('Page.enable')
  await send('Page.addScriptToEvaluateOnNewDocument', { source: REORDER_PATCH })

  await send('Page.navigate', { url: routesUrl })
  await sleep(4000)
  await evaluate(REORDER_PATCH) // сама страница уже загружена раньше add-script — патчим и напрямую

  report.settingsOpen = await evaluate(`Boolean(document.querySelector('.listik-settings'))`)

  const pipelineBefore = await evaluate(groupRows('Конвейеры'))
  const directBefore = await evaluate(groupRows('Прямая выдача'))
  report.groupsPresent = {
    pipeline: Array.isArray(pipelineBefore) && pipelineBefore.length > 0,
    direct: Array.isArray(directBefore) && directBefore.length > 0,
  }
  if (!Array.isArray(pipelineBefore) || pipelineBefore.length < 2) {
    throw new Error(`нужно хотя бы два маршрута-конвейера для перестановки: ${JSON.stringify(pipelineBefore)}`)
  }
  originalPipelineKeys = pipelineBefore.map((row) => row.key)
  const directKeys = (directBefore ?? []).map((row) => row.key)

  // выбрать вторую строку — карточка справа должна показать её заголовок
  report.selectClicked = await evaluate(clickRow('Конвейеры', 1))
  await sleep(500)
  report.selectedTitleShown = await evaluate(detailTitle)
  report.selectionMatches = report.selectedTitleShown === pipelineBefore[1].title

  // переставить кнопкой ▲: с этого момента база тронута — при любом сбое ниже
  // finally обязан вернуть исходный порядок.
  restoreNeeded = true
  report.stepClicked = await evaluate(clickStep('Конвейеры', 1, 'up'))
  await sleep(1500)

  const afterMove = await evaluate(groupRows('Конвейеры'))
  const expectedAfterMove = [originalPipelineKeys[1], originalPipelineKeys[0], ...originalPipelineKeys.slice(2)]
  report.afterMove = afterMove?.map((row) => row.key) ?? null
  report.orderChangedCorrectly = JSON.stringify(report.afterMove) === JSON.stringify(expectedAfterMove)

  const reorderCalls = await evaluate('window.__routesReorderCalls ?? []')
  const lastCall = reorderCalls[reorderCalls.length - 1]
  report.reorderBody = lastCall ?? null
  report.reorderHasAllKeys =
    Boolean(lastCall) &&
    Array.isArray(lastCall.keys) &&
    lastCall.keys.length === pipelineBefore.length + directKeys.length &&
    directKeys.every((key) => lastCall.keys.includes(key)) &&
    JSON.stringify(lastCall.keys.slice(0, pipelineBefore.length)) === JSON.stringify(expectedAfterMove)

  // перезагрузка страницы — порядок обязан пережить её (записан в базу, а не только в стор вкладки)
  await openTab()
  const afterReload = await evaluate(groupRows('Конвейеры'))
  report.afterReload = afterReload?.map((row) => row.key) ?? null
  report.persistedAfterReload = JSON.stringify(report.afterReload) === JSON.stringify(expectedAfterMove)

  // вернуть исходный порядок: ▼ на строке 0 — обратная перестановка той же пары
  report.restoreClicked = await evaluate(clickStep('Конвейеры', 0, 'down'))
  await sleep(1500)
  const afterRestore = await evaluate(groupRows('Конвейеры'))
  report.afterRestore = afterRestore?.map((row) => row.key) ?? null
  report.restoredCorrectly = JSON.stringify(report.afterRestore) === JSON.stringify(originalPipelineKeys)
  restoreNeeded = !report.restoredCorrectly

  /*
   * ── карточка выбранного маршрута (порция `e`): шапка сохраняется сама.
   * `Page.navigate` выше перезагрузил страницу — выбор маршрута живёт только в
   * памяти вкладки (`selectedKey` в `RoutesSettings.vue`), поэтому после
   * перезагрузки справа снова «Выбери маршрут слева»: строку нужно выбрать
   * заново. Порядок к этому моменту уже восстановлен (`afterRestore`), поэтому
   * строка 1 — снова тот же маршрут, что и в начале сценария.
   */
  report.cardRowReselected = await evaluate(clickRow('Конвейеры', 1))
  await sleep(500)
  report.cardTitleShown = await evaluate(detailTitle)

  cardKey = originalPipelineKeys[1]
  cardOriginal = await evaluate(fetchRoute(cardKey))
  if (!cardOriginal) throw new Error(`не нашли запись ${cardKey} для проверки карточки`)

  // 1) «Подпись»: серия «нажатий» без blur — ровно один PATCH после debounce, только hint
  const hintProbe = `${cardOriginal.hint} · автотест ${Date.now()}`
  hintRestoreNeeded = true
  const callsBeforeHint = (await evaluate(patchCallsCount)) ?? 0
  report.hintTyped = await evaluate(typeCardField(1, hintProbe))
  await sleep(1000)
  const hintCalls = ((await evaluate(patchCallsSince(callsBeforeHint))) ?? []).filter(
    (call) => call.key === cardKey && 'hint' in call.body,
  )
  report.hintCallsCount = hintCalls.length
  report.hintCallSingleKey = hintCalls.length > 0 && Object.keys(hintCalls[hintCalls.length - 1].body).length === 1
  report.hintCallValueMatches = hintCalls[hintCalls.length - 1]?.body.hint === hintProbe
  report.hintSavedShown = (await evaluate(saveStatusText)).includes('Сохранено')

  // вернуть исходную подпись тем же путём
  const callsBeforeHintRestore = (await evaluate(patchCallsCount)) ?? 0
  await evaluate(typeCardField(1, cardOriginal.hint))
  await sleep(1000)
  const hintRestoreCalls = (await evaluate(patchCallsSince(callsBeforeHintRestore))) ?? []
  report.hintRestored = hintRestoreCalls.some(
    (call) => call.key === cardKey && call.body.hint === cardOriginal.hint && Object.keys(call.body).length === 1,
  )
  hintRestoreNeeded = !report.hintRestored

  // 2) «Показывать автору» — сохранение сразу, PATCH с единственным ключом visible
  visibleRestoreNeeded = true
  const callsBeforeVisible = (await evaluate(patchCallsCount)) ?? 0
  report.visibleClicked = await evaluate(clickSwitch)
  await sleep(500)
  const visibleCalls = ((await evaluate(patchCallsSince(callsBeforeVisible))) ?? []).filter(
    (call) => call.key === cardKey && 'visible' in call.body,
  )
  report.visibleCallsCount = visibleCalls.length
  report.visibleCallSingleKey = visibleCalls.length > 0 && Object.keys(visibleCalls[0].body).length === 1
  report.visibleCallFlipped = visibleCalls[0]?.body.visible === !cardOriginal.visible

  const callsBeforeVisibleRestore = (await evaluate(patchCallsCount)) ?? 0
  await evaluate(clickSwitch)
  await sleep(500)
  const visibleRestoreCalls = (await evaluate(patchCallsSince(callsBeforeVisibleRestore))) ?? []
  report.visibleRestored = visibleRestoreCalls.some(
    (call) =>
      call.key === cardKey && call.body.visible === cardOriginal.visible && Object.keys(call.body).length === 1,
  )
  visibleRestoreNeeded = !report.visibleRestored

  // 3) уровень — семь кнопок-глифов, тоже сразу, PATCH с единственным ключом icon
  const levelBefore = await evaluate(levelState)
  report.levelButtonsCount = levelBefore?.count ?? 0
  report.levelInitialChecked = levelBefore?.checked ?? -1
  const levelTargetIndex = report.levelInitialChecked === 0 ? 1 : 0
  iconRestoreNeeded = true
  const callsBeforeIcon = (await evaluate(patchCallsCount)) ?? 0
  report.levelClicked = await evaluate(clickLevel(levelTargetIndex))
  await sleep(500)
  const iconCalls = ((await evaluate(patchCallsSince(callsBeforeIcon))) ?? []).filter(
    (call) => call.key === cardKey && 'icon' in call.body,
  )
  report.iconCallsCount = iconCalls.length
  report.iconCallSingleKey = iconCalls.length > 0 && Object.keys(iconCalls[0].body).length === 1

  const callsBeforeIconRestore = (await evaluate(patchCallsCount)) ?? 0
  await evaluate(clickLevel(report.levelInitialChecked))
  await sleep(500)
  const iconRestoreCalls = (await evaluate(patchCallsSince(callsBeforeIconRestore))) ?? []
  report.iconRestored = iconRestoreCalls.some(
    (call) => call.key === cardKey && 'icon' in call.body && Object.keys(call.body).length === 1,
  )
  iconRestoreNeeded = !report.iconRestored
  const levelAfterRestore = await evaluate(levelState)
  report.levelRestoredChecked = levelAfterRestore?.checked === report.levelInitialChecked

  /*
   * ── карточка прямой выдачи (порция `f`): редактор argv и явное сохранение.
   * Берём `dsh`: исходная команда читается из `GET /api/routes` (а не из
   * памяти), правится через UI и возвращается прямым PATCH в `finalize()` —
   * и на успехе сценария, и на любом падении посреди него.
   */
  directKey = 'dsh'
  const directRowsBefore = (await evaluate(groupRows('Прямая выдача'))) ?? []
  const dshIndex = directRowsBefore.findIndex((row) => row.key === directKey)
  if (dshIndex < 0) {
    throw new Error(`в группе «Прямая выдача» нет маршрута ${directKey}: ${JSON.stringify(directRowsBefore)}`)
  }
  report.directRowClicked = await evaluate(clickRow('Прямая выдача', dshIndex))
  await sleep(700)

  directOriginal = await evaluate(fetchRoute(directKey))
  if (!directOriginal?.command?.length) throw new Error(`у маршрута ${directKey} нет command — нечего править`)
  const originalCommand = directOriginal.command
  const originalArgs = originalCommand.slice(0, -1)
  const originalPrompt = originalCommand[originalCommand.length - 1]

  report.directArgsShown = await evaluate(directArgValues)
  report.directArgsMatchServer = JSON.stringify(report.directArgsShown) === JSON.stringify(originalArgs)
  report.directPromptMatchServer = (await evaluate(fieldValue(PROMPT_SELECTOR))) === originalPrompt
  report.directHolderShown = await evaluate(
    `document.querySelector('.listik-route-direct__holder')?.textContent.includes(${JSON.stringify(directOriginal.harness)}) ?? false`,
  )
  report.directHolderReadonly = await evaluate(
    `document.querySelectorAll('.listik-route-direct__holder input, .listik-route-direct__holder select').length === 0`,
  )

  // без правок «Сохранить» выключена: пустой PATCH сервер отклоняет
  const idleState = await evaluate(directSaveState)
  report.directIdleDisabled = idleState.present === true && idleState.disabled === true
  report.directIdleReason = idleState.reason

  // 1) правка промпта — «Сохранить» доступна
  const promptProbe = `${originalPrompt} Приписка автотеста ${Date.now()}.`
  report.directPromptTyped = await evaluate(setFieldValue(PROMPT_SELECTOR, promptProbe))
  await sleep(400)
  const dirtyState = await evaluate(directSaveState)
  report.directDirtyEnabled = dirtyState.disabled === false

  // 2) `{foo}` в промпте — кнопка выключена, причина называет подстановку, поле помечено
  await evaluate(setFieldValue(PROMPT_SELECTOR, `${promptProbe} {foo}`))
  await sleep(400)
  const badPromptState = await evaluate(directSaveState)
  report.directBadPromptDisabled = badPromptState.disabled === true
  report.directBadPromptReason = badPromptState.reason
  report.directBadPromptNamed = badPromptState.reason.includes('{foo}')
  report.directBadPromptInvalid = await evaluate(ariaInvalid(PROMPT_SELECTOR))

  await evaluate(setFieldValue(PROMPT_SELECTOR, promptProbe))
  await sleep(400)
  report.directPromptFixedEnabled = (await evaluate(directSaveState)).disabled === false

  // 3) то же самое в аргументе — правило одно на всю команду
  const argProbeIndex = originalArgs.length > 1 ? 1 : 0
  await evaluate(setDirectArg(argProbeIndex, `${originalArgs[argProbeIndex]}{foo}`))
  await sleep(400)
  const badArgState = await evaluate(directSaveState)
  report.directBadArgDisabled = badArgState.disabled === true
  report.directBadArgNamed = badArgState.reason.includes('{foo}')
  report.directBadArgInvalid = await evaluate(argAriaInvalid(argProbeIndex))

  await evaluate(setDirectArg(argProbeIndex, originalArgs[argProbeIndex]))
  await sleep(400)
  report.directArgFixedEnabled = (await evaluate(directSaveState)).disabled === false

  // 4) уход с несохранённой карточки спрашивает подтверждение и «Остаться» держит правки
  const otherIndex = dshIndex === 0 ? 1 : 0
  await evaluate(clickRow('Прямая выдача', otherIndex))
  await sleep(500)
  report.directLeaveAsked = await evaluate(leaveDialogShown)
  report.directStayClicked = await evaluate(clickByText('Остаться'))
  await sleep(500)
  report.directStillEditing = (await evaluate(fieldValue(PROMPT_SELECTOR))) === promptProbe

  // 5) сохранение: один PATCH, в теле только command и только массивом
  directRestoreNeeded = true
  const callsBeforeDirectSave = (await evaluate(patchCallsCount)) ?? 0
  report.directSaveClicked = await evaluate(clickDirectSave)
  await sleep(1500)
  const directCalls = ((await evaluate(patchCallsSince(callsBeforeDirectSave))) ?? []).filter(
    (call) => call.key === directKey,
  )
  report.directPatchCount = directCalls.length
  const directBody = directCalls[0]?.body ?? {}
  report.directPatchKeys = Object.keys(directBody)
  report.directPatchOnlyCommand =
    report.directPatchKeys.length === 1 && report.directPatchKeys[0] === 'command'
  report.directPatchIsArray = Array.isArray(directBody.command)
  report.directPatchPromptMatches =
    Array.isArray(directBody.command) && directBody.command[directBody.command.length - 1] === promptProbe

  // 6) перезагрузка страницы — новый промпт на месте (он в базе, а не в памяти вкладки)
  await openTab()
  const directRowsAfterReload = (await evaluate(groupRows('Прямая выдача'))) ?? []
  await evaluate(clickRow('Прямая выдача', directRowsAfterReload.findIndex((row) => row.key === directKey)))
  await sleep(700)
  report.directPromptPersisted = (await evaluate(fieldValue(PROMPT_SELECTOR))) === promptProbe

  // 7) порядок аргументов: ▼ на первой строке, сохранить, проверить запись в базе
  const expectedArgs = originalArgs.length > 1
    ? [originalArgs[1], originalArgs[0], ...originalArgs.slice(2)]
    : originalArgs
  report.directArgMoved = await evaluate(clickArgStep(0, 'down'))
  await sleep(500)
  report.directArgsAfterMove = await evaluate(directArgValues)
  report.directArgOrderChanged =
    JSON.stringify(report.directArgsAfterMove) === JSON.stringify(expectedArgs)
  report.directOrderSaveClicked = await evaluate(clickDirectSave)
  await sleep(1500)
  const directAfterSave = await evaluate(fetchRoute(directKey))
  report.directSavedCommand = directAfterSave?.command ?? null
  report.directOrderPersisted =
    JSON.stringify(report.directSavedCommand) === JSON.stringify([...expectedArgs, promptProbe])

  report.consoleErrors = consoleErrors
  report.ok =
    report.settingsOpen === true &&
    report.groupsPresent.pipeline &&
    report.groupsPresent.direct &&
    report.selectClicked === true &&
    report.selectionMatches === true &&
    report.stepClicked === true &&
    report.orderChangedCorrectly === true &&
    report.reorderHasAllKeys === true &&
    report.persistedAfterReload === true &&
    report.restoreClicked === true &&
    report.restoredCorrectly === true &&
    report.cardRowReselected === true &&
    report.cardTitleShown === pipelineBefore[1].title &&
    report.hintCallsCount === 1 &&
    report.hintCallSingleKey === true &&
    report.hintCallValueMatches === true &&
    report.hintSavedShown === true &&
    report.hintRestored === true &&
    report.visibleCallsCount === 1 &&
    report.visibleCallSingleKey === true &&
    report.visibleCallFlipped === true &&
    report.visibleRestored === true &&
    report.levelButtonsCount === 7 &&
    report.iconCallsCount === 1 &&
    report.iconCallSingleKey === true &&
    report.iconRestored === true &&
    report.levelRestoredChecked === true &&
    report.directRowClicked === true &&
    report.directArgsMatchServer === true &&
    report.directPromptMatchServer === true &&
    report.directHolderShown === true &&
    report.directHolderReadonly === true &&
    report.directIdleDisabled === true &&
    report.directDirtyEnabled === true &&
    report.directBadPromptDisabled === true &&
    report.directBadPromptNamed === true &&
    report.directBadPromptInvalid === true &&
    report.directPromptFixedEnabled === true &&
    report.directBadArgDisabled === true &&
    report.directBadArgNamed === true &&
    report.directBadArgInvalid === true &&
    report.directArgFixedEnabled === true &&
    report.directLeaveAsked === true &&
    report.directStayClicked === true &&
    report.directStillEditing === true &&
    report.directSaveClicked === true &&
    report.directPatchCount === 1 &&
    report.directPatchOnlyCommand === true &&
    report.directPatchIsArray === true &&
    report.directPatchPromptMatches === true &&
    report.directPromptPersisted === true &&
    report.directArgMoved === true &&
    report.directArgOrderChanged === true &&
    report.directOrderSaveClicked === true &&
    report.directOrderPersisted === true &&
    consoleErrors.length === 0
} catch (error) {
  report.error = String(error?.stack ?? error)
} finally {
  await finalize()
}

/* Откат команды делает finalize() — уже после подсчёта report.ok, поэтому его
 * исход проверяется здесь: маршрут, оставшийся с командой автотеста, — это
 * провал сценария, а не мелочь. */
if (directRestoreNeeded && report.directRestoreOk !== true) {
  report.ok = false
  report.error = report.error ?? `исходная команда маршрута ${directKey} не восстановлена`
}

console.log(JSON.stringify(report, null, 2))
if (report.ok) {
  console.error(
    'ок: вкладка «Маршруты» — список/выбор/перестановка, автосохранение карточки конвейера и редактор argv прямой выдачи работают',
  )
} else {
  console.error(`ошибка: ${report.error ?? 'сценарий не прошёл — см. отчёт выше'}`)
  process.exitCode = 1
}
