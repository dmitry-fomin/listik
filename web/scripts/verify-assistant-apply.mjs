/**
 * Проверка применения подсказок помощника DeepSeek в форме «Новая задача»
 * (`web/src/components/AssistantField.vue`): кнопки «Применить текст»,
 * «Добавить критерии (N)» и «Выбрать маршрут» больше не закрывают поповер, а
 * прячут применённую секцию целиком — вместе с заголовком и кнопкой; отметки
 * «применено» снимаются в начале `ask()`, поэтому и новое открытие, и
 * «Повторить» показывают ответ целиком; когда видимых секций с кнопкой
 * применения не осталось, панель пишет «Все предложения применены.» (видимая
 * безкнопочная ветка «DeepSeek не выбрал маршрут» подсказке не мешает).
 *
 * Поднимает mock-api в режиме `--routes --assistant` (ответ подбирается по
 * тексту поля: `full`, `same`, `blocked`, `noroute`, `noacc`, `error`), отдаёт
 * собранный `web/dist` и гоняет сценарии в headless Chrome через CDP.
 *
 * Запуск: node scripts/verify-assistant-apply.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок
 *   `--routes --assistant`).
 *
 * Печатает JSON-отчёт: `cases[]` (имя, ok, что увидели) и `consoleErrors`.
 * Код возврата 1, если хоть один сценарий не прошёл.
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
const chromePort = 9200 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-assist-'))

/** Доступные имена кнопок помощника — по ним скрипт находит поле и его панель. */
const DESCRIPTION_LABEL = 'Помощник: Описание · ТЗ'
const ACCEPTANCE_LABEL = 'Помощник: Критерии приёмки'
/** Видимые тексты панели и заголовки секций (классы `listik-assist__*`). */
const ALL_APPLIED = 'Все предложения применены.'
const NO_ROUTE = 'DeepSeek не выбрал маршрут'
const TEXT_BLOCK = 'Переписанный текст'
const ACCEPTANCE_BLOCK = 'Дописать в приёмку'
const ROUTE_BLOCK = 'Маршрут'
const ROUTE_PICKER = '[role="radiogroup"][aria-label="Маршрут запуска"]'

/* ── Сценарии ───────────────────────────────────────────────────────────── */

/** Состояние формы и панели помощника: секции с кнопками, подсказки, значения полей. */
const STATE = `(() => {
  const fieldOf = (label) => {
    const trigger = [...document.querySelectorAll('.listik-assist__trigger')]
      .find((el) => el.getAttribute('aria-label') === label);
    const field = trigger?.closest('.listik-assist')?.querySelector('textarea, input');
    return field ? field.value : null;
  };
  const drawer = [...document.querySelectorAll('.ui-drawer')]
    .find((el) => el.querySelector('.ui-drawer__title')?.textContent.includes('Новая задача')) ?? null;
  const panel = document.querySelector('.ui-popover__panel .listik-assist__panel');
  const blocks = panel ? [...panel.querySelectorAll('.listik-assist__block')] : [];
  const blockOf = (title) => blocks
    .find((el) => el.querySelector('.listik-assist__block-title')?.textContent.trim() === title) ?? null;
  const routeBlock = blockOf(${JSON.stringify(ROUTE_BLOCK)});
  const text = (el) => el?.textContent.replace(/\\s+/g, ' ').trim() ?? null;
  return {
    modal: Boolean(drawer),
    selectedRoute: drawer
      ?.querySelector(${JSON.stringify(`${ROUTE_PICKER} [role="radio"].is-on`)})
      ?.getAttribute('data-route-key') ?? null,
    description: fieldOf(${JSON.stringify(DESCRIPTION_LABEL)}),
    acceptance: fieldOf(${JSON.stringify(ACCEPTANCE_LABEL)}),
    panelOpen: Boolean(panel),
    loading: Boolean(panel?.querySelector('.listik-assist__state')),
    error: panel?.querySelector('.ui-alert') ? text(panel.querySelector('.ui-alert')) : null,
    headTitle: text(panel?.querySelector('.listik-assist__head-title')),
    badge: text(panel?.querySelector('.ui-badge')),
    reason: text(panel?.querySelector('.listik-assist__head + .listik-section__hint')),
    footnote: text(panel?.querySelector('.listik-assist__footnote')),
    hints: panel
      ? [...panel.querySelectorAll('.listik-section__hint')].map((el) => text(el))
      : [],
    blocks: blocks.map((el) => {
      const button = el.querySelector('button');
      return {
        title: text(el.querySelector('.listik-assist__block-title')),
        button: button ? { text: text(button), disabled: button.disabled } : null,
      };
    }),
    suggestionText: panel?.querySelector('.listik-assist__text')?.textContent ?? null,
    criteria: panel
      ? [...panel.querySelectorAll('.listik-assist__list li')].map((el) => el.textContent.trim())
      : [],
    routeKey: text(routeBlock?.querySelector('.ui-badge')),
    routeTitle: text(routeBlock?.querySelector('.listik-assist__route')),
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
    mock = await startMock(apiPort, root, ['--routes', '--assistant'])
    staticServer = await serveDist(pagePort, apiPort, dist)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }

  chrome = startChrome(chromePath, chromePort, profile)

  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

  const state = () => evaluate(STATE)

  /** Кнопка помощника у поля: клик по ней — toggle поповера кита. */
  const clickTrigger = (label) => evaluate(`(() => {
    const trigger = [...document.querySelectorAll('.listik-assist__trigger')]
      .find((el) => el.getAttribute('aria-label') === ${JSON.stringify(label)});
    if (!trigger) return false;
    trigger.click();
    return true;
  })()`)

  /**
   * Кнопка применения внутри секции панели — настоящим mouse-событием: именно
   * так проверяется, что кит не закрывает поповер по клику внутри своей панели
   * (`.click()` этот путь не трогает). У `disabled`-кнопки браузер клика не
   * шлёт — сценарий ждёт, что секция останется на месте.
   */
  async function clickBlockButton(title) {
    const point = await evaluate(`(() => {
      const panel = document.querySelector('.ui-popover__panel .listik-assist__panel');
      const block = panel ? [...panel.querySelectorAll('.listik-assist__block')]
        .find((el) => el.querySelector('.listik-assist__block-title')?.textContent.trim() === ${JSON.stringify(title)}) : null;
      const button = block?.querySelector('button');
      if (!button) return null;
      const rect = button.getBoundingClientRect();
      return {
        x: Math.round(rect.left + rect.width / 2),
        y: Math.round(rect.top + rect.height / 2),
        viewport: { width: window.innerWidth, height: window.innerHeight },
      };
    })()`)
    if (!point) return false
    if (point.x < 0 || point.y < 0 || point.x >= point.viewport.width || point.y >= point.viewport.height) {
      throw new Error(`кнопка секции «${title}» вне окна: ${JSON.stringify(point)}`)
    }
    await send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1,
    })
    await send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1,
    })
    return true
  }

  /** «Повторить» в алерте ошибки: единственная точка второго запроса. */
  const clickRetry = () => evaluate(`(() => {
    const button = document.querySelector('.ui-popover__panel .listik-assist__panel .ui-alert button');
    if (!button) return false;
    button.click();
    return true;
  })()`)

  /** Ввод в «Описание · ТЗ»: UiTextarea — нативный textarea с v-model по input. */
  const typeDescription = (value) => evaluate(`(() => {
    const trigger = [...document.querySelectorAll('.listik-assist__trigger')]
      .find((el) => el.getAttribute('aria-label') === ${JSON.stringify(DESCRIPTION_LABEL)});
    const field = trigger?.closest('.listik-assist')?.querySelector('textarea');
    if (!field) return false;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(field, ${JSON.stringify(value)});
    field.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  })()`)

  const openNewTask = () => evaluate(`(() => {
    const button = [...document.querySelectorAll('button')]
      .find((el) => el.textContent.trim() === 'Новая задача');
    if (!button) return false;
    button.click();
    return true;
  })()`)

  const closePanel = async () => {
    if (!(await state()).panelOpen) return
    await clickTrigger(DESCRIPTION_LABEL)
    const closed = await waitFor(async () => ((await state()).panelOpen ? null : true), 3000)
    if (!closed) throw new Error('поповер помощника не закрылся')
  }

  /** Escape закрывает верхний слой оверлеев; панель помощника перед этим убираем сами. */
  async function closeModal() {
    if (!(await state()).modal) return
    await closePanel()
    for (const type of ['rawKeyDown', 'keyUp']) {
      await send('Input.dispatchKeyEvent', {
        type,
        key: 'Escape',
        code: 'Escape',
        windowsVirtualKeyCode: 27,
        nativeVirtualKeyCode: 27,
      })
    }
    const closed = await waitFor(async () => ((await state()).modal ? null : true), 3000)
    if (!closed) throw new Error('форма «Новая задача» не закрылась')
  }

  /** Открыть форму заново: на закрытии `NewTaskModal` сбрасывает поля и маршрут. */
  async function openModal() {
    if (!(await state()).modal) {
      if (!(await openNewTask())) throw new Error('кнопка «Новая задача» не найдена')
      const opened = await waitFor(async () => ((await state()).modal ? true : null), 5000)
      if (!opened) throw new Error('форма «Новая задача» не открылась')
    }
    // Кнопок помощника нет, пока не приехал `GET /api/assistant/status` (один запрос за сессию).
    const buttons = await waitFor(
      async () => ((await evaluate(`document.querySelectorAll('.listik-assist__trigger').length`)) > 0 ? true : null),
      8000,
    )
    if (!buttons) throw new Error('кнопки помощника не появились: мок не отдал /api/assistant/status')
  }

  /** Ответ загружен: спиннера нет, панель либо с ошибкой, либо с секциями. */
  const loadedState = async (timeout = 10000) =>
    waitFor(async () => {
      const seen = await state()
      if (!seen.panelOpen || seen.loading) return null
      return seen.error || seen.blocks.length ? seen : null
    }, timeout)

  /**
   * Предусловие любого сценария: открыта «Новая задача», в «Описание · ТЗ»
   * введён указанный текст, помощник этого поля открыт, ответ загружен.
   * Форму открываем заново: кейсы не зависят друг от друга, а маршрут в форме
   * снова умолчательный. Поповер закрываем тем же триггером — закрытие отметки
   * «применено» не снимает, их снимает только `ask()`. Отметки внутри одной
   * формы проверяют кейсы 12: они закрывают и открывают один поповер.
   */
  async function scenario(text) {
    await closeModal()
    await openModal()
    await closePanel()
    if (!(await typeDescription(text))) throw new Error('поле «Описание · ТЗ» не найдено')
    if (!(await clickTrigger(DESCRIPTION_LABEL))) throw new Error('кнопка помощника у «Описание · ТЗ» не найдена')
    const loaded = await loadedState()
    if (!loaded) throw new Error(`ответ помощника не загрузился: ${JSON.stringify(await state())}`)
    return loaded
  }

  /** Секции с кнопкой применения, которые видит пользователь. */
  const blockTitles = (seen) => (seen.blocks ?? []).map((block) => block.title)

  await send('Page.navigate', { url })
  // Ждём, пока доска отрисует карточки (мок отвечает сразу, запас — на шрифты и SSE).
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
  if (!(await openNewTask())) throw new Error('кнопка «Новая задача» не найдена')
  await openModal()

  // 5. «Применить текст»: текст ушёл в поле, поповер открыт, секция скрыта.
  await record('full: «Применить текст» — текст в поле, поповер открыт, секция скрыта', async () => {
    const before = await scenario('full')
    const expected = before.suggestionText
    const clicked = await clickBlockButton(TEXT_BLOCK)
    const after = await waitFor(async () => {
      const seen = await state()
      return seen.panelOpen && !blockTitles(seen).includes(TEXT_BLOCK) ? seen : null
    })
    const ok = Boolean(clicked) && Boolean(expected) && Boolean(after)
      && after.description === expected
      && blockTitles(after).includes(ACCEPTANCE_BLOCK)
      && blockTitles(after).includes(ROUTE_BLOCK)
    return {
      ok,
      expect: 'поле описания = текст подсказки, поповер открыт, «Переписанного текста» нет, соседние секции видны',
      got: {
        clicked,
        expected,
        field: after?.description ?? (await state()).description,
        panelOpen: after?.panelOpen ?? (await state()).panelOpen,
        blocks: after ? blockTitles(after) : null,
      },
    }
  })

  // 6. «Добавить критерии (N)»: оба пункта в приёмке, скрыта только эта секция.
  await record('full: «Добавить критерии» — критерии в приёмке, скрыта только их секция', async () => {
    const before = await scenario('full')
    const criteria = before.criteria
    const clicked = await clickBlockButton(ACCEPTANCE_BLOCK)
    const after = await waitFor(async () => {
      const seen = await state()
      return seen.panelOpen && !blockTitles(seen).includes(ACCEPTANCE_BLOCK) ? seen : null
    })
    const ok = Boolean(clicked) && criteria.length === 2 && Boolean(after)
      && criteria.every((item) => (after.acceptance ?? '').includes(item))
      && blockTitles(after).includes(TEXT_BLOCK)
      && blockTitles(after).includes(ROUTE_BLOCK)
    return {
      ok,
      expect: 'оба критерия в «Критерии приёмки», поповер открыт, «Дописать в приёмку» скрыта, текст и маршрут видны',
      got: {
        clicked,
        criteria,
        acceptance: after?.acceptance ?? (await state()).acceptance,
        blocks: after ? blockTitles(after) : null,
      },
    }
  })

  // 7. «Выбрать маршрут»: маршрут выбран в матрице, секция исчезла целиком.
  await record('full: «Выбрать маршрут» — маршрут выбран в форме, секции нет совсем', async () => {
    const before = await scenario('full')
    const routeKey = before.routeKey
    const clicked = await clickBlockButton(ROUTE_BLOCK)
    const after = await waitFor(async () => {
      const seen = await state()
      return seen.panelOpen && !blockTitles(seen).includes(ROUTE_BLOCK) ? seen : null
    })
    const ok = Boolean(clicked) && Boolean(routeKey) && Boolean(after)
      && after.selectedRoute === routeKey
    return {
      ok,
      expect: `маршрут ${routeKey} выбран в матрице формы, поповер открыт, секции «Маршрут» нет (ни «Уже выбран», ни «не выбрал маршрут»)`,
      got: {
        clicked,
        routeKey,
        selectedBefore: before.selectedRoute,
        selectedAfter: after?.selectedRoute ?? (await state()).selectedRoute,
        panelOpen: after?.panelOpen ?? (await state()).panelOpen,
        blocks: after ? blockTitles(after) : null,
      },
    }
  })

  // 8. Все три применены: шапка, бейдж, причина и сноска на месте, подсказка и пустая панель.
  await record('full: применены все три — шапка и подсказка «Все предложения применены.»', async () => {
    const before = await scenario('full')
    await clickBlockButton(TEXT_BLOCK)
    await clickBlockButton(ACCEPTANCE_BLOCK)
    await clickBlockButton(ROUTE_BLOCK)
    const after = await waitFor(async () => {
      const seen = await state()
      return seen.panelOpen && blockTitles(seen).length === 0 && seen.hints.includes(ALL_APPLIED) ? seen : null
    })
    const ok = Boolean(after)
      && after.headTitle === 'Помощник · deepseek-mock'
      && Boolean(after.badge?.startsWith('сложность:'))
      && Boolean(before.reason) && after.reason === before.reason
      && Boolean(after.footnote)
      && after.hints.includes(ALL_APPLIED)
      && blockTitles(after).length === 0
    return {
      ok,
      expect: 'поповер открыт, шапка «Помощник · deepseek-mock», бейдж сложности, строка причины, сноска и подсказка; секций нет',
      got: {
        headTitle: after?.headTitle ?? null,
        badge: after?.badge ?? null,
        reason: after?.reason ?? null,
        expectedReason: before.reason,
        footnote: after?.footnote ?? null,
        hints: after?.hints ?? null,
        blocks: after ? blockTitles(after) : null,
      },
    }
  })

  // 9. `noroute`: безкнопочная ветка видна и подсказке не мешает.
  await record('noroute: применены текст и критерии — подсказка и ветка «не выбрал маршрут»', async () => {
    const before = await scenario('noroute')
    const routeBlock = before.blocks.find((block) => block.title === ROUTE_BLOCK)
    await clickBlockButton(TEXT_BLOCK)
    await clickBlockButton(ACCEPTANCE_BLOCK)
    const after = await waitFor(async () => {
      const seen = await state()
      return seen.panelOpen && seen.hints.includes(ALL_APPLIED) ? seen : null
    })
    const ok = Boolean(after)
      && Boolean(routeBlock) && routeBlock.button === null
      && before.hints.includes(NO_ROUTE)
      && after.hints.includes(NO_ROUTE)
      && after.hints.includes(ALL_APPLIED)
      && blockTitles(after).length === 1 && blockTitles(after)[0] === ROUTE_BLOCK
    return {
      ok,
      expect: 'ветка «DeepSeek не выбрал маршрут» видна, подсказка «Все предложения применены.» видна, секций с кнопкой нет',
      got: {
        routeBlock,
        hints: after?.hints ?? null,
        blocks: after ? blockTitles(after) : null,
      },
    }
  })

  // 10. `blocked`: заблокированная кнопка секцию не скрывает, подсказки нет.
  await record('blocked: секция с заблокированной кнопкой видна, клик её не скрывает', async () => {
    const before = await scenario('blocked')
    const blockedButton = before.blocks.find((block) => block.title === ROUTE_BLOCK)?.button
    await clickBlockButton(TEXT_BLOCK)
    await clickBlockButton(ACCEPTANCE_BLOCK)
    const applied = await waitFor(async () => {
      const seen = await state()
      const titles = blockTitles(seen)
      return seen.panelOpen && !titles.includes(TEXT_BLOCK) && !titles.includes(ACCEPTANCE_BLOCK) ? seen : null
    })
    const routeBlock = applied?.blocks.find((block) => block.title === ROUTE_BLOCK)
    const clicked = await clickBlockButton(ROUTE_BLOCK)
    await sleep(150)
    const after = await state()
    const ok = Boolean(applied)
      && blockedButton?.disabled === true
      && routeBlock?.button?.disabled === true
      && !applied.hints.includes(ALL_APPLIED)
      && Boolean(clicked)
      && blockTitles(after).includes(ROUTE_BLOCK)
    return {
      ok,
      expect: 'кнопка маршрута disabled, подсказки нет, клик по ней секцию не скрывает',
      got: {
        blockedButton: blockedButton ?? null,
        routeButton: routeBlock?.button ?? null,
        clicked,
        hints: applied?.hints ?? null,
        blocks: blockTitles(after),
      },
    }
  })

  // 11. `same`: «Текст уже такой» disabled — клик секцию не скрывает и поле не меняет.
  await record('same: «Текст уже такой» disabled — секция и поле не меняются', async () => {
    const before = await scenario('same')
    const textButton = before.blocks.find((block) => block.title === TEXT_BLOCK)?.button
    const clicked = await clickBlockButton(TEXT_BLOCK)
    await sleep(150)
    const after = await state()
    const ok = textButton?.disabled === true
      && textButton?.text === 'Текст уже такой'
      && before.description === 'same'
      && Boolean(clicked)
      && blockTitles(after).includes(TEXT_BLOCK)
      && after.description === 'same'
    return {
      ok,
      expect: 'кнопка «Текст уже такой» disabled, клик секцию не скрыл, поле описания прежнее',
      got: { textButton: textButton ?? null, clicked, field: after.description, blocks: blockTitles(after) },
    }
  })

  // 12а. Сброс при новом открытии: применённые текст и критерии снова видны.
  await record('сброс: закрыть и открыть поповер — все три секции снова видны', async () => {
    await scenario('full')
    await clickBlockButton(TEXT_BLOCK)
    await clickBlockButton(ACCEPTANCE_BLOCK)
    const hidden = await waitFor(async () => {
      const seen = await state()
      const titles = blockTitles(seen)
      return seen.panelOpen && !titles.includes(TEXT_BLOCK) && !titles.includes(ACCEPTANCE_BLOCK) ? seen : null
    })
    // Текст поля не трогаем: секции обязана вернуть именно `ask()` нового открытия.
    await closePanel()
    await clickTrigger(DESCRIPTION_LABEL)
    const after = await waitFor(async () => {
      const seen = await state()
      return seen.panelOpen && !seen.loading && blockTitles(seen).length === 3 ? seen : null
    })
    const ok = Boolean(hidden) && Boolean(after)
      && [TEXT_BLOCK, ACCEPTANCE_BLOCK, ROUTE_BLOCK].every((title) => blockTitles(after).includes(title))
    return {
      ok,
      expect: 'после повторного открытия видны все три секции',
      got: { hidden: hidden ? blockTitles(hidden) : null, blocks: after ? blockTitles(after) : null },
    }
  })

  // 12б. Сброс в `ask()`: «Повторить» после ошибки возвращает применённую секцию.
  await record('сброс в ask(): «Повторить» после ошибки возвращает секцию приёмки', async () => {
    const first = await scenario('error')
    const retried = await clickRetry()
    const loaded = await waitFor(async () => {
      const seen = await state()
      return seen.panelOpen && !seen.loading && !seen.error && blockTitles(seen).length === 3 ? seen : null
    })
    await clickBlockButton(ACCEPTANCE_BLOCK)
    const applied = await waitFor(async () => {
      const seen = await state()
      return seen.panelOpen && !blockTitles(seen).includes(ACCEPTANCE_BLOCK) ? seen : null
    })
    await closePanel()
    await clickTrigger(DESCRIPTION_LABEL)
    const second = await waitFor(async () => {
      const seen = await state()
      return seen.panelOpen && !seen.loading && seen.error ? seen : null
    })
    await clickRetry()
    const again = await waitFor(async () => {
      const seen = await state()
      return seen.panelOpen && !seen.loading && !seen.error && blockTitles(seen).length === 3 ? seen : null
    })
    const ok = Boolean(first.error) && Boolean(retried) && Boolean(loaded) && Boolean(applied)
      && Boolean(second.error)
      && Boolean(again)
      && blockTitles(again).includes(ACCEPTANCE_BLOCK)
      && again.description === 'error'
    return {
      ok,
      expect: 'после «Повторить» секция «Дописать в приёмку» снова видна, текст описания остался «error»',
      got: {
        errors: [first.error, second.error],
        retried,
        appliedBlocks: applied ? blockTitles(applied) : null,
        blocks: again ? blockTitles(again) : null,
        field: again?.description ?? null,
      },
    }
  })

  // 13. `noacc`: пустой список критериев секцию не рисует (гард `applyAcceptance`).
  await record('noacc: секции «Дописать в приёмку» нет изначально', async () => {
    const before = await scenario('noacc')
    const ok = !blockTitles(before).includes(ACCEPTANCE_BLOCK)
      && before.criteria.length === 0
      && blockTitles(before).includes(TEXT_BLOCK)
      && blockTitles(before).includes(ROUTE_BLOCK)
    return {
      ok,
      expect: 'секции нет, критериев в ответе нет, текст и маршрут на месте',
      got: { blocks: blockTitles(before), criteria: before.criteria },
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
if (report.error || failed > 0) process.exitCode = 1
