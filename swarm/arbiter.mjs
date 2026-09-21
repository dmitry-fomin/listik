// Арбитр конфликта ребейза закрытой задачи (шаг 5 барьера, порция e). Единственное
// место, где модель участвует в фазе слияния, и только для закрытой задачи, исполнителя
// у которой уже нет — открытые замороженные задачи конфликт разрешают сами (шаг 7,
// не здесь). Чистое (`renderArgv`…`buildPrompt`) — только обычные объекты внутрь и
// наружу; `runArbiter`/`resolveWithArbiter` — оркестрация: `spawn` прямой импорт (как
// `runIntegrationCommand` в `barrier.mjs`), git/listik/fs приходят параметрами.
import {spawn} from "node:child_process";
import {openSync, closeSync, readFileSync as readFileSyncNode} from "node:fs";
import path from "node:path";
import {ARBITER_MARK} from "./barrier.mjs";

const SPEC_LIMIT = 20000;
const OUTPUT_TAIL_BYTES = 64 * 1024;
const MAX_STOPS = 10;

// ------------------------------------------------------------------- чистое ---

// Подстановка `{prompt}`, `{task_id}`, `{worktree}`, `{files}` (файлы через запятую,
// если `vars.files` — массив) в каждом элементе `template`; неизвестные `{…}` не трогать.
export function renderArgv(template, vars) {
  const files = Array.isArray(vars.files) ? vars.files.join(",") : (vars.files ?? "");
  const map = {prompt: vars.prompt, task_id: vars.task_id, worktree: vars.worktree, files};
  return (template || []).map(item => {
    let out = item;
    for (const [k, v] of Object.entries(map)) {
      out = out.split(`{${k}}`).join(v ?? "");
    }
    return out;
  });
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Id из `knownIds`, встреченные в `subjects` целым токеном (границы — не
// `[A-Za-z0-9_-]`), в порядке первого упоминания. `t1` внутри `t10` не считается.
export function otherSideIds(subjects, knownIds) {
  const found = [];
  const seen = new Set();
  for (const subj of subjects || []) {
    for (const id of knownIds || []) {
      if (seen.has(id)) continue;
      const re = new RegExp(`(?<![A-Za-z0-9_-])${escapeRe(id)}(?![A-Za-z0-9_-])`);
      if (re.test(subj)) {
        seen.add(id);
        found.push(id);
      }
    }
  }
  return found;
}

function truncateSpec(text) {
  const s = text ?? "";
  if (s.length <= SPEC_LIMIT) return s;
  return `${s.slice(0, SPEC_LIMIT)}\n…[ТЗ обрезано, показано ${SPEC_LIMIT} из ${s.length} символов]`;
}

function cardBlock(c) {
  return [
    `id: ${c.id}`,
    `title: ${c.title ?? ""}`,
    `description: ${c.description ?? ""}`,
    `acceptance: ${c.acceptance ?? ""}`,
  ].join("\n");
}

// Промпт на русском: что произошло, как читать маркеры, обе стороны, правила. Порция
// не зовёт `git rebase --continue/--abort` — это явный запрет для модели в правилах.
export function buildPrompt({task, others, conflicts, base, worktree, specText, otherSpecs, taskLog,
  mainLog, stopSubject}) {
  const otherIds = (others || []).map(o => o.id);
  const files = (conflicts || []).join(", ");
  const lines = [];

  lines.push("# Конфликт слияния роя — арбитр");
  lines.push("");
  lines.push("## 1. Что произошло");
  lines.push(`Закрытая задача ${task.id} перебазируется на ${(base || "").slice(0, 7)}. ` +
    `Конфликтуют файлы: ${files}. Дерево: ${worktree}. Ребейз остановлен на коммите: ${stopSubject}.`);
  lines.push("");
  lines.push("## 2. Как читать маркеры");
  lines.push(`\`<<<<<<< HEAD\` — основная ветка с уже влитыми задачами (${otherIds.join(", ") || "нет"}); ` +
    `\`>>>>>>>\` — коммит этой задачи; между \`|||||||\` и \`=======\` — общая база.`);
  lines.push("");
  lines.push(`## 3. Сторона «эта задача»`);
  lines.push(cardBlock(task));
  lines.push("ТЗ:");
  lines.push(truncateSpec(specText));
  lines.push("Темы её коммитов:");
  lines.push((taskLog || []).join("\n"));
  lines.push("");
  lines.push("## 4. Сторона «основная ветка»");
  (others || []).forEach((o, i) => {
    lines.push(cardBlock(o));
    lines.push("ТЗ:");
    lines.push(truncateSpec((otherSpecs || [])[i]));
    lines.push("");
  });
  lines.push("Темы коммитов основной ветки:");
  lines.push((mainLog || []).join("\n"));
  lines.push("");
  lines.push("## 5. Правила");
  lines.push(`- Правь только перечисленные файлы: ${files}.`);
  lines.push("- Убери все маркеры конфликта.");
  lines.push("- Сохрани намерение обеих сторон, не выбирай одну «по умолчанию».");
  lines.push("- Не выполняй git add, git commit, git rebase --continue, git rebase --abort, " +
    "git checkout, git stash — этим занимается рой.");
  lines.push("- Закончи с кодом 0.");
  lines.push("- Если непротиворечивое слияние невозможно — ничего не правь и выйди с ненулевым " +
    "кодом, объяснив причину в stdout.");
  return lines.join("\n");
}

// ------------------------------------------------------------------- запуск ---

function tailOutput(logPath) {
  try {
    const buf = readFileSyncNode(logPath);
    const tail = buf.length > OUTPUT_TAIL_BYTES ? buf.subarray(buf.length - OUTPUT_TAIL_BYTES) : buf;
    return tail.toString("utf8");
  } catch {
    return "";
  }
}

// `spawn` группы, stdout+stderr сразу в fd `logPath` (не pipe — длинный вывод иначе
// дедлочит). Таймаут: `SIGTERM` группе, через 5 с `SIGKILL` группе. Окружение —
// `process.env` без изменений (арбитр не воркер этой карточки, никаких `LISTIK_*`).
export function runArbiter({argv, cwd, timeoutSec, logPath}) {
  return new Promise((resolvePromise) => {
    const logFd = openSync(logPath, "a");
    const child = spawn(argv[0], argv.slice(1), {
      cwd, env: process.env, detached: true, stdio: ["ignore", logFd, logFd],
    });
    let settled = false;
    let timedOut = false;
    let killTimer = null;
    const finish = (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(termTimer);
      if (killTimer) clearTimeout(killTimer);
      try { closeSync(logFd); } catch { /* уже закрыт */ }
      resolvePromise({code: timedOut ? null : code, timedOut, output: tailOutput(logPath), pid: child.pid});
    };
    const termTimer = setTimeout(() => {
      timedOut = true;
      try { process.kill(-child.pid, "SIGTERM"); } catch { /* уже нет */ }
      killTimer = setTimeout(() => {
        try { process.kill(-child.pid, "SIGKILL"); } catch { /* уже нет */ }
        finish(null);
      }, 5000);
    }, timeoutSec * 1000);

    child.on("exit", (code) => {
      if (timedOut) return;
      finish(code);
    });
    child.on("error", () => {
      if (timedOut) return;
      finish(1);
    });
  });
}

// ------------------------------------------------------------------- оркестрация ---

function stampFile(d) {
  return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

function loadSpec(fs, projectPath, specPath) {
  if (!specPath) return "ТЗ недоступно";
  const abs = path.isAbsolute(specPath) ? specPath : path.join(projectPath, specPath);
  try {
    return fs.readFileSync(abs, "utf8");
  } catch {
    return "ТЗ недоступно";
  }
}

async function abortIfInProgress(git, worktree) {
  try {
    if (await git.rebaseInProgress(worktree)) await git.rebaseAbort(worktree);
  } catch {
    /* дерево задачи, не основное */
  }
}

// Вызывается только из шага 5 `runBarrier`, когда `rebase` вернул `{ok: false}` и
// `swarmConfig.arbiter` непуст. Цикл не более `MAX_STOPS` остановок ребейза.
export async function resolveWithArbiter({git, listik, fs, config, swarmConfig, log, projectPath, task,
  card, worktree, base, tasks, now}) {
  const branch = (task.branch || "").trim() || `task/${task.id}`;
  const knownIds = (tasks || []).map(t => t.id);
  const specText = loadSpec(fs, projectPath, card.spec_path);

  let mb, mainSubjects, taskLog;
  try {
    mb = await git.mergeBase(projectPath, branch, base);
    mainSubjects = await git.logSubjects(projectPath, `${mb}..${base}`);
    taskLog = await git.logSubjects(projectPath, `${mb}..${branch}`);
  } catch (err) {
    await abortIfInProgress(git, worktree);
    return {ok: false, reason: `git: ${err.message ?? err}`};
  }

  const otherIds = otherSideIds(mainSubjects, knownIds);
  const others = [];
  for (const id of otherIds) {
    try {
      const oc = await listik.show(id);
      others.push({
        id, title: oc.title, description: oc.description, acceptance: oc.acceptance,
        specText: loadSpec(fs, projectPath, oc.spec_path),
      });
    } catch {
      others.push({id, title: "", description: "", acceptance: "", specText: "ТЗ недоступно"});
    }
  }
  const otherSpecs = others.map(o => o.specText);

  const stamp = stampFile(now instanceof Date ? now : new Date());
  fs.mkdirSync(config.logDir, {recursive: true});

  const resolvedFiles = new Set();

  for (let n = 1; n <= MAX_STOPS; n++) {
    let conflicts;
    try {
      conflicts = await git.conflictedFiles(worktree);
    } catch (err) {
      await abortIfInProgress(git, worktree);
      return {ok: false, reason: `git: ${err.message ?? err}`};
    }
    if (!conflicts.length) {
      await abortIfInProgress(git, worktree);
      return {ok: false, reason: "нет конфликтных файлов"};
    }
    for (const f of conflicts) resolvedFiles.add(f);

    let stopSubject = "";
    try {
      const subjects = await git.logSubjects(worktree, "REBASE_HEAD");
      stopSubject = subjects[0] || "";
    } catch { /* оставляем пусто */ }

    const promptPath = path.resolve(config.logDir, `arbiter-${task.id}-${stamp}-${n}.prompt.md`);
    const logPath = path.resolve(config.logDir, `arbiter-${task.id}-${stamp}-${n}.log`);

    const prompt = buildPrompt({
      task: {id: task.id, title: card.title, description: card.description, acceptance: card.acceptance},
      others, conflicts, base, worktree, specText, otherSpecs, taskLog, mainLog: mainSubjects, stopSubject,
    });
    fs.writeFileSync(promptPath, prompt, "utf8");

    const argv = renderArgv(swarmConfig.arbiter, {prompt: promptPath, task_id: task.id, worktree, files: conflicts});

    const startedAt = Date.now();
    const res = await runArbiter({argv, cwd: worktree, timeoutSec: swarmConfig.arbiterTimeout, logPath});
    const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
    const codeDesc = res.timedOut ? "таймаут" : String(res.code);
    log.action(`арбитр ${task.id} (${n}): ${argv[0]} → код ${codeDesc} (${seconds} с), лог ${logPath}`);

    let stillRebasing;
    try {
      stillRebasing = await git.rebaseInProgress(worktree);
    } catch (err) {
      await abortIfInProgress(git, worktree);
      return {ok: false, reason: `git: ${err.message ?? err}`, logPath};
    }
    if (!stillRebasing) {
      // арбитр сам завершил ребейз (`--continue`/`--abort`) — не трогаем git дальше.
      return {ok: false, reason: "арбитр тронул git", logPath};
    }

    if (res.timedOut) {
      await abortIfInProgress(git, worktree);
      return {ok: false, reason: `таймаут ${swarmConfig.arbiterTimeout} с`, logPath};
    }
    if (res.code !== 0) {
      await abortIfInProgress(git, worktree);
      return {ok: false, reason: `код ${res.code}`, logPath};
    }
    const remainingMarkers = git.hasConflictMarkers(worktree, conflicts);
    if (remainingMarkers.length) {
      await abortIfInProgress(git, worktree);
      return {ok: false, reason: `маркеры остались: ${remainingMarkers.join(", ")}`, logPath};
    }
    try {
      await git.add(worktree, conflicts);
    } catch (err) {
      await abortIfInProgress(git, worktree);
      return {ok: false, reason: `git add: ${err.message ?? err}`, logPath};
    }
    let stillConflicted;
    try {
      stillConflicted = await git.conflictedFiles(worktree);
    } catch (err) {
      await abortIfInProgress(git, worktree);
      return {ok: false, reason: `git: ${err.message ?? err}`, logPath};
    }
    if (stillConflicted.length) {
      await abortIfInProgress(git, worktree);
      return {ok: false, reason: "не все файлы разрешены", logPath};
    }

    let contResult;
    try {
      contResult = await git.rebaseContinue(worktree);
    } catch (err) {
      await abortIfInProgress(git, worktree);
      return {ok: false, reason: `rebase --continue: ${err.message ?? err}`, logPath};
    }
    if (contResult.ok) {
      const files = [...resolvedFiles].sort();
      try {
        await listik.comment(task.id, `${ARBITER_MARK} ${JSON.stringify({
          base, files, stops: n, prompt: promptPath, log: logPath,
        })}`);
      } catch (err) {
        log.line(`comment ${task.id} ошибка: ${err.message ?? err}`);
      }
      return {ok: true, files};
    }
    // {ok: false, conflicts} — следующая остановка, цикл продолжается.
  }

  await abortIfInProgress(git, worktree);
  return {ok: false, reason: `больше ${MAX_STOPS} остановок ребейза`};
}
