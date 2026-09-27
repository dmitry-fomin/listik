/**
 * Проверка сортировки вида «Список» (`web/src/views/ListView.vue`, `sortedRows`) —
 * шаг listik-1e49, порция a: возраст, приоритет и зависимости сортируются числом.
 *
 * Поднимает mock-api в режиме `--list-sort` (семь задач `listik-sort-*` с литералами
 * возраста, приоритетами и блокерами), отдаёт собранный `web/dist`, открывает доску
 * в headless Chrome через CDP, включает все колонки через `localStorage['listik.columns']`,
 * переходит на вкладку «Список» и кликает по заголовкам сортируемых колонок.
 *
 * Запуск: node scripts/verify-list-sort.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок `--list-sort`).
 *
 * Печатает JSON-отчёт: `cases[]` (имя кейса, ok, ожидаемый и увиденный порядок id,
 * `ariaSort`) и `consoleErrors`. Код возврата 1, если хоть один кейс не прошёл, есть
 * `error` или в консоли были ошибки.
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

/** Ожидаемые порядки — захардкожены, без приставки `listik-sort-`. */
const ids = (line) => line.split(' ').map((name) => `listik-sort-${name}`)
const INITIAL = ids('alfa echo fox golf charlie bravo delta')
const CASES = [
  ['ID', 'asc', 'alfa bravo charlie delta echo fox golf'],
  ['ID', 'desc', 'golf fox echo delta charlie bravo alfa'],
  ['Держит, время', 'asc', 'echo golf alfa delta bravo fox charlie'],
  ['Держит, время', 'desc', 'bravo delta alfa golf echo fox charlie'],
  ['На этапе', 'asc', 'bravo echo delta charlie alfa golf fox'],
  ['На этапе', 'desc', 'golf alfa charlie delta echo bravo fox'],
  ['Обновлена', 'asc', 'alfa echo fox golf charlie bravo delta'],
  ['Обновлена', 'desc', 'delta bravo charlie golf fox echo alfa'],
  ['Приоритет', 'asc', 'golf fox charlie alfa bravo echo delta'],
  ['Приоритет', 'desc', 'delta echo alfa bravo fox charlie golf'],
  ['Ждёт', 'asc', 'echo fox golf charlie bravo alfa delta'],
  ['Ждёт', 'desc', 'delta alfa echo fox golf charlie bravo'],
  ['Её ждут', 'asc', 'alfa echo golf bravo delta charlie fox'],
  ['Её ждут', 'desc', 'fox charlie alfa echo golf bravo delta'],
]
const ARIA = { asc: 'ascending', desc: 'descending' }

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

const report = { cases: [], consoleErrors: [] }
const record = (name, run) => recordCase(report, name, run)
let mock = null
let staticServer = null
let chrome = null
const page = process.argv[2] ?? null

try {
  let url = page
  if (!url) {
    if (!existsSync(join(dist, 'index.html'))) {
      throw new Error('нет web/dist — соберите: npx vite build --configLoader runner')
    }
    const apiPort = await freePort()
    const pagePort = await freePort()
    mock = await startMock(apiPort, root, ['--list-sort'])
    staticServer = await serveDist(pagePort, apiPort, dist)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }

  chrome = startChrome(chromePath, chromePort, profile)

  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

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

  const loaded = await waitFor(async () => {
    const seen = await evaluate(TABLE)
    return !seen.skeleton && seen.order.length === INITIAL.length
      && seen.badges.includes('её ждут 2') && seen.badges.includes('ждёт 2')
      ? seen
      : null
  }, 10000)
  if (!loaded) throw new Error(`список не загрузился со срезом зависимостей: ${JSON.stringify(await evaluate(TABLE))}`)

  await record('initial', async () => {
    const got = loaded.order
    const ariaSort = loaded.headers.map((header) => header.ariaSort)
    const ok = JSON.stringify(got) === JSON.stringify(INITIAL) && ariaSort.every((value) => value === 'none')
    return { ok, expect: INITIAL, got, ariaSort }
  })

  for (const [label, direction, line] of CASES) {
    await record(`${label} ${direction}`, async () => {
      const expect = ids(line)
      const clicked = await evaluate(`(() => {
        const button = [...document.querySelectorAll('.listik-list__table th > button.ui-data-table__sort-btn')]
          .find((item) => item.textContent.replace(/\\s+/g, ' ').trim() === ${JSON.stringify(label)});
        if (!button) return false;
        button.click();
        return true;
      })()`)
      if (!clicked) return { ok: false, expect, got: `нет кнопки сортировки «${label}»`, ariaSort: null }
      // Ждём и порядок: на провале в `got` — последний увиденный.
      let last = null
      const ok = await waitFor(async () => {
        const seen = await evaluate(TABLE)
        const header = seen.headers.find((item) => item.label === label)
        last = { order: seen.order, ariaSort: header?.ariaSort ?? null }
        return last.ariaSort === ARIA[direction] && !seen.skeleton
          && JSON.stringify(seen.order) === JSON.stringify(expect)
      }, 5000)
      return { ok, expect, got: last.order, ariaSort: last.ariaSort }
    })
  }

  report.consoleErrors = consoleErrors
  socket.close()
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
