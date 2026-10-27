#!/usr/bin/env node
// Проверка интерактивной карты сервиса: node check.mjs docs/<имя>-map.html [--statuses "a,b,c"]
// Связность: блоки ↔ STEPS ↔ STATUSES, сценарии ↔ кнопки ↔ HINTS, палитра.
// Геометрия: текст влезает в блок и в холст, блоки не налезают друг на друга, стрелки не идут сквозь блоки.
// Ширина текста — оценка (символы × кегль × коэффициент), окончательная проверка — скриншот.
import { readFileSync } from 'node:fs'

const [file, flag, list] = process.argv.slice(2)
if (!file) { console.error('usage: node check.mjs <map.html> [--statuses "a,b,c"]'); process.exit(2) }
const html = readFileSync(file, 'utf8')
const errors = [], add = m => errors.push(m)
const attr = (tag, name) => tag.match(new RegExp(`\\s${name}="([^"]*)"`))?.[1]
const num = (tag, name) => Number(attr(tag, name))
const unescape = t => t.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"')
const width = (text, size, bold) => unescape(text).length * size * (bold ? 0.56 : 0.52)

const PALETTE = ['human', 'llm', 'worker', 'bot', 'note']
const svg = (html.match(/<svg[\s\S]*?<\/svg>/)?.[0] ?? '').replace(/<!--[\s\S]*?-->/g, '')
const vb = (attr(svg.slice(0, 300), 'viewBox') ?? '0 0 1440 0').split(/\s+/).map(Number)
const [W, H] = [vb[2], vb[3]]

// блоки
const nodes = [...svg.matchAll(/<g class="node"([^>]*)>([\s\S]*?)<\/g>/g)].map(([, head, body]) => {
  const rect = body.match(/<rect[^>]*>/)?.[0] ?? ''
  return {
    id: attr(head, 'data-id'), off: / data-off/.test(head), scen: (attr(head, 'data-in') ?? '').split(' ').filter(Boolean),
    cls: attr(rect, 'class'), x: num(rect, 'x'), y: num(rect, 'y'), w: num(rect, 'width'), h: num(rect, 'height'),
    texts: [...body.matchAll(/<text class="(t|s|data)"[^>]*>([^<]*)<\/text>/g)].map(m => ({ cls: m[1], text: m[2] })),
  }
})
const ids = nodes.map(n => n.id)
if (!nodes.length) add('нет ни одного <g class="node">')
for (const id of new Set(ids)) if (ids.filter(x => x === id).length > 1) add(`блок ${id} повторяется`)

// итоги пути и посылки (не блоки: без номера и подсказки по клику)
const segments = d => {
  const segs = []
  let x = 0, y = 0
  for (const [, c, a, b] of d.matchAll(/([MHVL])\s*([\d.]+)(?:[ ,]([\d.]+))?/g)) {
    const [px, py] = [x, y]
    if (c === 'M' || c === 'L') { x = +a; y = +b } else if (c === 'H') x = +a; else y = +a
    if (c !== 'M') segs.push([px, py, x, y])
  }
  return segs
}
const extras = [...svg.matchAll(/<g class="(end|msg)"([^>]*)>([\s\S]*?)<\/g>/g)].map(([, kind, head, body]) => {
  let x, y, w, h
  if (kind === 'end') { const r = body.match(/<rect[^>]*>/)?.[0] ?? ''; [x, y, w, h] = ['x', 'y', 'width', 'height'].map(k => num(r, k)) }
  else {
    const p = segments(attr(body.match(/<path class="msg"[^>]*>/)?.[0] ?? '', 'd') ?? '').flat()
    const xs = p.filter((_, i) => i % 2 === 0), ys = p.filter((_, i) => i % 2)
    ;[x, y, w, h] = [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)]
  }
  return { kind, x, y, w, h, scen: (attr(head, 'data-in') ?? '').split(' ').filter(Boolean),
    texts: [...body.matchAll(/<text class="(et|m)"[^>]*>([^<]*)<\/text>/g)].map(m => ({ cls: m[1], text: m[2] })) }
})

// JS-данные
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1] ?? ''
const block = name => script.match(new RegExp(`const ${name} = [\\[{]([\\s\\S]*?)\\n[\\]}];`))?.[1] ?? ''
const stepKeys = [...block('STEPS').matchAll(/^\s+(\w+): \{ title:/gm)].map(m => m[1])
const statuses = [...block('STATUSES').matchAll(/\['([^']+)', '(\w+)'(?:, '(\w*)')?(?:, '([^']*)')?\]/g)].map(m => ({ name: m[1], id: m[2], skip: m[3], sep: m[4] }))
const hintKeys = [...block('HINTS').matchAll(/^\s+(\w+):/gm)].map(m => m[1])
const buttons = [...html.matchAll(/<button data-sc="(\w+)"/g)].map(m => m[1])

for (const id of ids) if (!stepKeys.includes(id)) add(`у блока ${id} нет подсказки в STEPS`)
for (const k of stepKeys) if (!ids.includes(k)) add(`STEPS.${k} без блока на схеме`)
if (!statuses.length) add('STATUSES пуст: путь по статусам не нарисован')
for (const s of statuses) if (!ids.includes(s.id)) add(`статус «${s.name}» ссылается на несуществующий блок ${s.id}`)
for (const s of statuses) if (s.skip && !buttons.includes(s.skip)) add(`статус «${s.name}»: нет сценария ${s.skip}`)
if (buttons[0] !== 'all') add('первая кнопка сценария должна быть data-sc="all"')
for (const b of buttons) if (!hintKeys.includes(b)) add(`у сценария ${b} нет текста в HINTS`)
const used = new Set([...svg.matchAll(/data-in="([^"]*)"/g)].flatMap(m => m[1].split(' ').filter(Boolean)))
for (const sc of used) if (!buttons.includes(sc)) add(`data-in="${sc}" без кнопки сценария`)
for (const b of buttons) if (b !== 'all' && !used.has(b)) add(`сценарий ${b} не включает ни одного блока`)

if (flag === '--statuses' && list) {
  const names = statuses.map(s => s.name)
  for (const n of list.split(',').map(s => s.trim()).filter(Boolean)) if (!names.includes(n)) add(`статус «${n}» из кода не нарисован`)
}

// палитра и текст в блоках
if (/data-phone=/.test(html)) add('атрибут data-phone устарел — переименуй в data-pii (и в CSS)')
const pii = /data-pii=/.test(svg)
for (const n of nodes) {
  if (!PALETTE.includes(n.cls)) add(`блок ${n.id}: класс «${n.cls}» не из палитры ${PALETTE.join('/')}`)
  if (n.cls !== 'note' && !n.texts.some(t => t.cls === 't')) add(`блок ${n.id}: нет заголовка .t`)
  const s = n.texts.filter(t => t.cls === 's').length, d = n.texts.filter(t => t.cls === 'data').length
  if (n.cls !== 'note' && pii && d === 0) add(`блок ${n.id}: нет строк .data для режима «Куда уходят данные»`)
  if (d && s && d !== s && n.cls !== 'note') add(`блок ${n.id}: строк .s ${s}, а .data ${d} — в режиме данных блок прыгнет`)
  for (const t of n.texts) {
    const w = width(t.text, t.cls === 't' ? 13 : 12, t.cls === 't')
    if (w > n.w - 10) add(`блок ${n.id}: «${t.text}» не влезает (~${Math.round(w)} из ${n.w - 10} px)`)
  }
  if (n.x < 120 || n.x + n.w > W || n.y < 50 || n.y + n.h > H) add(`блок ${n.id} за пределами холста или на подписях дорожек`)
  const title = n.texts.find(t => t.cls === 't')?.text ?? ''
  if (n.off && !/выкл\.|план/.test(title)) add(`блок ${n.id}: data-off, а в заголовке нет «выкл.» или «план»`)
}

for (const e of extras) {
  const name = e.kind === 'end' ? 'итог' : 'посылка'
  if (!e.texts.length) add(`${name} без текста`)
  if (e.kind === 'end' && !/^Итог:/.test(e.texts[0]?.text ?? '')) add(`итог «${e.texts[0]?.text}»: начни с «Итог:»`)
  for (const t of e.texts) {
    const w = width(t.text, 12, t.cls === 'et')
    if (w > e.w - 10) add(`${name}: «${t.text}» не влезает (~${Math.round(w)} из ${e.w - 10} px)`)
  }
  if (e.x < 120 || e.x + e.w > W || e.y < 50 || e.y + e.h > H) add(`${name} «${e.texts[0]?.text}» за пределами холста`)
  for (const n of nodes) if (e.x < n.x + n.w && n.x < e.x + e.w && e.y < n.y + n.h && n.y < e.y + e.h)
    add(`${name} «${e.texts[0]?.text}» налезает на блок ${n.id}`)
}

// «?» у подписи дорожки — с подсказкой по наведению
for (const m of svg.matchAll(/<g class="q"[^>]*>([\s\S]*?)<\/g>/g))
  if ((m[1].match(/<title>([^<]*)<\/title>/)?.[1].trim().length ?? 0) < 15) add('«?» у дорожки без подсказки в <title>')

// шаг, который ходит вовне или тратит деньги, показан в сценарии «Если сломается»
for (const n of nodes) {
  const out = ['worker', 'bot'].includes(n.cls) || n.texts.some(t => t.cls === 't' && t.text.includes('₽'))
  if (out && !n.off && !n.scen.includes('fail')) add(`блок ${n.id} ходит вовне — покажи сбой: data-in="… fail" и заметка .note с последствием`)
}
if (used.has('fail') && !nodes.some(n => n.cls === 'note' && n.scen.includes('fail'))) add('в сценарии fail нет ни одной заметки .note о сбое')

// видимые подписи — словами для новичка: без имён таблиц и полей (snake_case) и флагов команд (--flag)
const jargon = /\b[a-z]+_[a-z_]+\b|(^|\s)--[a-z]/
for (const n of nodes) for (const t of n.texts) if (jargon.test(t.text)) add(`блок ${n.id}: «${t.text}» — внутреннее имя, напиши словами`)
for (const m of svg.matchAll(/<text class="(l|et|m)"[^>]*>([^<]*)<\/text>/g)) if (jargon.test(m[2])) add(`подпись «${m[2]}» — внутреннее имя, напиши словами`)

// дорожки: фоновые полосы во всю ширину; класс блока следует из дорожки (кроме .note)
const lanes = [...svg.matchAll(/<rect x="0" y="([\d.]+)" width="[\d.]+" height="([\d.]+)"/g)].map(m => ({ y: +m[1], h: +m[2] }))
const lanesBottom = Math.max(0, ...lanes.map(l => l.y + l.h))
for (const l of lanes) {
  const cls = new Set(nodes.filter(n => n.cls !== 'note' && n.y >= l.y && n.y < l.y + l.h).map(n => n.cls))
  if (cls.size > 1) add(`дорожка y=${l.y}: блоки разных классов (${[...cls].join(', ')}) — класс блока следует из дорожки`)
}

// легенда (ниже дорожек) — ровно то, что есть на схеме
const legendRects = [...svg.matchAll(/<rect class="(\w+)"([^>]*)>/g)].filter(m => num(m[0], 'y') >= lanesBottom)
const legend = new Set(legendRects.map(m => m[1]).filter(c => PALETTE.includes(c)))
const usedCls = new Set(nodes.map(n => n.cls))
for (const c of usedCls) if (!legend.has(c)) add(`класса .${c} нет в легенде`)
for (const c of legend) if (!usedCls.has(c)) add(`в легенде .${c}, а на схеме такого блока нет`)
const dashes = [...svg.matchAll(/<path class="arr dash" d="M[\d.]+,([\d.]+)/g)].map(m => +m[1])
if (dashes.some(y => y < lanesBottom) !== dashes.some(y => y >= lanesBottom)) add('пунктирные стрелки на схеме и пунктир в легенде — либо оба, либо ни одного')
const offUsed = nodes.some(n => n.off), offLegend = legendRects.some(m => / data-off/.test(m[2]))
for (const [kind, name] of [['end', 'итог пути'], ['msg', 'посылка']]) {
  const inLegend = new RegExp(`<(rect|path) class="${kind}"[^>]*>`, 'g')
  const legendHas = [...svg.matchAll(inLegend)].some(m => (m[1] === 'rect' ? num(m[0], 'y') : segments(attr(m[0], 'd') ?? '')[0]?.[1]) >= lanesBottom)
  if (extras.some(e => e.kind === kind) !== legendHas) add(`${name} на схеме и в легенде — либо оба, либо ни одного`)
}
if (offUsed !== offLegend) add('data-off на схеме и образец data-off в легенде — либо оба, либо ни одного')
const rub = nodes.some(n => n.texts.some(t => t.cls === 't' && t.text.includes('₽')))
const rubLegend = [...svg.matchAll(/<text class="l"[^>]*>([^<]*)<\/text>/g)].some(m => m[1].includes('₽'))
if (rub !== rubLegend) add('₽ в заголовках блоков и строка «₽» в легенде — либо оба, либо ни одного')

// подписи стрелок и легенды — в холсте
for (const m of svg.matchAll(/<text class="l" x="([\d.]+)" y="[\d.]+">([^<]*)<\/text>/g)) {
  const right = Number(m[1]) + width(m[2], 11)
  if (right > W - 4) add(`подпись «${m[2]}» вылезает за правый край (~${Math.round(right)} из ${W})`)
}

// блоки не пересекаются
const inner = (n, pad) => ({ x1: n.x + pad, y1: n.y + pad, x2: n.x + n.w - pad, y2: n.y + n.h - pad })
for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
  const a = inner(nodes[i], 0), b = inner(nodes[j], 0)
  if (a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2) add(`блоки ${nodes[i].id} и ${nodes[j].id} налезают друг на друга`)
}

// стрелки не проходят сквозь блоки (концы у кромки блока допустимы)
for (const m of svg.matchAll(/<path class="arr[^"]*" d="([^"]+)"/g)) {
  const d = m[1], segs = segments(d)
  if (/[a-z]/.test(d.replace(/[\d.,\s]/g, '').replace(/[MHVL]/g, ''))) add(`стрелка «${d}»: только абсолютные M/H/V/L`)
  for (const [x1, y1, x2, y2] of segs) for (const n of nodes) {
    const r = inner(n, 3)
    const [lx, hx, ly, hy] = [Math.min(x1, x2), Math.max(x1, x2), Math.min(y1, y2), Math.max(y1, y2)]
    if ((x1 === x2 || y1 === y2) && lx < r.x2 && hx > r.x1 && ly < r.y2 && hy > r.y1)
      add(`стрелка «${d}» проходит сквозь блок ${n.id}`)
  }
}

if (errors.length) { console.log(errors.map(e => '✗ ' + e).join('\n')); process.exit(1) }
console.log(`✓ ${nodes.length} блоков, ${extras.length} итогов и посылок, ${statuses.length} статусов, сценарии: ${buttons.join(', ')}`)
