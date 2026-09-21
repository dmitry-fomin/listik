import {test} from "node:test";
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtempSync, writeFileSync, mkdirSync} from "node:fs";
import nodeFs from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {
  covers, outsideScope, parseMarked, mergedRecord, sortForMerge, mergeCandidates, haltCards,
  frozenBy, tailLines, runBarrier, MERGED_MARK, FIRST_CHANGE_MARK, HALT_LABEL, SWARM_AUTHOR,
} from "../barrier.mjs";

test("covers — таблица", () => {
  assert.equal(covers("docs", "docs/API.md"), true);
  assert.equal(covers("docs/", "docs/API.md"), true);
  assert.equal(covers("docs", "docs-old/x"), false);
  assert.equal(covers("", "a"), false);
  assert.equal(covers("a/b", "a/b"), true);
});

test("outsideScope: пустая declared — все файлы вне области", () => {
  assert.deepEqual(outsideScope(["x.txt"], []), ["x.txt"]);
});

test("outsideScope: покрытый файл не попадает", () => {
  assert.deepEqual(outsideScope(["docs/API.md", "x.txt"], ["docs"]), ["x.txt"]);
});

test("parseMarked: пропускает чужого автора и неразборный JSON", () => {
  const comments = [
    {author: "human:me", text: `${MERGED_MARK} {"sha":"x"}`, created_at: "2026-01-01T00:00:00Z"},
    {author: SWARM_AUTHOR, text: `${MERGED_MARK} not-json`, created_at: "2026-01-01T00:01:00Z"},
    {author: SWARM_AUTHOR, text: `${MERGED_MARK} {"sha":"y"}`, created_at: "2026-01-01T00:02:00Z"},
  ];
  const out = parseMarked(comments, MERGED_MARK);
  assert.deepEqual(out, [{created_at: "2026-01-01T00:02:00Z", data: {sha: "y"}}]);
});

test("mergedRecord: нет маркера → null", () => {
  assert.equal(mergedRecord({comments: [{author: SWARM_AUTHOR, text: "что-то", created_at: "t"}]}), null);
});

test("mergedRecord: две записи роя — data последней по created_at", () => {
  const card = {
    comments: [
      {author: SWARM_AUTHOR, text: `${MERGED_MARK} {"sha":"a"}`, created_at: "2026-01-01T00:00:00Z"},
      {author: SWARM_AUTHOR, text: `${MERGED_MARK} {"sha":"b"}`, created_at: "2026-01-02T00:00:00Z"},
    ],
  };
  assert.deepEqual(mergedRecord(card), {sha: "b"});
});

function card(id, {firstChange, launchedAt} = {}) {
  const comments = firstChange
    ? [{author: SWARM_AUTHOR, text: `${FIRST_CHANGE_MARK} {}`, created_at: firstChange}]
    : [];
  return {id, comments, launched_at: launchedAt ?? null};
}

test("sortForMerge: с маркером раньше без", () => {
  const a = card("a", {firstChange: "2026-01-01T00:00:10Z"});
  const b = card("b", {});
  assert.deepEqual(sortForMerge([b, a]).map(c => c.id), ["a", "b"]);
});

test("sortForMerge: две с FIRST_CHANGE_MARK — маркер решает, launched_at не важен", () => {
  const a = card("a", {firstChange: "2026-01-01T00:00:10Z", launchedAt: "2026-01-01T00:00:50Z"});
  const b = card("b", {firstChange: "2026-01-01T00:00:20Z", launchedAt: "2026-01-01T00:00:05Z"});
  assert.deepEqual(sortForMerge([b, a]).map(c => c.id), ["a", "b"]);
});

test("sortForMerge: ничья по маркеру и launched_at — меньший id первым", () => {
  const a = card("b", {firstChange: "2026-01-01T00:00:10Z", launchedAt: "2026-01-01T00:00:20Z"});
  const b = card("a", {firstChange: "2026-01-01T00:00:10Z", launchedAt: "2026-01-01T00:00:20Z"});
  assert.deepEqual(sortForMerge([a, b]).map(c => c.id), ["a", "b"]);
});

test("sortForMerge: два без маркера — по launched_at, потом id", () => {
  const a = card("b", {launchedAt: "2026-01-01T00:00:10Z"});
  const b = card("a", {launchedAt: "2026-01-01T00:00:20Z"});
  assert.deepEqual(sortForMerge([b, a]).map(c => c.id), ["b", "a"]);
});

function task(id, over = {}) {
  return {id, status: "open", labels: [], created_at: "2026-01-01T00:00:00Z", ...over};
}

test("mergeCandidates: фильтрация", () => {
  const tasks = [
    task("open", {status: "open", labels: ["port:5170"], worktree: "/x"}),
    task("no-port", {status: "done", labels: [], worktree: "/x"}),
    task("bad-port", {status: "done", labels: ["port:abc"], worktree: "/x"}),
    task("main", {status: "done", labels: ["port:5171"], worktree: "main"}),
    task("MAIN", {status: "done", labels: ["port:5172"], worktree: "MAIN"}),
    task("master-sp", {status: "done", labels: ["port:5173"], worktree: " master "}),
    task("master", {status: "done", labels: ["port:5174"], worktree: "master"}),
    task("no-wt", {status: "done", labels: ["port:5175"], worktree: ""}),
    task("ok", {status: "done", labels: ["port:5176"], worktree: "/repo/.worktrees/ok",
      branch: "task/ok"}),
  ];
  const out = mergeCandidates(tasks);
  assert.deepEqual(out.map(c => c.id), ["ok"]);
  assert.deepEqual(out[0], {id: "ok", worktree: "/repo/.worktrees/ok", branch: "task/ok",
    needs_owner: undefined});
});

test("haltCards: только открытые с меткой, порядок по created_at", () => {
  const tasks = [
    task("closed", {status: "done", labels: [HALT_LABEL], created_at: "2026-01-01T00:00:00Z"}),
    task("late", {status: "open", labels: [HALT_LABEL], created_at: "2026-01-02T00:00:00Z"}),
    task("early", {status: "open", labels: [HALT_LABEL], created_at: "2026-01-01T00:00:00Z"}),
    task("no-label", {status: "open", labels: [], created_at: "2026-01-01T00:00:00Z"}),
  ];
  assert.deepEqual(haltCards(tasks), ["early", "late"]);
});

test("frozenBy", () => {
  const tasks = [
    task("a", {status: "open", labels: ["frozen-by:human:me"]}),
    task("b", {status: "open", labels: ["frozen-by:human:me"]}),
    task("c", {status: "done", labels: ["frozen-by:human:me"]}),
    task("d", {status: "open", labels: []}),
  ];
  const map = frozenBy(tasks);
  assert.deepEqual(map.get("human:me"), ["a", "b"]);
  assert.equal(map.size, 1);
});

test('tailLines("a\\nb\\nc\\n", 2) === "b\\nc"', () => {
  assert.equal(tailLines("a\nb\nc\n", 2), "b\nc");
});

// --------------------------------------------------------- runBarrier ---

// `git` может отсутствовать на машине судьи — тогда блок пропускается целиком.
let gitAvailable = true;
try {
  execFileSync("git", ["--version"], {stdio: "ignore"});
} catch {
  gitAvailable = false;
}

const emptyConfig = gitAvailable ? join(mkdtempSync(join(tmpdir(), "swarm-barrier-cfg-")), "gitconfig") : null;
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
let GitError;
if (gitAvailable) {
  git = await import("../git.mjs");
  ({GitError} = git);
}

function sh(cwd, ...args) {
  return execFileSync("git", args, {cwd, encoding: "utf8"});
}

function initRepo() {
  const repo = mkdtempSync(join(tmpdir(), "swarm-barrier-repo-"));
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

function makeLog() {
  const lines = [];
  return {
    lines,
    line: (t) => lines.push(t),
    action: (t) => lines.push(t),
  };
}

// Подставной клиент listik: show() по очереди/ошибке из showQueue, comment()/needsOwner()
// просто пишут в журнал вызовов.
function fakeListik(showQueue = {}) {
  const calls = {show: [], comment: [], needsOwner: []};
  const shownCount = {};
  return {
    calls,
    async show(id) {
      calls.show.push(id);
      const entry = showQueue[id];
      if (entry === undefined) throw new Error(`no show fixture for ${id}`);
      const n = (shownCount[id] = (shownCount[id] || 0) + 1) - 1;
      const value = Array.isArray(entry) ? entry[Math.min(n, entry.length - 1)] : entry;
      if (value instanceof Error) throw value;
      return value;
    },
    async comment(id, text) {
      calls.comment.push({id, text});
      return {id};
    },
    async needsOwner(id, text) {
      calls.needsOwner.push({id, text});
      return {id};
    },
  };
}

const suite = gitAvailable ? test : test.skip;

suite("barrier 1: порядок по первой правке, HEAD линейный, MERGED_MARK у обеих", async () => {
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  const treeT2 = addWorktree(repo, "t2");
  writeFileSync(join(treeT1, "a.txt"), "a\n");
  sh(treeT1, "add", "a.txt");
  sh(treeT1, "commit", "-q", "-m", "t1");
  writeFileSync(join(treeT2, "b.txt"), "b\n");
  sh(treeT2, "add", "b.txt");
  sh(treeT2, "commit", "-q", "-m", "t2");

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "done", worktree: treeT2, branch: "task/t2", labels: ["port:1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [
      {author: SWARM_AUTHOR, text: `${FIRST_CHANGE_MARK} {}`, created_at: "2026-01-01T00:00:20Z"},
    ], write_scope: ["a.txt"]},
    t2: {id: "t2", comments: [
      {author: SWARM_AUTHOR, text: `${FIRST_CHANGE_MARK} {}`, created_at: "2026-01-01T00:00:10Z"},
    ], write_scope: ["other/"]},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });

  assert.deepEqual(result.order, ["t2", "t1"]);
  assert.deepEqual(result.mergedNow, ["t2", "t1"]);
  assert.equal(result.gate, null);
  assert.ok(log.lines.some(l => l === "барьер: кандидатов 2, уже влито 0, порядок: t2, t1"));

  const history = sh(repo, "log", "--format=%s").trim().split("\n");
  assert.deepEqual(history, ["t1", "t2", "start"]);

  const c1 = listik.calls.comment.find(c => c.id === "t1");
  const c2 = listik.calls.comment.find(c => c.id === "t2");
  assert.ok(c1 && c1.text.startsWith(MERGED_MARK));
  const rec1 = JSON.parse(c1.text.slice(MERGED_MARK.length).trim());
  const rec2 = JSON.parse(c2.text.slice(MERGED_MARK.length).trim());
  assert.deepEqual(rec1.files, ["a.txt"]);
  assert.deepEqual(rec1.outside, []);
  assert.deepEqual(rec2.files, ["b.txt"]);
  assert.deepEqual(rec2.outside, ["b.txt"]);
});

suite("barrier 2: грязное дерево не вливается, чистая вливается, gate unmerged", async () => {
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  const treeT2 = addWorktree(repo, "t2");
  writeFileSync(join(treeT1, "a.txt"), "a\n");
  sh(treeT1, "add", "a.txt");
  sh(treeT1, "commit", "-q", "-m", "t1");
  writeFileSync(join(treeT1, "untracked.txt"), "x\n");
  writeFileSync(join(treeT2, "b.txt"), "b\n");
  sh(treeT2, "add", "b.txt");
  sh(treeT2, "commit", "-q", "-m", "t2");

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "done", worktree: treeT2, branch: "task/t2", labels: ["port:1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const statusBefore = sh(treeT1, "status", "--porcelain");
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });

  assert.deepEqual(result.unmerged, ["t1"]);
  assert.deepEqual(result.merged, ["t2"]);
  assert.deepEqual(result.gate, {reason: "unmerged", ids: ["t1"]});
  assert.equal(sh(treeT1, "status", "--porcelain"), statusBefore);
  assert.equal(await git.headSha(repo), await git.headSha(treeT2));
  assert.ok(listik.calls.needsOwner[0].text.startsWith("рой: не влита — в дереве"));
});

suite("barrier 3: конфликт — первая по порядку влита, вторая needs-owner, откат полный", async () => {
  const repo = initRepo();
  writeFileSync(join(repo, "f.txt"), "base\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "f base");
  const treeT1 = addWorktree(repo, "t1");
  writeFileSync(join(treeT1, "f.txt"), "t1-side\n");
  sh(treeT1, "add", "f.txt");
  sh(treeT1, "commit", "-q", "-m", "t1 edits f");
  const treeT2 = addWorktree(repo, "t2");
  writeFileSync(join(treeT2, "f.txt"), "t2-side\n");
  sh(treeT2, "add", "f.txt");
  sh(treeT2, "commit", "-q", "-m", "t2 edits f");

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "done", worktree: treeT2, branch: "task/t2", labels: ["port:1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const t2HeadBefore = await git.headSha(treeT2);
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });

  assert.deepEqual(result.mergedNow, ["t1"]);
  assert.deepEqual(result.unmerged, ["t2"]);
  assert.equal(await git.rebaseInProgress(treeT2), false);
  assert.equal(await git.headSha(treeT2), t2HeadBefore);
  assert.equal(await git.headSha(repo), await git.headSha(treeT1));
  const noteText = listik.calls.needsOwner[0].text;
  assert.match(noteText, /конфликтует: f\.txt/);
});

suite("barrier 4: needs_owner:true — show не вызывается, needs-owner повторно не ставится", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"], needs_owner: true}];
  const listik = fakeListik({});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.equal(listik.calls.show.length, 0);
  assert.equal(listik.calls.needsOwner.length, 0);
});

suite("barrier 5: повтор после влития — идемпотентно, comment не пишется", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  writeFileSync(join(tree, "a.txt"), "a\n");
  sh(tree, "add", "a.txt");
  sh(tree, "commit", "-q", "-m", "t1");
  await git.rebase(tree, "main");
  await git.mergeFfOnly(repo, "task/t1");
  const sha = await git.headSha(repo);

  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"]}];
  const record = {sha, branch: "task/t1", base: sha, files: ["a.txt"], declared: [], outside: ["a.txt"]};
  const listik = fakeListik({
    t1: {id: "t1", comments: [
      {author: SWARM_AUTHOR, text: `${MERGED_MARK} ${JSON.stringify(record)}`, created_at: "2026-01-01T00:00:00Z"},
    ], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.merged, ["t1"]);
  assert.deepEqual(result.mergedNow, []);
  assert.equal(listik.calls.comment.length, 0);
});

suite("barrier 6: открытая halt-карточка — гейт halt, слияний нет", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  writeFileSync(join(tree, "a.txt"), "a\n");
  sh(tree, "add", "a.txt");
  sh(tree, "commit", "-q", "-m", "t1");
  const tasks = [
    {id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"]},
    {id: "halt1", status: "open", labels: [HALT_LABEL], created_at: "2026-01-01T00:00:00Z"},
  ];
  const listik = fakeListik({});
  const log = makeLog();
  const before = await git.headSha(repo);
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.halt, ["halt1"]);
  assert.deepEqual(result.gate, {reason: "halt", ids: ["halt1"]});
  assert.equal(await git.headSha(repo), before);
  assert.equal(listik.calls.show.length, 0);
});

suite("barrier 7: detached HEAD — слияний нет, unmerged оба, needs-owner не вызван", async () => {
  const repo = initRepo();
  const t1 = addWorktree(repo, "t1");
  const t2 = addWorktree(repo, "t2");
  sh(repo, "checkout", "-q", "--detach", "HEAD");
  const tasks = [
    {id: "t1", status: "done", worktree: t1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "done", worktree: t2, branch: "task/t2", labels: ["port:1"]},
  ];
  const listik = fakeListik({});
  const log = makeLog();
  const before = await git.headSha(repo);
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged.sort(), ["t1", "t2"]);
  assert.equal(listik.calls.needsOwner.length, 0);
  assert.equal(await git.headSha(repo), before);
});

suite("barrier 8: dryRun — HEAD не меняется, ни comment ни needs-owner, лог [dry-run] влить", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  writeFileSync(join(tree, "a.txt"), "a\n");
  sh(tree, "add", "a.txt");
  sh(tree, "commit", "-q", "-m", "t1");
  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"]}];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const log = makeLog();
  const before = await git.headSha(repo);
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: true}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.equal(await git.headSha(repo), before);
  assert.equal(listik.calls.comment.length, 0);
  assert.equal(listik.calls.needsOwner.length, 0);
  assert.ok(log.lines.some(l => l.includes("[dry-run] влить t1")));
  assert.deepEqual(result.mergedNow, []);
});

suite("barrier 9: ветка удалена, каталога нет — пропуск молча", async () => {
  const repo = initRepo();
  const tasks = [{id: "t1", status: "done", worktree: join(repo, ".worktrees", "gone"), branch: "task/gone", labels: ["port:1"]}];
  const listik = fakeListik({});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.merged, []);
  assert.deepEqual(result.unmerged, []);
  assert.equal(listik.calls.show.length, 0);
  assert.equal(result.gate, null);
});

suite("barrier 10: каталога нет, ветка впереди HEAD — needs-owner missing_tree", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  writeFileSync(join(tree, "a.txt"), "a\n");
  sh(tree, "add", "a.txt");
  sh(tree, "commit", "-q", "-m", "t1");
  const fakePath = join(repo, ".worktrees", "missing-t1");
  const tasks = [{id: "t1", status: "done", worktree: fakePath, branch: "task/t1", labels: ["port:1"]}];
  const listik = fakeListik({});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.equal(listik.calls.show.length, 0);
  assert.equal(listik.calls.needsOwner.length, 1);
  assert.match(listik.calls.needsOwner[0].text, /каталога дерева/);
});

suite("barrier 11: aheadCount 0 без записи MERGED_MARK — comment пишется один раз, в merged", async () => {
  const repo = initRepo();
  addWorktree(repo, "t1"); // ветка = HEAD, ahead 0
  const fakePath = join(repo, ".worktrees", "missing-t1");
  const tasks = [{id: "t1", status: "done", worktree: fakePath, branch: "task/t1", labels: ["port:1"]}];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: ["x"]}});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.merged, ["t1"]);
  assert.equal(listik.calls.comment.length, 1);
  assert.ok(listik.calls.comment[0].text.startsWith(MERGED_MARK));
});

suite("barrier 12: GitError при rebase без начатого ребейза — rebase_error, HEAD прежний", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  writeFileSync(join(tree, "a.txt"), "a\n");
  sh(tree, "add", "a.txt");
  sh(tree, "commit", "-q", "-m", "t1");
  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"]}];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const log = makeLog();
  const badGit = {...git, rebase: async () => { throw new GitError("boom: bad onto", ["rebase"], 1); }};
  const before = await git.headSha(repo);
  const result = await runBarrier({
    listik, git: badGit, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.match(listik.calls.needsOwner[0].text, /`rebase`:/);
  assert.equal(await git.headSha(repo), before);
});

suite("barrier 13: mergeFfOnly ok:false — ff_failed, HEAD прежний для этого кандидата", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  writeFileSync(join(tree, "a.txt"), "a\n");
  sh(tree, "add", "a.txt");
  sh(tree, "commit", "-q", "-m", "t1");
  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"]}];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const log = makeLog();
  const badGit = {...git, mergeFfOnly: async () => ({ok: false, stderr: "not fast forward"})};
  const before = await git.headSha(repo);
  const result = await runBarrier({
    listik, git: badGit, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.match(listik.calls.needsOwner[0].text, /`merge --ff-only`:/);
  assert.equal(await git.headSha(repo), before);
});

suite("barrier 14: открытая задача и закрытая без port: — git не трогают", async () => {
  const repo = initRepo();
  const treeOpen = addWorktree(repo, "open1");
  writeFileSync(join(treeOpen, "x.txt"), "x\n");
  sh(treeOpen, "add", "x.txt");
  sh(treeOpen, "commit", "-q", "-m", "open1");
  const treeNoPort = addWorktree(repo, "noport1");
  const tasks = [
    {id: "open1", status: "open", worktree: treeOpen, branch: "task/open1", labels: ["port:1"]},
    {id: "noport1", status: "done", worktree: treeNoPort, branch: "task/noport1", labels: []},
  ];
  const listik = fakeListik({});
  const log = makeLog();
  const before = await git.headSha(repo);
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.merged, []);
  assert.deepEqual(result.unmerged, []);
  assert.equal(await git.headSha(repo), before);
  assert.equal(listik.calls.show.length, 0);
});

suite("barrier 15: show бросает для одного — needs-owner, следующий кандидат влит", async () => {
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  writeFileSync(join(treeT1, "a.txt"), "a\n");
  sh(treeT1, "add", "a.txt");
  sh(treeT1, "commit", "-q", "-m", "t1");
  const treeT2 = addWorktree(repo, "t2");
  writeFileSync(join(treeT2, "b.txt"), "b\n");
  sh(treeT2, "add", "b.txt");
  sh(treeT2, "commit", "-q", "-m", "t2");
  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "done", worktree: treeT2, branch: "task/t2", labels: ["port:1"]},
  ];
  const listik = fakeListik({
    t1: new Error("boom"),
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.deepEqual(result.mergedNow, ["t2"]);
  assert.equal(listik.calls.needsOwner.length, 1);
});
