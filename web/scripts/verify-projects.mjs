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
 * Перед настоящим удалением DELETE проекта перехватывается через CDP `Fetch` и
 * получает подменный ответ, до сервера эти ответы не доходят (listik-uq4f):
 * ответ 409 открывает второй диалог «Удалить вместе с задачами?», а ответ 500 —
 * и на первом запросе после 409, и на удалении с задачами — его не открывает и
 * закрывает подтверждение, текст ошибки виден в алерте раздела.
 * Отказ сети и 401 в действиях раздела доходят до общего обработчика доски
 * (listik-l0it): 401 при удалении открывает окно «Нужен токен Listik», отказ сети
 * на тумблере поднимает плашку «Сервер Listik недоступен» на доске. 409/500 не дают
 * ни окна токена, ни общих алертов доски.
 * Сбой перечитывания списка после удачного действия (GET /api/projects →
 * подменный 500) не гасится: текст виден в алерте раздела, а повторное нажатие
 * тумблера при живом чтении возвращает проект и сбрасывает алерт (listik-q1xh).
 * Работает с живой страницей (dev или прод) и настоящим API Listik, поэтому
 * трогает базу: используйте временный каталог и slug вида `listik-check-*`.
 * Печатает JSON-отчёт с проверками полей (`checks`, итог — `ok`); код возврата 1,
 * если проверка не прошла или сценарий упал, 2 — при ошибке аргументов.
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

/** Отчёт сценария; `checks` — сверка полей с ожиданиями: `{ name, ok, got }`. */
const report = { checks: [] }
const check = (name, ok, got) => report.checks.push({ name, ok: Boolean(ok), got })
let socket = null
try {
  const client = connect(await cdpTarget(port))
  socket = client.socket
  await client.ready
  const { send, events } = client
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
  // скрыть добавленный проект тумблером
  await evaluate(`(() => {
    const card = [...document.querySelectorAll('.ui-entity-card')]
      .find((el) => el.querySelector('.ui-entity-card__title')?.textContent.trim() === ${JSON.stringify(slug)});
    card?.querySelector('[role=switch]')?.click();
  })()`)
  await sleep(3000)
  // проект ищется по секциям: у скрытого проекта заголовок тоже есть на странице (listik-q9yq)
  report.afterHide = await evaluate(boardState)

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
  report.afterRestore = await evaluate(boardState)

  // состояние возврата должно пережить перезагрузку: возврат — не только вид, но и база
  await send('Page.navigate', { url: settingsUrl })
  await sleep(4000)
  report.afterReload = await evaluate(boardState)

  // отказ удаления (listik-uq4f): DELETE проекта получает подменный ответ, до сервера не доходит
  const T409 = 'проверка listik-uq4f: у проекта есть задачи (409)'
  const T500 = 'проверка listik-uq4f: сбой сервера 500'
  const T500F = 'проверка listik-uq4f: сбой удаления с задачами 500'
  let stub = { code: 409, text: T409 }
  /** Подмена ответа на чтение списка (`GET /api/projects`, ровно путь списка):
   *  не null только на время сценария listik-q1xh — действие над проектом идёт на
   *  сервер, а перечитывание списка получает 500. */
  let listStub = null
  report.intercepted = []
  // обработчик — до `Fetch.enable`, иначе перехваченный запрос повиснет
  socket.addEventListener('message', (message) => {
    const data = JSON.parse(message.data)
    if (data.method !== 'Fetch.requestPaused') return
    const { requestId, request } = data.params
    let reply
    if (stub.offline) {
      report.intercepted.push(`${request.method} ${request.url} → offline`)
      reply = send('Fetch.failRequest', { requestId, errorReason: 'ConnectionRefused' })
    } else if (
      listStub &&
      request.method === 'GET' &&
      new URL(request.url).pathname === '/api/projects'
    ) {
      report.intercepted.push(`GET ${request.url} → ${listStub.code}`)
      reply = send('Fetch.fulfillRequest', {
        requestId,
        responseCode: listStub.code,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json; charset=utf-8' }],
        body: Buffer.from(JSON.stringify({ ok: false, error: listStub.text })).toString('base64'),
      })
    } else if (request.method === 'DELETE') {
      report.intercepted.push(`DELETE ${request.url} → ${stub.code}`)
      reply = send('Fetch.fulfillRequest', {
        requestId,
        responseCode: stub.code,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json; charset=utf-8' }],
        body: Buffer.from(JSON.stringify({ ok: false, error: stub.text })).toString('base64'),
      })
    } else {
      reply = send('Fetch.continueRequest', { requestId })
    }
    reply.catch((error) => console.error(`перехват ${request.url}: ${error.message}`))
  })
  await send('Fetch.enable', {
    patterns: [
      { urlPattern: `*/api/projects/${slug}*` },
      { urlPattern: '*/api/projects*' },
      { urlPattern: '*/api/health*' },
    ],
  })

  const openRemove = () => evaluate(`(() => {
    const card = [...document.querySelectorAll('.ui-entity-card')]
      .find((el) => el.querySelector('.ui-entity-card__title')?.textContent.trim() === ${JSON.stringify(slug)});
    const btn = [...(card?.querySelectorAll('button') ?? [])].find((b) => (b.getAttribute('aria-label') || '').startsWith('Удалить проект'));
    btn?.click();
  })()`)
  const clickButton = (label) => evaluate(
    `[...document.querySelectorAll('button')].find((b) => b.textContent.trim() === ${JSON.stringify(label)})?.click()`,
  )
  /** Состояние раздела после шага; `alert` — есть ли в алерте раздела текст заглушки шага. */
  const removeState = (text) => evaluate(`(() => {
    const dialogOpen = (needle) => [...document.querySelectorAll('[role=alertdialog], .ui-modal')].some((el) => el.textContent.includes(needle));
    return {
      forceShown: dialogOpen('Удалить вместе с задачами'),
      confirmOpen: dialogOpen('Убрать репозиторий'),
      alert: [...document.querySelectorAll('.listik-projects .ui-alert')].some((el) => el.textContent.includes(${JSON.stringify(text)})),
      stillThere: [...document.querySelectorAll('.ui-entity-card__title')].some((el) => el.textContent.trim() === ${JSON.stringify(slug)}),
    };
  })()`)

  // listik-q1xh: удачное действие над проектом + сбой перечитывания списка
  // (`GET /api/projects` → подменный 500). Текст остаётся в алерте раздела;
  // повторное нажатие тумблера при живом чтении сбрасывает алерт и возвращает
  // проект в исходную видимость (свитч скрытой строки повторно шлёт archived=0).
  const TLIST = 'проверка listik-q1xh: сбой перечитывания 500'
  const clickSwitch = () => evaluate(`(() => {
    const card = [...document.querySelectorAll('.ui-entity-card')]
      .find((el) => el.querySelector('.ui-entity-card__title')?.textContent.trim() === ${JSON.stringify(slug)});
    card?.querySelector('[role=switch]')?.click();
  })()`)
  const alertHas = (text) => evaluate(
    `[...document.querySelectorAll('.listik-projects .ui-alert')].some((el) => el.textContent.includes(${JSON.stringify(text)}))`,
  )
  // прячем по-настоящему: подменяется чтение после «вернуть на доску»
  await clickSwitch()
  await sleep(3000)
  listStub = { code: 500, text: TLIST }
  await clickSwitch()
  await sleep(3000)
  report.refreshFail = {
    alert: await alertHas(TLIST),
    ...JSON.parse(await evaluate(boardState)),
  }
  listStub = null
  await clickSwitch()
  await sleep(3000)
  report.refreshRecovery = {
    alertEmpty: await evaluate(`document.querySelectorAll('.listik-projects .ui-alert').length === 0`),
    ...JSON.parse(await evaluate(boardState)),
  }

  // 1. 409 → второй диалог, «Оставить» закрывает его, проект на месте
  stub = { code: 409, text: T409 }
  await openRemove()
  await sleep(1200)
  await clickButton('Убрать')
  await sleep(2000)
  {
    const { forceShown } = await removeState(T409)
    report.remove409 = { forceShown }
  }
  await clickButton('Оставить')
  await sleep(1500)
  {
    const { forceShown, stillThere } = await removeState(T409)
    report.after409Keep = { forceShown, stillThere }
  }

  // 2. 500 сразу после попытки с 409: второй диалог не открывается, подтверждение закрыто
  stub = { code: 500, text: T500 }
  await openRemove()
  await sleep(1200)
  await clickButton('Убрать')
  await sleep(2000)
  report.remove500 = await removeState(T500)

  // 3. 409 → второй диалог, 500 на «Удалить с задачами» закрывает его
  stub = { code: 409, text: T409 }
  await openRemove()
  await sleep(1200)
  await clickButton('Убрать')
  await sleep(2000)
  const forceBefore = (await removeState(T409)).forceShown
  stub = { code: 500, text: T500F }
  await clickButton('Удалить с задачами')
  await sleep(2000)
  report.forceFail500 = { forceBefore, ...(await removeState(T500F)) }

  // listik-l0it: 401 и отказ сети в действиях раздела доходят до общего обработчика доски
  const T401 = 'проверка listik-l0it: нужен токен (401)'
  /** На доску роутером, без перезагрузки: состояние стора переживает переход. */
  const toBoard = () => evaluate(`document.querySelector('a.listik-shell__brand-link')?.click()`)
  const tokenAsked = `[...document.querySelectorAll('.ui-modal')].some((el) => el.textContent.includes('Нужен токен Listik'))`
  /** Общие сигналы доски: плашка отказа сети, алерт последней ошибки, окно токена. */
  const boardAlerts = () => evaluate(`(() => {
    const topAlert = (needle) => [...document.querySelectorAll('.listik-shell__top .ui-alert')].some((el) => el.textContent.includes(needle));
    return {
      plaque: topAlert('Запустите сервер командой'),
      lastErrorAlert: topAlert('Последняя операция завершилась ошибкой'),
      tokenAsked: ${tokenAsked},
    };
  })()`)

  // 4. после 409/500 шагов 1–3 на доске нет ни плашки, ни алерта ошибки, ни окна токена
  await toBoard()
  await sleep(1500)
  report.boardAfterConflicts = await boardAlerts()
  await send('Page.navigate', { url: settingsUrl })
  await sleep(4000)

  // 5. 401 на удалении: окно токена, диалоги закрыты, проект на месте; перезагрузка снимает окно
  stub = { code: 401, text: T401 }
  await openRemove()
  await sleep(1200)
  await clickButton('Убрать')
  await sleep(2000)
  report.remove401 = { ...(await removeState(T401)), tokenAsked: await evaluate(tokenAsked) }
  await send('Page.navigate', { url: settingsUrl })
  await sleep(4000)
  report.afterTokenReload = {
    tokenAsked: await evaluate(tokenAsked),
    stillThere: await evaluate(
      `[...document.querySelectorAll('.ui-entity-card__title')].some((el) => el.textContent.trim() === ${JSON.stringify(slug)})`,
    ),
  }

  // 6. отказ сети на тумблере «скрыть»: проект на доске, алерт раздела, плашка на доске
  stub = { offline: true }
  await evaluate(`(() => {
    const card = [...document.querySelectorAll('.ui-entity-card')]
      .find((el) => el.querySelector('.ui-entity-card__title')?.textContent.trim() === ${JSON.stringify(slug)});
    card?.querySelector('[role=switch]')?.click();
  })()`)
  await sleep(2000)
  report.archiveOffline = {
    alert: await evaluate(
      `[...document.querySelectorAll('.listik-projects .ui-alert')].some((el) => el.textContent.includes('Сервер Listik недоступен'))`,
    ),
    ...JSON.parse(await evaluate(boardState)),
  }
  await toBoard()
  await sleep(1500)
  report.offlineBoard = await boardAlerts()
  stub = { code: 409, text: T409 }

  await send('Fetch.disable')

  // после снятия отказа и перезагрузки на доске нет ни плашки, ни алерта, ни окна токена
  await send('Page.navigate', { url })
  await sleep(4000)
  report.boardAfterRecovery = await boardAlerts()
  await send('Page.navigate', { url: settingsUrl })
  await sleep(4000)

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
  // сверка полей отчёта с ожиданиями (listik-w7jf)
  const rowsBefore = report.rowsBefore
  const afterAdd = JSON.parse(report.afterAdd)
  check('settingsOpen', report.settingsOpen === true, report.settingsOpen)
  check('addFormFields', report.addFormFields === 3, report.addFormFields)
  check('submitLabel', report.submitLabel === 'Добавить', report.submitLabel)
  check(
    'afterAdd',
    afterAdd.hasSlug === true && afterAdd.rows === rowsBefore + 1 && !afterAdd.error,
    { ...afterAdd, rowsBefore },
  )
  const editForm = JSON.parse(report.editForm)
  check(
    'editForm',
    editForm.fields === 2 && editForm.slugShown === true && editForm.submit === 'Сохранить',
    editForm,
  )
  check('inlineInputs', report.inlineInputs === 0, report.inlineInputs)
  const onBoard = (state) => state.onBoard === true && state.inHidden === false
  const hidden = (state) => state.onBoard === false && state.inHidden === true
  const afterHide = JSON.parse(report.afterHide)
  check('afterHide', hidden(afterHide), afterHide)
  const hiddenSwitch = JSON.parse(report.hiddenSwitch)
  check('hiddenSwitch', hiddenSwitch.found === true && hiddenSwitch.checked === 'true', hiddenSwitch)
  const afterRestore = JSON.parse(report.afterRestore)
  check('afterRestore', onBoard(afterRestore), afterRestore)
  const afterReload = JSON.parse(report.afterReload)
  check('afterReload', onBoard(afterReload), afterReload)
  check('refreshFail', report.refreshFail.alert === true && hidden(report.refreshFail), report.refreshFail)
  check(
    'refreshRecovery',
    report.refreshRecovery.alertEmpty === true && onBoard(report.refreshRecovery),
    report.refreshRecovery,
  )
  check('remove409', report.remove409.forceShown === true, report.remove409)
  check(
    'after409Keep',
    report.after409Keep.forceShown === false && report.after409Keep.stillThere === true,
    report.after409Keep,
  )
  /** Ошибка удаления: диалоги закрыты, текст в алерте раздела, проект на месте. */
  const failedRemove = (state) =>
    state.forceShown === false && state.confirmOpen === false && state.alert === true && state.stillThere === true
  check('remove500', failedRemove(report.remove500), report.remove500)
  check(
    'forceFail500',
    report.forceFail500.forceBefore === true && failedRemove(report.forceFail500),
    report.forceFail500,
  )
  const boardClean = (state) => state.plaque === false && state.lastErrorAlert === false && state.tokenAsked === false
  check('boardAfterConflicts', boardClean(report.boardAfterConflicts), report.boardAfterConflicts)
  check(
    'remove401',
    report.remove401.tokenAsked === true &&
      report.remove401.forceShown === false &&
      report.remove401.confirmOpen === false &&
      report.remove401.stillThere === true,
    report.remove401,
  )
  check(
    'afterTokenReload',
    report.afterTokenReload.tokenAsked === false && report.afterTokenReload.stillThere === true,
    report.afterTokenReload,
  )
  check(
    'archiveOffline',
    report.archiveOffline.alert === true && onBoard(report.archiveOffline),
    report.archiveOffline,
  )
  check(
    'offlineBoard',
    report.offlineBoard.plaque === true && report.offlineBoard.tokenAsked === false,
    report.offlineBoard,
  )
  check('boardAfterRecovery', boardClean(report.boardAfterRecovery), report.boardAfterRecovery)
  check('confirmShown', report.confirmShown === true, report.confirmShown)
  const afterRemove = JSON.parse(report.afterRemove)
  check(
    'afterRemove',
    afterRemove.stillThere === false && afterRemove.rows === rowsBefore,
    { ...afterRemove, rowsBefore },
  )
  const hits = (pattern) => report.intercepted.filter((entry) => pattern.test(entry)).length
  check(
    'intercepted',
    hits(/^DELETE .* → 409$/) >= 1 &&
      hits(/^DELETE .* → 500$/) >= 2 &&
      hits(/^DELETE .* → 401$/) >= 1 &&
      hits(/ → offline$/) >= 1 &&
      hits(/^GET .* → 500$/) >= 1,
    report.intercepted,
  )
  check('consoleErrors', report.consoleErrors.length === 0, report.consoleErrors)
  report.ok = report.checks.every((entry) => entry.ok)
} catch (error) {
  report.error = String(error?.stack ?? error)
  report.ok = false
} finally {
  socket?.close()
  // профиль удаляем после выхода Chrome: иначе он успевает дописать каталог после rmSync
  const exited = new Promise((resolve) => {
    if (chrome.exitCode !== null || chrome.signalCode !== null) resolve()
    else chrome.once('exit', resolve)
  })
  chrome.kill()
  await Promise.race([exited, sleep(5000)])
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  } catch {
    /* временный профиль уберёт система */
  }
}

console.log(JSON.stringify(report, null, 2))
if (report.ok) {
  console.error(`ок: «Репозитории» — все ${report.checks.length} проверок сценария прошли`)
} else {
  const failed = report.checks.filter((entry) => !entry.ok).map((entry) => entry.name)
  console.error(
    `ошибка: ${report.error?.split('\n')[0] ?? `не прошли проверки: ${failed.join(', ')} — см. отчёт выше`}`,
  )
  process.exitCode = 1
}
