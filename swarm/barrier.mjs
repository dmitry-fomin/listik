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

// Первые `n` строк — тот же разбор, что у `tailLines`, но с начала.
function headLines(text, n) {
  let lines = (text ?? "").split("\n");
  if (lines.length && (text ?? "").endsWith("\n")) lines = lines.slice(0, -1);
  return lines.slice(0, n).join("\n");
}

function listikErrText(err) {
  return `${err.code ?? "error"}/${err.message ?? err}/${err.hint ?? ""}`;
}

function hintFor(id) {
  return `после исправления: listik needs-owner ${id} --clear "…" — рой вольёт на следующем тике`;
}

function withHint(id, body) {
  return `рой: не влита — ${body} ${hintFor(id)}`;
}

async function needsOwnerSafe(listik, log, id, text) {
  try {
    await listik.needsOwner(id, text);
  } catch (err) {
    log.line(`needs-owner ${id} ошибка: ${listikErrText(err)}`);
  }
}

// Оркестрация барьера волны: rebase → ff-merge по одной, запись факта, гейт.
// Порция c — конфликт ребейза здесь всегда отказ (`rebaseAbort`), без арбитра.
export async function runBarrier({listik, git, fs, config, swarmConfig, log, tasks, projectPath, now}) {
  void swarmConfig;
  void now;
  const dryRun = !!(config && config.dryRun);

  const halt = haltCards(tasks);
  if (halt.length) {
    log.line(`стоп: открыта карточка ${halt[0]} — слияния и запуски остановлены`);
    return {merged: [], mergedNow: [], unmerged: [], halt, gate: {reason: "halt", ids: halt}, order: []};
  }

  const candidates = mergeCandidates(tasks);
  const branch = await git.currentBranch(projectPath);
  if (branch == null) {
    log.line("основное дерево не на ветке — слияния не делаются");
    const ids = candidates.map(c => c.id);
    return {
      merged: [], mergedNow: [], unmerged: ids, halt: [],
      gate: ids.length ? {reason: "unmerged", ids} : null, order: [],
    };
  }

  const merged = [];
  const mergedNow = [];
  const unmerged = [];
  const live = [];
  let skippedSilently = 0;
  let skippedMissing = 0;

  // Шаг 3: живость кандидата (каталог/ветка), needs_owner и missing_tree — без show.
  for (const c of candidates) {
    const cbranch = (c.branch || "").trim() || `task/${c.id}`;
    if (c.needs_owner) {
      unmerged.push(c.id);
      log.line(`${c.id} не влита: ждёт человека`);
      continue;
    }
    const dirExists = fs.existsSync(c.worktree || "");
    if (dirExists) {
      live.push({id: c.id, worktree: c.worktree, branch: cbranch});
      continue;
    }
    const branchOk = await git.branchExists(projectPath, cbranch);
    if (!branchOk) {
      skippedSilently++;
      continue;
    }
    const ahead = await git.aheadCount(projectPath, "HEAD", cbranch);
    if (ahead === 0) {
      live.push({id: c.id, worktree: c.worktree, branch: cbranch});
      continue;
    }
    skippedMissing++;
    if (!dryRun) {
      unmerged.push(c.id);
      const body = `каталога дерева ${c.worktree} нет, а ветка ${cbranch} ещё впереди HEAD — ` +
        `верни дерево (listik worktree ${c.id}) или убери ветку.`;
      log.action(`needs-owner ${c.id}: missing_tree`);
      await needsOwnerSafe(listik, log, c.id, withHint(c.id, body));
    }
  }

  // Шаг 4: show() для живых, классификация «уже влита» (запись факта, если её нет).
  const toSort = [];
  for (const entry of live) {
    let card;
    try {
      card = await listik.show(entry.id);
    } catch (err) {
      log.line(`show ${entry.id} ошибка: ${listikErrText(err)}`);
      if (!dryRun) {
        unmerged.push(entry.id);
        log.action(`needs-owner ${entry.id}: show_error`);
        await needsOwnerSafe(listik, log, entry.id,
          withHint(entry.id, `рой не смог прочитать карточку: ${listikErrText(err)}.`));
      }
      continue;
    }

    const rec = mergedRecord(card);
    if (rec) {
      merged.push(entry.id);
      log.line(`${entry.id} уже влита`);
      continue;
    }

    const ahead = await git.aheadCount(projectPath, "HEAD", entry.branch);
    if (ahead === 0) {
      merged.push(entry.id);
      log.line(`${entry.id} уже влита`);
      if (!dryRun) {
        const sha = await git.mergeBase(projectPath, "HEAD", entry.branch);
        const record = {
          sha, branch: entry.branch, base: sha, files: [],
          declared: card.write_scope || [], outside: [],
        };
        try {
          await listik.comment(entry.id, `${MERGED_MARK} ${JSON.stringify(record)}`);
        } catch (err) {
          log.line(`comment ${entry.id} ошибка: ${listikErrText(err)}`);
        }
      }
      continue;
    }

    toSort.push({...card, worktree: entry.worktree, branch: entry.branch});
  }

  const alreadyMergedCount = merged.length;
  const N = candidates.length - skippedSilently - skippedMissing;
  const sorted = sortForMerge(toSort);
  const order = sorted.map(x => x.id);
  log.line(`барьер: кандидатов ${N}, уже влито ${alreadyMergedCount}, порядок: ${order.join(", ")}`);

  // Шаг 5: rebase → merge --ff-only по одной, в порядке `order`.
  for (const entry of sorted) {
    if (dryRun) {
      let sha7 = "?";
      try {
        sha7 = (await git.headSha(projectPath)).slice(0, 7);
      } catch { /* лог всё равно печатаем */ }
      log.action(`[dry-run] влить ${entry.id} (rebase на ${sha7}, затем merge --ff-only ${entry.branch})`);
      continue;
    }

    let dirty;
    try {
      dirty = await git.isDirty(entry.worktree);
    } catch (err) {
      unmerged.push(entry.id);
      log.action(`needs-owner ${entry.id}: dirty_check_error`);
      await needsOwnerSafe(listik, log, entry.id,
        withHint(entry.id, "`git status`: " + (err.message ?? String(err)) + "."));
      continue;
    }
    if (dirty) {
      let porcelain = "";
      try {
        porcelain = await git.statusPorcelain(entry.worktree);
      } catch { /* лучшее из доступного */ }
      const body = `в дереве ${entry.worktree} незакоммиченные правки или новые файлы:\n` +
        `${headLines(porcelain, 5)}\nзакоммить в ветку ${entry.branch} или убери.`;
      unmerged.push(entry.id);
      log.action(`needs-owner ${entry.id}: dirty_tree`);
      await needsOwnerSafe(listik, log, entry.id, withHint(entry.id, body));
      continue;
    }

    const base = await git.headSha(projectPath);
    let rebaseRes;
    try {
      rebaseRes = await git.rebase(entry.worktree, base);
    } catch (err) {
      unmerged.push(entry.id);
      log.action(`needs-owner ${entry.id}: rebase_error`);
      await needsOwnerSafe(listik, log, entry.id, withHint(entry.id, "`rebase`: " + (err.message ?? String(err))));
      continue;
    }
    if (!rebaseRes.ok) {
      await git.rebaseAbort(entry.worktree);
      const sha7 = base.slice(0, 7);
      const files = (rebaseRes.conflicts || []).join(", ");
      const body = `rebase на ${sha7} конфликтует: ${files}; ребейз откачен, разреши сам в дереве ` +
        `${entry.worktree} (git rebase ${base}).`;
      unmerged.push(entry.id);
      log.action(`needs-owner ${entry.id}: rebase_conflict`);
      await needsOwnerSafe(listik, log, entry.id, withHint(entry.id, body));
      continue;
    }

    const ff = await git.mergeFfOnly(projectPath, entry.branch);
    if (!ff.ok) {
      const body = "`merge --ff-only`: " + ff.stderr;
      unmerged.push(entry.id);
      log.action(`needs-owner ${entry.id}: ff_failed`);
      await needsOwnerSafe(listik, log, entry.id, withHint(entry.id, body));
      continue;
    }

    const sha = await git.headSha(projectPath);
    const files = await git.changedFiles(projectPath, base, sha);
    const declared = entry.write_scope || [];
    const outside = outsideScope(files, declared);
    const record = {sha, branch: entry.branch, base, files, declared, outside};
    try {
      await listik.comment(entry.id, `${MERGED_MARK} ${JSON.stringify(record)}`);
    } catch (err) {
      log.line(`comment ${entry.id} ошибка: ${listikErrText(err)}`);
    }
    const sha7 = sha.slice(0, 7);
    const outsideDesc = outside.length ? outside.join(", ") : "нет";
    log.action(`влито ${entry.id} → ${sha7} (файлов ${files.length}, вне области: ${outsideDesc})`);
    merged.push(entry.id);
    mergedNow.push(entry.id);
  }

  const gate = unmerged.length ? {reason: "unmerged", ids: unmerged} : null;
  return {merged, mergedNow, unmerged, halt: [], gate, order};
}
