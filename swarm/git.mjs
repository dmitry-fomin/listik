// Тонкий слой над git для барьера роя. Каждая экспортируемая функция — ровно одна
// git-команда из списка, разрешённого порцией b (docs/specs/steps/listik-dzf0.b.md).
// Никакого общего `git(repo, ...args)`: набор команд, которые рой может выполнить в
// чужом репозитории, читается только здесь. `--no-optional-locks` — как в
// `listik/worktree.py`, чтобы опрос чужого дерева не срывал коммит воркера в нём.
import {execFile} from "node:child_process";
import {existsSync, readFileSync} from "node:fs";
import {resolve} from "node:path";

const MAX_BUFFER = 64 * 1024 * 1024;

export class GitError extends Error {
  constructor(message, args, code) {
    super(message);
    this.args = args;
    this.code = code;
  }
}

function runGit(repo, args) {
  return new Promise((res) => {
    execFile("git", ["--no-optional-locks", "-C", repo, ...args], {maxBuffer: MAX_BUFFER},
      (err, stdout, stderr) => {
        const code = err ? (typeof err.code === "number" ? err.code : 1) : 0;
        res({code, stdout: stdout ?? "", stderr: stderr ?? ""});
      });
  });
}

// Отказ человеку: первые три непустые строки stderr, без трейсбека — как
// `listik/worktree.py:_git_message`.
function gitMessage({stdout, stderr, code}) {
  const text = stderr.trim() ? stderr : `${stderr}\n${stdout}`;
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
  if (!lines.length) return `git завершился с кодом ${code}`;
  return lines.slice(0, 3).join("; ");
}

async function mustOk(repo, args) {
  const res = await runGit(repo, args);
  if (res.code !== 0) throw new GitError(gitMessage(res), args, res.code);
  return res.stdout;
}

// ------------------------------------------------------------------ чтение

export async function headSha(repo) {
  return (await mustOk(repo, ["rev-parse", "HEAD"])).trim();
}

export async function currentBranch(repo) {
  const res = await runGit(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"]);
  if (res.code === 0) return res.stdout.trim();
  if (res.code === 1) return null;
  throw new GitError(gitMessage(res), ["symbolic-ref", "--quiet", "--short", "HEAD"], res.code);
}

export async function branchExists(repo, branch) {
  const args = ["rev-parse", "--verify", "--quiet", `refs/heads/${branch}`];
  const res = await runGit(repo, args);
  if (res.code === 0) return true;
  if (res.code === 1) return false;
  throw new GitError(gitMessage(res), args, res.code);
}

export async function aheadCount(repo, base, branch) {
  const out = await mustOk(repo, ["rev-list", "--count", `${base}..${branch}`]);
  return Number(out.trim());
}

export async function isAncestor(repo, a, b) {
  const args = ["merge-base", "--is-ancestor", a, b];
  const res = await runGit(repo, args);
  if (res.code === 0) return true;
  if (res.code === 1) return false;
  throw new GitError(gitMessage(res), args, res.code);
}

export async function isDirty(tree) {
  return (await mustOk(tree, ["status", "--porcelain"])).trim().length > 0;
}

export async function statusPorcelain(tree) {
  return mustOk(tree, ["status", "--porcelain"]);
}

export async function worktreeList(repo) {
  const out = await mustOk(repo, ["worktree", "list", "--porcelain"]);
  const entries = [];
  let current = null;
  for (const line of out.split("\n")) {
    if (line.startsWith("worktree ")) {
      current = {path: line.slice("worktree ".length).trim(), branch: ""};
      entries.push(current);
    } else if (current === null) {
      continue;
    } else if (line.startsWith("branch ")) {
      const ref = line.slice("branch ".length).trim();
      current.branch = ref.startsWith("refs/heads/") ? ref.slice("refs/heads/".length) : ref;
    }
  }
  return entries;
}

export async function rebaseInProgress(tree) {
  const merge = (await mustOk(tree, ["rev-parse", "--git-path", "rebase-merge"])).trim();
  const apply = (await mustOk(tree, ["rev-parse", "--git-path", "rebase-apply"])).trim();
  return existsSync(resolve(tree, merge)) || existsSync(resolve(tree, apply));
}

export async function conflictedFiles(tree) {
  const out = await mustOk(tree, ["-c", "core.quotepath=false", "diff", "--name-only",
    "--diff-filter=U"]);
  return out.split("\n").map(l => l.trim()).filter(Boolean).sort();
}

function isConflictMarkerLine(line) {
  return line.startsWith("<<<<<<< ") || line === "=======" || line.startsWith(">>>>>>> ");
}

export function hasConflictMarkers(tree, files) {
  const out = [];
  for (const f of files) {
    let text;
    try {
      text = readFileSync(resolve(tree, f), "utf8");
    } catch {
      continue;
    }
    if (text.split("\n").some(isConflictMarkerLine)) out.push(f);
  }
  return out;
}

export async function changedFiles(repo, base, ref) {
  const out = await mustOk(repo, ["-c", "core.quotepath=false", "diff", "--name-only",
    `${base}..${ref}`]);
  return out.split("\n").map(l => l.trim()).filter(Boolean).sort();
}

export async function mergeBase(repo, a, b) {
  return (await mustOk(repo, ["merge-base", a, b])).trim();
}

// Темы коммитов (`git log --format=%s`) для произвольного `rev` (диапазон, ref, ...);
// пустой вывод → [] (порция e: арбитр).
export async function logSubjects(repo, rev) {
  const out = await mustOk(repo, ["log", "--format=%s", rev]);
  return out.split("\n").map(l => l.trim()).filter(Boolean);
}

// ------------------------------------------------------------------ запись

async function rebaseResult(tree, res, args) {
  if (res.code === 0) return {ok: true};
  if (await rebaseInProgress(tree)) {
    return {ok: false, conflicts: await conflictedFiles(tree), stderr: res.stderr};
  }
  throw new GitError(gitMessage(res), args, res.code);
}

export async function rebase(tree, onto) {
  const args = ["-c", "core.editor=true", "-c", "merge.conflictStyle=diff3", "rebase", onto];
  const res = await runGit(tree, args);
  return rebaseResult(tree, res, args);
}

export async function rebaseAbort(tree) {
  await mustOk(tree, ["rebase", "--abort"]);
}

export async function rebaseContinue(tree) {
  const args = ["-c", "core.editor=true", "rebase", "--continue"];
  const res = await runGit(tree, args);
  return rebaseResult(tree, res, args);
}

export async function add(tree, files) {
  await mustOk(tree, ["add", ...files]);
}

export async function snapshotCommit(tree, message) {
  if (!(await isDirty(tree))) return null;
  await mustOk(tree, ["add", "-A"]);
  await mustOk(tree, ["-c", "user.name=listik-swarm", "-c", "user.email=swarm@listik.local",
    "-c", "commit.gpgsign=false", "commit", "-q", "-m", message]);
  return headSha(tree);
}

export async function mergeFfOnly(repo, branch) {
  const res = await runGit(repo, ["merge", "--ff-only", branch]);
  return {ok: res.code === 0, stderr: res.stderr};
}

export async function removeWorktree(repo, path) {
  const res = await runGit(repo, ["worktree", "remove", "--force", path]);
  return {ok: res.code === 0, stderr: res.stderr};
}

export async function deleteBranch(repo, branch) {
  const res = await runGit(repo, ["branch", "-d", branch]);
  return {ok: res.code === 0, stderr: res.stderr};
}
