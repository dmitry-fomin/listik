// Барьер волны роя. Хелперы ниже (`covers`…`tailLines`) — чистая часть: ни `spawn`,
// ни `fs`, ни `Date.now()`, только обычные объекты внутрь и наружу; порции c–e зовут
// их по имени и не имеют права менять. `runBarrier` и его частные хелперы (шаги 7–9,
// интеграция) — оркестрация: git/listik/fs приходят параметрами, `spawn` — прямой
// импорт (команды интеграции, порция d).
import {spawn} from "node:child_process";
import path from "node:path";
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
export async function runBarrier({listik, git, fs, config, swarmConfig, log, tasks, projectPath, now,
  swarmJsonPath}) {
  const dryRun = !!(config && config.dryRun);
  const EMPTY_TAIL = {unfrozen: [], integration: null, cleaned: []};

  const halt = haltCards(tasks);
  if (halt.length) {
    log.line(`стоп: открыта карточка ${halt[0]} — слияния и запуски остановлены`);
    return {
      merged: [], mergedNow: [], unmerged: [], halt, gate: {reason: "halt", ids: halt}, order: [],
      ...EMPTY_TAIL,
    };
  }

  const candidates = mergeCandidates(tasks);
  const candidateMap = new Map(candidates.map(c => [c.id, c]));
  const branch = await git.currentBranch(projectPath);
  if (branch == null) {
    log.line("основное дерево не на ветке — слияния не делаются");
    const ids = candidates.map(c => c.id);
    return {
      merged: [], mergedNow: [], unmerged: ids, halt: [],
      gate: ids.length ? {reason: "unmerged", ids} : null, order: [], ...EMPTY_TAIL,
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

  let gate = unmerged.length ? {reason: "unmerged", ids: unmerged} : null;

  // ---------------------------------------------------------- шаг 7: разморозка ---
  const toUnfreeze = collectFrozenCandidates(tasks, unmerged);
  const tasksById = new Map((tasks || []).map(t => [t.id, t]));

  if (dryRun) {
    for (const {f, owner} of toUnfreeze) {
      log.action(`[dry-run] разморозить ${f} (владелец ${owner})`);
    }
    const pending = merged;
    if (!pending.length) {
      log.line("интеграция: нечего проверять");
    } else {
      const cmdCount = swarmConfig && Array.isArray(swarmConfig.integration) ? swarmConfig.integration.length : 0;
      log.action(`[dry-run] интеграция: ${cmdCount} команд`);
      log.action(`[dry-run] убрать деревья: ${pending.join(", ")}`);
    }
    return {merged, mergedNow, unmerged, halt: haltCards(tasks), gate, order, ...EMPTY_TAIL};
  }

  const unfrozen = [];
  for (const {f, owner} of toUnfreeze) {
    let card;
    try {
      card = await listik.show(f);
    } catch (err) {
      log.line(`show ${f} ошибка: ${listikErrText(err)}`);
      continue;
    }
    const marked = parseMarked(card.comments, UNFROZEN_MARK).filter(m => m.data && m.data.owner === owner);
    const hasMark = marked.length > 0;
    const cardLabels = card.labels || [];
    const hasFrozenLabel = cardLabels.some(l => typeof l === "string" && l.startsWith("frozen-by:"));

    if (hasMark && !hasFrozenLabel) continue; // уже разморожена целиком — повтор молча

    const newLabels = cardLabels.filter(l => !(typeof l === "string" && l.startsWith("frozen-by:")));

    if (hasMark && hasFrozenLabel) {
      try {
        await listik.setLabels(f, newLabels);
      } catch (err) {
        log.line(`setLabels ${f} ошибка: ${listikErrText(err)}`);
        continue;
      }
      const t = tasksById.get(f);
      if (t) t.labels = newLabels;
      log.action(`разморожена ${f} (владелец ${owner}): метка снята повторно`);
      unfrozen.push(f);
      continue;
    }

    const task = tasksById.get(f);
    const worktree = (task && task.worktree) || "";
    const treeMissing = !worktree || !fs.existsSync(worktree);
    let base;
    try {
      base = await git.headSha(projectPath);
    } catch (err) {
      log.line(`headSha ${projectPath} ошибка: ${listikErrText(err)}`);
      continue;
    }

    let snapshot = null;
    let rebased = false;
    let conflicts = [];
    let error;
    let treeStatus;
    let next;

    if (treeMissing) {
      treeStatus = "missing";
      next = `дерево ${worktree || "(неизвестно)"} отсутствует — восстанови его сам, затем продолжай задачу`;
    } else {
      try {
        snapshot = await git.snapshotCommit(worktree, "рой: снимок незакоммиченных правок перед rebase");
      } catch (err) {
        log.line(`snapshot ${f} ошибка: ${listikErrText(err)}`);
        continue;
      }
      let r;
      try {
        r = await git.rebase(worktree, base);
      } catch (err) {
        if (await git.rebaseInProgress(worktree)) await git.rebaseAbort(worktree);
        error = err.message ?? String(err);
      }
      if (error !== undefined) {
        next = `перебазируй сам: git rebase ${base} в ${worktree}; ошибка: ${error}; потом продолжай задачу`;
      } else if (r.ok) {
        rebased = true;
        next = `дерево перебазировано на ${base.slice(0, 7)}, продолжай задачу`;
      } else {
        await git.rebaseAbort(worktree);
        conflicts = r.conflicts || [];
        const files = conflicts.join(", ");
        next = `перебазируй сам: git rebase ${base} в ${worktree}; конфликтуют: ${files}; потом продолжай задачу`;
      }
    }

    const record = {owner, sha: base, rebased, snapshot, conflicts, next};
    if (error !== undefined) record.error = error;
    if (treeStatus) record.tree = treeStatus;
    try {
      await listik.comment(f, `${UNFROZEN_MARK} ${JSON.stringify(record)}`);
    } catch (err) {
      log.line(`comment ${f} ошибка: ${listikErrText(err)}`);
      continue;
    }
    try {
      await listik.setLabels(f, newLabels);
    } catch (err) {
      log.line(`setLabels ${f} ошибка: ${listikErrText(err)}`);
      continue;
    }
    if (task) task.labels = newLabels;
    const desc = treeMissing ? "дерева нет" : rebased ? "rebase чистый" : `конфликт: ${conflicts.join(", ")}`;
    log.action(`разморожена ${f} (владелец ${owner}): ${desc}`);
    unfrozen.push(f);
  }

  // ------------------------------------------------------- шаг 8: интеграция ---
  const pending = merged;
  let integration = null;
  const cleaned = [];

  if (pending.length) {
    const integrationCmds = swarmConfig && Array.isArray(swarmConfig.integration) ? swarmConfig.integration : null;

    if (integrationCmds === null) {
      log.line("интеграция: не настроены");
      integration = null;
      gate = await createHaltCard({
        listik, log, tasks, config, mergedNow, pending, kind: "not-configured", swarmJsonPath,
      }) ?? gate;
    } else if (!integrationCmds.length) {
      log.line("интеграция: команды не заданы (пустой список) — считаю зелёными");
      integration = "green";
    } else {
      const stamp = stampFile(now instanceof Date ? now : new Date());
      fs.mkdirSync(config.logDir, {recursive: true});
      const logPath = path.join(config.logDir, `integration-${config.project}-${stamp}.log`);
      const logFd = fs.openSync(logPath, "a");
      const timeoutSec = (swarmConfig && swarmConfig.integrationTimeout) || 1800;

      integration = "green";
      let failed = null;
      for (const argv of integrationCmds) {
        fs.writeSync(logFd, `$ ${argv.join(" ")}\n`);
        const res = await runIntegrationCommand(argv, projectPath, logFd, timeoutSec);
        const codeDesc = res.timedOut ? "таймаут" : String(res.code);
        log.action(`интеграция: ${argv.join(" ")} → код ${codeDesc} (${(res.ms / 1000).toFixed(1)} с)`);
        if (res.timedOut || res.code !== 0) {
          integration = "red";
          failed = {argv, res, timeoutSec};
          break;
        }
      }
      fs.closeSync(logFd);

      if (integration === "red") {
        const logText = fs.readFileSync(logPath, "utf8");
        gate = await createHaltCard({
          listik, log, tasks, config, mergedNow, pending, kind: "red", failed, logPath, logText,
          git: git, projectPath,
        }) ?? gate;
      }
    }

    if (integration === "green") {
      for (const id of pending) {
        const cleanedId = await cleanupOne({listik, git, fs, log, id, candidateMap, projectPath});
        if (cleanedId) cleaned.push(cleanedId);
      }
    }
  } else {
    log.line("интеграция: нечего проверять");
  }

  const finalHalt = gate && gate.reason === "halt" ? gate.ids : haltCards(tasks);
  return {merged, mergedNow, unmerged, halt: finalHalt, gate, order, unfrozen, integration, cleaned};
}

// Владелец → замороженные им открытые задачи, готовые к разморозке: владелец `done`
// и не входит в `unmerged` этого прохода (влит сейчас/ранее либо закрыт без коммитов).
function collectFrozenCandidates(tasks, unmerged) {
  const doneOwners = new Set((tasks || []).filter(t => t.status === "done").map(t => t.id));
  const unmergedSet = new Set(unmerged);
  const map = frozenBy(tasks);
  const out = [];
  for (const [owner, ids] of map) {
    if (!doneOwners.has(owner)) continue;
    if (unmergedSet.has(owner)) continue;
    for (const f of ids) out.push({f, owner});
  }
  return out;
}

function stampFile(d) {
  return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

// Одна команда интеграции: группа процессов, SIGTERM по таймауту, SIGKILL через 5с.
function runIntegrationCommand(argv, cwd, logFd, timeoutSec) {
  return new Promise((resolvePromise) => {
    const start = Date.now();
    const child = spawn(argv[0], argv.slice(1), {
      cwd, env: process.env, detached: true, stdio: ["ignore", logFd, logFd],
    });
    let timedOut = false;
    let killTimer = null;
    const termTimer = setTimeout(() => {
      timedOut = true;
      try { process.kill(-child.pid, "SIGTERM"); } catch { /* уже нет */ }
      killTimer = setTimeout(() => {
        try { process.kill(-child.pid, "SIGKILL"); } catch { /* уже нет */ }
      }, 5000);
    }, timeoutSec * 1000);
    child.on("exit", (code) => {
      clearTimeout(termTimer);
      if (killTimer) clearTimeout(killTimer);
      resolvePromise({code: timedOut ? null : code, timedOut, ms: Date.now() - start});
    });
    child.on("error", () => {
      clearTimeout(termTimer);
      if (killTimer) clearTimeout(killTimer);
      resolvePromise({code: 1, timedOut: false, ms: Date.now() - start});
    });
  });
}

// Карточка-стоп (красная интеграция или «не настроена»): `create` → текст → `needsOwner`.
// Возвращает новый `gate`, либо `null`, если `create` отказал (вызывающий держит старый gate).
async function createHaltCard({listik, log, tasks, config, mergedNow, pending, kind, swarmJsonPath,
  failed, logPath, logText, git, projectPath}) {
  const title = kind === "red"
    ? `рой: интеграционные тесты красные — ${config.project}`
    : `рой: интеграционные тесты не настроены — ${config.project}`;
  let haltCard;
  try {
    haltCard = await listik.create({
      title, project: config.project, type: "question", labels: [HALT_LABEL],
      discoveredFrom: mergedNow[0] ?? pending[0],
    });
  } catch (err) {
    log.line(`создание карточки-стоп ошибка: ${listikErrText(err)}`);
    return {reason: "halt", ids: []};
  }

  let text;
  if (kind === "red") {
    const codeDesc = failed.res.timedOut ? `таймаут ${failed.timeoutSec} с` : String(failed.res.code);
    let sha7 = "?";
    try {
      sha7 = (await git.headSha(projectPath)).slice(0, 7);
    } catch { /* лучшее из доступного */ }
    text = `рой: интеграционные тесты красные после слияния ${pending.join(", ")}.\n` +
      `команда: ${failed.argv.join(" ")}\n` +
      `код: ${codeDesc}\n` +
      `лог: ${logPath}\n` +
      `хвост:\n${tailLines(logText, 40)}\n\n` +
      `что делать: почини основную ветку (HEAD ${sha7}), затем закрой эту карточку ` +
      `(listik done ${haltCard.id} -r "…") — рой снова прогонит тесты и продолжит; деревья влитых ` +
      `задач сохранены до зелёных тестов.`;
  } else {
    text = `рой: интеграционные тесты для проекта ${config.project} не настроены — в ${swarmJsonPath} ` +
      `добавь ключ integration (список команд argv) в projects.${config.project} или на верхнем ` +
      `уровне; пустой список [] значит «тестов нет».\nПотом закрой эту карточку (listik done ${haltCard.id} ` +
      `-r "…") — рой прогонит тесты и продолжит.`;
  }

  try {
    await listik.needsOwner(haltCard.id, text);
  } catch (err) {
    log.line(`needs-owner ${haltCard.id} ошибка: ${listikErrText(err)}`);
    return {reason: "halt", ids: []};
  }
  log.action(`стоп: карточка ${haltCard.id} (${kind === "red" ? "интеграция красная" : "не настроена"})`);
  const openHalts = haltCards(tasks);
  return {reason: "halt", ids: [...openHalts, haltCard.id]};
}

// Дерево/ветка убранной задачи. `id` без записи в `candidateMap` не бывает — он пришёл из
// `mergeCandidates(tasks)`, откуда и построен `candidateMap`. Возвращает `id`, если дерево
// убрано (успех вызывает `listik.set`), иначе `null`.
async function cleanupOne({listik, git, fs, log, id, candidateMap, projectPath}) {
  const cand = candidateMap.get(id);
  if (!cand) return null;
  const branchName = cand.branch || `task/${id}`;

  const isAnc = await git.isAncestor(projectPath, branchName, "HEAD");
  if (!isAnc) {
    log.line(`дерево ${id} не убрано: ветка не влита`);
    return null;
  }

  // `git worktree remove --force` зовётся независимо от того, жив ли каталог на диске: даже
  // после ручного `rm -rf` дерево остаётся зарегистрированным в git (prunable), и без этого
  // вызова `branch -d` откажет «used by worktree at …». Каталога физически нет — и это не
  // ошибка remove, а штатный повод убрать ветку следом.
  const dirPath = cand.worktree || "";
  const dirExists = dirPath && fs.existsSync(dirPath);
  const rm = await git.removeWorktree(projectPath, dirPath);
  if (!rm.ok) {
    const stderrText = rm.stderr || "";
    if (!/not a working tree/i.test(stderrText)) {
      log.line(`${id}: remove не удался, ветку не трогаю`);
      return null;
    }
    log.line(`${id}: дерева нет, ветку убираю`);
  } else if (!dirExists) {
    log.line(`${id}: дерева нет, ветку убираю`);
  }

  const del = await git.deleteBranch(projectPath, branchName);
  if (!del.ok) {
    const exists = await git.branchExists(projectPath, branchName);
    if (exists) {
      log.line(`${id}: удаление ветки не удалось, оставляю`);
      return null;
    }
  }

  try {
    await listik.set(id, {worktree: "", branch: ""});
  } catch (err) {
    log.line(`set ${id} ошибка: ${listikErrText(err)}`);
    return null;
  }
  log.action(`убрано дерево ${id}`);
  return id;
}
