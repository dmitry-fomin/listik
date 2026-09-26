/**
 * Проверка раздела «Маршруты» страницы настроек (`/settings/routes` →
 * `RoutesSettings.vue`; страница открывается прямым адресом, вкладок и модалки
 * настроек больше нет):
 * список не пуст и в нём есть обе группы («Конвейеры»/«Рой») — двумя
 * карточками `UiCard` со счётчиком-бейджем у заголовка; группы «Прямая выдача»
 * нет (такого текста на странице нет вовсе). Клик по строке выбирает маршрут.
 * Список лишён органов управления: внутри группы нет ни узла `UiRecordList`, ни
 * кнопок «Переместить…»/«Убрать…»/«Удалить…», ни кнопки «Завести маршрут» —
 * перестановки и удаления у модели данных нет.
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
 * трогает базу маршрутов: исходные значения полей возвращаются прямым `PATCH`
 * в `finalize()` и при успехе сценария, и при падении посреди него. Этот откат
 * живёт в `finalize()`, общей для обычного `finally` и для `SIGINT`/`SIGTERM`:
 * голый `try/finally` не сработал бы на Ctrl-C (Node завершает процесс по
 * умолчанию раньше, чем размотался бы стек), поэтому оба сигнала перехвачены
 * отдельно и вызывают тот же откат перед выходом. Та же схема —
 * `hintRestoreNeeded`/`visibleRestoreNeeded`/`iconRestoreNeeded` и прямой
 * `PATCH` в `finalize()` — страхует карточку маршрута.
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

/* ── перехват fetch на странице: копится в window.__routesPatchCalls (тела
 * PATCH /api/routes/<key> — автосохранение карточки, порция `e`), добавлен как
 * «скрипт на новый документ» — переживает Page.navigate сам, повторно
 * вставлять после перезагрузки не нужно. */
const ROUTES_INTERCEPT = `(() => {
  window.__routesPatchCalls = window.__routesPatchCalls ?? [];
  if (window.__routesFetchPatched) return;
  window.__routesFetchPatched = true;
  const original = window.fetch.bind(window);
  window.fetch = function (input, init) {
    try {
      const reqUrl = typeof input === 'string' ? input : (input && input.url) || '';
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

/** Контейнер строки списка — своя разметка раздела (треб. 3), не DOM `UiRecordList`. */
const ROW = '.listik-routes-settings__row'

const groupRows = (title) => `(() => {
  const group = [...document.querySelectorAll('.listik-routes-settings__group')]
    .find((el) => el.querySelector('.listik-section__title')?.textContent.trim() === ${JSON.stringify(title)});
  if (!group) return null;
  return [...group.querySelectorAll('${ROW}')].map((row) => ({
    key: row.querySelector('.listik-routes-row')?.getAttribute('data-key') ?? null,
    title: row.querySelector('.listik-routes-row__title')?.textContent.trim() ?? null,
  }));
})()`

const clickRow = (title, index) => `(() => {
  const group = [...document.querySelectorAll('.listik-routes-settings__group')]
    .find((el) => el.querySelector('.listik-section__title')?.textContent.trim() === ${JSON.stringify(title)});
  const rows = group ? [...group.querySelectorAll('${ROW}')] : [];
  const btn = rows[${index}]?.querySelector('.listik-routes-row');
  if (!btn) return false;
  btn.click();
  return true;
})()`

/**
 * Органы управления списком: `UiRecordList` (любым своим классом), кнопки
 * перемещения/удаления строки и кнопка заведения маршрута. Проверка падает,
 * даже если `UiRecordList` вернуть с `:reorderable="false"` — важен сам узел.
 */
const controlAudit = (title) => `(() => {
  const group = [...document.querySelectorAll('.listik-routes-settings__group')]
    .find((el) => el.querySelector('.listik-section__title')?.textContent.trim() === ${JSON.stringify(title)});
  if (!group) return null;
  const badLabels = [...group.querySelectorAll('[aria-label]')]
    .map((el) => el.getAttribute('aria-label') ?? '')
    .filter((label) => /^(Переместить|Убрать|Удалить)/.test(label));
  return {
    hasRecordList: Boolean(group.querySelector('[class*="ui-record-list"]')),
    badLabels,
    addButton: [...group.querySelectorAll('button')].some((b) => b.textContent.trim() === 'Завести маршрут'),
    rowCount: group.querySelectorAll('${ROW}').length,
    badge: group.querySelector('.listik-routes-settings__group-head .ui-badge')?.textContent.trim() ?? null,
  };
})()`

const detailTitle = `document.querySelector('.listik-routes-settings__card input')?.value ?? null`

/** Открыть раздел «Маршруты»: у него свой адрес, кликать нечего. */
const openTab = async () => {
  await send('Page.navigate', { url: routesUrl })
  await sleep(4000)
}

/** Прямой PATCH записи в обход UI — страховка `finally` для карточки (порция `e`). */
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

const patchCallsSince = (from) => `window.__routesPatchCalls.slice(${from})`
const patchCallsCount = `window.__routesPatchCalls.length`
const saveStatusText = `document.querySelector('.listik-routes-settings__card .ui-save-status')?.textContent ?? ''`

const report = { ok: false }
let cardKey = null
let cardOriginal = null
let hintRestoreNeeded = false
let visibleRestoreNeeded = false
let iconRestoreNeeded = false
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
  console.error(`прервано сигналом ${signal} — база возвращена к исходным значениям, если они были тронуты`)
  process.exit(130)
}
process.on('SIGINT', () => { void handleSignal('SIGINT') })
process.on('SIGTERM', () => { void handleSignal('SIGTERM') })

try {
  await send('Runtime.enable')
  await send('Page.enable')
  await send('Page.addScriptToEvaluateOnNewDocument', { source: ROUTES_INTERCEPT })

  await send('Page.navigate', { url: routesUrl })
  await sleep(4000)
  await evaluate(ROUTES_INTERCEPT) // сама страница уже загружена раньше add-script — патчим и напрямую

  report.settingsOpen = await evaluate(`Boolean(document.querySelector('.listik-settings'))`)

  const pipelineBefore = await evaluate(groupRows('Конвейеры'))
  const swarmBefore = await evaluate(groupRows('Рой'))
  report.groupsPresent = {
    pipeline: Array.isArray(pipelineBefore) && pipelineBefore.length > 0,
    swarm: Array.isArray(swarmBefore),
    noDirect: (await evaluate(`!document.body.innerText.includes('Прямая выдача')`)) === true,
  }
  if (!Array.isArray(pipelineBefore) || pipelineBefore.length < 2) {
    throw new Error(`нужно хотя бы два маршрута-конвейера, чтобы проверить выбор строки: ${JSON.stringify(pipelineBefore)}`)
  }

  // список без органов управления: ни UiRecordList, ни перемещения/удаления/заведения
  const pipelineAudit = await evaluate(controlAudit('Конвейеры'))
  const swarmAudit = await evaluate(controlAudit('Рой'))
  report.controlsAudit = { pipeline: pipelineAudit, swarm: swarmAudit }
  report.controlsAbsent =
    Boolean(pipelineAudit) &&
    Boolean(swarmAudit) &&
    pipelineAudit.hasRecordList === false &&
    swarmAudit.hasRecordList === false &&
    pipelineAudit.badLabels.length === 0 &&
    swarmAudit.badLabels.length === 0 &&
    pipelineAudit.addButton === false &&
    swarmAudit.addButton === false
  report.countersMatch =
    Boolean(pipelineAudit) &&
    Boolean(swarmAudit) &&
    pipelineAudit.badge === String(pipelineAudit.rowCount) &&
    swarmAudit.badge === String(swarmAudit.rowCount)

  // выбрать вторую строку — карточка справа должна показать её заголовок
  report.selectClicked = await evaluate(clickRow('Конвейеры', 1))
  await sleep(500)
  report.selectedTitleShown = await evaluate(detailTitle)
  report.selectionMatches = report.selectedTitleShown === pipelineBefore[1].title

  /*
   * ── карточка выбранного маршрута (порция `e`): шапка сохраняется сама.
   * Строка уже выбрана выше; повторный клик — тот же путь, что у человека.
   */
  report.cardRowReselected = await evaluate(clickRow('Конвейеры', 1))
  await sleep(500)
  report.cardTitleShown = await evaluate(detailTitle)

  cardKey = pipelineBefore[1].key
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

  report.consoleErrors = consoleErrors
  report.ok =
    report.settingsOpen === true &&
    report.groupsPresent.pipeline &&
    report.groupsPresent.swarm &&
    report.groupsPresent.noDirect &&
    report.controlsAbsent === true &&
    report.countersMatch === true &&
    report.selectClicked === true &&
    report.selectionMatches === true &&
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
    consoleErrors.length === 0
} catch (error) {
  report.error = String(error?.stack ?? error)
} finally {
  await finalize()
}

console.log(JSON.stringify(report, null, 2))
if (report.ok) {
  console.error('ок: раздел «Маршруты» — группы, выбор строки и автосохранение карточки работают')
} else {
  console.error(`ошибка: ${report.error ?? 'сценарий не прошёл — см. отчёт выше'}`)
  process.exitCode = 1
}
