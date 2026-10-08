/**
 * Проверка событий ленты панели задачи — справочник `FEED_EVENT_KINDS`, `feedEventMark`,
 * отбор `feedEvents` и строка `feedEventTitle` (`web/src/lib/dictionaries.ts`),
 * шаг listik-9zpk, порция a.
 *
 * Группы кейсов: a — справочник равен таблице ТЗ, иконки есть в `lib/icons.ts`;
 * b — отбор под фильтрами (вопрос с текстом не дублирует комментарий, закрытие видно);
 * c — строки событий без сырых ключей и `null`; d — подписи и отбор берутся только из
 * справочника (временные подмены строк, откат в `finally`). Фикстура не мутируется.
 * Без браузера, мока и `web/dist`: функции импортируются прямо из исходника.
 *
 * Запуск (из `web/`): node scripts/verify-feed-events.mjs
 *
 * Печатает JSON-отчёт `{ cases: [{ name, ok, want, got }] }`. Код возврата 1, если хоть
 * один кейс не прошёл (включая исключение внутри кейса) или скрипт упал.
 */
import { isDeepStrictEqual } from 'node:util'
import { FEED_EVENT_KINDS, feedEventMark, feedEvents, feedEventTitle } from '../src/lib/dictionaries.ts'
import { icons } from '../src/lib/icons.ts'

const TABLE = [
  { value: 'created', label: 'создана', icon: 'plus', feed: true },
  { value: 'stage', label: 'этап', icon: 'play', feed: true },
  { value: 'status', label: 'статус', icon: 'columns', feed: true },
  { value: 'claim', label: 'взял в работу', icon: 'hand', feed: true },
  { value: 'heartbeat', label: 'heartbeat', icon: 'heart', feed: true },
  { value: 'release', label: 'освободил', icon: 'user', feed: true },
  { value: 'route', label: 'маршрут', icon: 'route-direct', feed: true },
  { value: 'question', label: 'нужен человек', icon: 'question', feed: true },
  { value: 'answer', label: 'вопрос снят', icon: 'answer', feed: true },
  { value: 'document_error', label: 'ошибка документа', icon: 'warning', feed: true },
  { value: 'document_restored', label: 'документ восстановлен', icon: 'check', feed: true },
  { value: 'comment', label: 'комментарий', icon: 'comment', feed: false },
  { value: 'note', label: 'заметка', icon: 'edit', feed: false },
  { value: 'type_change', label: 'смена типа', icon: 'task', feed: false },
  { value: 'document_uploaded', label: 'документ загружен', icon: 'copy', feed: false },
  { value: 'swarm_parent_error', label: 'ошибка пересчёта эпика', icon: 'warning', feed: false },
  { value: 'revoke', label: 'отзыв запуска', icon: 'close', feed: false },
  { value: 'import', label: 'импорт', icon: 'refresh', feed: false },
  { value: 'rejected', label: 'карантин', icon: 'lock', feed: false },
]

let tick = 0
/** Событие со всеми полями `TaskEvent`; не указанные — `null`, `ts` возрастает. */
function ev(kind, fields = {}) {
  tick += 1
  return {
    ts: `2026-09-27T10:00:${String(tick).padStart(2, '0')}Z`,
    kind,
    from_value: null,
    to_value: null,
    actor: null,
    harness: null,
    note: null,
    duration_s: null,
    ...fields,
  }
}

const FIXTURE = [
  ev('created', { to_value: 'open', actor: 'me' }), // 1
  ev('stage', { to_value: 's1-spec', note: 'этап -> s1-spec (sticky)', transition: 'sticky' }), // 2
  ev('claim', { to_value: 'claude', actor: 'agent:claude' }), // 3
  ev('status', { from_value: 'open', to_value: 'in_progress' }), // 4
  ev('heartbeat', { actor: 'agent:claude' }), // 5
  ev('question', { note: 'Какой порт?' }), // 6
  ev('answer', { note: '8787' }), // 7
  ev('question', { note: null }), // 8
  ev('answer', { note: '   ' }), // 9
  ev('comment', { to_value: 'journal' }), // 10
  ev('note', { note: 'порции: создано 1' }), // 11
  ev('route', { to_value: 'full-low' }), // 12
  ev('type_change', { from_value: 'task', to_value: 'epic' }), // 13
  ev('document_error', { note: 'spec_path x: нет файла' }), // 14
  ev('document_restored'), // 15
  ev('document_uploaded'), // 16
  ev('swarm_parent_error'), // 17
  ev('revoke', { from_value: '1', to_value: '2' }), // 18
  ev('import'), // 19
  ev('release', { from_value: 'claude', to_value: '', actor: 'agent:claude' }), // 20
  ev('status', { from_value: 'in_progress', to_value: 'done' }), // 21
  ev('zzz-unknown'), // 22
]
const SNAPSHOT = JSON.stringify(FIXTURE)

/** Событие фикстуры по номеру (с 1). */
const n = (num) => FIXTURE[num - 1]
/** Номера событий фикстуры по идентичности; чужой объект — `'чужой'`. */
const nums = (list) => list.map((event) => (FIXTURE.indexOf(event) === -1 ? 'чужой' : FIXTURE.indexOf(event) + 1))
const table = () => FEED_EVENT_KINDS.map(({ value, label, icon, feed }) => ({ value, label, icon, feed }))

const CANCELLED = ev('status', { from_value: 'in_progress', to_value: 'cancelled' })

const cases = []
function check(name, want, run) {
  let got
  try {
    got = run()
  } catch (error) {
    cases.push({ name, ok: false, want, got: { error: String(error) } })
    return
  }
  cases.push({ name, ok: isDeepStrictEqual(got, want), want, got })
}

/** Временная подмена поля строки справочника; исходное значение возвращается в `finally`. */
function withRow(kind, field, value, run) {
  const row = FEED_EVENT_KINDS.find((item) => item.value === kind)
  if (!row) throw new Error(`в справочнике нет строки ${kind}`)
  const saved = row[field]
  row[field] = value
  try {
    return run()
  } finally {
    row[field] = saved
  }
}

// ── a. Справочник ──────────────────────────────────────────────────────────────
check('a: FEED_EVENT_KINDS поэлементно равен таблице ТЗ', TABLE, table)
check('a: иконка каждой строки есть в lib/icons.ts', [], () =>
  FEED_EVENT_KINDS.filter((item) => !icons[item.icon]).map((item) => `${item.value}: ${item.icon}`),
)
check('a: feedEventMark незнакомого вида', { value: 'zzz-unknown', label: 'zzz-unknown', icon: 'dot', feed: false }, () => {
  const { value, label, icon, feed } = feedEventMark('zzz-unknown')
  return { value, label, icon, feed }
})

// ── b. Отбор ───────────────────────────────────────────────────────────────────
check('b: all — ровно №1, 2, 3, 4, 5, 8, 9, 12, 14, 15, 20, 21', [1, 2, 3, 4, 5, 8, 9, 12, 14, 15, 20, 21], () =>
  nums(feedEvents('all', FIXTURE)),
)
check('b: вопрос один раз — all по [№6, №7] пуст', [], () => nums(feedEvents('all', [n(6), n(7)])))
check('b: закрытие видно — №21 в выдаче all', true, () => feedEvents('all', FIXTURE).includes(n(21)))
check('b: закрытие видно — status in_progress → cancelled проходит all', [true], () =>
  feedEvents('all', [CANCELLED]).map((event) => event === CANCELLED),
)
check('b: journal — ровно [№2]', [2], () => nums(feedEvents('journal', FIXTURE)))
for (const filter of ['question', 'review', 'verdict']) {
  check(`b: ${filter} — пусто`, [], () => nums(feedEvents(filter, FIXTURE)))
}

// ── c. Строки ──────────────────────────────────────────────────────────────────
const TITLES = [
  ['№1', n(1), 'создана'],
  ['№2', n(2), 'этап — → s1-spec · sticky'],
  ['stage s3-impl → s4-judge, handoff', ev('stage', { from_value: 's3-impl', to_value: 's4-judge', note: 'этап -> s4-judge (handoff)', transition: 'handoff' }), 'этап s3-impl → s4-judge · handoff'],
  ['stage s2-review → s3-impl, note null', ev('stage', { from_value: 's2-review', to_value: 's3-impl' }), 'этап s2-review → s3-impl'],
  ['№3', n(3), 'взял в работу · agent:claude'],
  ['claim, actor null', ev('claim', { to_value: 'claude' }), 'взял в работу · —'],
  ['№20', n(20), 'освободил · agent:claude'],
  ['№5', n(5), 'heartbeat · agent:claude'],
  ['№4', n(4), 'статус открыта → в работе'],
  ['№21', n(21), 'статус в работе → готова'],
  ['status in_progress → cancelled', CANCELLED, 'статус в работе → отменена'],
  ['status null → done', ev('status', { to_value: 'done' }), 'статус — → готова'],
  ['№12', n(12), 'маршрут — → full-low'],
  ['№14', n(14), 'ошибка документа'],
  ['№15', n(15), 'документ восстановлен'],
  ['№8', n(8), 'нужен человек'],
  ['№9', n(9), 'вопрос снят'],
  ['№13', n(13), 'смена типа'],
]
for (const [name, event, want] of TITLES) {
  check(`c: строка ${name}`, want, () => feedEventTitle(event))
}
check('c: сырых ключей нет — строки выдачи all без null/undefined/своего kind', [], () =>
  feedEvents('all', FIXTURE)
    .map((event) => ({ event, title: feedEventTitle(event) }))
    .filter(
      ({ event, title }) =>
        title.includes('null') || title.includes('undefined') || (event.kind !== 'heartbeat' && title.includes(event.kind)),
    )
    .map(({ event, title }) => ({ n: nums([event])[0], kind: event.kind, title })),
)

// ── d. Справочник — единственный источник ──────────────────────────────────────
const SUBSTITUTED = {
  1: '@@created',
  2: '@@stage — → s1-spec · sticky',
  3: '@@claim · agent:claude',
  4: '@@status открыта → в работе',
  5: '@@heartbeat · agent:claude',
  8: '@@question',
  9: '@@answer',
  12: '@@route — → full-low',
  14: '@@document_error',
  15: '@@document_restored',
  20: '@@release · agent:claude',
  21: '@@status в работе → готова',
}
let allEvents = []
try {
  allEvents = feedEvents('all', FIXTURE)
} catch {
  // Отбор уже упал в группе b; здесь без подмен подписи.
}
for (const event of allEvents) {
  const num = nums([event])[0]
  check(`d: подпись №${num} (${event.kind}) — только из справочника`, SUBSTITUTED[num] ?? null, () =>
    withRow(event.kind, 'label', `@@${event.kind}`, () => feedEventTitle(event)),
  )
}
check('d: type_change.feed = true пропускает №13 в all', true, () =>
  withRow('type_change', 'feed', true, () => nums(feedEvents('all', FIXTURE)).includes(13)),
)
check('d: status.feed = false убирает №4 и №21 из all', [], () =>
  withRow('status', 'feed', false, () => nums(feedEvents('all', FIXTURE)).filter((num) => num === 4 || num === 21)),
)
check('d: после подмен справочник снова равен таблице ТЗ', TABLE, table)

// ── Фикстура не мутирована ─────────────────────────────────────────────────────
check('фикстура не изменилась после групп a–d', SNAPSHOT, () => JSON.stringify(FIXTURE))

console.log(JSON.stringify({ cases }, null, 2))
if (cases.some((c) => !c.ok)) process.exitCode = 1
