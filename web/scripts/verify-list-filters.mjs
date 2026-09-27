/**
 * Проверка фильтров вида «Список» (`web/src/store/listik.ts`, `loadListTasks`;
 * `web/src/views/ListView.vue`) — шаг listik-0xy8, порция b: здоровье, зависимости,
 * «Обновлена» и оркестратор «—» уходят на сервер параметрами `GET /api/tasks`, а таблица
 * и `из <total>` в подсказке показывают ответ сервера как есть, без фильтра на клиенте.
 *
 * Поднимает mock-api с `--fill=40` (45 задач, несколько страниц), отдаёт собранный
 * `web/dist`, открывает доску в headless Chrome через CDP, переходит на вкладку «Список»
 * и включает фильтры по одному. Мок новые параметры не применяет (см. его шапку), поэтому
 * ожидание — ответ мока на тот же запрос: если клиент досеет страницу сам, набор строк
 * разойдётся с ответом. Запросы списка берутся из `GET /__requests` (`list_queries`).
 * Шестой кейс `sort resets page` (шаг 2-p1l8) со второй страницы кликает сортировку
 * «Приоритет» и ждёт первую страницу: запросы только с `offset=0`, строки — ответ мока по порядку.
 *
 * Запуск: node scripts/verify-list-filters.mjs
 *   Нужен собранный `web/dist` (`npx vite build --configLoader runner`).
 *
 * Печатает JSON-отчёт: `cases[]` (имя кейса, ok, найденный запрос или все увиденные,
 * ожидание `{ total, ids }` и последний снимок `{ ids, hint }`) и `consoleErrors`.
 * Код возврата 1, если хоть один кейс не прошёл, есть `error` или в консоли были ошибки.
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
const profile = mkdtempSync(join(tmpdir(), 'listik-list-filters-'))

/** Параметры, которых без фильтров в запросе списка быть не должно. */
const FILTER_KEYS = ['deps', 'health', 'updated_from', 'updated_to', 'orchestrator']
const PAGE_SIZE = 25

/** Снимок таблицы: id строк, скелетон, подсказка «выбрано N из M». */
const SNAPSHOT = `(() => {
  const text = (el) => el?.textContent?.replace(/\\s+/g, ' ').trim() ?? '';
  const table = document.querySelector('.listik-list__table');
  const rows = table ? [...table.querySelectorAll('tbody tr.ui-data-table__row')] : [];
  return {
    ids: rows.map((row) => text(row.querySelector('button.listik-link .listik-mono'))),
    skeleton: table?.querySelector('tbody .ui-skeleton') != null,
    hint: text(document.querySelector('.listik-section__hint')),
  };
})()`

const RESET_BUTTON = `[...document.querySelectorAll('button')]
  .find((button) => button.textContent.replace(/\\s+/g, ' ').trim() === 'Сбросить фильтры')`

/** Клик по радио внутри группы `[role="radiogroup"][aria-label=group]`: по aria-label или тексту. */
const clickRadio = (group, label) => `(() => {
  const box = [...document.querySelectorAll('[role="radiogroup"]')]
    .find((item) => item.getAttribute('aria-label') === ${JSON.stringify(group)});
  const radio = [...(box?.querySelectorAll('[role="radio"]') ?? [])].find((item) =>
    item.getAttribute('aria-label') === ${JSON.stringify(label)} || item.textContent.trim() === ${JSON.stringify(label)});
  if (!radio) return false;
  radio.click();
  return true;
})()`

const report = { cases: [], consoleErrors: [] }
const record = (name, run) => recordCase(report, name, run)
let mock = null
let staticServer = null
let chrome = null

try {
  if (!existsSync(join(dist, 'index.html'))) {
    throw new Error('нет web/dist — соберите: npx vite build --configLoader runner')
  }
  const apiPort = await freePort()
  const pagePort = await freePort()
  mock = await startMock(apiPort, root, ['--fill=40'])
  staticServer = await serveDist(pagePort, apiPort, dist)
  const url = `http://127.0.0.1:${pagePort}/?token=mock-token`

  /** Мок напрямую: служебные ручки и «ожидаемый ответ» на тот же запрос. */
  const mockCall = async (path, body) => {
    const response = await fetch(`http://127.0.0.1:${apiPort}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(`${path}: ${payload.error ?? 'ошибка мока'}`)
    return payload.data
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

  const tabClicked = await evaluate(`(() => {
    const tab = [...document.querySelectorAll('.ui-tabs__list [role="tab"]')]
      .find((item) => item.textContent.trim().startsWith('Список'));
    if (!tab) return false;
    tab.click();
    return true;
  })()`)
  if (!tabClicked) throw new Error('вкладки «Список» нет')

  const listed = await waitFor(async () => {
    const seen = await evaluate(SNAPSHOT)
    return !seen.skeleton && seen.ids.length > 0 ? seen : null
  }, 10000)
  if (!listed) throw new Error(`список не загрузился: ${JSON.stringify(await evaluate(SNAPSHOT))}`)

  const isListQuery = (search) => {
    const params = new URLSearchParams(search)
    return params.get('limit') === String(PAGE_SIZE) && params.get('offset') === '0'
  }
  const sameIds = (left, right) => JSON.stringify(left) === JSON.stringify(right)

  /** Шаги 4–6 требования: запрос списка по проверке кейса, ответ мока на него, снимок. */
  const verify = async (check) => {
    let seen = []
    const query = await waitFor(async () => {
      seen = (await mockCall('/__requests')).list_queries
      return seen.find((search) => isListQuery(search) && check(new URLSearchParams(search))) ?? null
    }, 5000)
    if (!query) {
      const snap = await evaluate(SNAPSHOT)
      return { ok: false, query: seen, expect: null, got: { ids: snap.ids.slice().sort(), hint: snap.hint } }
    }
    const answer = await mockCall(`/api/tasks${query}`)
    const expect = { total: answer.total, ids: answer.tasks.map((task) => task.id).sort() }
    const totalMark = new RegExp(`из ${expect.total}(\\D|$)`)
    let got = null
    const ok = await waitFor(async () => {
      const snap = await evaluate(SNAPSHOT)
      got = { ids: snap.ids.slice().sort(), hint: snap.hint }
      return !snap.skeleton && sameIds(got.ids, expect.ids) && totalMark.test(snap.hint)
    }, 5000)
    return { ok: Boolean(ok), query, expect, got }
  }

  await record('baseline', () => verify((params) => FILTER_KEYS.every((key) => !params.has(key))))
  const baseTotal = report.cases[0].expect?.total
  if (baseTotal !== undefined && baseTotal <= PAGE_SIZE) {
    report.error = 'в фикстуре одна страница, проверка пагинации бессмысленна'
  }

  /** Сбросить фильтры, если есть активные: кнопка ушла, скелетона нет. */
  const resetFilters = async () => {
    const clicked = await evaluate(`(() => { const button = ${RESET_BUTTON}; if (!button) return false; button.click(); return true; })()`)
    if (!clicked) return
    const settled = await waitFor(async () => {
      const gone = await evaluate(`!(${RESET_BUTTON})`)
      return gone && !(await evaluate(SNAPSHOT)).skeleton
    }, 5000)
    if (!settled) throw new Error('фильтры не сбросились: кнопка «Сбросить фильтры» или скелетон остались')
  }

  const pickOrchestrator = async () => {
    const opened = await evaluate(`(() => {
      const field = [...document.querySelectorAll('.ui-filter-field')]
        .find((item) => item.querySelector('.ui-filter-field__label')?.textContent.trim() === 'Оркестратор');
      const trigger = field?.querySelector('.ui-select__trigger');
      if (!trigger) return false;
      trigger.click();
      return true;
    })()`)
    if (!opened) return 'нет UiSelect «Оркестратор»'
    const picked = await waitFor(
      () =>
        evaluate(`(() => {
          const option = [...document.querySelectorAll('[role="option"]')].find((item) => item.textContent.trim() === '—');
          if (!option) return false;
          option.click();
          return true;
        })()`),
      3000,
    )
    return picked ? true : 'нет опции «—» в UiSelect «Оркестратор»'
  }

  const radio = (group, label) => async () =>
    (await evaluate(clickRadio(group, label))) ? true : `нет радио «${label}» в группе «${group}»`

  const CASES = [
    ['health=dead', radio('Фильтр по здоровью', 'брошены'), (params) => params.get('health') === 'dead'],
    ['deps=ready', radio('Фильтр по зависимостям', 'можно брать'), (params) => params.get('deps') === 'ready'],
    ['orchestrator=none', pickOrchestrator, (params) => params.get('orchestrator') === 'none'],
    [
      'updated_from',
      radio('Диапазон обновления', 'сутки'),
      (params) => /^\d{4}-\d{2}-\d{2}$/.test(params.get('updated_from') ?? '') && !params.has('updated_to'),
    ],
  ]

  if (!report.error) {
    for (const [name, act, check] of CASES) {
      await record(name, async () => {
        await resetFilters()
        await mockCall('/__requests', {})
        const acted = await act()
        if (acted !== true) return { ok: false, query: null, expect: null, got: acted }
        return verify(check)
      })
    }
  }

  /** Запросы списка страницы (`limit=25`, любой `offset`) и подпись активной страницы. */
  const listQueries = async () =>
    (await mockCall('/__requests')).list_queries.filter(
      (search) => new URLSearchParams(search).get('limit') === String(PAGE_SIZE),
    )
  const PAGED = `({
    snap: ${SNAPSHOT},
    page: document.querySelector('button.ui-paginator__page[aria-current="page"]')?.getAttribute('aria-label') ?? null,
  })`
  const snapshot = async () => {
    const { snap, page } = await evaluate(PAGED)
    return { ids: snap.ids, page, skeleton: snap.skeleton }
  }

  if (!report.error) {
    await record('sort resets page', async () => {
      await resetFilters()
      const paged = await evaluate(`(() => {
        const button = document.querySelector('button.ui-paginator__page[aria-label="Страница 2"]');
        if (!button) return false;
        button.click();
        return true;
      })()`)
      if (!paged) return { ok: false, query: null, expect: null, got: 'нет кнопки «Страница 2»' }
      const onSecond = await waitFor(async () => {
        const second = (await listQueries()).some((search) => new URLSearchParams(search).get('offset') === String(PAGE_SIZE))
        const snap = await snapshot()
        return second && !snap.skeleton && snap.page === 'Страница 2'
      }, 5000)
      if (!onSecond) return { ok: false, query: await listQueries(), expect: null, got: 'не перешли на «Страницу 2»' }

      await mockCall('/__requests', {})
      const sorted = await evaluate(`(() => {
        const button = [...document.querySelectorAll('.listik-list__table th > button.ui-data-table__sort-btn')]
          .find((item) => item.textContent.replace(/\\s+/g, ' ').trim() === 'Приоритет');
        if (!button) return false;
        button.click();
        return true;
      })()`)
      if (!sorted) return { ok: false, query: null, expect: null, got: 'нет кнопки сортировки «Приоритет»' }

      let seen = []
      const query = await waitFor(async () => {
        seen = await listQueries()
        return seen.find((search) => {
          const params = new URLSearchParams(search)
          return params.get('sort') === 'priority' && params.get('dir') === 'asc'
        }) ?? null
      }, 5000)
      if (!query) {
        const { ids, page } = await snapshot()
        return { ok: false, query: seen, expect: null, got: { ids, page } }
      }
      const expect = (await mockCall(`/api/tasks${query}`)).tasks.map((task) => task.id)
      let got = null
      const ok = await waitFor(async () => {
        const allFirst = (await listQueries()).every((search) => new URLSearchParams(search).get('offset') === '0')
        const { ids, page, skeleton } = await snapshot()
        got = { ids, page }
        return allFirst && page === 'Страница 1' && !skeleton && sameIds(ids, expect)
      }, 5000)
      return { ok: Boolean(ok), query, expect, got }
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
