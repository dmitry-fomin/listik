// Барьер волны роя. Хелперы ниже (`covers`…`tailLines`) — чистая часть: ни `spawn`,
// ни `fs`, ни `Date.now()`, только обычные объекты внутрь и наружу; порции c–e зовут
// их по имени и не имеют права менять. `runBarrier` и его частные хелперы (шаги 7–9,
// интеграция) — оркестрация: git/listik/fs приходят параметрами, `spawn` — прямой
// импорт (команды интеграции, порция d).
import {spawn, execFileSync} from "node:child_process";
import path from "node:path";
import {OPEN_STATUSES, isFrozen, portOf, REJECTED_MARK, isSoftQuestion, openQuestion,
  fromComments} from "./decide.mjs";
import {resolveWithArbiter} from "./arbiter.mjs";

export {REJECTED_MARK};

export const MERGED_MARK = "рой: влито:";
export const UNFROZEN_MARK = "рой: разморожена:";
export const FIRST_CHANGE_MARK = "рой: первая правка замечена:";
export const HALT_LABEL = "swarm:halt";
export const SWARM_AUTHOR = "agent:listik-swarm";
export const ARBITER_MARK = "рой: арбитр:";

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

// Откат ребейза в дереве кандидата: ошибка git здесь не значит, что сломано
// основное дерево, и из `runBarrier` не вылетает.
async function abortQuiet(git, worktree) {
  try {
    if (await git.rebaseInProgress(worktree)) await git.rebaseAbort(worktree);
  } catch {
    /* дерево задачи, не projectPath */
  }
}



// Оркестрация барьера волны: rebase → ff-merge по одной, запись факта, гейт.
// Порция c — конфликт ребейза здесь всегда отказ (`rebaseAbort`), без арбитра.
export async function runBarrier({listik, git, fs, config, swarmConfig, log, tasks, projectPath, now,
  swarmJsonPath, swarmModel}) {
  const dryRun = !!(config && config.dryRun);
  const EMPTY_TAIL = {unfrozen: [], integration: null, cleaned: [], rejected: []};

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
  const pending = [];
  const live = [];
  let skippedSilently = 0;
  let skippedMissing = 0;
  let skippedOut = 0;

  async function markUnmerged(id, reason, body) {
    if (dryRun) return;
    unmerged.push(id);
    log.action(`needs-owner ${id}: ${reason}`);
    await needsOwnerSafe(listik, log, id, withHint(id, body));
  }

  // Шаг 3: живость кандидата (каталог/ветка), needs_owner (мягкий — show один раз) и missing_tree.
  for (const c of candidates) {
    const cbranch = (c.branch || "").trim() || `task/${c.id}`;
    let shown = null;
    if (c.needs_owner) {
      try {
        shown = await listik.show(c.id);
      } catch (err) {
        skippedOut++;
        unmerged.push(c.id);
        log.line(`${c.id} не влита: ждёт человека`);
        continue;
      }
      const q = openQuestion(fromComments(shown.comments));
      if (!q || !isSoftQuestion(q.text)) {
        skippedOut++;
        unmerged.push(c.id);
        log.line(`${c.id} не влита: ждёт человека`);
        continue;
      }
    }
    const dirExists = fs.existsSync(c.worktree || "");
    let branchOk;
    try {
      branchOk = await git.branchExists(projectPath, cbranch);
    } catch (err) {
      skippedOut++;
      await markUnmerged(c.id, "branch_error",
        `ветка ${cbranch}: ${err.message ?? String(err)}.`);
      continue;
    }
    if (dirExists) {
      if (!branchOk) {
        skippedOut++;
        await markUnmerged(c.id, "missing_branch",
          `каталог дерева ${c.worktree} есть, а ветки ${cbranch} нет — верни ветку ` +
          `или убери дерево.`);
        continue;
      }
      live.push({id: c.id, worktree: c.worktree, branch: cbranch, shown});
      continue;
    }
    if (!branchOk) {
      skippedSilently++;
      continue;
    }
    let ahead;
    try {
      ahead = await git.aheadCount(projectPath, "HEAD", cbranch);
    } catch (err) {
      skippedOut++;
      await markUnmerged(c.id, "ahead_error",
        `ветка ${cbranch}: ${err.message ?? String(err)}.`);
      continue;
    }
    if (ahead === 0) {
      live.push({id: c.id, worktree: c.worktree, branch: cbranch, shown});
      continue;
    }
    skippedMissing++;
    await markUnmerged(c.id, "missing_tree",
      `каталога дерева ${c.worktree} нет, а ветка ${cbranch} ещё впереди HEAD — ` +
      `верни дерево (listik worktree ${c.id}) или убери ветку.`);
  }

  // Шаг 4: show() для живых (повторно не зовём, если шаг 3 уже прочитал карточку).
  const toSort = [];
  for (const entry of live) {
    let card = entry.shown || null;
    if (!card) {
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
    }

    const rec = mergedRecord(card);
    if (rec) {
      merged.push(entry.id);
      pending.push(entry.id);
      log.line(`${entry.id} уже влита`);
      continue;
    }

    let ahead;
    try {
      ahead = await git.aheadCount(projectPath, "HEAD", entry.branch);
    } catch (err) {
      await markUnmerged(entry.id, "ahead_error",
        `ветка ${entry.branch}: ${err.message ?? String(err)}.`);
      continue;
    }
    if (ahead === 0 && !fs.existsSync(entry.worktree)) {
      merged.push(entry.id);
      log.line(`${entry.id} уже влита`);
      if (!dryRun) {
        let sha;
        try {
          sha = await git.mergeBase(projectPath, "HEAD", entry.branch);
        } catch (err) {
          log.line(`mergeBase ${entry.id} ошибка: ${listikErrText(err)}`);
          continue;
        }
        const record = {
          sha, branch: entry.branch, base: sha, files: [],
          declared: card.write_scope || [], outside: [],
        };
        try {
          await listik.comment(entry.id, `${MERGED_MARK} ${JSON.stringify(record)}`);
          pending.push(entry.id);
        } catch (err) {
          log.line(`comment ${entry.id} ошибка: ${listikErrText(err)}`);
        }
      }
      continue;
    }

    toSort.push({...card, worktree: entry.worktree, branch: entry.branch});
  }

  const alreadyMergedCount = merged.length;
  const N = candidates.length - skippedSilently - skippedMissing - skippedOut;
  const sorted = sortForMerge(toSort);
  const order = sorted.map(x => x.id);
  log.line(`барьер: кандидатов ${N}, уже влито ${alreadyMergedCount}, порядок: ${order.join(", ")}`);

  const verifyCmds = swarmConfig && Array.isArray(swarmConfig.verify) ? swarmConfig.verify : null;
  const verifyTimeout = (swarmConfig && swarmConfig.verifyTimeout) || 1800;
  const verifyRetries = swarmConfig && swarmConfig.verifyRetries != null ? swarmConfig.verifyRetries : 1;
  const rejected = [];
  if (!verifyCmds || !verifyCmds.length) {
    log.line("верификатор: команд нет");
  }

  async function rejectOne({entry, base, reason, argv = null, code = null, timedOut = false,
    logPath = null, logText = ""}) {
    const attempts = parseMarked(entry.comments, REJECTED_MARK).length;
    const attempt = attempts + 1;
    const sha7 = (base || "").slice(0, 7);
    const toHuman = attempts >= verifyRetries;
    const dest = toHuman ? "человеку" : "воркеру";
    log.action(`не принята ${entry.id}: ${reason}, попытка ${attempt} из ${verifyRetries} → ${dest}`);

    const next = reason === "red"
      ? `почини тесты в дереве ${entry.worktree} (ветка ${entry.branch}, база ${sha7}), закоммить в ветку и снова listik done`
      : `ветка не меняет ни одного файла относительно ${sha7} — сделай работу, закоммить в ветку ${entry.branch} и снова listik done; если задача и правда без правок — needs-owner человеку`;
    const record = {
      reason,
      attempt,
      sha: base,
      command: argv,
      code,
      timed_out: !!timedOut,
      log: logPath,
      tail: reason === "red" ? tailLines(logText, 40) : "",
      next,
    };
    try {
      await listik.comment(entry.id, `${REJECTED_MARK} ${JSON.stringify(record)}`);
    } catch (err) {
      log.line(`comment ${entry.id} ошибка: ${listikErrText(err)}`);
    }

    if (toHuman) {
      const argvDesc = Array.isArray(argv) ? argv.join(" ") : "";
      const codeDesc = timedOut ? "таймаут" : String(code);
      const body = reason === "red"
        ? `отклонена ${attempt} раз: тесты красные — ${argvDesc}, код ${codeDesc}, лог ${logPath}`
        : `отклонена ${attempt} раз: пустой дифф относительно ${sha7}`;
      unmerged.push(entry.id);
      log.action(`needs-owner ${entry.id}: rejected_${reason}`);
      await needsOwnerSafe(listik, log, entry.id, withHint(entry.id, body));
      return;
    }

    try {
      await listik.set(entry.id, {status: "open", stage: "s3-impl"});
      rejected.push(entry.id);
    } catch (err) {
      log.line(`set ${entry.id} ошибка: ${listikErrText(err)}`);
      unmerged.push(entry.id);
    }
  }

  // Шаг 5: rebase → пустой дифф → верификатор → merge --ff-only по одной, в порядке `order`.
  for (const entry of sorted) {
    if (dryRun) {
      let sha7 = "?";
      try {
        sha7 = (await git.headSha(projectPath)).slice(0, 7);
      } catch { /* лог всё равно печатаем */ }
      log.action(`[dry-run] влить ${entry.id} (rebase на ${sha7}, затем merge --ff-only ${entry.branch})`);
      const cmdCount = verifyCmds ? verifyCmds.length : 0;
      log.action(`[dry-run] проверить ${entry.id}: ${cmdCount} команд`);
      continue;
    }

    try {
      if (await git.rebaseInProgress(entry.worktree)) await git.rebaseAbort(entry.worktree);
    } catch (err) {
      unmerged.push(entry.id);
      log.action(`needs-owner ${entry.id}: rebase_abort_error`);
      await needsOwnerSafe(listik, log, entry.id,
        withHint(entry.id, "`rebase --abort`: " + (err.message ?? String(err))));
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
      await abortQuiet(git, entry.worktree);
      unmerged.push(entry.id);
      log.action(`needs-owner ${entry.id}: rebase_error`);
      await needsOwnerSafe(listik, log, entry.id, withHint(entry.id, "`rebase`: " + (err.message ?? String(err))));
      continue;
    }
    let usedArbiter = false;
    if (!rebaseRes.ok) {
      const sha7 = base.slice(0, 7);
      const files = (rebaseRes.conflicts || []).join(", ");

      if (!(swarmConfig && swarmConfig.arbiter)) {
        await abortQuiet(git, entry.worktree);
        const body = `rebase на ${sha7} конфликтует: ${files}; арбитр не настроен (ключ arbiter в ` +
          `swarm.json); ребейз откачен, разреши сам в дереве ${entry.worktree} (git rebase ${base}).`;
        unmerged.push(entry.id);
        log.action(`needs-owner ${entry.id}: rebase_conflict`);
        await needsOwnerSafe(listik, log, entry.id, withHint(entry.id, body));
        continue;
      }

      let arbRes;
      try {
        arbRes = await resolveWithArbiter({
          git, listik, fs, config, swarmConfig, log, projectPath, task: entry, card: entry,
          worktree: entry.worktree, base, tasks, now, model: swarmModel,
        });
      } catch (err) {
        await abortQuiet(git, entry.worktree);
        arbRes = {ok: false, reason: err.message ?? String(err)};
      }
      if (!arbRes.ok) {
        await abortQuiet(git, entry.worktree);
        const logPart = arbRes.logPath ? `, лог ${arbRes.logPath}` : "";
        const body = `rebase на ${sha7} конфликтует: ${files}; арбитр не справился: ${arbRes.reason}` +
          `${logPart}; ребейз откачен, разреши сам в дереве ${entry.worktree} (git rebase ${base}).`;
        unmerged.push(entry.id);
        log.action(`needs-owner ${entry.id}: rebase_conflict`);
        await needsOwnerSafe(listik, log, entry.id, withHint(entry.id, body));
        continue;
      }
      usedArbiter = true;
    }

    let changed = [];
    try {
      changed = await git.changedFiles(projectPath, base, entry.branch);
    } catch (err) {
      log.line(`changedFiles ${entry.id} ошибка: ${listikErrText(err)}`);
    }
    if (!changed.length) {
      await rejectOne({entry, base, reason: "empty"});
      continue;
    }

    if (verifyCmds && verifyCmds.length) {
      const stamp = stampFile(now instanceof Date ? now : new Date());
      fs.mkdirSync(config.logDir, {recursive: true});
      const logPath = path.join(config.logDir, `verify-${entry.id}-${stamp}.log`);
      const logFd = fs.openSync(logPath, "a");
      const env = envForVerify(entry);
      let failed = null;
      try {
        for (const argv of verifyCmds) {
          fs.writeSync(logFd, `$ ${argv.join(" ")}\n`);
          const res = await runIntegrationCommand(argv, entry.worktree, logFd, verifyTimeout, env);
          const codeDesc = res.timedOut ? "таймаут" : String(res.code);
          log.action(`верификатор ${entry.id}: ${argv.join(" ")} → код ${codeDesc} (${(res.ms / 1000).toFixed(1)} с)`);
          if (res.timedOut || res.code !== 0) {
            failed = {argv, res};
            break;
          }
        }
      } finally {
        fs.closeSync(logFd);
      }
      if (failed) {
        const logText = fs.readFileSync(logPath, "utf8");
        await rejectOne({
          entry, base, reason: "red",
          argv: failed.argv,
          code: failed.res.code,
          timedOut: failed.res.timedOut,
          logPath,
          logText,
        });
        continue;
      }
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
    let files = [];
    try {
      files = await git.changedFiles(projectPath, base, sha);
    } catch (err) {
      log.line(`changedFiles ${entry.id} ошибка: ${listikErrText(err)}`);
    }
    const declared = entry.write_scope || [];
    const outside = outsideScope(files, declared);
    const record = {sha, branch: entry.branch, base, files, declared, outside};
    if (usedArbiter) record.arbiter = true;
    let marked = false;
    try {
      await listik.comment(entry.id, `${MERGED_MARK} ${JSON.stringify(record)}`);
      marked = true;
    } catch (err) {
      log.line(`comment ${entry.id} ошибка: ${listikErrText(err)}`);
    }
    const sha7 = sha.slice(0, 7);
    const outsideDesc = outside.length ? outside.join(", ") : "нет";
    log.action(`влито ${entry.id} → ${sha7} (файлов ${files.length}, вне области: ${outsideDesc})`);
    merged.push(entry.id);
    mergedNow.push(entry.id);
    if (marked) pending.push(entry.id);
  }

  let gate = unmerged.length ? {reason: "unmerged", ids: unmerged} : null;

  // ---------------------------------------------------------- шаг 7: разморозка ---
  const toUnfreeze = collectFrozenCandidates(tasks, unmerged, rejected);
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
        if (await git.rebaseInProgress(worktree)) await git.rebaseAbort(worktree);
      } catch (err) {
        log.line(`rebaseAbort ${f} ошибка: ${listikErrText(err)}`);
        continue;
      }
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
        await abortQuiet(git, worktree);
        error = err.message ?? String(err);
      }
      if (error !== undefined) {
        next = `перебазируй сам: git rebase ${base} в ${worktree}; ошибка: ${error}; потом продолжай задачу`;
      } else if (r.ok) {
        rebased = true;
        next = `дерево перебазировано на ${base.slice(0, 7)}, продолжай задачу`;
      } else {
        await abortQuiet(git, worktree);
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
  return {merged, mergedNow, unmerged, halt: finalHalt, gate, order, unfrozen, integration, cleaned,
    rejected};
}

// Владелец → замороженные им открытые задачи, готовые к разморозке: владелец `done`
// и не входит в `unmerged` этого прохода (влит сейчас/ранее либо закрыт без коммитов).
function collectFrozenCandidates(tasks, unmerged, rejected) {
  const doneOwners = new Set((tasks || []).filter(t => t.status === "done").map(t => t.id));
  const skip = new Set([...(unmerged || []), ...(rejected || [])]);
  const map = frozenBy(tasks);
  const out = [];
  for (const [owner, ids] of map) {
    if (!doneOwners.has(owner)) continue;
    if (skip.has(owner)) continue;
    for (const f of ids) out.push({f, owner});
  }
  return out;
}

function stampFile(d) {
  return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

// Одна команда интеграции: группа процессов, SIGTERM по таймауту, SIGKILL через 5с.
function killGroup(pid, signal) {
  if (!pid) return;
  try { process.kill(-pid, signal); } catch { /* лидер мог уже выйти */ }
  try {
    execFileSync("kill", ["-s", signal === "SIGKILL" ? "KILL" : "TERM", `-${pid}`],
      {stdio: "ignore", timeout: 2000});
  } catch { /* группы уже нет */ }
}

function envForVerify(task) {
  const env = {...process.env};
  const port = portOf(task);
  if (port != null) env.LISTIK_DEV_PORT = String(port);
  else delete env.LISTIK_DEV_PORT;
  return env;
}

function runIntegrationCommand(argv, cwd, logFd, timeoutSec, env = process.env) {
  return new Promise((resolvePromise) => {
    const start = Date.now();
    const child = spawn(argv[0], argv.slice(1), {
      cwd, env, detached: true, stdio: ["ignore", logFd, logFd],
    });
    let settled = false;
    let timedOut = false;
    let killTimer = null;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(termTimer);
      if (killTimer) clearTimeout(killTimer);
      resolvePromise({...result, ms: Date.now() - start});
    };
    const termTimer = setTimeout(() => {
      timedOut = true;
      killGroup(child.pid, "SIGTERM");
      killTimer = setTimeout(() => {
        killGroup(child.pid, "SIGKILL");
        finish({code: null, timedOut: true});
      }, 5000);
    }, timeoutSec * 1000);
    child.on("exit", (code) => {
      if (timedOut) return;
      finish({code, timedOut: false});
    });
    child.on("error", () => {
      if (timedOut) return;
      finish({code: 1, timedOut: false});
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
  const branchName = (cand.branch || "").trim() || `task/${id}`;

  try {
    const exists = await git.branchExists(projectPath, branchName);
    if (!exists) {
      log.line(`дерево ${id} не убрано: ветки нет`);
      return null;
    }
    const isAnc = await git.isAncestor(projectPath, branchName, "HEAD");
    if (!isAnc) {
      log.line(`дерево ${id} не убрано: ветка не влита`);
      return null;
    }
  } catch (err) {
    log.line(`дерево ${id} не убрано: ${err.message ?? err}`);
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
