/**
 * Проверка сортировки вида «Список» (`web/src/views/ListView.vue`) — шаг 2-p1l8: «Список»
 * отправляет сортировку серверу параметрами `sort`/`dir` запроса `GET /api/tasks` и показывает
 * строки в порядке ответа, без своей сортировки.
 *
 * Поднимает mock-api в режиме `--list-sort` (семь задач `listik-sort-*` с литералами
 * возраста, приоритетами и блокерами) и до запуска Chrome сверяет с таблицей кейсов ответ
 * мока на каждую пару `sort`/`dir`: расхождение — `error`, браузерные кейсы не идут. Затем
 * отдаёт собранный `web/dist`, открывает доску в headless Chrome через CDP, включает все
 * колонки через `localStorage['listik.columns']`, переходит на вкладку «Список» и кликает по
 * заголовкам сортируемых колонок. Запросы списка — события `Network.requestWillBeSent`
 * (`GET /api/tasks` с `limit=25`): после каждого клика ровно один новый, с ожидаемыми
 * `sort`/`dir`.
 *
 * Запуск: node scripts/verify-list-sort.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок `--list-sort`),
 *   сверка мока с таблицей пропускается.
 *
 * Печатает JSON-отчёт: `cases[]` (имя кейса, ok, ожидаемый и увиденный порядок id,
 * `ariaSort`, `query` — `search` новых запросов списка) и `consoleErrors`. Код возврата 1,
 * если хоть один кейс не прошёл, есть `error` или в консоли были ошибки.
 */
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { cdpTarget, connect, freePort, serveDist, startChrome, startMock } from './lib/browser-harness.mjs'
import { record as recordCase, waitFor } from './lib/verify-helpers.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')
const chromePath = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const chromePort = 9800 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-list-sort-'))

/** Каталог колонок `ListView.vue` дословно: ключ, подпись заголовка, порядок. */
const COLUMNS = [
  ['id', 'ID'],
  ['title', 'Заголовок'],
  ['project', 'Проект'],
  ['status_title', 'Статус'],
  ['stage_title', 'Этап'],
  ['priority_title', 'Приоритет'],
  ['issue_type', 'Тип'],
  ['orchestrator_title', 'Оркестратор'],
  ['holder_title', 'Держит'],
  ['holder_age', 'Держит, время'],
  ['stage_age', 'На этапе'],
  ['updated_age', 'Обновлена'],
  ['blocked_count', 'Ждёт'],
  ['waiting_count', 'Её ждут'],
  ['labels', 'Метки'],
].map(([key, label], order) => ({ key, label, visible: true, order }))

const SORTABLE = [
  'ID',
  'Заголовок',
  'Проект',
  'Статус',
  'Приоритет',
  'Держит, время',
  'На этапе',
  'Обновлена',
  'Ждёт',
  'Её ждут',
]

/**
 * Кейсы по порядку: имя («подпись направление» или `initial`), ожидаемый порядок и
 * `sort`/`dir` запроса. Порядки захардкожены, без приставки `listik-sort-`.
 */
const ids = (line) => line.split(' ').map((name) => `listik-sort-${name}`)
const CASES = [
  ['initial', 'alfa echo fox golf charlie bravo delta', 'updated_at', 'desc'],
  ['ID asc', 'alfa bravo charlie delta echo fox golf', 'id', 'asc'],
  ['ID desc', 'golf fox echo delta charlie bravo alfa', 'id', 'desc'],
  ['Заголовок asc', 'alfa bravo charlie delta echo fox golf', 'title', 'asc'],
  ['Заголовок desc', 'golf fox echo delta charlie bravo alfa', 'title', 'desc'],
  ['Проект asc', 'alfa echo fox golf charlie bravo delta', 'project', 'asc'],
  ['Проект desc', 'alfa echo fox golf charlie bravo delta', 'project', 'desc'],
  ['Статус asc', 'alfa echo golf bravo delta fox charlie', 'status_title', 'asc'],
  ['Статус desc', 'fox charlie alfa echo golf bravo delta', 'status_title', 'desc'],
  ['Держит, время asc', 'echo golf alfa delta bravo fox charlie', 'holder_hours', 'asc'],
  ['Держит, время desc', 'bravo delta alfa golf echo fox charlie', 'holder_hours', 'desc'],
  ['На этапе asc', 'bravo echo delta charlie alfa golf fox', 'stage_hours', 'asc'],
  ['На этапе desc', 'golf alfa charlie delta echo bravo fox', 'stage_hours', 'desc'],
  ['Обновлена asc', 'alfa echo fox golf charlie bravo delta', 'updated_at', 'desc'],
  ['Обновлена desc', 'delta bravo charlie golf fox echo alfa', 'updated_at', 'asc'],
  ['Приоритет asc', 'golf fox charlie alfa bravo echo delta', 'priority', 'asc'],
  ['Приоритет desc', 'delta echo alfa bravo fox charlie golf', 'priority', 'desc'],
  ['Ждёт asc', 'echo fox golf charlie bravo alfa delta', 'blocked_count', 'asc'],
  ['Ждёт desc', 'delta alfa echo fox golf charlie bravo', 'blocked_count', 'desc'],
  ['Её ждут asc', 'alfa echo golf bravo delta charlie fox', 'waiting_for_count', 'asc'],
  ['Её ждут desc', 'fox charlie alfa echo golf bravo delta', 'waiting_for_count', 'desc'],
].map(([name, line, sort, dir]) => ({ name, expect: ids(line), sort, dir }))
const ARIA = { asc: 'ascending', desc: 'descending' }
const PAGE_SIZE = '25'

/** Состояние таблицы: подписи сортируемых заголовков с `aria-sort`, строки, скелетон, бейджи. */
const TABLE = `(() => {
  const text = (el) => el?.textContent?.replace(/\\s+/g, ' ').trim() ?? '';
  const table = document.querySelector('.listik-list__table');
  if (!table) return null;
  const rows = [...table.querySelectorAll('tbody tr.ui-data-table__row')];
  return {
    headers: [...table.querySelectorAll('th > button.ui-data-table__sort-btn')].map((button) => ({
      label: text(button),
      ariaSort: button.parentElement.getAttribute('aria-sort'),
    })),
    skeleton: table.querySelector('tbody .ui-skeleton') !== null,
    order: rows.map((row) => text(row.querySelector('button.listik-link .listik-mono'))),
    badges: [...table.querySelectorAll('tbody .ui-badge')].map(text),
  };
})()`

const same = (left, right) => JSON.stringify(left) === JSON.stringify(right)
/** Совпадают ли `sort`/`dir` запроса с ожидаемыми. */
const queryMatches = (search, { sort, dir }) => {
  const params = new URLSearchParams(search ?? '')
  return params.get('sort') === sort && params.get('dir') === dir
}

/** Ответ мока на каждую пару `sort`/`dir` таблицы — до браузера; расхождения списком. */
async function mockMismatches(apiPort) {
  const mismatches = []
  for (const item of CASES) {
    const response = await fetch(`http://127.0.0.1:${apiPort}/api/tasks?limit=${PAGE_SIZE}&sort=${item.sort}&dir=${item.dir}`)
    const payload = await response.json()
    const got = payload.data?.tasks?.map((task) => task.id) ?? payload
    if (!same(got, item.expect)) mismatches.push({ case: item.name, expect: item.expect, got })
  }
  return mismatches
}

const report = { cases: [], consoleErrors: [] }
const record = (name, run) => recordCase(report, name, run)
let mock = null
let staticServer = null
let chrome = null
const page = process.argv[2] ?? null

async function browserCases(url) {
  chrome = startChrome(chromePath, chromePort, profile)

  const { socket, ready, send, evaluate, consoleErrors, events } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')
  await send('Network.enable')

  /** `search` запросов списка по порядку: `GET /api/tasks` с `limit=25`, без `OPTIONS`. */
  const listQueries = () =>
    events
      .filter((event) => event.method === 'Network.requestWillBeSent' && event.params.request.method === 'GET')
      .map((event) => new URL(event.params.request.url))
      .filter((target) => target.pathname === '/api/tasks' && target.searchParams.get('limit') === PAGE_SIZE)
      .map((target) => target.search)

  await send('Page.navigate', { url })
  const boardReady = await waitFor(
    async () => ((await evaluate(`document.querySelectorAll('.listik-task-card').length`)) > 0 ? true : null),
    15000,
  )
  if (!boardReady) throw new Error(`доска не отрисовалась: ${JSON.stringify(consoleErrors)}`)

  // Пока открыта доска, `ListView` ещё не создан: настройки колонок он прочтёт при переходе.
  await evaluate(`localStorage.setItem('listik.columns', ${JSON.stringify(JSON.stringify(COLUMNS))})`)
  const tabClicked = await evaluate(`(() => {
    const tab = [...document.querySelectorAll('.ui-tabs__list [role="tab"]')]
      .find((item) => item.textContent.trim().startsWith('Список'));
    if (!tab) return false;
    tab.click();
    return true;
  })()`)
  if (!tabClicked) throw new Error('вкладки «Список» нет')

  const headed = await waitFor(async () => {
    const seen = await evaluate(TABLE)
    const labels = seen?.headers.map((header) => header.label) ?? []
    return SORTABLE.every((label) => labels.includes(label)) ? seen : null
  }, 10000)
  if (!headed) {
    const labels = (await evaluate(TABLE))?.headers.map((header) => header.label) ?? []
    throw new Error(`в шапке нет всех сортируемых колонок: найдены ${JSON.stringify(labels)}`)
  }

  const [initial, ...columnCases] = CASES
  const loaded = await waitFor(async () => {
    const seen = await evaluate(TABLE)
    return !seen.skeleton && seen.order.length === initial.expect.length
      && seen.badges.includes('её ждут 2') && seen.badges.includes('ждёт 2')
      ? seen
      : null
  }, 10000)
  if (!loaded) throw new Error(`список не загрузился со срезом зависимостей: ${JSON.stringify(await evaluate(TABLE))}`)

  await record(initial.name, async () => {
    const got = loaded.order
    const ariaSort = loaded.headers.map((header) => header.ariaSort)
    const query = listQueries()[0] ?? null
    const ok = same(got, initial.expect) && ariaSort.every((value) => value === 'none')
      && queryMatches(query, initial)
    return { ok, expect: initial.expect, got, ariaSort, query }
  })

  for (const item of columnCases) {
    await record(item.name, async () => {
      const cut = item.name.lastIndexOf(' ')
      const label = item.name.slice(0, cut)
      const direction = item.name.slice(cut + 1)
      const before = listQueries().length
      const clicked = await evaluate(`(() => {
        const button = [...document.querySelectorAll('.listik-list__table th > button.ui-data-table__sort-btn')]
          .find((item) => item.textContent.replace(/\\s+/g, ' ').trim() === ${JSON.stringify(label)});
        if (!button) return false;
        button.click();
        return true;
      })()`)
      if (!clicked) {
        return { ok: false, expect: item.expect, got: `нет кнопки сортировки «${label}»`, ariaSort: null, query: [] }
      }
      // Ждём и порядок: на провале в `got` — последний увиденный.
      let last = null
      const settled = await waitFor(async () => {
        const seen = await evaluate(TABLE)
        const header = seen.headers.find((entry) => entry.label === label)
        last = { order: seen.order, ariaSort: header?.ariaSort ?? null }
        return last.ariaSort === ARIA[direction] && !seen.skeleton && same(seen.order, item.expect)
      }, 5000)
      // Запросы считаются после того, как порядок и `aria-sort` сошлись.
      const query = listQueries().slice(before)
      const ok = Boolean(settled) && query.length === 1 && queryMatches(query[0], item)
      return { ok, expect: item.expect, got: last.order, ariaSort: last.ariaSort, query }
    })
  }

  report.consoleErrors = consoleErrors
  socket.close()
}

try {
  let url = page
  let mismatches = []
  if (!url) {
    if (!existsSync(join(dist, 'index.html'))) {
      throw new Error('нет web/dist — соберите: npx vite build --configLoader runner')
    }
    const apiPort = await freePort()
    const pagePort = await freePort()
    mock = await startMock(apiPort, root, ['--list-sort'])
    mismatches = await mockMismatches(apiPort)
    staticServer = await serveDist(pagePort, apiPort, dist)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }
  if (mismatches.length > 0) {
    report.error = `мок расходится с таблицей кейсов: ${JSON.stringify(mismatches)}`
  } else {
    await browserCases(url)
  }
} catch (error) {
  report.error = String(error?.stack ?? error)
} finally {
  chrome?.kill()
  staticServer?.close()
  mock?.kill()
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  } catch {
    /* временный профиль уберёт система */
  }
}

console.log(JSON.stringify(report, null, 2))
const failed = report.cases.filter((item) => !item.ok).length
if (report.error || failed > 0 || report.consoleErrors.length > 0) process.exitCode = 1
