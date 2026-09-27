/**
 * Проверка телефонной очереди (listik-8bnh): смена проекта не теряется, а ответы,
 * запрошенные для прежнего проекта, не ложатся под новый селект —
 * `web/src/components/PhoneQueue.vue` (`reload`, «Показать ещё», вотчер проекта).
 *
 * Поднимает `scripts/mock-api.mjs --fill=60 --slow-list=fill --slow-ms=<SLOW>`:
 * список проекта `fill` («Заполнитель») отдаётся через SLOW мс, а вычисляется в
 * момент прихода запроса, — так в браузере воспроизводится медленное чтение,
 * пока пользователь уже выбрал другой проект. Отдаёт собранный `web/dist` и
 * гоняет сценарии в headless Chrome по CDP, телефон — эмуляцией 390×844
 * (`Emulation.setDeviceMetricsOverride`, mobile) до навигации. Только клики и
 * чтение DOM, как пользователь; ожидаемые `total` проектов берутся запросом к
 * моку до сценариев.
 *
 * Сценарии:
 *   A. «Заполнитель», сразу «Listik»: под «Listik» не бывает строк другого
 *      проекта, в конце 20 строк `listik`;
 *   B. «Заполнитель», «Показать ещё» и сразу «Listik»: страница `fill` не
 *      дописывается в список `listik`;
 *   D. продолжение B: «Показать ещё» дописывает задачи `listik`;
 *   C. событие задачи во время медленного чтения `fill`: обновление очереди
 *      выполняется после чтения, заголовок строки становится меткой (последний —
 *      меняет заглушку).
 *
 * Запуск: node scripts/verify-phone-queue.mjs
 *   Нужен собранный `web/dist` (`npm run build`). Печатает JSON-отчёт
 *   `{cases: [{name, ok, expect, got}], consoleErrors}`; код возврата 1, если хоть
 *   один сценарий не прошёл.
 */
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  cdpTarget,
  connect,
  freePort,
  serveDist,
  sleep,
  startChrome,
  startMock,
} from './lib/browser-harness.mjs'
import { record as recordCase, waitFor } from './lib/verify-helpers.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')
const chromePath = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

/** Задержка медленного списка `fill` в моке; от неё считаются все окна наблюдения. */
const SLOW = 1500
const PAGE = 20

const report = { cases: [], consoleErrors: [] }
const record = (name, run) => recordCase(report, name, run)

/** Одна выборка очереди: строки, бейдж «N из M», значение селекта, есть ли «Показать ещё». */
const SNAPSHOT = `(() => {
  const rows = [...document.querySelectorAll('.listik-mobile-task')].map((el) => ({
    id: el.querySelector('.listik-mono')?.textContent?.trim() ?? '',
    project: el.querySelector('.listik-mobile-task__meta .ui-badge')?.textContent?.trim() ?? '',
    title: el.querySelector('.listik-mobile-task__title')?.textContent?.trim() ?? '',
  }));
  const select = document.querySelector('.listik-phone-toolbar [role="combobox"][aria-label="Проект"]');
  return {
    rows,
    badge: document.querySelector('.listik-phone-queue__head .ui-badge')?.textContent?.replace(/\\s+/g, ' ').trim() ?? null,
    project: select?.querySelector('.ui-select__value')?.textContent?.replace(/\\s+/g, ' ').trim() ?? null,
    more: Boolean(document.querySelector('.listik-phone-queue__more')),
  };
})()`

/** Сжатая выборка для отчёта: число строк, проекты по счёту, бейдж, селект. */
function brief(snap) {
  if (!snap) return null
  const projects = {}
  for (const row of snap.rows) projects[row.project] = (projects[row.project] ?? 0) + 1
  return { rows: snap.rows.length, projects, badge: snap.badge, project: snap.project, more: snap.more }
}

const allOf = (snap, project) => snap.rows.every((row) => row.project === project)

let mock = null
let staticServer = null
let chrome = null
let profile = null

try {
  if (!existsSync(join(dist, 'index.html'))) {
    throw new Error('нет web/dist/index.html — соберите: npm run build')
  }
  profile = mkdtempSync(join(tmpdir(), 'listik-phone-queue-'))
  const apiPort = await freePort()
  const pagePort = await freePort()
  const chromePort = await freePort()
  mock = await startMock(apiPort, root, ['--fill=60', '--slow-list=fill', `--slow-ms=${SLOW}`])
  const mockApi = `http://127.0.0.1:${apiPort}`

  /** `total` открытых задач проекта прямо из мока (пустой slug — все проекты). */
  const totalOf = async (project) => {
    const query = project ? `project=${encodeURIComponent(project)}&limit=1` : 'limit=1'
    const response = await fetch(`${mockApi}/api/tasks?${query}`)
    const body = await response.json()
    if (!body.ok || typeof body.data?.total !== 'number') throw new Error(`мок не отдал total для «${project || 'все'}»`)
    return body.data.total
  }
  const totals = { listik: await totalOf('listik'), fill: await totalOf('fill'), all: await totalOf('') }

  staticServer = await serveDist(pagePort, apiPort, dist)
  const url = `http://127.0.0.1:${pagePort}/?token=mock-token`

  chrome = startChrome(chromePath, chromePort, profile)
  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')
  // Узкий экран до навигации: `useIsPhone` видит порог и App.vue показывает PhoneQueue.
  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })

  const snapshot = () => evaluate(SNAPSHOT)

  /**
   * Выбор проекта в селекте: клик по триггеру, ожидание опций, клик по опции с
   * точным текстом. Выборка `before` снята в том же вызове прямо перед кликом по
   * опции — это «момент выбора»; возврат — после одной макрозадачи (Vue успел
   * перерисовать).
   */
  const chooseProject = async (label) => {
    const opened = await evaluate(`(() => {
      const trigger = document.querySelector('.listik-phone-toolbar [role="combobox"][aria-label="Проект"]');
      if (!trigger) return false;
      trigger.click();
      return true;
    })()`)
    if (!opened) return { ok: false, why: 'нет селекта «Проект»' }
    const listed = await waitFor(
      async () => ((await evaluate(`document.querySelectorAll('[role="listbox"] [role="option"]').length`)) > 0 ? true : null),
      3000,
    )
    if (!listed) return { ok: false, why: 'список проектов не открылся' }
    return evaluate(`(async () => {
      const before = ${SNAPSHOT};
      const option = [...document.querySelectorAll('[role="listbox"] [role="option"]')]
        .find((el) => el.textContent.replace(/\\s+/g, ' ').trim() === ${JSON.stringify(label)});
      if (!option) return { ok: false, why: 'нет опции ' + ${JSON.stringify(label)}, before };
      option.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
      return { ok: true, before };
    })()`)
  }

  const clickMore = () =>
    evaluate(`(() => {
      const button = document.querySelector('.listik-phone-queue__more');
      if (!button) return false;
      button.click();
      return true;
    })()`)

  /** Выборки не реже раза в 100 мс, пока не пройдёт `ms` от `since`. */
  const observe = async (since, ms) => {
    const seen = []
    while (Date.now() - since < ms) {
      seen.push(await snapshot())
      await sleep(50)
    }
    seen.push(await snapshot())
    return seen
  }

  /** Новая навигация и исходная очередь «все проекты»: 20 строк, «20 из <всего>»; потом 500 мс. */
  const freshQueue = async () => {
    await send('Page.navigate', { url })
    const initial = await waitFor(async () => {
      const snap = await snapshot()
      return snap.rows.length === PAGE && snap.badge === `${PAGE} из ${totals.all}` ? snap : null
    }, 15000)
    if (!initial) return { ok: false, got: { why: 'не дождались исходной очереди «все проекты»', seen: brief(await snapshot()) } }
    await sleep(500)
    return { ok: true }
  }

  let bObserved = false

  await record('A. быстрая смена проекта: ответ прежнего проекта не ложится под новый селект', async () => {
    const expect = `после «Заполнитель» → «Listik» ни в одной выборке нет строк не listik; в конце селект «Listik», ${PAGE} строк listik, бейдж «${PAGE} из ${totals.listik}»`
    const start = await freshQueue()
    if (!start.ok) return { ok: false, expect, got: start.got }
    const fill = await chooseProject('Заполнитель')
    if (!fill.ok) return { ok: false, expect, got: { why: fill.why } }
    const listik = await chooseProject('Listik')
    const since = Date.now()
    if (!listik.ok) return { ok: false, expect, got: { why: listik.why } }
    if (listik.before.rows.length !== 0) {
      return { ok: false, expect, got: { why: 'гонка не воспроизведена: при выборе «Listik» строки уже были', before: brief(listik.before) } }
    }
    const seen = await observe(since, 2 * SLOW)
    const last = seen.at(-1)
    const foreign = seen.find((snap) => !allOf(snap, 'listik'))
    const ok = !foreign
      && last.project === 'Listik'
      && last.rows.length === PAGE
      && allOf(last, 'listik')
      && last.badge === `${PAGE} из ${totals.listik}`
    return { ok, expect, got: { samples: seen.length, foreign: brief(foreign), last: brief(last) } }
  })

  await record('B. «Показать ещё», затем смена проекта: страница прежнего проекта не дописывается', async () => {
    const expect = `после «Показать ещё» на fill и выбора «Listik» ни в одной выборке нет строк fill; в конце ${PAGE} строк listik, бейдж «${PAGE} из ${totals.listik}»`
    const start = await freshQueue()
    if (!start.ok) return { ok: false, expect, got: start.got }
    const fill = await chooseProject('Заполнитель')
    if (!fill.ok) return { ok: false, expect, got: { why: fill.why } }
    const fillReady = await waitFor(async () => {
      const snap = await snapshot()
      return snap.rows.length === PAGE && allOf(snap, 'fill') ? snap : null
    }, 3 * SLOW)
    if (!fillReady) return { ok: false, expect, got: { why: `не дождались ${PAGE} строк fill`, seen: brief(await snapshot()) } }
    if (!(await clickMore())) return { ok: false, expect, got: { why: 'нет кнопки «Показать ещё» на fill', seen: brief(fillReady) } }
    const listik = await chooseProject('Listik')
    const since = Date.now()
    if (!listik.ok) return { ok: false, expect, got: { why: listik.why } }
    if (listik.before.rows.length !== PAGE || !allOf(listik.before, 'fill')) {
      return { ok: false, expect, got: { why: `гонка не воспроизведена: при выборе «Listik» не ${PAGE} строк fill`, before: brief(listik.before) } }
    }
    const seen = await observe(since, SLOW + 1000)
    bObserved = true
    const last = seen.at(-1)
    const stale = seen.find((snap) => snap.rows.some((row) => row.project === 'fill'))
    const ok = !stale
      && last.rows.length === PAGE
      && allOf(last, 'listik')
      && last.badge === `${PAGE} из ${totals.listik}`
    return { ok, expect, got: { samples: seen.length, stale: brief(stale), last: brief(last) } }
  })

  await record('D. после смены проекта «Показать ещё» дописывает новый проект', async () => {
    const want = Math.min(2 * PAGE, totals.listik)
    const expect = `после «Показать ещё» ${want} строк listik, бейдж «${want} из ${totals.listik}»${want === totals.listik ? ', кнопки «Показать ещё» нет' : ''}`
    if (!bObserved) return { ok: false, expect, got: { why: 'сценарий B не дошёл до окна наблюдения' } }
    const before = await snapshot()
    if (!(await clickMore())) return { ok: false, expect, got: { why: 'нет кнопки «Показать ещё»', seen: brief(before) } }
    const done = await waitFor(async () => {
      const snap = await snapshot()
      return snap.rows.length === want
        && allOf(snap, 'listik')
        && snap.badge === `${want} из ${totals.listik}`
        && (want < totals.listik || !snap.more)
        ? snap
        : null
    }, 3000)
    return { ok: Boolean(done), expect, got: brief(done ?? (await snapshot())) }
  })

  await record('C. обновление очереди, пришедшее во время чтения, не теряется', async () => {
    const label = `метка-${Date.now()}`
    const expect = `за ${3 * SLOW + 1500} мс заголовок listik-fill-002 становится «${label}», в конце все строки fill и бейдж «${PAGE} из ${totals.fill}»`
    const start = await freshQueue()
    if (!start.ok) return { ok: false, expect, got: start.got }
    const streams = await waitFor(async () => {
      const body = await (await fetch(`${mockApi}/__requests`)).json()
      return body.data?.streams >= 1 ? body.data.streams : null
    }, 5000)
    if (!streams) return { ok: false, expect, got: { why: 'поток событий /api/stream не открылся' } }
    const fill = await chooseProject('Заполнитель')
    if (!fill.ok) return { ok: false, expect, got: { why: fill.why } }
    await sleep(300)
    const before = await snapshot()
    if (before.rows.length !== 0) {
      return { ok: false, expect, got: { why: 'гонка не воспроизведена: строки fill пришли раньше события', before: brief(before) } }
    }
    const event = await fetch(`${mockApi}/__event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'task', payload: { id: 'listik-fill-002' }, patch: { title: label } }),
    })
    const sent = (await event.json()).data
    const labelled = await waitFor(async () => {
      const snap = await snapshot()
      return snap.rows.some((row) => row.id === 'listik-fill-002' && row.title === label) ? snap : null
    }, 3 * SLOW + 1500)
    const last = labelled ?? (await snapshot())
    const row = last.rows.find((item) => item.id === 'listik-fill-002') ?? null
    const ok = Boolean(labelled) && allOf(last, 'fill') && last.badge === `${PAGE} из ${totals.fill}`
    return { ok, expect, got: { clients: sent?.clients ?? null, row, last: brief(last) } }
  })

  report.consoleErrors = consoleErrors
  report.cases.push({
    name: 'consoleErrors пуст',
    ok: consoleErrors.length === 0,
    expect: 'ни одного необработанного исключения или console.error',
    got: consoleErrors,
  })

  socket.close()
} catch (error) {
  report.error = String(error?.stack ?? error)
} finally {
  chrome?.kill()
  staticServer?.close()
  mock?.kill()
  if (profile) {
    try {
      rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
    } catch {
      /* временный профиль уберёт система */
    }
  }
}

console.log(JSON.stringify(report, null, 2))
const failed = report.cases.filter((item) => !item.ok).length
if (report.error || failed > 0) process.exitCode = 1
