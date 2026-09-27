/**
 * Проверка раздела «Маршруты» страницы настроек (`/settings/routes` →
 * `RoutesSettings.vue`; страница открывается прямым адресом, вкладок и модалки
 * настроек больше нет):
 * список не пуст и в нём есть обе группы («Конвейеры»/«Рой») — двумя
 * карточками `UiCard` со счётчиком-бейджем у заголовка; группы «Прямая выдача»
 * нет (такого текста на странице нет вовсе). Клик по строке выбирает маршрут.
 * Список лишён органов управления: внутри группы нет ни узла `UiRecordList`, ни
 * кнопок «Переместить…»/«Убрать…»/«Удалить…», ни кнопки «Завести маршрут» —
 * перестановки у модели данных нет, а удаление маршрута роя живёт в карточке
 * справа (`RouteSwarmCard`, listik-4ky0 порция a), не в строках списка.
 *
 * Порция `a` (удаление маршрута роя): прямым API заводится временный маршрут
 * `kind=swarm` и задача с его `launch_route`; дальше три проверки —
 * Escape в диалоге не шлёт DELETE; подтверждение по уже удалённому прямым API
 * маршруту отвечает 404, виден алерт раздела и строка пропадает; успешное
 * удаление — ровно один DELETE со статусом 200, `tasks_cleared` в сообщении
 * равен ответу, карточка задачи очищена от `launch_route`, справа снова
 * «Выбери маршрут слева», а за окном дебаунса (1000мс) на удалённый ключ не
 * уходит ни одного PATCH. Временные маршрут и задача убираются в `finalize()`.
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
 * Порция `b` (автосохранение карточки харнесса, `/settings/harnesses`):
 * выбирается харнесс `kind=exec`, серия вводов в «Подпись» без потери фокуса
 * даёт ровно один `PATCH /api/harnesses/<key>` с единственным ключом `hint`,
 * статус «Сохранено», кнопки «Сохранить» нет, `GET` возвращает новое значение.
 * Дальше три проверки — правка + blur без ожидания дебаунса уходит не позже
 * 300 мс; клик по тумблеру «В списках выбора» — один PATCH с ключом `enabled`;
 * правка + сразу выбор другого харнесса в списке — один PATCH на ключ первого
 * (досохранение при размонтировании). Все значения возвращаются тем же путём
 * (ввод/клик), а `finalize()` страхует прямым PATCH (`harnessHintRestoreNeeded`/
 * `harnessEnabledRestoreNeeded`).
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
 * PATCH /api/routes/<key> — автосохранение карточки, порция `e`),
 * window.__routesDeleteCalls (DELETE /api/routes/<key> со статусом и
 * tasks_cleared из ответа — удаление маршрута, порция `a`) и
 * window.__harnessPatchCalls (PATCH /api/harnesses/<key> с меткой `t` —
 * автосохранение карточки харнесса, порция `b`), добавлен как «скрипт на новый
 * документ» — переживает Page.navigate сам, повторно вставлять после
 * перезагрузки не нужно. Прямые вызовы API сценария идут тем же fetch и тоже
 * считаются — поэтому подсчёт всегда «с момента» (снимок длины). */
const ROUTES_INTERCEPT = `(() => {
  window.__routesPatchCalls = window.__routesPatchCalls ?? [];
  window.__routesDeleteCalls = window.__routesDeleteCalls ?? [];
  window.__harnessPatchCalls = window.__harnessPatchCalls ?? [];
  if (window.__routesFetchPatched) return;
  window.__routesFetchPatched = true;
  const original = window.fetch.bind(window);
  window.fetch = function (input, init) {
    let del = null;
    try {
      const reqUrl = typeof input === 'string' ? input : (input && input.url) || '';
      const routeMatch = reqUrl.match(/\\/api\\/routes\\/([^/?]+)$/);
      if (routeMatch && init && init.method === 'PATCH' && typeof init.body === 'string') {
        window.__routesPatchCalls.push({ key: routeMatch[1], body: JSON.parse(init.body) });
      }
      if (routeMatch && init && init.method === 'DELETE') {
        del = { key: routeMatch[1], status: null, cleared: null };
        window.__routesDeleteCalls.push(del);
      }
      const harnessMatch = reqUrl.match(/\\/api\\/harnesses\\/([^/?]+)$/);
      if (harnessMatch && init && init.method === 'PATCH' && typeof init.body === 'string') {
        window.__harnessPatchCalls.push({ key: harnessMatch[1], body: JSON.parse(init.body), t: Date.now() });
      }
    } catch (_e) { /* тело не JSON — не мешаем запросу */ }
    const out = original(input, init);
    if (del) {
      out.then((response) => {
        del.status = response.status;
        try { return response.clone().json() } catch (_e) { return null }
      }).then((payload) => {
        const data = payload && payload.data !== undefined ? payload.data : payload;
        if (data && typeof data === 'object') del.cleared = data.tasks_cleared ?? null;
      }).catch(() => { /* сеть отказала — статуса нет */ });
    }
    return out;
  };
})()`

/** Адрес раздела «Маршруты» с тем же токеном, что передали скрипту. */
const routesUrl = (() => {
  const target = new URL(url)
  target.pathname = '/settings/routes'
  return target.toString()
})()

/** Адрес раздела «Харнессы» — карточка с автосохранением (порция `b`). */
const harnessesUrl = (() => {
  const target = new URL(url)
  target.pathname = '/settings/harnesses'
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

/* ── карточка харнесса (порция `b`): список слева, PATCH-перехват ── */

/** Каталог харнессов со страницы (токен из localStorage, как у forcePatch). */
const fetchHarnesses = `(async () => {
  const token = localStorage.getItem('listik.token') ?? ''
  try {
    const response = await fetch('/api/harnesses', { headers: { Authorization: 'Bearer ' + token } })
    if (!response.ok) return null
    const payload = await response.json()
    const data = payload.data ?? payload
    return data.harnesses ?? null
  } catch (_e) {
    return null
  }
})()`

/** Клик по строке харнесса в списке слева (data-key у кнопки строки). */
const clickHarnessRow = (key) => `(() => {
  const row = [...document.querySelectorAll('.listik-harnesses__row')]
    .find((el) => el.querySelector('.listik-routes-row')?.getAttribute('data-key') === ${JSON.stringify(key)})
  const btn = row?.querySelector('.listik-routes-row')
  if (!btn) return false
  btn.click()
  return true
})()`

/**
 * Та же посимвольная печать, что typeCardField, но для карточки харнесса:
 * поля «Имя»/«Подпись» лежат в `.listik-harness-card__fields` (index 0/1).
 */
const typeHarnessField = (index, value) => `(async () => {
  const input = document.querySelectorAll('.listik-harness-card .listik-harness-card__fields input')[${index}]
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

/**
 * Печать в поле карточки и сразу blur, не дожидаясь дебаунса: возвращает
 * { delay, calls } — задержку до ближайшего перехваченного PATCH (метка `t`
 * в записи перехвата) и число вызовов за время ожидания.
 */
const typeHarnessFieldBlur = (index, value) => `(async () => {
  const input = document.querySelectorAll('.listik-harness-card .listik-harness-card__fields input')[${index}]
  if (!input) return null
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  let acc = ''
  for (const ch of ${JSON.stringify(value)}) {
    acc += ch
    setter.call(input, acc)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise((r) => setTimeout(r, 15))
  }
  const before = window.__harnessPatchCalls.length
  const t0 = Date.now()
  input.dispatchEvent(new FocusEvent('blur'))
  while (window.__harnessPatchCalls.length === before && Date.now() - t0 < 2000) {
    await new Promise((r) => setTimeout(r, 10))
  }
  const calls = window.__harnessPatchCalls.slice(before)
  return { delay: calls.length > 0 ? calls[0].t - t0 : -1, calls: calls.length }
})()`

const clickHarnessSwitch = `(() => {
  const btn = document.querySelector('.listik-harness-card .ui-switch__track')
  if (!btn) return false
  btn.click()
  return true
})()`

const harnessPatchCallsSince = (from) => `window.__harnessPatchCalls.slice(${from})`
const harnessPatchCallsCount = `window.__harnessPatchCalls.length`
const harnessSaveStatusText = `document.querySelector('.listik-harness-card .ui-save-status')?.textContent ?? ''`

/* ── удаление маршрута роя (порция `a`) ── */

/**
 * Прямой вызов API со страницы (токен — из localStorage, как у forcePatch):
 * возвращает `{status, data}` (`data` — уже развёрнутый конверт `ok/data`).
 */
const apiCall = (method, path, body) => `(async () => {
  const token = localStorage.getItem('listik.token') ?? ''
  const init = {
    method: ${JSON.stringify(method)},
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
    body: ${body === undefined ? 'undefined' : JSON.stringify(JSON.stringify(body))},
  }
  try {
    const response = await fetch(${JSON.stringify(path)}, init)
    let payload = null
    try { payload = await response.json() } catch (_e) { /* тела нет */ }
    const data = payload && payload.data !== undefined ? payload.data : payload
    return { status: response.status, data }
  } catch (error) {
    return { status: 0, data: String(error) }
  }
})()`

/** Первый харнесс каталога — для обязательной ячейки роли временного маршрута. */
const firstHarnessKey = `(async () => {
  const token = localStorage.getItem('listik.token') ?? ''
  try {
    const response = await fetch('/api/harnesses', { headers: { Authorization: 'Bearer ' + token } })
    if (!response.ok) return null
    const payload = await response.json()
    const data = payload.data ?? payload
    return (data.harnesses || [])[0]?.key ?? null
  } catch (_e) {
    return null
  }
})()`

/** Кнопка «Удалить маршрут» — в карточке справа, не в строке списка. */
const clickRemoveRoute = `(() => {
  const card = document.querySelector('.listik-routes-settings__card')
  const btn = [...(card?.querySelectorAll('button') ?? [])]
    .find((b) => b.textContent.trim() === 'Удалить маршрут')
  if (!btn) return false
  btn.click()
  return true
})()`

/** Диалог удаления маршрута — `UiConfirmDialog` (role="alertdialog"). */
const removeDialogOpen = `(() => {
  const dialog = [...document.querySelectorAll('[role="alertdialog"]')]
    .find((el) => (el.textContent || '').includes('Удалить маршрут'))
  return Boolean(dialog)
})()`

const confirmRemoveDialog = `(() => {
  const dialog = [...document.querySelectorAll('[role="alertdialog"]')]
    .find((el) => (el.textContent || '').includes('Удалить маршрут'))
  const btn = [...(dialog?.querySelectorAll('button') ?? [])]
    .find((b) => b.textContent.trim() === 'Удалить')
  if (!btn) return false
  btn.click()
  return true
})()`

/** Алерт раздела с текстом ошибки (`routesSettingsError`, «Не получилось»). */
const routesErrorShown = `(() => {
  const alert = [...document.querySelectorAll('.listik-routes-settings > .ui-alert')]
    .find((el) => (el.textContent || '').includes('Не получилось'))
  return alert ? true : false
})()`

/** Сообщение об удалении с числом снятых карточек — текст или null. */
const removedNoteText = `(() => {
  const alert = [...document.querySelectorAll('.ui-alert')]
    .find((el) => (el.textContent || '').includes('удалён'))
  return alert ? (alert.textContent || '').replace(/\\s+/g, ' ').trim() : null
})()`

/** Пустое состояние правой панели — «Выбери маршрут слева». */
const emptyPanelShown = `(() => {
  const panel = document.querySelector('.listik-routes-settings__panel')
  return Boolean(panel && (panel.textContent || '').includes('Выбери маршрут слева'))
})()`

/** Опрос выражения до truthy — вместо фиксированного сна на реакцию UI. */
const until = async (expression, tries = 24, delay = 250) => {
  for (let attempt = 0; attempt < tries; attempt += 1) {
    const value = await evaluate(expression)
    if (value) return value
    await sleep(delay)
  }
  return null
}

const report = { ok: false }
let cardKey = null
let cardOriginal = null
let hintRestoreNeeded = false
let visibleRestoreNeeded = false
let iconRestoreNeeded = false
/** Карточка харнесса (порция `b`) — свои флаги отката для finalize(). */
let harnessCardKey = null
let harnessCardOriginal = null
let harnessHintRestoreNeeded = false
let harnessEnabledRestoreNeeded = false
/** Временные маршрут и задача порции `a` — убираются в finalize() при любом исходе. */
let tmpRouteKey = null
let tmpTaskId = null
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
  // Временные маршрут и задача убираются первыми — пока страница и сокет живы.
  if (tmpRouteKey || tmpTaskId) {
    try {
      await withTimeout(
        (async () => {
          if (tmpTaskId) await evaluate(apiCall('DELETE', `/api/tasks/${tmpTaskId}`))
          if (tmpRouteKey) await evaluate(apiCall('DELETE', `/api/routes/${tmpRouteKey}`))
          report.tempCleanupDone = true
        })(),
        10000,
      )
    } catch (cleanupError) {
      report.tempCleanupDone = false
      report.tempCleanupError = String(cleanupError?.stack ?? cleanupError)
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
  // Страховка карточки харнесса (порция `b`): прямой PATCH исходных hint/enabled.
  if ((harnessHintRestoreNeeded || harnessEnabledRestoreNeeded) && harnessCardKey && harnessCardOriginal) {
    try {
      await withTimeout(
        (async () => {
          const body = {}
          if (harnessHintRestoreNeeded) body.hint = harnessCardOriginal.hint
          if (harnessEnabledRestoreNeeded) body.enabled = harnessCardOriginal.enabled
          report.harnessRestoreFallbackOk =
            (await evaluate(apiCall('PATCH', `/api/harnesses/${harnessCardKey}`, body)))?.status === 200
        })(),
        10000,
      )
    } catch (restoreError) {
      report.harnessRestoreFallbackOk = false
      report.harnessRestoreError = String(restoreError?.stack ?? restoreError)
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

  /*
   * ── удаление маршрута роя (порция `a`) ──
   * Временный маршрут `kind=swarm` и задача с его `route` — прямым API, оба
   * убираются в `finalize()` при любом исходе. Страница перезагружается после
   * заведения: список `store.routes` читался при монтировании раздела.
   */
  tmpRouteKey = `verify-del-${Date.now().toString(36)}`
  const harnessKey = (await evaluate(firstHarnessKey)) ?? 'devin'
  const tempRouteBody = {
    kind: 'swarm',
    key: tmpRouteKey,
    title: 'Временный рой автотеста',
    hint: 'verify-routes-settings',
    visible: true,
    roles: { impl: { harness: harnessKey } },
  }
  const createdRoute = await evaluate(apiCall('POST', '/api/routes', tempRouteBody))
  const createdTask = await evaluate(apiCall('POST', '/api/tasks', {
    title: `временная задача verify-routes-settings ${tmpRouteKey}`,
    route: tmpRouteKey,
  }))
  tmpTaskId = createdTask?.data?.id ?? null
  report.tempSetup = {
    routeStatus: createdRoute?.status ?? null,
    taskStatus: createdTask?.status ?? null,
    taskId: tmpTaskId,
    taskRoute: createdTask?.data?.launch_route ?? null,
  }
  if (createdRoute?.status !== 201 || !tmpTaskId) {
    throw new Error(`временные маршрут/задача не завелись: ${JSON.stringify(report.tempSetup)}`)
  }

  const reloadPage = async () => {
    await openTab()
    await evaluate(ROUTES_INTERCEPT) // страховка: сам перехват идемпотентен
  }

  /** Строки группы «Рой» без временного ключа — ждём пропадания записи. */
  const untilRowGone = async () => {
    for (let attempt = 0; attempt < 24; attempt += 1) {
      const rows = (await evaluate(groupRows('Рой'))) ?? []
      if (!rows.some((row) => row.key === tmpRouteKey)) return true
      await sleep(250)
    }
    return false
  }

  // 1) выбор маршрута, «Удалить маршрут», Escape → DELETE не уходит
  await reloadPage()
  const swarmRows1 = (await evaluate(groupRows('Рой'))) ?? []
  const swarmIndex1 = swarmRows1.findIndex((row) => row.key === tmpRouteKey)
  report.removeRowListed = swarmIndex1 >= 0
  if (swarmIndex1 < 0) throw new Error(`маршрут ${tmpRouteKey} не появился в группе «Рой»`)
  report.removeRowClicked = await evaluate(clickRow('Рой', swarmIndex1))
  await sleep(500)
  report.removeButtonClicked = await evaluate(clickRemoveRoute)
  report.removeDialogShown = await until(removeDialogOpen)
  const deletesBeforeEscape = (await evaluate(`window.__routesDeleteCalls.length`)) ?? 0
  await send('Input.dispatchKeyEvent', {
    type: 'rawKeyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27,
  })
  await send('Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27,
  })
  report.escapeClosesDialog = (await until(`!(${removeDialogOpen})`)) === true
  report.deletesAfterEscape =
    ((await evaluate(`window.__routesDeleteCalls.slice(${deletesBeforeEscape})`)) ?? [])
      .filter((call) => call.key === tmpRouteKey).length

  // 2) диалог снова открыт; маршрут удалён прямым API → подтверждение даёт 404,
  //    строки нет, справа «Выбери маршрут слева», виден алерт раздела
  await evaluate(clickRemoveRoute)
  report.removeDialogReopened = Boolean(await until(removeDialogOpen))
  const directDelete = await evaluate(apiCall('DELETE', `/api/routes/${tmpRouteKey}`))
  report.directDeleteStatus = directDelete?.status ?? null
  const deletesBefore404 = (await evaluate(`window.__routesDeleteCalls.length`)) ?? 0
  report.remove404Clicked = await evaluate(confirmRemoveDialog)
  report.rowGoneAfter404 = await untilRowGone()
  const delete404Calls =
    ((await evaluate(`window.__routesDeleteCalls.slice(${deletesBefore404})`)) ?? [])
      .filter((call) => call.key === tmpRouteKey)
  report.delete404Count = delete404Calls.length
  report.delete404Status = delete404Calls[0]?.status ?? null
  report.emptyPanelAfter404 = Boolean(await until(emptyPanelShown))
  report.errorAlertAfter404 = Boolean(await until(routesErrorShown))

  //    маршрут тем же ключом заведён заново, задаче возвращён launch_route
  const recreated = await evaluate(apiCall('POST', '/api/routes', tempRouteBody))
  const repatched = await evaluate(apiCall('PATCH', `/api/tasks/${tmpTaskId}`, { route: tmpRouteKey }))
  report.tempRouteRecreated = recreated?.status === 201
  report.taskRouteRestored = repatched?.status === 200

  // 3) успешное удаление через UI: один DELETE 200, tasks_cleared в сообщении
  //    равен ответу, строки нет, пустое состояние, у задачи launch_route пуст,
  //    за окном дебаунса (1000мс) PATCH на удалённый ключ не уходит
  await reloadPage()
  const swarmRows2 = (await evaluate(groupRows('Рой'))) ?? []
  const swarmIndex2 = swarmRows2.findIndex((row) => row.key === tmpRouteKey)
  report.recreatedRowListed = swarmIndex2 >= 0
  if (swarmIndex2 < 0) {
    throw new Error(`пересозданный маршрут ${tmpRouteKey} не появился в группе «Рой»`)
  }
  await evaluate(clickRow('Рой', swarmIndex2))
  await sleep(500)
  const patchBeforeDelete = (await evaluate(patchCallsCount)) ?? 0
  const deletesBeforeOk = (await evaluate(`window.__routesDeleteCalls.length`)) ?? 0
  await evaluate(clickRemoveRoute)
  report.removeDialogOpened2 = Boolean(await until(removeDialogOpen))
  report.removeConfirmClicked = await evaluate(confirmRemoveDialog)
  report.rowGoneAfterDelete = await untilRowGone()
  const deleteOkCalls =
    ((await evaluate(`window.__routesDeleteCalls.slice(${deletesBeforeOk})`)) ?? [])
      .filter((call) => call.key === tmpRouteKey)
  report.deleteOkCount = deleteOkCalls.length
  report.deleteOkStatus = deleteOkCalls[0]?.status ?? null
  report.tasksCleared = deleteOkCalls[0]?.cleared ?? null
  report.removedNote = await until(removedNoteText)
  const noteMatch = /снят у (\d+) карточ(?:ки|ек)/.exec(report.removedNote ?? '')
  report.removedNoteCount = noteMatch ? Number(noteMatch[1]) : null
  report.emptyPanelAfterDelete = Boolean(await until(emptyPanelShown))
  const taskAfter = await evaluate(apiCall('GET', `/api/tasks/${tmpTaskId}`))
  report.taskRouteCleared =
    taskAfter?.status === 200 && !(taskAfter.data?.launch_route)
  await sleep(1000)
  const latePatchCalls =
    ((await evaluate(patchCallsSince(patchBeforeDelete))) ?? [])
      .filter((call) => call.key === tmpRouteKey)
  report.latePatchCount = latePatchCalls.length

  /*
   * ── карточка харнесса (порция `b`): автосохранение на /settings/harnesses ──
   * Харнесс `kind=exec` — у него есть и текстовые поля, и командный блок;
   * второй харнесс нужен, чтобы проверить досохранение при размонтировании.
   * Перехват — тот же ROUTES_INTERCEPT, PATCH попадают в __harnessPatchCalls.
   */
  await send('Page.navigate', { url: harnessesUrl })
  await sleep(4000)

  const harnessesList = await evaluate(fetchHarnesses)
  const execHarness = (harnessesList ?? []).find((item) => item.kind === 'exec')
  const otherHarness = (harnessesList ?? []).find((item) => item.key !== execHarness?.key)
  if (!execHarness || !otherHarness) {
    throw new Error(`нужны харнесс kind=exec и ещё один: ${JSON.stringify(harnessesList)}`)
  }
  harnessCardKey = execHarness.key
  harnessCardOriginal = execHarness

  report.harnessRowClicked = await evaluate(clickHarnessRow(harnessCardKey))
  report.harnessCardShown = Boolean(await until(`Boolean(document.querySelector('.listik-harness-card'))`))
  report.harnessSaveButtonGone =
    (await evaluate(`[...document.querySelectorAll('.listik-harness-card button')]
      .every((b) => b.textContent.trim() !== 'Сохранить')`)) === true

  // 1) «Подпись»: серия «нажатий» без blur — ровно один PATCH, только hint
  const harnessHintProbe = `${harnessCardOriginal.hint} · автотест ${Date.now()}`
  harnessHintRestoreNeeded = true
  const hBeforeHint = (await evaluate(harnessPatchCallsCount)) ?? 0
  report.harnessHintTyped = await evaluate(typeHarnessField(1, harnessHintProbe))
  await sleep(1000)
  const hHintCalls = ((await evaluate(harnessPatchCallsSince(hBeforeHint))) ?? []).filter(
    (call) => call.key === harnessCardKey && 'hint' in call.body,
  )
  report.harnessHintCallsCount = hHintCalls.length
  report.harnessHintSingleKey =
    hHintCalls.length > 0 && Object.keys(hHintCalls[hHintCalls.length - 1].body).length === 1
  report.harnessHintValueMatches = hHintCalls[hHintCalls.length - 1]?.body.hint === harnessHintProbe
  report.harnessSavedShown = (await evaluate(harnessSaveStatusText)).includes('Сохранено')
  const harnessReadBack = await evaluate(apiCall('GET', `/api/harnesses/${harnessCardKey}`))
  report.harnessGetReturnsHint =
    harnessReadBack?.status === 200 && harnessReadBack?.data?.hint === harnessHintProbe

  // исходная подпись — тем же вводом
  const hBeforeHintRestore = (await evaluate(harnessPatchCallsCount)) ?? 0
  await evaluate(typeHarnessField(1, harnessCardOriginal.hint))
  await sleep(1000)
  report.harnessHintRestored =
    ((await evaluate(harnessPatchCallsSince(hBeforeHintRestore))) ?? []).some(
      (call) =>
        call.key === harnessCardKey &&
        call.body.hint === harnessCardOriginal.hint &&
        Object.keys(call.body).length === 1,
    )
  harnessHintRestoreNeeded = !report.harnessHintRestored

  // 2) правка «Подписи» и сразу blur — PATCH уходит не позже 300 мс
  const harnessHintBlur = `${harnessCardOriginal.hint} · blur ${Date.now()}`
  harnessHintRestoreNeeded = true
  const hBeforeBlur = (await evaluate(harnessPatchCallsCount)) ?? 0
  const blurProbe = await evaluate(typeHarnessFieldBlur(1, harnessHintBlur))
  report.harnessBlurDelay = blurProbe?.delay ?? null
  report.harnessBlurCallsInWait = blurProbe?.calls ?? null
  await sleep(1000)
  const hBlurCalls = ((await evaluate(harnessPatchCallsSince(hBeforeBlur))) ?? []).filter(
    (call) => call.key === harnessCardKey,
  )
  report.harnessBlurCallsCount = hBlurCalls.length
  report.harnessBlurSingleKey =
    hBlurCalls.length > 0 && Object.keys(hBlurCalls[0].body).length === 1
  report.harnessBlurValueMatches = hBlurCalls[0]?.body.hint === harnessHintBlur

  const hBeforeBlurRestore = (await evaluate(harnessPatchCallsCount)) ?? 0
  await evaluate(typeHarnessField(1, harnessCardOriginal.hint))
  await sleep(1000)
  report.harnessBlurRestored =
    ((await evaluate(harnessPatchCallsSince(hBeforeBlurRestore))) ?? []).some(
      (call) =>
        call.key === harnessCardKey &&
        call.body.hint === harnessCardOriginal.hint &&
        Object.keys(call.body).length === 1,
    )
  harnessHintRestoreNeeded = !report.harnessBlurRestored

  // 3) тумблер «В списках выбора» — сразу, PATCH с единственным ключом enabled
  harnessEnabledRestoreNeeded = true
  const hBeforeEnabled = (await evaluate(harnessPatchCallsCount)) ?? 0
  report.harnessSwitchClicked = await evaluate(clickHarnessSwitch)
  await sleep(700)
  const hEnabledCalls = ((await evaluate(harnessPatchCallsSince(hBeforeEnabled))) ?? []).filter(
    (call) => call.key === harnessCardKey && 'enabled' in call.body,
  )
  report.harnessEnabledCallsCount = hEnabledCalls.length
  report.harnessEnabledSingleKey =
    hEnabledCalls.length > 0 && Object.keys(hEnabledCalls[0].body).length === 1
  report.harnessEnabledFlipped = hEnabledCalls[0]?.body.enabled === !harnessCardOriginal.enabled

  const hBeforeEnabledRestore = (await evaluate(harnessPatchCallsCount)) ?? 0
  await evaluate(clickHarnessSwitch)
  await sleep(700)
  report.harnessEnabledRestored =
    ((await evaluate(harnessPatchCallsSince(hBeforeEnabledRestore))) ?? []).some(
      (call) =>
        call.key === harnessCardKey &&
        call.body.enabled === harnessCardOriginal.enabled &&
        Object.keys(call.body).length === 1,
    )
  harnessEnabledRestoreNeeded = !report.harnessEnabledRestored

  // 4) правка «Подписи» и сразу выбор другого харнесса — досохранение при
  //    размонтировании: ровно один PATCH на ключ первого харнесса
  const harnessHintSwitch = `${harnessCardOriginal.hint} · уход ${Date.now()}`
  harnessHintRestoreNeeded = true
  await evaluate(typeHarnessField(1, harnessHintSwitch))
  const hBeforeUnmount = (await evaluate(harnessPatchCallsCount)) ?? 0
  report.harnessOtherClicked = await evaluate(clickHarnessRow(otherHarness.key))
  await sleep(1000)
  const hUnmountCallsAll = (await evaluate(harnessPatchCallsSince(hBeforeUnmount))) ?? []
  const hUnmountCalls = hUnmountCallsAll.filter((call) => call.key === harnessCardKey)
  report.harnessUnmountCallsCount = hUnmountCalls.length
  report.harnessUnmountTotalCount = hUnmountCallsAll.length
  report.harnessUnmountSingleKey =
    hUnmountCalls.length > 0 && Object.keys(hUnmountCalls[0].body).length === 1
  report.harnessUnmountValueMatches = hUnmountCalls[0]?.body.hint === harnessHintSwitch
  report.harnessOtherCardShown =
    (await evaluate(
      `document.querySelector('.listik-harnesses .listik-routes-row.is-selected')?.getAttribute('data-key')`,
    )) === otherHarness.key

  // вернуть значение: выбрать первый харнесс снова и вернуть подпись вводом
  report.harnessBackClicked = await evaluate(clickHarnessRow(harnessCardKey))
  await sleep(700)
  const hBeforeFinalRestore = (await evaluate(harnessPatchCallsCount)) ?? 0
  await evaluate(typeHarnessField(1, harnessCardOriginal.hint))
  await sleep(1000)
  report.harnessUnmountRestored =
    ((await evaluate(harnessPatchCallsSince(hBeforeFinalRestore))) ?? []).some(
      (call) =>
        call.key === harnessCardKey &&
        call.body.hint === harnessCardOriginal.hint &&
        Object.keys(call.body).length === 1,
    )
  harnessHintRestoreNeeded = !report.harnessUnmountRestored

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
    /* удаление маршрута роя (порция `a`) */
    report.tempSetup?.routeStatus === 201 &&
    report.tempSetup?.taskStatus === 201 &&
    report.tempSetup?.taskRoute === tmpRouteKey &&
    report.removeRowListed === true &&
    report.removeRowClicked === true &&
    report.removeButtonClicked === true &&
    report.removeDialogShown === true &&
    report.escapeClosesDialog === true &&
    report.deletesAfterEscape === 0 &&
    report.removeDialogReopened === true &&
    report.directDeleteStatus === 200 &&
    report.remove404Clicked === true &&
    report.delete404Count === 1 &&
    report.delete404Status === 404 &&
    report.rowGoneAfter404 === true &&
    report.emptyPanelAfter404 === true &&
    report.errorAlertAfter404 === true &&
    report.tempRouteRecreated === true &&
    report.taskRouteRestored === true &&
    report.recreatedRowListed === true &&
    report.removeDialogOpened2 === true &&
    report.removeConfirmClicked === true &&
    report.deleteOkCount === 1 &&
    report.deleteOkStatus === 200 &&
    (report.tasksCleared ?? 0) >= 1 &&
    report.removedNoteCount === report.tasksCleared &&
    report.rowGoneAfterDelete === true &&
    report.emptyPanelAfterDelete === true &&
    report.taskRouteCleared === true &&
    report.latePatchCount === 0 &&
    /* карточка харнесса (порция `b`): автосохранение */
    report.harnessRowClicked === true &&
    report.harnessCardShown === true &&
    report.harnessSaveButtonGone === true &&
    report.harnessHintTyped === true &&
    report.harnessHintCallsCount === 1 &&
    report.harnessHintSingleKey === true &&
    report.harnessHintValueMatches === true &&
    report.harnessSavedShown === true &&
    report.harnessGetReturnsHint === true &&
    report.harnessHintRestored === true &&
    report.harnessBlurDelay !== null &&
    report.harnessBlurDelay >= 0 &&
    report.harnessBlurDelay <= 300 &&
    report.harnessBlurCallsCount === 1 &&
    report.harnessBlurSingleKey === true &&
    report.harnessBlurValueMatches === true &&
    report.harnessBlurRestored === true &&
    report.harnessSwitchClicked === true &&
    report.harnessEnabledCallsCount === 1 &&
    report.harnessEnabledSingleKey === true &&
    report.harnessEnabledFlipped === true &&
    report.harnessEnabledRestored === true &&
    report.harnessOtherClicked === true &&
    report.harnessUnmountCallsCount === 1 &&
    report.harnessUnmountTotalCount === 1 &&
    report.harnessUnmountSingleKey === true &&
    report.harnessUnmountValueMatches === true &&
    report.harnessOtherCardShown === true &&
    report.harnessBackClicked === true &&
    report.harnessUnmountRestored === true &&
    consoleErrors.length === 0
} catch (error) {
  report.error = String(error?.stack ?? error)
} finally {
  await finalize()
}

console.log(JSON.stringify(report, null, 2))
if (report.ok) {
  console.error('ок: «Маршруты» — группы, выбор строки, автосохранение карточки и удаление маршрута роя; «Харнессы» — автосохранение карточки')
} else {
  console.error(`ошибка: ${report.error ?? 'сценарий не прошёл — см. отчёт выше'}`)
  process.exitCode = 1
}
