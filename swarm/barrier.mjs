// Чистая часть барьера волны роя: ни `spawn`, ни `fs`, ни `Date.now()` — только
// обычные объекты внутрь и наружу. Порции c–e зовут эти функции по имени и не
// имеют права их менять.
import {OPEN_STATUSES, isFrozen, portOf} from "./decide.mjs";

export const MERGED_MARK = "рой: влито:";
export const UNFROZEN_MARK = "рой: разморожена:";
export const FIRST_CHANGE_MARK = "рой: первая правка замечена:";
export const HALT_LABEL = "swarm:halt";
export const SWARM_AUTHOR = "agent:listik-swarm";

const MAIN_WORKTREE_MARKERS = new Set(["main", "master"]);

// `path == entry` или лежит в его поддереве — семантика `listik/scope.py:covers`.
export function covers(entry, path) {
  const e = (entry || "").replace(/\/+$/, "");
  const p = (path || "").replace(/\/+$/, "");
  if (!e || !p) return false;
  return p === e || p.startsWith(e + "/");
}

// Файлы, не покрытые ни одной записью `declared` (пустой `declared` → все файлы вне области).
export function outsideScope(files, declared) {
  return (files || []).filter(f => !(declared || []).some(entry => covers(entry, f)));
}

function cmpCreatedAt(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

// Комментарии роя (`author === SWARM_AUTHOR`), начинающиеся с `mark` — как
// `{created_at, data}`, отсортированные по `created_at`. Неразборный JSON пропускается.
export function parseMarked(comments, mark) {
  const out = [];
  for (const c of comments || []) {
    if (c.author !== SWARM_AUTHOR) continue;
    const text = c.text;
    if (typeof text !== "string" || !text.startsWith(mark)) continue;
    let data;
    try {
      data = JSON.parse(text.slice(mark.length).trim());
    } catch {
      continue;
    }
    out.push({created_at: c.created_at, data});
  }
  out.sort((a, b) => cmpCreatedAt(a.created_at, b.created_at));
  return out;
}

export function firstChangeAt(comments) {
  const marked = parseMarked(comments, FIRST_CHANGE_MARK);
  return marked.length ? marked[0].created_at : null;
}

// Из двух записей роя (`MERGED_MARK`) — `data` последней по `created_at`; `null`, если
// маркера нет вовсе.
export function mergedRecord(card) {
  const marked = parseMarked((card || {}).comments, MERGED_MARK);
  return marked.length ? marked[marked.length - 1].data : null;
}

export function mergeOrderKey(card) {
  return [firstChangeAt((card || {}).comments) ?? "~", card.launched_at ?? "~", card.id];
}

function cmpKey(a, b) {
  for (let i = 0; i < a.length; i++) {
    const c = cmpCreatedAt(String(a[i]), String(b[i]));
    if (c !== 0) return c;
  }
  return 0;
}

export function sortForMerge(cards) {
  return [...cards].sort((a, b) => cmpKey(mergeOrderKey(a), mergeOrderKey(b)));
}

export function isSwarmTask(task) {
  return portOf(task) != null;
}

// Задачи проекта, готовые к слиянию: закрыты (`done`), дерево не пусто и не `main`/
// `master`, порт роя есть. Живость каталога/ветки — не здесь, её проверяет порция c.
export function mergeCandidates(tasks) {
  return (tasks || [])
    .filter(t => t.status === "done" && (t.worktree || "").trim() &&
      !MAIN_WORKTREE_MARKERS.has((t.worktree || "").trim().toLowerCase()) && isSwarmTask(t))
    .map(t => ({id: t.id, worktree: t.worktree, branch: t.branch, needs_owner: t.needs_owner}));
}

// Открытые задачи с меткой `HALT_LABEL`, по `created_at` возрастающе — массив id.
export function haltCards(tasks) {
  return (tasks || [])
    .filter(t => OPEN_STATUSES.has(t.status) && (t.labels || []).includes(HALT_LABEL))
    .sort((a, b) => cmpCreatedAt(a.created_at, b.created_at))
    .map(t => t.id);
}

// Владелец заморозки → id замороженных им открытых задач.
export function frozenBy(tasks) {
  const map = new Map();
  for (const t of tasks || []) {
    if (!OPEN_STATUSES.has(t.status)) continue;
    const owner = isFrozen(t);
    if (!owner) continue;
    if (!map.has(owner)) map.set(owner, []);
    map.get(owner).push(t.id);
  }
  return map;
}

// Последние `n` строк, соединённые "\n" — строки как `str.splitlines()`
// (без завершающего пустого элемента после хвостового `\n`).
export function tailLines(text, n = 40) {
  let lines = (text ?? "").split("\n");
  if (lines.length && (text ?? "").endsWith("\n")) lines = lines.slice(0, -1);
  return lines.slice(-n).join("\n");
}
