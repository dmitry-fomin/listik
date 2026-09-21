import {test} from "node:test";
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtempSync, writeFileSync, mkdirSync, readdirSync, readFileSync} from "node:fs";
import nodeFs from "node:fs";
import {tmpdir} from "node:os";
import {join, resolve, dirname} from "node:path";
import {fileURLToPath} from "node:url";
import {renderArgv, otherSideIds, buildPrompt, runArbiter, resolveWithArbiter} from "../arbiter.mjs";
import {ARBITER_MARK} from "../barrier.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = resolve(HERE, "fixtures/fake-arbiter.mjs");

// `git` может отсутствовать на машине судьи — тогда блок пропускается целиком.
let gitAvailable = true;
try {
  execFileSync("git", ["--version"], {stdio: "ignore"});
} catch {
  gitAvailable = false;
}

const emptyConfig = gitAvailable ? join(mkdtempSync(join(tmpdir(), "swarm-arbiter-cfg-")), "gitconfig") : null;
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
  const repo = mkdtempSync(join(tmpdir(), "swarm-arbiter-repo-"));
  sh(repo, "init", "-q", "-b", "main");
  sh(repo, "config", "user.name", "Test");
  sh(repo, "config", "user.email", "test@example.com");
  writeFileSync(join(repo, "f.txt"), "base\n");
  writeFileSync(join(repo, "g.txt"), "base\n");
  sh(repo, "add", "f.txt", "g.txt");
  sh(repo, "commit", "-q", "-m", "f base");
  return repo;
}

function addWorktree(repo, id) {
  const path = join(repo, ".worktrees", id);
  mkdirSync(join(repo, ".worktrees"), {recursive: true});
  sh(repo, "worktree", "add", "-q", "-b", `task/${id}`, path, "HEAD");
  return path;
}

function makeLog() {
  const lines = [];
  return {lines, line: (t) => lines.push(t), action: (t) => lines.push(t)};
}

function fakeListik(showMap = {}) {
  const calls = {show: [], comment: []};
  return {
    calls,
    async show(id) {
      calls.show.push(id);
      const v = showMap[id];
      if (v === undefined) throw new Error(`no show fixture for ${id}`);
      if (v instanceof Error) throw v;
      return v;
    },
    async comment(id, text) {
      calls.comment.push({id, text});
      return {id};
    },
  };
}

function tmpLogDir() {
  return mkdtempSync(join(tmpdir(), "swarm-arbiter-log-"));
}

const suite = gitAvailable ? test : test.skip;

// ---------------------------------------------------------------- чистое ---

test("renderArgv: подстановка известных плейсхолдеров, {other} нетронут", () => {
  const out = renderArgv(["x", "-p", "{prompt}", "{task_id}:{files}", "{other}"], {
    prompt: "/tmp/x.prompt.md", task_id: "t1", worktree: "/tmp/wt", files: ["a.txt", "b.txt"],
  });
  assert.deepEqual(out, ["x", "-p", "/tmp/x.prompt.md", "t1:a.txt,b.txt", "{other}"]);
});

test("otherSideIds: целый токен, без ложного t1 из t10", () => {
  assert.deepEqual(
    otherSideIds(["t1: fix", "listik-abcd (шаг…)", "noise"], ["t1", "listik-abcd", "t9"]),
    ["t1", "listik-abcd"],
  );
  assert.deepEqual(otherSideIds(["t10: fix", "t1 ok"], ["t1", "t10"]), ["t10", "t1"]);
});

test("buildPrompt: обе стороны, файлы, правила, обрезка ТЗ", () => {
  const bigSpec = "x".repeat(30000);
  const prompt = buildPrompt({
    task: {id: "task1", title: "T title", description: "T desc", acceptance: "T acc"},
    others: [{id: "other1", title: "O title", description: "O desc", acceptance: "O acc"}],
    conflicts: ["f.txt", "g.txt"],
    base: "deadbeef",
    worktree: "/tmp/wt",
    specText: bigSpec,
    otherSpecs: ["other spec text"],
    taskLog: ["task1 commit subj"],
    mainLog: ["other1 commit subj"],
    stopSubject: "task1 f edit",
  });
  assert.match(prompt, /task1/);
  assert.match(prompt, /other1/);
  assert.match(prompt, /T title/);
  assert.match(prompt, /T desc/);
  assert.match(prompt, /T acc/);
  assert.match(prompt, /O title/);
  assert.match(prompt, /O desc/);
  assert.match(prompt, /O acc/);
  assert.match(prompt, /f\.txt/);
  assert.match(prompt, /g\.txt/);
  assert.match(prompt, /\|\|\|\|\|\|\|/);
  assert.match(prompt, /git rebase --continue/);
  assert.match(prompt, /task1 f edit/);
  assert.match(prompt, /обрезано/);
  assert.ok(!prompt.includes(bigSpec)); // обрезано, полный текст не попал целиком
});

// ---------------------------------------------------------------- runArbiter ---

suite("runArbiter: hang + timeout → timedOut, процесс не живёт", async () => {
  const cwd = mkdtempSync(join(tmpdir(), "swarm-arbiter-hang-"));
  writeFileSync(join(cwd, "x.prompt.md"), "prompt\n");
  const logPath = join(cwd, "arbiter.log");
  process.env.FAKE_ARBITER_MODE = "hang";
  const res = await runArbiter({
    argv: ["node", FIXTURE, "x.prompt.md", ""], cwd, timeoutSec: 1, logPath,
  });
  delete process.env.FAKE_ARBITER_MODE;
  assert.equal(res.timedOut, true);
  assert.throws(() => process.kill(res.pid, 0));
});

suite("runArbiter: fail → code 1, output из лог-файла", async () => {
  const cwd = mkdtempSync(join(tmpdir(), "swarm-arbiter-fail-"));
  const promptPath = join(cwd, "x.prompt.md");
  writeFileSync(promptPath, "prompt\n");
  const logPath = join(cwd, "arbiter.log");
  process.env.FAKE_ARBITER_MODE = "fail";
  const res = await runArbiter({argv: ["node", FIXTURE, promptPath, ""], cwd, timeoutSec: 30, logPath});
  delete process.env.FAKE_ARBITER_MODE;
  assert.equal(res.code, 1);
  assert.match(res.output, /непротиворечивое слияние невозможно/);
});

// ------------------------------------------------------- resolveWithArbiter ---

// Готовит: base-репо с f.txt/g.txt, задачу task1 (правит f.txt), апстрим-коммит от имени
// other1 (тоже правит f.txt) — настоящий конфликт ребейза одной строки.
function setupSingleConflict() {
  const repo = initRepo();
  const worktree = addWorktree(repo, "task1");
  writeFileSync(join(worktree, "f.txt"), "task-side\n");
  sh(worktree, "commit", "-am", "task1 edits f");

  writeFileSync(join(repo, "f.txt"), "main-side\n");
  sh(repo, "commit", "-am", "other1 edits f");

  return {repo, worktree};
}

function baseCardAndTasks() {
  return {
    card: {id: "task1", title: "T title", description: "T desc", acceptance: "T acc", spec_path: null},
    tasks: [{id: "task1"}, {id: "other1"}],
  };
}

suite("resolveWithArbiter 5: ok, конфликт одной строки — успех, обе стороны в файле", async () => {
  const {repo, worktree} = setupSingleConflict();
  const base = await git.headSha(repo);
  const rebaseRes = await git.rebase(worktree, base);
  assert.equal(rebaseRes.ok, false);

  const {card, tasks} = baseCardAndTasks();
  const listik = fakeListik({
    other1: {id: "other1", title: "O title", description: "O desc", acceptance: "O acc", spec_path: null},
  });
  const log = makeLog();
  const logDir = tmpLogDir();
  const result = await resolveWithArbiter({
    git, listik, fs: nodeFs, config: {logDir}, swarmConfig: {arbiter: ["node", FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 30},
    log, projectPath: repo, task: {id: "task1", branch: "task/task1"}, card, worktree, base, tasks,
    now: new Date(),
  });

  assert.equal(result.ok, true);
  assert.deepEqual(result.files, ["f.txt"]);
  const content = readFileSync(join(worktree, "f.txt"), "utf8");
  assert.match(content, /main-side/);
  assert.match(content, /task-side/);
  assert.equal(await git.rebaseInProgress(worktree), false);

  const c = listik.calls.comment.find(c => c.id === "task1");
  assert.ok(c && c.text.startsWith(ARBITER_MARK));

  const promptFiles = readdirSync(logDir).filter(f => f.endsWith(".prompt.md"));
  assert.equal(promptFiles.length, 1);
  const promptText = readFileSync(join(logDir, promptFiles[0]), "utf8");
  assert.match(promptText, /task1/);
  assert.match(promptText, /other1/);
  const logFiles = readdirSync(logDir).filter(f => f.endsWith(".log"));
  assert.equal(logFiles.length, 1);
});

suite("resolveWithArbiter 6: цепочка из двух конфликтующих коммитов — два промпта, stops:2", async () => {
  const repo = initRepo();
  const worktree = addWorktree(repo, "task1");
  // `rebaseContinue` не передаёт `-c merge.conflictStyle=diff3` (порция b: "маркеры уже
  // в файлах" — верно только для первой остановки), поэтому для второй остановки этой
  // цепочки диффолт задаём в конфиге дерева, как в репозитории, где diff3 настроен глобально.
  sh(worktree, "config", "merge.conflictStyle", "diff3");
  writeFileSync(join(worktree, "f.txt"), "task-f\n");
  sh(worktree, "commit", "-am", "task1 edits f");
  writeFileSync(join(worktree, "g.txt"), "task-g\n");
  sh(worktree, "commit", "-am", "task1 edits g");

  writeFileSync(join(repo, "f.txt"), "main-f\n");
  writeFileSync(join(repo, "g.txt"), "main-g\n");
  sh(repo, "commit", "-am", "other1 edits f and g");

  const base = await git.headSha(repo);
  const rebaseRes = await git.rebase(worktree, base);
  assert.equal(rebaseRes.ok, false);

  const {card, tasks} = baseCardAndTasks();
  const listik = fakeListik({
    other1: {id: "other1", title: "O", description: "O", acceptance: "O", spec_path: null},
  });
  const log = makeLog();
  const logDir = tmpLogDir();
  const result = await resolveWithArbiter({
    git, listik, fs: nodeFs, config: {logDir}, swarmConfig: {arbiter: ["node", FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 30},
    log, projectPath: repo, task: {id: "task1", branch: "task/task1"}, card, worktree, base, tasks,
    now: new Date(),
  });

  assert.equal(result.ok, true);
  const promptFiles = readdirSync(logDir).filter(f => f.endsWith(".prompt.md"));
  assert.equal(promptFiles.length, 2);
  assert.equal(await git.rebaseInProgress(worktree), false);
  assert.match(readFileSync(join(worktree, "f.txt"), "utf8"), /main-f/);
  assert.match(readFileSync(join(worktree, "g.txt"), "utf8"), /main-g/);
});

suite("resolveWithArbiter 7: leave — маркеры остались, откат полный, comment не вызван", async () => {
  const {repo, worktree} = setupSingleConflict();
  const base = await git.headSha(repo);
  const headBefore = await git.headSha(repo);
  const rebaseRes = await git.rebase(worktree, base);
  assert.equal(rebaseRes.ok, false);

  const {card, tasks} = baseCardAndTasks();
  const listik = fakeListik({other1: {id: "other1", title: "", description: "", acceptance: "", spec_path: null}});
  const log = makeLog();
  process.env.FAKE_ARBITER_MODE = "leave";
  const result = await resolveWithArbiter({
    git, listik, fs: nodeFs, config: {logDir: tmpLogDir()},
    swarmConfig: {arbiter: ["node", FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 30},
    log, projectPath: repo, task: {id: "task1", branch: "task/task1"}, card, worktree, base, tasks,
    now: new Date(),
  });
  delete process.env.FAKE_ARBITER_MODE;

  assert.equal(result.ok, false);
  assert.match(result.reason, /маркеры остались/);
  assert.equal(await git.rebaseInProgress(worktree), false);
  assert.equal(await git.headSha(repo), headBefore);
  assert.equal(listik.calls.comment.length, 0);
});

suite("resolveWithArbiter 8: fail → код 1; hang+timeout:1 → таймаут", async () => {
  const {repo: repoA, worktree: wtA} = setupSingleConflict();
  const baseA = await git.headSha(repoA);
  await git.rebase(wtA, baseA);
  process.env.FAKE_ARBITER_MODE = "fail";
  const {card, tasks} = baseCardAndTasks();
  let listik = fakeListik({other1: {id: "other1", title: "", description: "", acceptance: "", spec_path: null}});
  let result = await resolveWithArbiter({
    git, listik, fs: nodeFs, config: {logDir: tmpLogDir()},
    swarmConfig: {arbiter: ["node", FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 30},
    log: makeLog(), projectPath: repoA, task: {id: "task1", branch: "task/task1"}, card, worktree: wtA, base: baseA,
    tasks, now: new Date(),
  });
  assert.equal(result.ok, false);
  assert.match(result.reason, /код 1/);

  const {repo: repoB, worktree: wtB} = setupSingleConflict();
  const baseB = await git.headSha(repoB);
  await git.rebase(wtB, baseB);
  process.env.FAKE_ARBITER_MODE = "hang";
  listik = fakeListik({other1: {id: "other1", title: "", description: "", acceptance: "", spec_path: null}});
  result = await resolveWithArbiter({
    git, listik, fs: nodeFs, config: {logDir: tmpLogDir()},
    swarmConfig: {arbiter: ["node", FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 1},
    log: makeLog(), projectPath: repoB, task: {id: "task1", branch: "task/task1"}, card, worktree: wtB, base: baseB,
    tasks, now: new Date(),
  });
  delete process.env.FAKE_ARBITER_MODE;
  assert.equal(result.ok, false);
  assert.match(result.reason, /таймаут/);
});

suite("resolveWithArbiter 9: режим git — «арбитр тронул git», rebaseAbort/mergeFfOnly не звали, HEAD прежний", async () => {
  const {repo, worktree} = setupSingleConflict();
  const base = await git.headSha(repo);
  const headBefore = await git.headSha(repo);
  await git.rebase(worktree, base);
  process.env.FAKE_ARBITER_MODE = "git";
  const {card, tasks} = baseCardAndTasks();
  const listik = fakeListik({other1: {id: "other1", title: "", description: "", acceptance: "", spec_path: null}});
  const result = await resolveWithArbiter({
    git, listik, fs: nodeFs, config: {logDir: tmpLogDir()},
    swarmConfig: {arbiter: ["node", FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 30},
    log: makeLog(), projectPath: repo, task: {id: "task1", branch: "task/task1"}, card, worktree, base, tasks,
    now: new Date(),
  });
  delete process.env.FAKE_ARBITER_MODE;

  assert.equal(result.ok, false);
  assert.match(result.reason, /арбитр тронул git/);
  assert.equal(await git.headSha(repo), headBefore);
});

suite("resolveWithArbiter: окружение арбитра без LISTIK_TASK_ID", async () => {
  const {repo, worktree} = setupSingleConflict();
  const base = await git.headSha(repo);
  await git.rebase(worktree, base);
  const envFile = join(mkdtempSync(join(tmpdir(), "swarm-arbiter-env-")), "env.json");
  process.env.FAKE_ARBITER_ENV_FILE = envFile;
  const {card, tasks} = baseCardAndTasks();
  const listik = fakeListik({other1: {id: "other1", title: "", description: "", acceptance: "", spec_path: null}});
  const result = await resolveWithArbiter({
    git, listik, fs: nodeFs, config: {logDir: tmpLogDir()},
    swarmConfig: {arbiter: ["node", FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 30},
    log: makeLog(), projectPath: repo, task: {id: "task1", branch: "task/task1"}, card, worktree, base, tasks,
    now: new Date(),
  });
  delete process.env.FAKE_ARBITER_ENV_FILE;

  assert.equal(result.ok, true);
  const env = JSON.parse(readFileSync(envFile, "utf8"));
  assert.equal(env.LISTIK_TASK_ID, undefined);
});
