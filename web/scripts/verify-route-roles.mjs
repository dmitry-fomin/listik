/**
 * Проверка редактора состава ролей маршрута (`RouteRolesEditor.vue`, listik-syu8):
 * во вкладке «Маршруты» настроек у пресета правится расклад ролей, сохранение шлёт
 * ровно один `PATCH /api/routes/<key>` с единственным ключом `roles`, новый расклад
 * переживает перезагрузку страницы, а негодная строка параметров запрос не отправляет.
 *
 * Работает с живой страницей и настоящим API Listik (как `verify-routes-settings.mjs`),
 * поэтому трогает базу маршрутов: исходный расклад выбранного маршрута возвращается
 * прямым `PATCH` в `finalize()` — и на успехе, и на падении, и по `SIGINT`/`SIGTERM`.
 *
 * Запуск: node scripts/verify-route-roles.mjs "http://localhost:5173/?token=<токен>"
 * Печатает JSON-отчёт и «ок»/«ошибка: …» последней строкой; код выхода ненулевой,
 * если сценарий не прошёл.
 */
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { cdpTarget, connect, sleep, startChrome } from './lib/browser-harness.mjs'

const url = process.argv[2]
if (!url) {
  console.error('нужно: node scripts/verify-route-roles.mjs "<url с токеном>"')
  process.exit(2)
}

const chromePath =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const port = 9890 + Math.floor(Math.random() * 90)
const profile = mkdtempSync(join(tmpdir(), 'listik-route-roles-'))
const chrome = startChrome(chromePath, port, profile, '1600,1100')

const client = connect(await cdpTarget(port))
await client.ready
const { send, evaluate, consoleErrors } = client

/** Перехват fetch: тела PATCH /api/routes/<key> и счётчик /api/routes/launchers. */
const FETCH_PATCH = `(() => {
  window.__rolesPatchCalls = window.__rolesPatchCalls ?? [];
  window.__launchersCalls = window.__launchersCalls ?? 0;
  if (window.__rolesFetchPatched) return;
  window.__rolesFetchPatched = true;
  const original = window.fetch.bind(window);
  window.fetch = function (input, init) {
    try {
      const reqUrl = typeof input === 'string' ? input : (input && input.url) || '';
      if (reqUrl.includes('/api/routes/launchers')) window.__launchersCalls += 1;
      const patchMatch = reqUrl.match(/\\/api\\/routes\\/([^/?]+)$/);
      if (patchMatch && init && init.method === 'PATCH' && typeof init.body === 'string') {
        window.__rolesPatchCalls.push({ key: patchMatch[1], body: JSON.parse(init.body) });
      }
    } catch (_e) { /* тело не JSON — не мешаем запросу */ }
    return original(input, init);
  };
})()`

const openSettings = `[...document.querySelectorAll('button')]
  .find((b) => b.textContent.trim() === 'Настройки')?.click()`
const openRoutesTab = `[...document.querySelectorAll('[role=tab]')]
  .find((b) => b.textContent.trim() === 'Маршруты')?.click()`

const clickPipelineRow = (index) => `(() => {
  const group = [...document.querySelectorAll('.listik-routes-settings__group')]
    .find((el) => el.querySelector('.listik-section__title')?.textContent.trim() === 'Конвейеры');
  const rows = group ? [...group.querySelectorAll('.ui-record-list__row')] : [];
  const btn = rows[${index}]?.querySelector('.listik-routes-row');
  if (!btn) return null;
  btn.click();
  return rows[${index}].querySelector('.listik-routes-row .listik-mono')?.textContent.trim() ?? null;
})()`

const editorPresent = `Boolean(document.querySelector('.listik-roles-editor'))`
const roleNames = `[...document.querySelectorAll('.listik-roles-editor__role-name')]
  .map((el) => el.textContent.trim())`

/** Роли в порядке разметки; `on` — состояние переключателя роли. */
const roleState = `[...document.querySelectorAll('.listik-roles-editor__role')].map((row) => ({
  name: row.querySelector('.listik-roles-editor__role-name')?.textContent.trim() ?? null,
  on: row.querySelector('input[type=checkbox]')?.checked ?? null,
  fields: [...row.querySelectorAll('.ui-field')].map((f) => f.textContent.trim().slice(0, 24)),
}))`

const saveButton = `[...document.querySelectorAll('.listik-roles-editor__actions button')]
  .find((b) => b.textContent.trim() === 'Сохранить расклад')`

const saveDisabled = `(() => { const b = ${saveButton}; return b ? b.disabled : null })()`
const clickSave = `(() => { const b = ${saveButton}; if (!b || b.disabled) return false; b.click(); return true })()`
const saveHint = `document.querySelector('.listik-roles-editor__hint')?.textContent.trim() ?? null`
const dangerAlert = `[...document.querySelectorAll('.listik-roles-editor .ui-alert')]
  .map((el) => el.textContent.trim()).find((text) => text.includes('не сохранён')) ?? null`

/** Ввод в поле роли: `which` — индекс роли, `label` — подпись поля кита. */
const typeInto = (roleIndex, label, value) => `(() => {
  const row = [...document.querySelectorAll('.listik-roles-editor__role')][${roleIndex}];
  if (!row) return false;
  const field = [...row.querySelectorAll('.ui-field')]
    .find((f) => f.textContent.trim().startsWith(${JSON.stringify(label)}));
  const input = field?.querySelector('input, textarea');
  if (!input) return false;
  const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(input, ${JSON.stringify(value)});
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()`

const paramsError = (roleIndex) => `(() => {
  const row = [...document.querySelectorAll('.listik-roles-editor__role')][${roleIndex}];
  const field = row ? [...row.querySelectorAll('.ui-field')]
    .find((f) => f.textContent.trim().startsWith('Параметры')) : null;
  return field?.querySelector('.ui-field__error')?.textContent.trim() ?? null;
})()`

const report = {}
let restoreKey = null
let restoreRoles = null
let finalized = false

const forcePatch = (key, body) => `(async () => {
  const token = new URLSearchParams(location.search).get('token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-Listik-Token'] = token;
  const res = await fetch('/api/routes/' + encodeURIComponent(key),
    { method: 'PATCH', headers, body: JSON.stringify(${JSON.stringify(body)}) });
  return res.ok;
})()`

async function finalize(code) {
  if (finalized) return
  finalized = true
  if (restoreKey && restoreRoles) {
    try {
      report.restored = await evaluate(forcePatch(restoreKey, { roles: restoreRoles }))
    } catch (error) {
      report.restoreFailed = String(error)
    }
  }
  console.log(JSON.stringify(report, null, 2))
  try {
    chrome.kill()
  } catch {
    /* уже мёртв */
  }
  rmSync(profile, { recursive: true, force: true })
  process.exit(code)
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    report.interrupted = signal
    void finalize(1)
  })
}

async function fail(message) {
  report.error = message
  console.error(`ошибка: ${message}`)
  await finalize(1)
}

try {
  await send('Page.enable')
  await send('Runtime.enable')
  await send('Page.addScriptToEvaluateOnNewDocument', { source: FETCH_PATCH })
  await send('Page.navigate', { url })
  await sleep(2500)
  await evaluate(FETCH_PATCH)

  await evaluate(openSettings)
  await sleep(400)
  await evaluate(openRoutesTab)
  await sleep(600)

  const key = await evaluate(clickPipelineRow(0))
  if (!key) await fail('не выбрался первый маршрут группы «Конвейеры»')
  report.key = key
  await sleep(600)

  report.editorPresent = await evaluate(editorPresent)
  if (!report.editorPresent) await fail('редактора состава ролей нет на карточке маршрута')
  report.roleNames = await evaluate(roleNames)
  if (JSON.stringify(report.roleNames) !== JSON.stringify(['ТЗ', 'Критик', 'Исполнитель', 'Судья'])) {
    await fail(`роли не в порядке spec/critic/impl/judge: ${JSON.stringify(report.roleNames)}`)
  }

  // исходный расклад — для откатa
  restoreKey = key
  restoreRoles = await evaluate(`(async () => {
    const token = new URLSearchParams(location.search).get('token');
    const headers = {};
    if (token) headers['X-Listik-Token'] = token;
    const res = await fetch('/api/routes', { headers });
    const data = await res.json();
    const record = (data.routes ?? []).find((r) => r.key === ${JSON.stringify(key)});
    return record ? record.roles : null;
  })()`)
  report.rolesBefore = restoreRoles
  if (!restoreRoles) await fail('не удалось прочитать исходный расклад маршрута')

  report.saveDisabledAtStart = await evaluate(saveDisabled)
  if (report.saveDisabledAtStart !== true) {
    await fail('кнопка сохранения активна на нетронутом черновике')
  }
  report.hintAtStart = await evaluate(saveHint)

  report.state = await evaluate(roleState)
  const implIndex = 2

  // 1) негодная строка параметров: ошибка под полем и ни одного запроса
  const callsBefore = await evaluate('window.__rolesPatchCalls.length')
  await evaluate(typeInto(implIndex, 'Параметры', 'Channel=glm'))
  await sleep(300)
  report.paramsErrorShown = await evaluate(paramsError(implIndex))
  report.saveDisabledOnBadParams = await evaluate(saveDisabled)
  report.patchCallsAfterBadParams =
    (await evaluate('window.__rolesPatchCalls.length')) - callsBefore
  if (!report.paramsErrorShown) await fail('негодный ключ параметра не дал ошибки под полем')
  if (report.saveDisabledOnBadParams !== true) await fail('сохранение доступно при ошибке параметров')
  if (report.patchCallsAfterBadParams !== 0) await fail('при ошибке параметров ушёл PATCH')

  // 2) годный расклад: правим подпись и параметры, сохраняем
  await evaluate(typeInto(implIndex, 'Параметры', 'channel=glm\nweb=false\nratio=-1.5'))
  await evaluate(typeInto(implIndex, 'Подпись', 'проверка'))
  await sleep(300)
  report.saveEnabled = (await evaluate(saveDisabled)) === false
  if (!report.saveEnabled) await fail(`сохранение недоступно: ${await evaluate(saveHint)}`)
  report.saveClicked = await evaluate(clickSave)
  await sleep(1200)

  const calls = await evaluate('window.__rolesPatchCalls')
  report.patchCalls = calls
  const mine = calls.filter((call) => call.key === key)
  if (mine.length !== 1) await fail(`ожидался ровно один PATCH, пришло ${mine.length}`)
  const body = mine[0].body
  report.patchBodyKeys = Object.keys(body)
  if (report.patchBodyKeys.join(',') !== 'roles') {
    await fail(`в теле PATCH не только roles: ${report.patchBodyKeys.join(',')}`)
  }
  const impl = body.roles?.impl
  if (!impl || impl.label !== 'проверка') await fail('в теле PATCH нет правки роли impl')
  if (impl.params?.channel !== 'glm' || impl.params?.web !== false || impl.params?.ratio !== -1.5) {
    await fail(`параметры разобрались не по типам: ${JSON.stringify(impl.params)}`)
  }
  report.paramsTyped = impl.params

  // 3) расклад переживает перезагрузку
  await send('Page.navigate', { url })
  await sleep(2500)
  const after = await evaluate(`(async () => {
    const token = new URLSearchParams(location.search).get('token');
    const headers = {};
    if (token) headers['X-Listik-Token'] = token;
    const res = await fetch('/api/routes', { headers });
    const data = await res.json();
    const record = (data.routes ?? []).find((r) => r.key === ${JSON.stringify(key)});
    return record ? record.roles : null;
  })()`)
  report.rolesAfterReload = after
  if (after?.impl?.label !== 'проверка') await fail('после перезагрузки расклад не тот, что сохранили')

  // 4) справочник запускаторов — один запрос за сессию доски
  await evaluate(openSettings)
  await sleep(400)
  await evaluate(openRoutesTab)
  await sleep(600)
  await evaluate(clickPipelineRow(0))
  await sleep(600)
  await evaluate(clickPipelineRow(1))
  await sleep(600)
  report.launchersCalls = await evaluate('window.__launchersCalls')
  if (report.launchersCalls !== 1) {
    await fail(`справочник запрошен ${report.launchersCalls} раз(а), ожидался один`)
  }

  report.consoleErrors = consoleErrors.slice(0, 5)
  if (report.consoleErrors.length > 0) await fail(`ошибки в консоли: ${report.consoleErrors[0]}`)

  report.ok = true
  console.log('ок')
  await finalize(0)
} catch (error) {
  await fail(String(error))
}
