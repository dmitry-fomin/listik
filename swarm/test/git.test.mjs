import {test} from "node:test";
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtempSync, writeFileSync, mkdirSync, existsSync, readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";

// `git` может отсутствовать на машине судьи — тогда файл пропускается целиком.
let gitAvailable = true;
try {
  execFileSync("git", ["--version"], {stdio: "ignore"});
} catch {
  gitAvailable = false;
}

// Прод без TTY не должен вставать на `vi` — окружение субпроцессов воспроизводит это:
// приёмка не зависит от `EDITOR`/`GIT_EDITOR` машины судьи.
const emptyConfig = gitAvailable ? join(mkdtempSync(join(tmpdir(), "swarm-git-cfg-")), "gitconfig") : null;
if (gitAvailable) writeFileSync(emptyConfig, "");
if (gitAvailable) {
  Object.assign(process.env, {
    GIT_CONFIG_GLOBAL: emptyConfig,
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_EDITOR: "true",
    GIT_TERMINAL_PROMPT: "0",
  });
}

let git;
if (gitAvailable) {
  git = await import("../git.mjs");
}

function sh(cwd, ...args) {
  return execFileSync("git", args, {cwd, encoding: "utf8"});
}

function initRepo() {
  const repo = mkdtempSync(join(tmpdir(), "swarm-git-repo-"));
  sh(repo, "init", "-q", "-b", "main");
  sh(repo, "config", "user.name", "Test");
  sh(repo, "config", "user.email", "test@example.com");
  writeFileSync(join(repo, "README.md"), "start\n");
  sh(repo, "add", "README.md");
  sh(repo, "commit", "-q", "-m", "start");
  return repo;
}

function addWorktree(repo, id) {
  const path = join(repo, ".worktrees", id);
  mkdirSync(join(repo, ".worktrees"), {recursive: true});
  sh(repo, "worktree", "add", "-q", "-b", `task/${id}`, path, "HEAD");
  return path;
}

const suite = gitAvailable ? test : test.skip;

suite("headSha / currentBranch (detached → null)", async () => {
  const repo = initRepo();
  const sha = await git.headSha(repo);
  assert.match(sha, /^[0-9a-f]{40}$/);
  assert.equal(await git.currentBranch(repo), "main");
  sh(repo, "checkout", "-q", "--detach", "HEAD");
  assert.equal(await git.currentBranch(repo), null);
});

suite("aheadCount 0 и 2", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "a");
  assert.equal(await git.aheadCount(repo, "main", "task/a"), 0);
  writeFileSync(join(tree, "f1.txt"), "1\n");
  sh(tree, "add", "f1.txt");
  sh(tree, "commit", "-q", "-m", "c1");
  writeFileSync(join(tree, "f2.txt"), "2\n");
  sh(tree, "add", "f2.txt");
  sh(tree, "commit", "-q", "-m", "c2");
  assert.equal(await git.aheadCount(repo, "main", "task/a"), 2);
});

suite("isAncestor: предок → true, сиблинг → false", async () => {
  const repo = initRepo();
  const base = await git.headSha(repo);
  const treeA = addWorktree(repo, "a");
  writeFileSync(join(treeA, "a.txt"), "a\n");
  sh(treeA, "add", "a.txt");
  sh(treeA, "commit", "-q", "-m", "a");
  const treeB = addWorktree(repo, "b");
  writeFileSync(join(treeB, "b.txt"), "b\n");
  sh(treeB, "add", "b.txt");
  sh(treeB, "commit", "-q", "-m", "b");
  assert.equal(await git.isAncestor(repo, base, "task/a"), true);
  assert.equal(await git.isAncestor(repo, "task/a", "task/b"), false);
});

suite("isDirty на неотслеживаемом файле", async () => {
  const repo = initRepo();
  assert.equal(await git.isDirty(repo), false);
  writeFileSync(join(repo, "untracked.txt"), "x\n");
  assert.equal(await git.isDirty(repo), true);
});

suite("rebase чистый — правки разных файлов, aheadCount сохранён", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "a");
  writeFileSync(join(tree, "task.txt"), "task\n");
  sh(tree, "add", "task.txt");
  sh(tree, "commit", "-q", "-m", "task change");
  writeFileSync(join(repo, "main.txt"), "main\n");
  sh(repo, "add", "main.txt");
  sh(repo, "commit", "-q", "-m", "main change");
  const before = await git.aheadCount(repo, "main", "task/a");
  const res = await git.rebase(tree, "main");
  assert.deepEqual(res, {ok: true});
  assert.equal(await git.aheadCount(repo, "main", "task/a"), before);
});

suite("rebase с конфликтом одной строки", async () => {
  const repo = initRepo();
  writeFileSync(join(repo, "f.txt"), "one\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "f base");
  const tree = addWorktree(repo, "a");
  writeFileSync(join(tree, "f.txt"), "task-side\n");
  sh(tree, "add", "f.txt");
  sh(tree, "commit", "-q", "-m", "task edits f");
  writeFileSync(join(repo, "f.txt"), "main-side\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "main edits f");

  const res = await git.rebase(tree, "main");
  assert.equal(res.ok, false);
  assert.deepEqual(res.conflicts, ["f.txt"]);
  assert.equal(await git.rebaseInProgress(tree), true);
  assert.deepEqual(git.hasConflictMarkers(tree, ["f.txt"]), ["f.txt"]);
  const content = readFileSync(join(tree, "f.txt"), "utf8");
  assert.match(content, /\|\|\|\|\|\|\|/);

  await git.rebaseAbort(tree);
});

suite("rebase на несуществующий onto бросает GitError", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "a");
  await assert.rejects(git.rebase(tree, "does-not-exist"), (err) => {
    assert.equal(err.constructor.name, "GitError");
    assert.ok(Array.isArray(err.args));
    assert.equal(typeof err.code, "number");
    assert.ok(err.message.length > 0);
    return true;
  });
  assert.equal(await git.rebaseInProgress(tree), false);
});

suite("rebaseAbort — дерево байт-в-байт как до ребейза", async () => {
  const repo = initRepo();
  writeFileSync(join(repo, "f.txt"), "one\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "f base");
  const tree = addWorktree(repo, "a");
  writeFileSync(join(tree, "f.txt"), "task-side\n");
  sh(tree, "add", "f.txt");
  sh(tree, "commit", "-q", "-m", "task edits f");
  writeFileSync(join(repo, "f.txt"), "main-side\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "main edits f");

  const statusBefore = sh(tree, "status", "--porcelain");
  const headBefore = sh(tree, "rev-parse", "HEAD");

  await git.rebase(tree, "main");
  assert.equal(await git.rebaseInProgress(tree), true);
  await git.rebaseAbort(tree);
  assert.equal(await git.rebaseInProgress(tree), false);
  assert.equal(sh(tree, "status", "--porcelain"), statusBefore);
  assert.equal(sh(tree, "rev-parse", "HEAD"), headBefore);
});

suite("разрешение руками + add + rebaseContinue", async () => {
  const repo = initRepo();
  writeFileSync(join(repo, "f.txt"), "one\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "f base");
  const tree = addWorktree(repo, "a");
  writeFileSync(join(tree, "f.txt"), "task-side\n");
  sh(tree, "add", "f.txt");
  sh(tree, "commit", "-q", "-m", "task edits f");
  writeFileSync(join(repo, "f.txt"), "main-side\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "main edits f");

  await git.rebase(tree, "main");
  writeFileSync(join(tree, "f.txt"), "resolved\n");
  await git.add(tree, ["f.txt"]);
  const res = await git.rebaseContinue(tree);
  assert.deepEqual(res, {ok: true});
});

suite("ребейз двух коммитов, конфликтует каждый — второй rebaseContinue снова {ok:false}", async () => {
  const repo = initRepo();
  writeFileSync(join(repo, "f.txt"), "one\n");
  writeFileSync(join(repo, "g.txt"), "one\n");
  sh(repo, "add", "f.txt", "g.txt");
  sh(repo, "commit", "-q", "-m", "base");
  const tree = addWorktree(repo, "a");
  writeFileSync(join(tree, "f.txt"), "task-f\n");
  sh(tree, "add", "f.txt");
  sh(tree, "commit", "-q", "-m", "task edits f");
  writeFileSync(join(tree, "g.txt"), "task-g\n");
  sh(tree, "add", "g.txt");
  sh(tree, "commit", "-q", "-m", "task edits g");
  writeFileSync(join(repo, "f.txt"), "main-f\n");
  writeFileSync(join(repo, "g.txt"), "main-g\n");
  sh(repo, "add", "f.txt", "g.txt");
  sh(repo, "commit", "-q", "-m", "main edits f and g");

  const first = await git.rebase(tree, "main");
  assert.equal(first.ok, false);
  assert.deepEqual(first.conflicts, ["f.txt"]);
  writeFileSync(join(tree, "f.txt"), "resolved-f\n");
  await git.add(tree, ["f.txt"]);
  const second = await git.rebaseContinue(tree);
  assert.equal(second.ok, false);
  assert.deepEqual(second.conflicts, ["g.txt"]);
});

suite("snapshotCommit грязное → sha, чистое → null", async () => {
  const repo = initRepo();
  assert.equal(await git.snapshotCommit(repo, "рой: снимок"), null);
  writeFileSync(join(repo, "dirty.txt"), "x\n");
  const sha = await git.snapshotCommit(repo, "рой: снимок");
  assert.match(sha, /^[0-9a-f]{40}$/);
  assert.equal(await git.isDirty(repo), false);
  const author = sh(repo, "log", "-1", "--format=%an");
  assert.equal(author.trim(), "listik-swarm");
});

suite("mergeFfOnly после ребейза → ok, HEAD совпадают", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "a");
  writeFileSync(join(tree, "task.txt"), "task\n");
  sh(tree, "add", "task.txt");
  sh(tree, "commit", "-q", "-m", "task change");
  writeFileSync(join(repo, "main.txt"), "main\n");
  sh(repo, "add", "main.txt");
  sh(repo, "commit", "-q", "-m", "main change");
  await git.rebase(tree, "main");
  const res = await git.mergeFfOnly(repo, "task/a");
  assert.equal(res.ok, true);
  assert.equal(await git.headSha(repo), await git.headSha(tree));
});

suite("mergeFfOnly расходящейся ветки → ok false, HEAD не изменился", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "a");
  writeFileSync(join(tree, "task.txt"), "task\n");
  sh(tree, "add", "task.txt");
  sh(tree, "commit", "-q", "-m", "task change");
  writeFileSync(join(repo, "main.txt"), "main\n");
  sh(repo, "add", "main.txt");
  sh(repo, "commit", "-q", "-m", "main change");
  const before = await git.headSha(repo);
  const res = await git.mergeFfOnly(repo, "task/a");
  assert.equal(res.ok, false);
  assert.equal(await git.headSha(repo), before);
});

suite("removeWorktree + deleteBranch влитой — каталога и ветки нет", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "a");
  writeFileSync(join(tree, "task.txt"), "task\n");
  sh(tree, "add", "task.txt");
  sh(tree, "commit", "-q", "-m", "task change");
  await git.mergeFfOnly(repo, "task/a");
  const rm = await git.removeWorktree(repo, tree);
  assert.equal(rm.ok, true);
  assert.equal(existsSync(tree), false);
  const del = await git.deleteBranch(repo, "task/a");
  assert.equal(del.ok, true);
  assert.equal(await git.branchExists(repo, "task/a"), false);
});

suite("deleteBranch невлитой → ok false, ветка на месте", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "a");
  writeFileSync(join(tree, "task.txt"), "task\n");
  sh(tree, "add", "task.txt");
  sh(tree, "commit", "-q", "-m", "task change");
  const res = await git.deleteBranch(repo, "task/a");
  assert.equal(res.ok, false);
  assert.equal(await git.branchExists(repo, "task/a"), true);
});

suite("changedFiles", async () => {
  const repo = initRepo();
  const base = await git.headSha(repo);
  const tree = addWorktree(repo, "a");
  writeFileSync(join(tree, "one.txt"), "1\n");
  writeFileSync(join(tree, "two.txt"), "2\n");
  sh(tree, "add", "one.txt", "two.txt");
  sh(tree, "commit", "-q", "-m", "two files");
  assert.deepEqual(await git.changedFiles(tree, base, "task/a"), ["one.txt", "two.txt"]);
});
