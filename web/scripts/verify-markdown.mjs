/**
 * Проверка markdown-вывода текстов карточки на доске (listik-k3bw, порция b):
 * поле описания рендерится `MarkdownProse` → `lib/markdown.ts` →
 * `renderMarkdownToHtml` кита, а не сырым текстом. Заголовок `##`, маркированный
 * и нумерованный списки, инлайн-код, блок в тройных кавычках и ссылка видны
 * разметкой; сырого markdown в видимом тексте не остаётся; HTML-вход экранирован
 * (нет элемента `script`, диалог не всплывал, угловые скобки видны как текст).
 *
 * Поднимает `scripts/mock-api.mjs` в режиме `--markdown` (карточка
 * `listik-markdown-case` с фикстурой) и отдаёт собранный `web/dist`; сценарии
 * гоняет в headless Chrome через CDP на общих помощниках `scripts/lib/*`.
 * Все кейсы ограничены поддеревом секции описания: корень — `<section
 * class="listik-section">` панели задачи с заголовком «Описание · ТЗ», а внутри
 * него markdown-контейнер `.listik-prose--markdown` (снято шагом 0 с реального
 * рендера). Поиск узлов идёт от этого корня, не от `document`.
 *
 * Запуск: node scripts/verify-markdown.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок `--markdown`).
 *   Путь к Chrome — из `process.env.CHROME_PATH` с дефолтом под macOS.
 *
 * Печатает JSON-отчёт: `cases[]` (имя, ok, ожидание, что увидели) и
 * `consoleErrors`. Код возврата 1, если хоть один кейс не прошёл, есть ошибки
 * консоли или сценарий упал целиком.
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
const chromePort = 9200 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-markdown-'))

/** Данные карточки-фикстуры мока `--markdown` (scripts/mock-api.mjs). */
const CASE_TITLE = 'Карточка с markdown-разметкой'
const SECTION_TITLE = 'Описание · ТЗ'

/** Ожидания кейсов — по фактическому рендеру, снятому шагом 0. */
const EXPECT = {
  headingTag: 'h2',
  heading: 'Разметка карточки',
  bullets: ['первый пункт маркированного списка', 'второй пункт маркированного списка'],
  ordered: ['первый пункт нумерованного списка', 'второй пункт нумерованного списка'],
  inlineCode: 'npm run build',
  codeLine: 'const answer = 42',
  linkText: 'текст',
  linkHref: 'https://example.com',
  escaped: '<script>alert(1)</script>',
  table: {
    head: ['Код', 'Файл', 'Итог'],
    rows: [
      ['31cc004', 'lexicon.jsonl', '40/41'],
      ['92ac192', 'нет', '41/41'],
    ],
  },
  mdClass: 'listik-prose--markdown',
}

/**
 * Снимок поддерева секции описания. Корень ищется по заголовку «Описание · ТЗ»
 * среди секций панели; markdown-контейнер берётся уже внутри корня, и все
 * выборки идут от него — ни одного `document.querySelector` по содержимому.
 */
const DESCRIPTION_STATE = `(() => {
  const sections = [...document.querySelectorAll('.ui-drawer .listik-section')];
  const title = ${JSON.stringify(SECTION_TITLE)};
  const root = sections.find((s) => s.querySelector('h4.listik-section__title')?.textContent.trim() === title);
  if (!root) {
    return { found: false, titles: sections.map((s) => s.querySelector('h4.listik-section__title')?.textContent?.trim() ?? null) };
  }
  const md = root.querySelector('.listik-prose--markdown');
  const scope = md ?? root;
  const textOf = (el) => (el?.textContent ?? '').replace(/\\s+/g, ' ').trim();
  const itemsOf = (list) => [...list.children].filter((el) => el.tagName === 'LI').map(textOf);
  const inlineCode = [...scope.querySelectorAll('code')].filter((el) => !el.closest('pre'));
  return {
    found: true,
    rootTitle: root.querySelector('h4.listik-section__title')?.textContent?.trim() ?? null,
    mdClass: md?.className ?? null,
    headings: [...scope.querySelectorAll('h1, h2, h3, h4, h5, h6')].map((el) => ({ tag: el.tagName.toLowerCase(), text: textOf(el) })),
    ul: [...scope.querySelectorAll('ul')].map(itemsOf),
    ol: [...scope.querySelectorAll('ol')].map(itemsOf),
    inlineCode: inlineCode.map(textOf),
    pre: [...scope.querySelectorAll('pre')].map((el) => ({ codeTag: el.querySelector('code')?.tagName.toLowerCase() ?? null, code: textOf(el.querySelector('code')) })),
    tables: [...scope.querySelectorAll('table')].map((el) => ({
      head: [...el.querySelectorAll('thead th')].map(textOf),
      rows: [...el.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map(textOf)),
    })),
    links: [...scope.querySelectorAll('a')].map((el) => ({ text: textOf(el), href: el.getAttribute('href') })),
    scripts: scope.querySelectorAll('script').length,
    innerText: scope.innerText,
  };
})()`

/** Строки видимого текста, начинающиеся с сырого маркера (`- `). */
function rawBulletLines(innerText) {
  return innerText.split(/\r?\n/).filter((line) => line.startsWith('- '))
}

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
    mock = await startMock(apiPort, root, ['--markdown'])
    staticServer = await serveDist(pagePort, apiPort, dist)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }

  chrome = startChrome(chromePath, chromePort, profile)

  const { socket, ready, send, evaluate, consoleErrors, events } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

  await send('Page.navigate', { url })
  const boardReady = await waitFor(
    async () => ((await evaluate(`document.querySelectorAll('.listik-task-card').length`)) > 0 ? true : null),
    15000,
  )
  if (!boardReady) {
    const diagnostics = await evaluate(`(() => ({
      url: location.href,
      token: localStorage.getItem('listik.token'),
      text: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 400),
      cards: document.querySelectorAll('.listik-task-card').length,
    }))()`)
    throw new Error(`доска не отрисовалась: ${JSON.stringify(diagnostics)} ${JSON.stringify(consoleErrors)}`)
  }

  // Открываем карточку фикстуры: только через доску, как соседние verify-*.
  const opened = await evaluate(`(() => {
    const card = [...document.querySelectorAll('.listik-task-card')]
      .find((el) => el.getAttribute('aria-label') === 'Открыть задачу ' + ${JSON.stringify(CASE_TITLE)});
    if (!card) return false;
    card.click();
    return true;
  })()`)
  if (!opened) throw new Error(`на доске нет карточки «${CASE_TITLE}» — мок запущен не с --markdown`)
  // Ждём саму секцию описания (панель догрузила карточку); кейсы пишутся и при
  // отсутствии markdown-контейнера — тогда они красные, а не падение сценария.
  const sectionReady = await waitFor(async () => {
    const state = await evaluate(DESCRIPTION_STATE)
    return state.found ? state : null
  }, 10000)
  if (!sectionReady) {
    throw new Error(`секция «${SECTION_TITLE}» не найдена: ${JSON.stringify(await evaluate(DESCRIPTION_STATE))}`)
  }

  const state = () => evaluate(DESCRIPTION_STATE)
  /** Диалог alert из неэкранированного `<script>`: CDP шлёт это событие при исполнении. */
  const dialogCount = () => events.filter((event) => event.method === 'Page.javascriptDialogOpening').length

  await record('markdown-контейнер', async () => {
    const seen = await state()
    const okClass = EXPECT.mdClass
    return {
      ok: Boolean(seen.mdClass && seen.mdClass.split(/\s+/).includes(okClass)),
      expect: `в секции «${SECTION_TITLE}» есть .${okClass}`,
      got: { rootTitle: seen.rootTitle, mdClass: seen.mdClass },
    }
  })

  await record('заголовок ##', async () => {
    const seen = await state()
    const match = seen.headings.find((item) => item.text === EXPECT.heading) ?? null
    return {
      ok: Boolean(match && match.tag === EXPECT.headingTag),
      expect: `<${EXPECT.headingTag}> с текстом «${EXPECT.heading}»`,
      got: seen.headings,
    }
  })

  await record('маркированный список', async () => {
    const seen = await state()
    return {
      ok: seen.ul.some((items) => JSON.stringify(items) === JSON.stringify(EXPECT.bullets)),
      expect: `<ul><li>${EXPECT.bullets.join('</li><li>')}</li></ul>`,
      got: seen.ul,
    }
  })

  await record('нумерованный список', async () => {
    const seen = await state()
    return {
      ok: seen.ol.some((items) => JSON.stringify(items) === JSON.stringify(EXPECT.ordered)),
      expect: `<ol><li>${EXPECT.ordered.join('</li><li>')}</li></ol>`,
      got: seen.ol,
    }
  })

  await record('инлайн-код', async () => {
    const seen = await state()
    return {
      ok: seen.inlineCode.includes(EXPECT.inlineCode),
      expect: `code вне pre с текстом «${EXPECT.inlineCode}»`,
      got: { inlineCode: seen.inlineCode, preCount: seen.pre.length },
    }
  })

  await record('блок кода', async () => {
    const seen = await state()
    const match = seen.pre.find((block) => block.code.includes(EXPECT.codeLine)) ?? null
    return {
      ok: Boolean(match && match.codeTag === 'code'),
      expect: `<pre><code> со строкой «${EXPECT.codeLine}»`,
      got: seen.pre,
    }
  })

  await record('таблица', async () => {
    const seen = await state()
    const match =
      seen.tables.find((table) => JSON.stringify(table.head) === JSON.stringify(EXPECT.table.head)) ?? null
    return {
      ok: Boolean(match && JSON.stringify(match.rows) === JSON.stringify(EXPECT.table.rows)),
      expect: `<table> с шапкой ${EXPECT.table.head.join(' | ')} и ${EXPECT.table.rows.length} строками`,
      got: seen.tables,
    }
  })

  await record('ссылка', async () => {
    const seen = await state()
    const match = seen.links.find((link) => link.href === EXPECT.linkHref && link.text === EXPECT.linkText) ?? null
    return {
      ok: Boolean(match),
      expect: `a[href="${EXPECT.linkHref}"] с текстом «${EXPECT.linkText}»`,
      got: seen.links,
    }
  })

  await record('сырой разметки не осталось', async () => {
    const seen = await state()
    const bullets = rawBulletLines(seen.innerText)
    const checks = {
      'нет ##': !seen.innerText.includes('##'),
      'нет строк с - в начале': bullets.length === 0,
      'нет тройных кавычек': !seen.innerText.includes('```'),
      'нет строки-разделителя таблицы': !seen.innerText.includes('|---|'),
    }
    return {
      ok: Object.values(checks).every(Boolean),
      expect: 'в видимом тексте секции нет ##, строк «- » и тройных обратных кавычек',
      got: { checks, bulletLines: bullets, innerText: seen.innerText },
    }
  })

  await record('экранирование HTML', async () => {
    const seen = await state()
    const dialogs = dialogCount()
    const checks = {
      'нет элемента script': seen.scripts === 0,
      'скрипт не исполнялся': dialogs === 0,
      'угловые скобки видны текстом': seen.innerText.includes(EXPECT.escaped),
    }
    return {
      ok: Object.values(checks).every(Boolean),
      expect: `нет <script>, нет диалога alert, видно текстом «${EXPECT.escaped}»`,
      got: { checks, scripts: seen.scripts, dialogs, visible: seen.innerText.includes(EXPECT.escaped) },
    }
  })

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
