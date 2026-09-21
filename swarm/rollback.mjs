// Предел откатов: чистые функции над карточкой `listik show --json`.
// Состояния у роя нет — всё считается из журнала карточки.
// FREEZE_MARK и SCOPE_MARK — копии listik/swarm_watch.py, обязаны совпадать.
import {parseMarked, SWARM_AUTHOR} from "./barrier.mjs";

export const FREEZE_MARK = "рой: заморожена:";
export const SCOPE_MARK = "рой: вне write_scope:";
export const LIMIT_PREFIX = "рой: предел откатов";
export const LAUNCH_AUTHOR = "agent:listik";
export const LAUNCH_PREFIX = "автостарт: маршрут ";

const GENERATION_RE = /поколение (\d+),/;

function plain(data) {
  return data && typeof data === "object" && !Array.isArray(data) ? data : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

// Старт снятого поколения — журнал `agent:listik` с «поколение <generation−1>,».
// Из нескольких подходящих берём самый ранний не позже заморозки.
function launchMinutes(comments, generation, at) {
  if (typeof generation !== "number" || !Number.isInteger(generation)) return null;
  const want = generation - 1;
  if (want < 0) return null;
  const atMs = Date.parse(at);
  if (!Number.isFinite(atMs)) return null;
  let best = null;
  for (const c of comments || []) {
    if (c.author !== LAUNCH_AUTHOR || c.kind !== "journal") continue;
    const text = c.text;
    if (typeof text !== "string" || !text.startsWith(LAUNCH_PREFIX)) continue;
    const match = GENERATION_RE.exec(text);
    if (!match || Number(match[1]) !== want) continue;
    const ms = Date.parse(c.created_at);
    if (!Number.isFinite(ms) || ms > atMs) continue;
    if (best == null || ms < best) best = ms;
  }
  if (best == null) return null;
  return Math.max(0, Math.round((atMs - best) / 60000));
}

export function freezeHistory(card) {
  const comments = (card || {}).comments;
  return parseMarked(comments, FREEZE_MARK).map(rec => {
    const data = plain(rec.data);
    const generation = data.generation;
    return {
      owner: data.owner,
      files: asArray(data.files),
      generation,
      at: rec.created_at,
      minutes: launchMinutes(comments, generation, rec.created_at),
    };
  });
}

export function lastParkAt(card) {
  let best = null;
  for (const c of (card || {}).comments || []) {
    if (c.kind !== "question" || c.author !== SWARM_AUTHOR) continue;
    if (typeof c.text !== "string" || !c.text.startsWith(LIMIT_PREFIX)) continue;
    if (typeof c.created_at !== "string") continue;
    if (best == null || c.created_at > best) best = c.created_at;
  }
  return best;
}

export function scopeDrift(card) {
  const fallback = (card || {}).write_scope || [];
  const records = parseMarked((card || {}).comments, SCOPE_MARK);
  if (!records.length) return {files: [], declared: fallback};
  const seen = new Set();
  for (const rec of records) {
    for (const file of asArray(plain(rec.data).files)) seen.add(file);
  }
  const declared = plain(records[records.length - 1].data).declared;
  return {
    files: [...seen].sort(),
    declared: Array.isArray(declared) ? declared : fallback,
  };
}

function sumMinutes(rows) {
  let total = 0;
  for (const row of rows) {
    if (typeof row.minutes === "number") total += row.minutes;
  }
  return total;
}

export function rollbackVerdict(card, maxFreezes) {
  const freezes = freezeHistory(card);
  const park = lastParkAt(card);
  const window = park == null ? freezes : freezes.filter(row => row.at > park);
  return {
    freezes,
    window,
    count: window.length,
    total: freezes.length,
    exceeded: maxFreezes != null && window.length > maxFreezes,
    minutes: sumMinutes(window),
    minutesTotal: sumMinutes(freezes),
    drift: scopeDrift(card),
    last: freezes.length ? freezes[freezes.length - 1] : null,
  };
}

function listOr(items, empty) {
  return Array.isArray(items) && items.length ? items.join(", ") : empty;
}

export function limitText(id, verdict, maxFreezes) {
  const evicted = (verdict.window || []).map(row => {
    const files = Array.isArray(row.files) && row.files.length
      ? row.files.join(", ")
      : "(файлы не записаны)";
    return `${row.owner}: ${files}`;
  }).join("; ");
  const drift = verdict.drift || {};
  return [
    `${LIMIT_PREFIX} — задача заморожена ${verdict.count} раз подряд ` +
      `(порог max_freezes = ${maxFreezes} в swarm.json), больше не перезапускаю.`,
    `вытесняли: ${evicted}`,
    `вне объявленного write_scope: ${listOr(drift.files, "нет")} ` +
      `(объявлено: ${listOr(drift.declared, "пусто")})`,
    `потрачено на откаты: ~${verdict.minutes} мин работы снятых поколений в этом окне; ` +
      `всего заморозок за жизнь задачи ${verdict.total}, ~${verdict.minutesTotal} мин`,
    "что делать: сверь границы ТЗ этой задачи и вытеснивших (write_scope), разведи их " +
      "или подними max_freezes для проекта в swarm.json; дерево и правки сохранены, " +
      "после слияния владельца барьер перебазирует дерево сам. Когда можно продолжать: " +
      `listik needs-owner ${id} --clear "…" — рой запустит её снова тем же маршрутом.`,
  ].join("\n");
}
