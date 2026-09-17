/**
 * Проверка вкладки «Маршруты» настроек (`ProjectSettings.vue` → `RoutesSettings.vue`):
 * список не пуст и в нём есть обе группы («Конвейеры»/«Прямая выдача»), клик по
 * строке выбирает маршрут, перестановка кнопкой ▲ (кит `UiRecordList`) пишется
 * в базу — порядок переживает перезагрузку страницы, а не живёт только в
 * памяти вкладки. Заодно проверяет тело запроса `POST /api/routes/reorder`
 * (перехват `window.fetch` на странице): в `keys` приходят ключи ВСЕХ
 * маршрутов, а не только той группы, где случилась перестановка.
 *
 * Работает с живой страницей (dev или прод) и настоящим API Listik, поэтому
 * трогает базу маршрутов: исходный порядок возвращается и при успехе сценария
 * (кнопкой ▼ по ходу проверки), и при падении посреди него (прямым
 * `POST /api/routes/reorder` на подобранный ранее исходный порядок) — этот
 * откат живёт в `finalize()`, общей для обычного `finally` и для `SIGINT`/
 * `SIGTERM`: голый `try/finally` не сработал бы на Ctrl-C (Node завершает
 * процесс по умолчанию раньше, чем размотался бы стек), поэтому оба сигнала
 * перехвачены отдельно и вызывают тот же откат перед выходом.
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

/* ── перехват fetch на странице: копится в window.__routesReorderCalls,
 * добавлен как «скрипт на новый документ» — переживает Page.navigate сам,
 * повторно вставлять после перезагрузки не нужно. */
const REORDER_PATCH = `(() => {
  window.__routesReorderCalls = window.__routesReorderCalls ?? [];
  if (window.__routesFetchPatched) return;
  window.__routesFetchPatched = true;
  const original = window.fetch.bind(window);
  window.fetch = function (input, init) {
    try {
      const reqUrl = typeof input === 'string' ? input : (input && input.url) || '';
      if (reqUrl.includes('/api/routes/reorder') && init && typeof init.body === 'string') {
        window.__routesReorderCalls.push(JSON.parse(init.body));
      }
    } catch (_e) { /* тело не JSON — не мешаем запросу */ }
    return original(input, init);
  };
})()`

const openSettings = `[...document.querySelectorAll('button')]
  .find((b) => b.textContent.trim() === 'Настройки')?.click()`

const openRoutesTab = `[...document.querySelectorAll('[role=tab]')]
  .find((b) => b.textContent.trim() === 'Маршруты')?.click()`

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

const openTab = async () => {
  await evaluate(openSettings)
  await sleep(1500)
  await evaluate(openRoutesTab)
  await sleep(1500)
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

const report = { ok: false }
let originalPipelineKeys = null
let restoreNeeded = false
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

  await send('Page.navigate', { url })
  await sleep(4000)
  await evaluate(REORDER_PATCH) // сама страница уже загружена раньше add-script — патчим и напрямую

  await openTab()
  report.settingsOpen = await evaluate(`Boolean(document.querySelector('.ui-modal'))`)

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
  await send('Page.navigate', { url })
  await sleep(4000)
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
    consoleErrors.length === 0
} catch (error) {
  report.error = String(error?.stack ?? error)
} finally {
  await finalize()
}

console.log(JSON.stringify(report, null, 2))
if (report.ok) {
  console.error('ок: вкладка «Маршруты» — список, выбор, перестановка и сохранение порядка работают')
} else {
  console.error(`ошибка: ${report.error ?? 'сценарий не прошёл — см. отчёт выше'}`)
  process.exitCode = 1
}
