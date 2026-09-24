import {test} from "node:test";
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtempSync, writeFileSync, mkdirSync, readdirSync, readFileSync} from "node:fs";
import nodeFs from "node:fs";
import {tmpdir} from "node:os";
import {join, resolve, dirname} from "node:path";
import {fileURLToPath} from "node:url";
import {
  covers, outsideScope, parseMarked, mergedRecord, sortForMerge, mergeCandidates, haltCards,
  frozenBy, tailLines, runBarrier, MERGED_MARK, FIRST_CHANGE_MARK, HALT_LABEL, SWARM_AUTHOR,
  UNFROZEN_MARK, ARBITER_MARK, REJECTED_MARK,
} from "../barrier.mjs";

const ARBITER_FIXTURE = resolve(dirname(fileURLToPath(import.meta.url)), "fixtures/fake-arbiter.mjs");

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

// Подставной клиент listik: show() по очереди/ошибке из showQueue, остальные методы пишут
// в журнал вызовов и, если задан соответствующий `opts.<name>Fail`, бросают ошибку.
function fakeListik(showQueue = {}, opts = {}) {
  const calls = {show: [], comment: [], needsOwner: [], setLabels: [], set: [], create: [], answer: []};
  const shownCount = {};
  let createSeq = 0;
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
      if (opts.commentFail && opts.commentFail(id, calls.comment.length)) {
        throw new Error("comment: подставной отказ");
      }
      return {id};
    },
    async needsOwner(id, text) {
      calls.needsOwner.push({id, text});
      if (opts.needsOwnerFail && opts.needsOwnerFail(id, calls.needsOwner.length)) {
        throw new Error("needs-owner: подставной отказ");
      }
      return {id};
    },
    async answer(id, text) {
      calls.answer.push({id, text});
      return {id};
    },
    async setLabels(id, labels) {
      calls.setLabels.push({id, labels});
      if (opts.setLabelsFail && opts.setLabelsFail(id, calls.setLabels.length)) {
        throw new Error("setLabels: подставной отказ");
      }
      return {id, labels};
    },
    async set(id, fields) {
      calls.set.push({id, fields});
      if (opts.setFail && opts.setFail(id)) {
        throw new Error("set: подставной отказ");
      }
      return {id, ...fields};
    },
    async create(args) {
      calls.create.push(args);
      if (opts.createFail) throw new Error("create: подставной отказ");
      createSeq++;
      const id = opts.createId ? opts.createId(createSeq) : `halt${createSeq}`;
      return {id};
    },
  };
}

const suite = gitAvailable ? test : test.skip;

// Каталог лога интеграции для тестов, где `swarmConfig.integration` непуст.
function tmpLogDir() {
  return mkdtempSync(join(tmpdir(), "swarm-barrier-log-"));
}

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
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
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
  const t2HeadBefore = await git.headSha(treeT2); // до сноса дерева t2 барьером (зелёная интеграция)
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });

  assert.deepEqual(result.unmerged, ["t1"]);
  assert.deepEqual(result.merged, ["t2"]);
  assert.deepEqual(result.gate, {reason: "unmerged", ids: ["t1"]});
  assert.equal(sh(treeT1, "status", "--porcelain"), statusBefore);
  assert.equal(await git.headSha(repo), t2HeadBefore);
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

suite("barrier 4: needs_owner:true и жёсткий вопрос — show один раз, unmerged, needs-owner пуст", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"], needs_owner: true}];
  const listik = fakeListik({
    t1: {id: "t1", comments: [
      {kind: "question", text: "Какой формат?", created_at: "2026-01-01T00:00:00Z"},
    ]},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.equal(listik.calls.show.length, 1);
  assert.equal(listik.calls.needsOwner.length, 0);
  assert.ok(log.lines.some(l => l.includes("ждёт человека")));
});

suite("barrier: закрытая с мягким вопросом и коммитом — влита, show один раз, answer не вызывался", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  writeFileSync(join(tree, "a.txt"), "a\n");
  sh(tree, "add", "a.txt");
  sh(tree, "commit", "-q", "-m", "t1");
  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"], needs_owner: true}];
  const listik = fakeListik({
    t1: {id: "t1", comments: [
      {kind: "question", text: "Какой формат?\nпо умолчанию: JSON", created_at: "2026-01-01T00:00:00Z"},
    ], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.merged, ["t1"]);
  assert.equal(listik.calls.show.length, 1);
  assert.equal(listik.calls.needsOwner.length, 0);
  assert.equal(listik.calls.answer.length, 0);
  assert.ok(listik.calls.comment.some(c => c.id === "t1" && c.text.startsWith(MERGED_MARK)));
});

suite("barrier: закрытая с needs_owner, вопрос уже отвечен — unmerged как жёсткий", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"], needs_owner: true}];
  const listik = fakeListik({
    t1: {id: "t1", comments: [
      {kind: "question", text: "Какой формат?\nпо умолчанию: JSON", created_at: "2026-01-01T00:00:00Z"},
      {kind: "answer", text: "XML", created_at: "2026-01-01T00:10:00Z"},
    ]},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.equal(listik.calls.show.length, 1);
  assert.equal(listik.calls.needsOwner.length, 0);
});

suite("barrier: ошибка show у кандидата с needs_owner — unmerged, без needs-owner", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"], needs_owner: true}];
  const listik = fakeListik({t1: new Error("show fail")});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.equal(listik.calls.show.length, 1);
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
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.deepEqual(result.mergedNow, ["t2"]);
  assert.equal(listik.calls.needsOwner.length, 1);
});

// ------------------------------------------------- шаги 7–9 (порция d) ---

function readLog(dir) {
  const files = nodeFs.readdirSync(dir).filter(f => f.startsWith("integration-"));
  assert.equal(files.length, 1, `ожидался один файл integration-*.log, нашлось: ${files.join(", ")}`);
  return {path: join(dir, files[0]), text: nodeFs.readFileSync(join(dir, files[0]), "utf8")};
}

// Репо с двумя влитыми не конфликтующими кандидатами (как barrier 1) — общая заготовка
// для тестов шагов 8–9.
function twoMergedSetup() {
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
    ], write_scope: []},
    t2: {id: "t2", comments: [
      {author: SWARM_AUTHOR, text: `${FIRST_CHANGE_MARK} {}`, created_at: "2026-01-01T00:00:10Z"},
    ], write_scope: []},
  });
  return {repo, treeT1, treeT2, tasks, listik};
}

suite("барьер шаг 9: зелёные → снос обоих деревьев", async () => {
  const {repo, treeT1, treeT2, tasks, listik} = twoMergedSetup();
  const logDir = tmpLogDir();
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {integration: [[process.execPath, "-e", "process.exit(0)"]]},
    log, tasks, projectPath: repo, now: new Date(),
  });

  assert.equal(result.integration, "green");
  assert.equal(nodeFs.existsSync(treeT1), false);
  assert.equal(nodeFs.existsSync(treeT2), false);
  assert.equal(await git.branchExists(repo, "task/t1"), false);
  assert.equal(await git.branchExists(repo, "task/t2"), false);
  assert.deepEqual(result.cleaned, ["t2", "t1"]);
  const setT1 = listik.calls.set.find(c => c.id === "t1");
  const setT2 = listik.calls.set.find(c => c.id === "t2");
  assert.deepEqual(setT1.fields, {worktree: "", branch: ""});
  assert.deepEqual(setT2.fields, {worktree: "", branch: ""});
  const {text} = readLog(logDir);
  assert.match(text, /process\.exit\(0\)/);

  // HEAD основного дерева — тот же, что после слияний (снос не переписывает main).
  const c1 = listik.calls.comment.find(c => c.id === "t1");
  const mergedSha = JSON.parse(c1.text.slice(MERGED_MARK.length).trim()).sha;
  assert.equal(await git.headSha(repo), mergedSha);
});

suite("барьер шаг 9: красная интеграция → карточка-стоп, деревья на месте", async () => {
  const {repo, treeT1, treeT2, tasks} = twoMergedSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [
      {author: SWARM_AUTHOR, text: `${FIRST_CHANGE_MARK} {}`, created_at: "2026-01-01T00:00:20Z"},
    ], write_scope: []},
    t2: {id: "t2", comments: [
      {author: SWARM_AUTHOR, text: `${FIRST_CHANGE_MARK} {}`, created_at: "2026-01-01T00:00:10Z"},
    ], write_scope: []},
  });
  const logDir = tmpLogDir();
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {integration: [[process.execPath, "-e", "process.stderr.write('boom'); process.exit(1)"]]},
    log, tasks, projectPath: repo, now: new Date(),
  });

  assert.equal(result.integration, "red");
  const created = listik.calls.create[0];
  assert.equal(created.type, "question");
  assert.deepEqual(created.labels, [HALT_LABEL]);
  assert.equal(created.discoveredFrom, "t2");
  const note = listik.calls.needsOwner[0];
  assert.equal(note.id, "halt1");
  assert.match(note.text, /код: 1/);
  assert.match(note.text, /boom/);
  assert.match(note.text, /listik done/);
  assert.match(note.text, new RegExp(`${logDir.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
  assert.match(note.text, new RegExp(process.execPath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.equal(nodeFs.existsSync(treeT1), true);
  assert.equal(nodeFs.existsSync(treeT2), true);
  assert.equal(await git.branchExists(repo, "task/t1"), true);
  assert.equal(await git.branchExists(repo, "task/t2"), true);
  assert.equal(listik.calls.set.length, 0);
  assert.deepEqual(result.gate, {reason: "halt", ids: ["halt1"]});
  assert.ok(Array.isArray(result.halt) && result.halt.includes("halt1"));
  assert.ok(log.lines.some(l => l === "стоп: карточка halt1 (интеграция красная)"));
});

suite("барьер шаг 9: интеграция не настроена → карточка-стоп с путём swarm.json", async () => {
  const {repo, treeT1, treeT2, tasks} = twoMergedSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: null}, swarmJsonPath: "/tmp/swarm.json",
    log, tasks, projectPath: repo, now: new Date(),
  });

  assert.equal(result.integration, null);
  const note = listik.calls.needsOwner[0];
  assert.match(note.text, /\/tmp\/swarm\.json/);
  assert.match(note.text, /не настроены/);
  assert.deepEqual(result.gate, {reason: "halt", ids: ["halt1"]});
  assert.equal(nodeFs.existsSync(treeT1), true);
  assert.equal(nodeFs.existsSync(treeT2), true);
  assert.equal(await git.branchExists(repo, "task/t1"), true);
  assert.equal(await git.branchExists(repo, "task/t2"), true);
  assert.equal(listik.calls.set.length, 0);
});

suite("барьер шаг 9: пустой список команд — зелёная, снос выполнен", async () => {
  const {repo, treeT1, treeT2, tasks, listik} = twoMergedSetup();
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.equal(result.integration, "green");
  assert.equal(nodeFs.existsSync(treeT1), false);
  assert.equal(nodeFs.existsSync(treeT2), false);
  assert.ok(log.lines.some(l => l.includes("пустой список")));
});

suite("барьер шаг 9: таймаут команды — красная, группа убита, барьер не ждёт 10с", async () => {
  const {repo, tasks} = twoMergedSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const pidFile = join(repo, "pid.txt");
  const log = makeLog();
  const start = Date.now();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {
      integration: [[process.execPath, "-e",
        "require('fs').writeFileSync(process.argv[1], String(process.pid)); setTimeout(()=>{}, 10000);",
        pidFile]],
      integrationTimeout: 1,
    },
    log, tasks, projectPath: repo, now: new Date(),
  });
  const elapsed = Date.now() - start;

  assert.equal(result.integration, "red");
  assert.ok(elapsed < 8000, `барьер вернулся не рано: ${elapsed}мс`);
  assert.match(listik.calls.needsOwner[0].text, /таймаут/);
  const pid = Number(nodeFs.readFileSync(pidFile, "utf8"));
  assert.throws(() => process.kill(pid, 0));
});

suite("барьер шаг 8: нечего проверять — merged пуст, маркер не выполнялся, new не вызывался", async () => {
  const repo = initRepo();
  const marker = join(repo, "marker.txt");
  const listik = fakeListik({});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: [[process.execPath, "-e", `require('fs').writeFileSync(${JSON.stringify(marker)}, "x")`]]},
    log, tasks: [], projectPath: repo, now: new Date(),
  });
  assert.equal(result.integration, null);
  assert.equal(nodeFs.existsSync(marker), false);
  assert.equal(listik.calls.create.length, 0);
  assert.ok(log.lines.some(l => l.includes("нечего проверять")));
});

suite("барьер шаг 8: вторая команда после красной не запускается", async () => {
  const {repo, tasks} = twoMergedSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const marker = join(repo, "marker.txt");
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: [
      [process.execPath, "-e", "process.exit(1)"],
      [process.execPath, "-e", `require('fs').writeFileSync(${JSON.stringify(marker)}, "x")`],
    ]},
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.equal(result.integration, "red");
  assert.equal(nodeFs.existsSync(marker), false);
});

// ------------------------------------------------------ шаг 7 (разморозка) ---

// Репо с влитым t1 (единственный кандидат) — заготовка для тестов разморозки.
function frozenSetup() {
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  writeFileSync(join(treeT1, "a.txt"), "a\n");
  sh(treeT1, "add", "a.txt");
  sh(treeT1, "commit", "-q", "-m", "t1");
  const treeT2 = addWorktree(repo, "t2");
  return {repo, treeT1, treeT2};
}

suite("барьер шаг 7: разморозка чистая — снимок, rebase, метка снята, владелец не тронут", async () => {
  const {repo, treeT1, treeT2} = frozenSetup();
  writeFileSync(join(treeT2, "other.txt"), "x\n"); // незакоммиченная правка другого файла

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2",
      labels: ["port:5171", "frozen-by:t1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], labels: ["port:5171", "frozen-by:t1"]},
  });
  const log = makeLog();
  const t1BranchTreeBefore = nodeFs.existsSync(treeT1);
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: null, log, tasks, projectPath: repo, now: new Date(),
  });

  assert.deepEqual(result.unfrozen, ["t2"]);
  assert.equal((await git.isDirty(treeT2)), false);
  assert.equal(sh(treeT2, "log", "-1", "--format=%an").trim(), "listik-swarm");
  assert.equal(await git.isAncestor(repo, "task/t1", "task/t2"), true);

  const c = listik.calls.comment.find(c => c.id === "t2");
  assert.ok(c, "ожидался comment t2");
  const rec = JSON.parse(c.text.slice(UNFROZEN_MARK.length).trim());
  assert.equal(rec.owner, "t1");
  assert.equal(rec.rebased, true);
  assert.equal(typeof rec.snapshot, "string");
  assert.deepEqual(rec.conflicts, []);

  const setLabelsT2 = listik.calls.setLabels.find(c => c.id === "t2");
  assert.ok(setLabelsT2 && !setLabelsT2.labels.some(l => l.startsWith("frozen-by:")));
  assert.ok(setLabelsT2.labels.includes("port:5171"), "port: сохранён при снятии frozen-by:");

  const t2Task = tasks.find(t => t.id === "t2");
  assert.ok(!t2Task.labels.some(l => l.startsWith("frozen-by:")));
  assert.deepEqual(t2Task.labels, ["port:5171"]);

  // владелец не тронут разморозкой (комментарий t1 — только штатный MERGED_MARK слияния,
  // не unfreeze-запись), дерево/ветка t1 целы до сноса (интеграция не настроена в этом тесте).
  const ownerComments = listik.calls.comment.filter(c => c.id === "t1");
  assert.ok(ownerComments.every(c => c.text.startsWith(MERGED_MARK)));
  assert.equal(listik.calls.setLabels.some(c => c.id === "t1"), false);
  assert.equal(nodeFs.existsSync(treeT1), t1BranchTreeBefore);
});

suite("барьер шаг 7: разморозка с конфликтом — откат, метка снята, next с git rebase", async () => {
  const repo = initRepo();
  writeFileSync(join(repo, "f.txt"), "base\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "f base");
  const treeT1 = addWorktree(repo, "t1");
  writeFileSync(join(treeT1, "f.txt"), "t1-side\n");
  sh(treeT1, "add", "f.txt");
  sh(treeT1, "commit", "-q", "-m", "t1 edits f");
  const treeT2 = addWorktree(repo, "t2");
  writeFileSync(join(treeT2, "f.txt"), "t2-side\n"); // незакоммичено, тот же файл/строка

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2", labels: ["frozen-by:t1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], labels: ["frozen-by:t1"]},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: null, log, tasks, projectPath: repo, now: new Date(),
  });

  assert.deepEqual(result.unfrozen, ["t2"]);
  assert.equal(await git.rebaseInProgress(treeT2), false);
  const snapshotSha = await git.headSha(treeT2);

  const c = listik.calls.comment.find(c => c.id === "t2");
  const rec = JSON.parse(c.text.slice(UNFROZEN_MARK.length).trim());
  assert.equal(rec.rebased, false);
  assert.deepEqual(rec.conflicts, ["f.txt"]);
  assert.equal(rec.snapshot, snapshotSha);
  assert.match(rec.next, /git rebase/);

  const t2Task = tasks.find(t => t.id === "t2");
  assert.ok(!t2Task.labels.some(l => l.startsWith("frozen-by:")));
});

suite("барьер шаг 7: владелец не влит (грязное дерево) — замороженная не тронута", async () => {
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  writeFileSync(join(treeT1, "a.txt"), "a\n");
  sh(treeT1, "add", "a.txt");
  sh(treeT1, "commit", "-q", "-m", "t1");
  writeFileSync(join(treeT1, "untracked.txt"), "x\n"); // грязное дерево — t1 не вольётся
  const treeT2 = addWorktree(repo, "t2");

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2", labels: ["frozen-by:t1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: null, log, tasks, projectPath: repo, now: new Date(),
  });

  assert.deepEqual(result.unfrozen, []);
  assert.equal(listik.calls.show.some(id => id === "t2"), false);
  assert.equal(listik.calls.comment.some(c => c.id === "t2"), false);
  assert.equal(listik.calls.setLabels.some(c => c.id === "t2"), false);
});

suite("барьер шаг 7: владелец ещё открыт — замороженная не тронута", async () => {
  const {repo, treeT1, treeT2} = frozenSetup();
  const tasks = [
    {id: "t1", status: "open", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2", labels: ["frozen-by:t1"]},
  ];
  const listik = fakeListik({});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: null, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unfrozen, []);
  assert.equal(listik.calls.show.length, 0);
});

suite("барьер шаг 7: владелец cancelled — замороженная не тронута", async () => {
  const {repo, treeT1, treeT2} = frozenSetup();
  const tasks = [
    {id: "t1", status: "cancelled", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2", labels: ["frozen-by:t1"]},
  ];
  const listik = fakeListik({});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: null, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unfrozen, []);
  assert.equal(listik.calls.show.length, 0);
});

suite("барьер шаг 7: повтор разморозки — марка есть, метки нет — ничего не пишется", async () => {
  // Метка frozen-by ещё стоит на самой задаче (иначе frozenBy(tasks) её даже не рассмотрит),
  // но свежий show() уже не находит frozen-by в карточке (прошлый тик снял её на сервере,
  // а UNFROZEN_MARK уже записан) — повторная разморозка должна пройти молча.
  const {repo, treeT1, treeT2} = frozenSetup();
  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2", labels: ["frozen-by:t1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {
      id: "t2",
      comments: [{author: SWARM_AUTHOR, text: `${UNFROZEN_MARK} ${JSON.stringify({owner: "t1"})}`,
        created_at: "2026-01-01T00:00:00Z"}],
      labels: [],
    },
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: null, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unfrozen, []);
  assert.equal(listik.calls.show.some(id => id === "t2"), true, "show вызывается — марка проверяется");
  assert.equal(listik.calls.comment.some(c => c.id === "t2"), false);
  assert.equal(listik.calls.setLabels.some(c => c.id === "t2"), false);
});

suite("барьер шаг 7: ошибка журнала — метка на месте, set не вызван, без исключения", async () => {
  const {repo, treeT1, treeT2} = frozenSetup();
  writeFileSync(join(treeT2, "other.txt"), "x\n");
  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2", labels: ["frozen-by:t1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], labels: ["frozen-by:t1"]},
  }, {commentFail: (id) => id === "t2"});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: null, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unfrozen, []);
  assert.equal(listik.calls.setLabels.some(c => c.id === "t2"), false);
  const t2Task = tasks.find(t => t.id === "t2");
  assert.ok(t2Task.labels.includes("frozen-by:t1"));
});

suite("барьер шаг 7: повтор — журнал есть, метка на месте — только setLabels, git не звался", async () => {
  const {repo, treeT1, treeT2} = frozenSetup();
  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2", labels: ["frozen-by:t1", "port:5171"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {
      id: "t2",
      comments: [{author: SWARM_AUTHOR, text: `${UNFROZEN_MARK} ${JSON.stringify({owner: "t1"})}`,
        created_at: "2026-01-01T00:00:00Z"}],
      labels: ["frozen-by:t1", "port:5171"],
    },
  });
  const headBefore = await git.headSha(treeT2);
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: null, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unfrozen, ["t2"]);
  assert.equal(listik.calls.comment.some(c => c.id === "t2"), false);
  const setLabelsT2 = listik.calls.setLabels.find(c => c.id === "t2");
  assert.ok(setLabelsT2);
  assert.deepEqual(setLabelsT2.labels, ["port:5171"]);
  const t2Task = tasks.find(t => t.id === "t2");
  assert.deepEqual(t2Task.labels, ["port:5171"]);
  assert.equal(await git.headSha(treeT2), headBefore); // git не звался
});

suite("барьер: каталог замороженной снесён вручную — tree: missing, метка снята", async () => {
  const {repo, treeT1} = frozenSetup();
  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: join(repo, ".worktrees", "gone-t2"), branch: "task/t2",
      labels: ["frozen-by:t1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], labels: ["frozen-by:t1"]},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: null, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unfrozen, ["t2"]);
  const c = listik.calls.comment.find(c => c.id === "t2");
  const rec = JSON.parse(c.text.slice(UNFROZEN_MARK.length).trim());
  assert.equal(rec.tree, "missing");
  assert.equal(rec.rebased, false);
  assert.equal(rec.snapshot, null);
  assert.match(rec.next, /отсутств/);
  const t2Task = tasks.find(t => t.id === "t2");
  assert.ok(!t2Task.labels.some(l => l.startsWith("frozen-by:")));
});

suite("барьер: dryRun — разморозка и снос только логируются, ничего не пишется", async () => {
  const {repo, treeT1, treeT2} = frozenSetup();
  await git.rebase(treeT1, "main");
  await git.mergeFfOnly(repo, "task/t1"); // t1 уже влит в прошлом проходе
  const sha = await git.headSha(repo);
  const record = {sha, branch: "task/t1", base: sha, files: ["a.txt"], declared: [], outside: []};
  writeFileSync(join(treeT2, "other.txt"), "x\n"); // грязная t2, как в кейсе 8 — не трогается

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2", labels: ["frozen-by:t1", "port:5171"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [
      {author: SWARM_AUTHOR, text: `${MERGED_MARK} ${JSON.stringify(record)}`, created_at: "2026-01-01T00:00:00Z"},
    ], write_scope: []},
  });
  const marker = join(repo, "marker.txt");
  const log = makeLog();
  const headBefore = await git.headSha(repo);
  const t2HeadBefore = await git.headSha(treeT2);
  const t2StatusBefore = sh(treeT2, "status", "--porcelain");
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: true, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: [[process.execPath, "-e", `require('fs').writeFileSync(${JSON.stringify(marker)}, "x")`]]},
    log, tasks, projectPath: repo, now: new Date(),
  });

  assert.equal(await git.headSha(repo), headBefore);
  assert.equal(nodeFs.existsSync(treeT1), true);
  assert.equal(await git.branchExists(repo, "task/t1"), true);
  // t2 (замороженная) — дерево и метки нетронуты, маркер интеграции не выполнялся.
  assert.equal(nodeFs.existsSync(treeT2), true);
  assert.equal(await git.headSha(treeT2), t2HeadBefore);
  assert.equal(sh(treeT2, "status", "--porcelain"), t2StatusBefore);
  assert.deepEqual(tasks.find(t => t.id === "t2").labels, ["frozen-by:t1", "port:5171"]);
  assert.equal(nodeFs.existsSync(marker), false);
  assert.equal(listik.calls.create.length, 0);
  assert.equal(listik.calls.comment.length, 0);
  assert.equal(listik.calls.setLabels.length, 0);
  assert.equal(listik.calls.set.length, 0);
  assert.ok(log.lines.some(l => l.includes("[dry-run] интеграция")));
  assert.ok(log.lines.some(l => l.includes("[dry-run] разморозить t2")));
  assert.deepEqual(result.merged, ["t1"]);
});

suite("барьер: ветка не предок HEAD — не удалять (подложенный MERGED_MARK без слияния)", async () => {
  const repo = initRepo();
  const treeT3 = addWorktree(repo, "t3"); // ветка на HEAD, ни разу не вливалась по-настоящему
  const tasks = [{id: "t3", status: "done", worktree: treeT3, branch: "task/t3", labels: ["port:1"]}];
  const record = {sha: "deadbeef", branch: "task/t3", base: "deadbeef", files: [], declared: [], outside: []};
  const listik = fakeListik({
    t3: {id: "t3", comments: [
      {author: SWARM_AUTHOR, text: `${MERGED_MARK} ${JSON.stringify(record)}`, created_at: "2026-01-01T00:00:00Z"},
    ], write_scope: []},
  });
  // Отвязываем t3 от HEAD, чтобы ветка реально не была влита.
  writeFileSync(join(treeT3, "c.txt"), "c\n");
  sh(treeT3, "add", "c.txt");
  sh(treeT3, "commit", "-q", "-m", "t3");
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.equal(result.integration, "green");
  assert.deepEqual(result.cleaned, []);
  assert.ok(log.lines.some(l => l.includes("не убрано")));
  assert.equal(nodeFs.existsSync(treeT3), true);
  assert.equal(await git.branchExists(repo, "task/t3"), true);
});

suite("барьер: каталог влитой убран вручную (rm -rf) — ветку всё равно убирают (r1 S2)", async () => {
  // Регрессия судьи r1: после ручного удаления каталога git продолжает считать дерево
  // зарегистрированным (prunable) — `branch -d` без предварительного `worktree remove
  // --force` отказывает «used by worktree at …». cleanupOne обязан звать remove всегда,
  // не только когда каталог физически существует.
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  writeFileSync(join(treeT1, "a.txt"), "a\n");
  sh(treeT1, "add", "a.txt");
  sh(treeT1, "commit", "-q", "-m", "t1");
  sh(repo, "merge", "-q", "--ff-only", "task/t1"); // влито (ahead 0)
  nodeFs.rmSync(treeT1, {recursive: true, force: true}); // каталог снесён вручную, git не в курсе

  const tasks = [{id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]}];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });

  assert.equal(result.integration, "green");
  assert.deepEqual(result.cleaned, ["t1"]);
  assert.equal(await git.branchExists(repo, "task/t1"), false);
  const setT1 = listik.calls.set.find(c => c.id === "t1");
  assert.deepEqual(setT1.fields, {worktree: "", branch: ""});
  assert.ok(log.lines.some(l => l === "t1: дерева нет, ветку убираю"));
  assert.equal(log.lines.some(l => l.includes("удаление ветки не удалось")), false);
});

suite("барьер: create отвечает ошибкой — деревья на месте, gate halt, без исключения", async () => {
  const {repo, treeT1, treeT2, tasks} = twoMergedSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [
      {author: SWARM_AUTHOR, text: `${FIRST_CHANGE_MARK} {}`, created_at: "2026-01-01T00:00:20Z"},
    ], write_scope: []},
    t2: {id: "t2", comments: [
      {author: SWARM_AUTHOR, text: `${FIRST_CHANGE_MARK} {}`, created_at: "2026-01-01T00:00:10Z"},
    ], write_scope: []},
  }, {createFail: true});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: [[process.execPath, "-e", "process.exit(1)"]]},
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.equal(result.integration, "red");
  assert.equal(nodeFs.existsSync(treeT1), true);
  assert.equal(nodeFs.existsSync(treeT2), true);
  assert.deepEqual(result.gate, {reason: "halt", ids: []});
  assert.deepEqual(result.halt, []);
  assert.equal(listik.calls.needsOwner.length, 0);
});

// --------------------------------------------------------- порция e: арбитр ---

// Тот же конфликт одного файла, что в сценарии "barrier 3": t1 вливается первой, t2
// конфликтует на rebase.
function conflictSetup() {
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
  return {repo, treeT1, treeT2, tasks};
}

suite("барьер + арбитр ok: обе влиты, MERGED_MARK второй с arbiter:true, обе строки в дереве", async () => {
  const {repo, tasks} = conflictSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const logDir = tmpLogDir();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {integration: [], arbiter: ["node", ARBITER_FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 30},
    log, tasks, projectPath: repo, now: new Date(),
  });

  assert.deepEqual(result.mergedNow, ["t1", "t2"]);
  assert.deepEqual(result.unmerged, []);
  const c2 = listik.calls.comment.find(c => c.id === "t2" && c.text.startsWith(MERGED_MARK));
  assert.ok(c2);
  const rec2 = JSON.parse(c2.text.slice(MERGED_MARK.length).trim());
  assert.equal(rec2.arbiter, true);
  const arbiterComment = listik.calls.comment.find(c => c.id === "t2" && c.text.startsWith(ARBITER_MARK));
  assert.ok(arbiterComment);
  const content = readFileSync(join(repo, "f.txt"), "utf8");
  assert.match(content, /t1-side/);
  assert.match(content, /t2-side/);
});

suite("барьер без арбитра: needs-owner содержит «арбитр не настроен»", async () => {
  const {repo, tasks} = conflictSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t2"]);
  assert.match(listik.calls.needsOwner[0].text, /арбитр не настроен \(ключ arbiter в swarm\.json\)/);
});

suite("барьер + арбитр fail: needs-owner содержит «арбитр не справился», «код 1», путь лога", async () => {
  const {repo, tasks} = conflictSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const logDir = tmpLogDir();
  process.env.FAKE_ARBITER_MODE = "fail";
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {integration: [], arbiter: ["node", ARBITER_FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 30},
    log, tasks, projectPath: repo, now: new Date(),
  });
  delete process.env.FAKE_ARBITER_MODE;

  assert.deepEqual(result.unmerged, ["t2"]);
  const noteText = listik.calls.needsOwner[0].text;
  assert.match(noteText, /арбитр не справился: код 1/);
  assert.match(noteText, /лог .*arbiter-t2-.*\.log/);
});

suite("барьер dryRun с настроенным арбитром: промпт-файлов нет", async () => {
  const {repo, tasks} = conflictSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const logDir = tmpLogDir();
  await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: true, project: "demo", logDir},
    swarmConfig: {integration: [], arbiter: ["node", ARBITER_FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 30},
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(readdirSync(logDir).filter(f => f.endsWith(".prompt.md")), []);
});

suite("барьер: разморозка с конфликтом + арбитр настроен — арбитр не зовётся (только для закрытых)", async () => {
  const repo = initRepo();
  writeFileSync(join(repo, "f.txt"), "base\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "f base");
  const treeT1 = addWorktree(repo, "t1");
  writeFileSync(join(treeT1, "f.txt"), "t1-side\n");
  sh(treeT1, "add", "f.txt");
  sh(treeT1, "commit", "-q", "-m", "t1 edits f");
  const treeT2 = addWorktree(repo, "t2");
  writeFileSync(join(treeT2, "f.txt"), "t2-side\n"); // незакоммичено, тот же файл/строка

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2", labels: ["frozen-by:t1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], labels: ["frozen-by:t1"]},
  });
  const log = makeLog();
  const logDir = tmpLogDir();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {integration: [], arbiter: ["node", ARBITER_FIXTURE, "{prompt}", "{files}"], arbiterTimeout: 30},
    log, tasks, projectPath: repo, now: new Date(),
  });

  assert.deepEqual(result.unfrozen, ["t2"]);
  assert.deepEqual(readdirSync(logDir).filter(f => f.endsWith(".prompt.md")), []);
});

suite("каталог есть, ветки нет — GitError не вылетает, needs-owner, сосед влит", async () => {
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  const treeT2 = addWorktree(repo, "t2");
  writeFileSync(join(treeT1, "a.txt"), "a\n");
  sh(treeT1, "add", "a.txt");
  sh(treeT1, "commit", "-q", "-m", "t1");
  writeFileSync(join(treeT2, "b.txt"), "b\n");
  sh(treeT2, "add", "b.txt");
  sh(treeT2, "commit", "-q", "-m", "t2");
  sh(repo, "update-ref", "-d", "refs/heads/task/t1");

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "done", worktree: treeT2, branch: "task/t2", labels: ["port:1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.ok(result.unmerged.includes("t1"));
  assert.ok(result.merged.includes("t2"));
  assert.ok(listik.calls.needsOwner.some(c => c.id === "t1"));
  assert.equal(nodeFs.existsSync(treeT1), true);
});

suite("разморозка при rebase in progress — abort до снимка, untracked не теряется", async () => {
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
  try {
    execFileSync("git", ["-c", "core.editor=true", "-c", "merge.conflictStyle=diff3",
      "rebase", "task/t1"], {cwd: treeT2, stdio: "ignore"});
  } catch { /* конфликт ожидаем */ }
  assert.equal(await git.rebaseInProgress(treeT2), true);
  writeFileSync(join(treeT2, "extra.txt"), "keep me\n");

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2",
      labels: ["port:5171", "frozen-by:t1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], labels: ["port:5171", "frozen-by:t1"]},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: null, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unfrozen, ["t2"]);
  assert.equal(await git.rebaseInProgress(treeT2), false);
  assert.equal(nodeFs.existsSync(join(treeT2, "extra.txt")), true);
  const rec = JSON.parse(listik.calls.comment.find(c => c.id === "t2" &&
    c.text.startsWith(UNFROZEN_MARK)).text.slice(UNFROZEN_MARK.length).trim());
  assert.equal(typeof rec.snapshot, "string");
  assert.equal(await git.isAncestor(treeT2, rec.snapshot, "HEAD"), true);
});

suite("comment после ff падает — дерево не сносят, целитель может повторить", async () => {
  const {repo, treeT1, treeT2, tasks} = twoMergedSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [
      {author: SWARM_AUTHOR, text: `${FIRST_CHANGE_MARK} {}`, created_at: "2026-01-01T00:00:20Z"},
    ], write_scope: []},
    t2: {id: "t2", comments: [
      {author: SWARM_AUTHOR, text: `${FIRST_CHANGE_MARK} {}`, created_at: "2026-01-01T00:00:10Z"},
    ], write_scope: []},
  }, {commentFail: (id) => id === "t2"});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.ok(!result.merged.includes("t2"));
  assert.ok(result.cleaned.includes("t1"));
  assert.ok(!result.cleaned.includes("t2"));
  assert.equal(nodeFs.existsSync(treeT2), true);
  assert.equal(await git.branchExists(repo, "task/t2"), true);
  assert.ok(!listik.calls.set.some(c => c.id === "t2"));
});

suite("comment маркера падает один раз — повтор записывает, задача влита", async () => {
  const {repo, tasks, listik: base} = twoMergedSetup();
  let t2Fails = 0;
  const listik = fakeListik({t1: await base.show("t1"), t2: await base.show("t2")}, {
    commentFail: (id) => id === "t2" && t2Fails++ === 0,
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.ok(result.merged.includes("t2"));
  assert.ok(!result.unmerged.includes("t2"));
  const marks = listik.calls.comment.filter(c => c.id === "t2" && c.text.startsWith(MERGED_MARK));
  assert.equal(marks.length, 2);
  assert.equal(marks[0].text, marks[1].text);
  assert.equal(listik.calls.needsOwner.length, 0);
});

suite("comment маркера падает дважды — ровно один повтор, needs-owner с командой, след. тик ждёт человека", async () => {
  const {repo, treeT2, tasks, listik: base} = twoMergedSetup();
  const listik = fakeListik({t1: await base.show("t1"), t2: await base.show("t2")},
    {commentFail: (id) => id === "t2"});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  const marks = listik.calls.comment.filter(c => c.id === "t2" && c.text.startsWith(MERGED_MARK));
  assert.equal(marks.length, 2);
  assert.ok(!result.merged.includes("t2"));
  assert.ok(!result.mergedNow.includes("t2"));
  assert.ok(result.unmerged.includes("t2"));
  assert.deepEqual(result.rejected, []);
  assert.ok(!listik.calls.comment.some(c => c.text.startsWith(REJECTED_MARK)));
  assert.ok(!listik.calls.set.some(c => c.id === "t2"));
  assert.ok(log.lines.includes("needs-owner t2: mark_failed"));
  const note = listik.calls.needsOwner.find(c => c.id === "t2");
  assert.ok(note.text.startsWith("рой: не влита — "));
  assert.ok(note.text.includes(`listik comment t2 '${marks[0].text}' -k journal --actor agent:listik-swarm`));

  // Следующий тик: needs_owner с жёстким вопросом роя — гейт шага 3, не rejectOne.
  const head = await git.headSha(repo);
  const next = fakeListik({t2: {id: "t2", comments: [
    {kind: "question", text: note.text, created_at: "2026-01-01T00:01:00Z"},
  ], write_scope: []}});
  const log2 = makeLog();
  const r2 = await runBarrier({
    listik: next, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log: log2,
    tasks: [{...tasks[1], needs_owner: true}], projectPath: repo, now: new Date(),
  });
  assert.ok(log2.lines.includes("t2 не влита: ждёт человека"));
  assert.deepEqual(r2.rejected, []);
  assert.equal(next.calls.set.length, 0);
  assert.equal(await git.headSha(repo), head);
  assert.equal(nodeFs.existsSync(treeT2), true);
});

suite("changedFiles бросает до слияния — needs-owner diff_error, не empty", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const wrapped = {...git, async changedFiles() { throw new GitError("boom diff", ["diff"], 128); }};
  const log = makeLog();
  const before = await git.headSha(repo);
  const result = await runBarrier({
    listik, git: wrapped, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.equal(await git.headSha(repo), before);
  assert.deepEqual(result.rejected, []);
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.equal(listik.calls.set.length, 0);
  assert.ok(!listik.calls.comment.some(c => c.text.startsWith(REJECTED_MARK)));
  assert.ok(log.lines.includes("needs-owner t1: diff_error"));
  const note = listik.calls.needsOwner[0].text;
  assert.ok(note.startsWith("рой: не влита — "));
  assert.ok(note.includes("git diff --name-only"));
});

suite("changedFiles бросает после слияния — маркер с files_error, задача влита", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  let n = 0;
  const wrapped = {...git, async changedFiles(...args) {
    if (n++ === 0) return git.changedFiles(...args);
    throw new GitError("boom diff", ["diff"], 128);
  }};
  const log = makeLog();
  const result = await runBarrier({
    listik, git: wrapped, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.ok(result.merged.includes("t1"));
  const c = listik.calls.comment.find(x => x.id === "t1" && x.text.startsWith(MERGED_MARK));
  const rec = JSON.parse(c.text.slice(MERGED_MARK.length).trim());
  assert.deepEqual(rec.files, []);
  assert.deepEqual(rec.outside, []);
  assert.match(rec.files_error, /boom diff/);
  assert.ok(log.lines.some(l => l.startsWith("влито t1")));
});

suite("rebaseAbort бросает — runBarrier не вылетает, кандидат в unmerged", async () => {
  const {repo, tasks} = conflictSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const wrapped = {
    ...git,
    async rebaseAbort() {
      throw new GitError("boom abort", ["rebase", "--abort"], 128);
    },
  };
  const log = makeLog();
  const result = await runBarrier({
    listik, git: wrapped, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.ok(result.unmerged.includes("t2"));
  assert.ok(result.merged.includes("t1"));
});

suite("resolveWithArbiter бросает — тик барьера не падает, rebase откачен", async () => {
  const {repo, treeT2, tasks} = conflictSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const logDir = tmpLogDir();
  const fsStub = {
    existsSync: (p) => nodeFs.existsSync(p),
    mkdirSync() { throw new Error("mkdir boom"); },
  };
  const t2Head = await git.headSha(treeT2);
  const result = await runBarrier({
    listik, git, fs: fsStub, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {integration: [], arbiter: ["node", ARBITER_FIXTURE, "{prompt}", "{files}"],
      arbiterTimeout: 30},
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.ok(result.unmerged.includes("t2"));
  assert.equal(await git.rebaseInProgress(treeT2), false);
  assert.equal(await git.headSha(treeT2), t2Head);
});

suite("rebase in progress у закрытой — abort до isDirty, не dirty_tree", async () => {
  const repo = initRepo();
  writeFileSync(join(repo, "f.txt"), "base\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "f base");
  const treeT1 = addWorktree(repo, "t1");
  writeFileSync(join(treeT1, "f.txt"), "t1-side\n");
  sh(treeT1, "add", "f.txt");
  sh(treeT1, "commit", "-q", "-m", "t1 edits f");
  writeFileSync(join(repo, "f.txt"), "main-side\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "main edits f");
  try {
    execFileSync("git", ["-c", "core.editor=true", "-c", "merge.conflictStyle=diff3",
      "rebase", "main"], {cwd: treeT1, stdio: "ignore"});
  } catch { /* конфликт ожидаем */ }
  assert.equal(await git.rebaseInProgress(treeT1), true);

  const tasks = [
    {id: "t1", status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:1"]},
  ];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.match(listik.calls.needsOwner[0].text, /конфликтует/);
  assert.ok(!listik.calls.needsOwner[0].text.includes("незакоммиченные"));
  assert.equal(await git.rebaseInProgress(treeT1), false);
});

suite("таймаут: лидер умер от SIGTERM, потомок игнорирует — SIGKILL группе", async () => {
  const {repo, tasks} = twoMergedSetup();
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const childPidFile = join(repo, "child.pid");
  const outer = `const {spawn}=require("child_process");` +
    `const fs=require("fs");` +
    `const c=spawn(process.execPath,["-e",` +
    `"process.on('SIGTERM',()=>{});setTimeout(()=>{},30000);"],{stdio:"ignore"});` +
    `fs.writeFileSync(process.argv[process.argv.length-1], String(c.pid));` +
    `process.on("SIGTERM",()=>process.exit(0));` +
    `setTimeout(()=>{}, 30000);`;
  const log = makeLog();
  const start = Date.now();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {
      integration: [[process.execPath, "-e", outer, childPidFile]],
      integrationTimeout: 1,
    },
    log, tasks, projectPath: repo, now: new Date(),
  });
  const elapsed = Date.now() - start;
  assert.equal(result.integration, "red");
  assert.ok(elapsed < 9000, `вернулся слишком поздно: ${elapsed}мс`);
  const childPid = Number(readFileSync(childPidFile, "utf8"));
  assert.throws(() => process.kill(childPid, 0));
});

suite("rebase бросает при начатом конфликтном ребейзе — abort, HEAD дерева прежний", async () => {
  const repo = initRepo();
  writeFileSync(join(repo, "f.txt"), "base\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "f base");
  const tree = addWorktree(repo, "t1");
  writeFileSync(join(tree, "f.txt"), "t1-side\n");
  sh(tree, "add", "f.txt");
  sh(tree, "commit", "-q", "-m", "t1 edits f");
  writeFileSync(join(repo, "f.txt"), "main-side\n");
  sh(repo, "add", "f.txt");
  sh(repo, "commit", "-q", "-m", "main edits f");
  const treeHeadBefore = await git.headSha(tree);
  const mainBefore = await git.headSha(repo);

  const wrapped = {
    ...git,
    rebase: async (t, onto) => {
      const res = await git.rebase(t, onto);
      if (!res.ok) throw new GitError("boom mid-conflict", ["rebase"], 1);
      return res;
    },
  };
  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "task/t1", labels: ["port:1"]}];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const log = makeLog();
  const result = await runBarrier({
    listik, git: wrapped, fs: nodeFs, config: {dryRun: false}, swarmConfig: null, log, tasks,
    projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.match(listik.calls.needsOwner[0].text, /`rebase`:/);
  assert.equal(await git.rebaseInProgress(tree), false);
  assert.equal(await git.headSha(tree), treeHeadBefore);
  assert.equal(await git.headSha(repo), mainBefore);
});

suite("cleanupOne: branch с пробелами — trim, дерево убирают", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  writeFileSync(join(tree, "a.txt"), "a\n");
  sh(tree, "add", "a.txt");
  sh(tree, "commit", "-q", "-m", "t1");
  await git.rebase(tree, "main");
  await git.mergeFfOnly(repo, "task/t1");
  const sha = await git.headSha(repo);
  const tasks = [{id: "t1", status: "done", worktree: tree, branch: "  task/t1  ", labels: ["port:1"]}];
  const record = {sha, branch: "task/t1", base: sha, files: ["a.txt"], declared: [], outside: []};
  const listik = fakeListik({
    t1: {id: "t1", comments: [
      {author: SWARM_AUTHOR, text: `${MERGED_MARK} ${JSON.stringify(record)}`, created_at: "2026-01-01T00:00:00Z"},
    ], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.cleaned, ["t1"]);
  assert.equal(nodeFs.existsSync(tree), false);
  assert.equal(await git.branchExists(repo, "task/t1"), false);
});

suite("лог кандидатов N вычитает needs_owner, missing_branch, ahead_error", async () => {
  const repo = initRepo();
  const treeOk = addWorktree(repo, "tok");
  writeFileSync(join(treeOk, "ok.txt"), "ok\n");
  sh(treeOk, "add", "ok.txt");
  sh(treeOk, "commit", "-q", "-m", "tok");
  const treeNeed = addWorktree(repo, "tneed");
  const treeMiss = addWorktree(repo, "tmiss");
  writeFileSync(join(treeMiss, "m.txt"), "m\n");
  sh(treeMiss, "add", "m.txt");
  sh(treeMiss, "commit", "-q", "-m", "tmiss");
  sh(repo, "update-ref", "-d", "refs/heads/task/tmiss");
  addWorktree(repo, "terr");
  const treeAhead = addWorktree(repo, "tahead");
  writeFileSync(join(treeAhead, "h.txt"), "h\n");
  sh(treeAhead, "add", "h.txt");
  sh(treeAhead, "commit", "-q", "-m", "tahead");
  const fakeAhead = join(repo, ".worktrees", "missing-tahead");
  const fakeErr = join(repo, ".worktrees", "missing-terr");
  const fakeSilent = join(repo, ".worktrees", "gone");

  const tasks = [
    {id: "tok", status: "done", worktree: treeOk, branch: "task/tok", labels: ["port:1"]},
    {id: "tneed", status: "done", worktree: treeNeed, branch: "task/tneed", labels: ["port:1"],
      needs_owner: true},
    {id: "tmiss", status: "done", worktree: treeMiss, branch: "task/tmiss", labels: ["port:1"]},
    {id: "terr", status: "done", worktree: fakeErr, branch: "task/terr", labels: ["port:1"]},
    {id: "tahead", status: "done", worktree: fakeAhead, branch: "task/tahead", labels: ["port:1"]},
    {id: "tsilent", status: "done", worktree: fakeSilent, branch: "task/gone", labels: ["port:1"]},
  ];
  const wrapped = {
    ...git,
    aheadCount: async (r, base, branch) => {
      if (branch === "task/terr") throw new GitError("ahead boom", ["rev-list"], 128);
      return git.aheadCount(r, base, branch);
    },
  };
  const listik = fakeListik({
    tok: {id: "tok", comments: [], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git: wrapped, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.ok(log.lines.some(l => l === "барьер: кандидатов 1, уже влито 0, порядок: tok"),
    log.lines.filter(l => l.startsWith("барьер:")).join(" | "));
  assert.ok(result.merged.includes("tok"));
});

// ------------------------------------------------- верификатор и пустой дифф ---

function commitFile(tree, name, content, msg) {
  writeFileSync(join(tree, name), content);
  sh(tree, "add", name);
  sh(tree, "commit", "-q", "-m", msg);
}

function verifyLogFiles(dir, id) {
  return nodeFs.readdirSync(dir).filter(f => f.startsWith(`verify-${id}-`)).map(f => ({
    name: f, path: join(dir, f), text: nodeFs.readFileSync(join(dir, f), "utf8"),
  }));
}

function parseRejected(listik, id) {
  const c = listik.calls.comment.find(x => x.id === id && x.text.startsWith(REJECTED_MARK));
  assert.ok(c, `ожидался comment ${id} с REJECTED_MARK`);
  return JSON.parse(c.text.slice(REJECTED_MARK.length).trim());
}

function doneTask(id, worktree, over = {}) {
  return {id, status: "done", worktree, branch: `task/${id}`, labels: ["port:1"], ...over};
}

suite("верификатор: красный → rejected, HEAD прежний, set open/s3-impl", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: [], labels: ["port:1"]}});
  const logDir = tmpLogDir();
  const log = makeLog();
  const before = await git.headSha(repo);
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {integration: [], verify: [[process.execPath, "-e", "process.exit(1)"]]},
    log, tasks, projectPath: repo, now: new Date(),
  });

  assert.equal(await git.headSha(repo), before);
  assert.deepEqual(result.rejected, ["t1"]);
  assert.deepEqual(result.unmerged, []);
  assert.equal(result.gate, null);
  const rejectedComments = listik.calls.comment.filter(c => c.text.startsWith(REJECTED_MARK));
  assert.equal(rejectedComments.length, 1);
  const rec = parseRejected(listik, "t1");
  assert.equal(rec.reason, "red");
  assert.equal(rec.attempt, 1);
  assert.equal(typeof rec.tail, "string");
  assert.equal(typeof rec.code, "number");
  assert.ok(Array.isArray(rec.command));
  assert.match(rec.next, /listik done/);
  assert.equal(listik.calls.set.length, 1);
  assert.deepEqual(listik.calls.set[0].fields, {status: "open", stage: "s3-impl"});
  assert.equal(listik.calls.needsOwner.length, 0);
  const logs = verifyLogFiles(logDir, "t1");
  assert.equal(logs.length, 1);
  assert.ok(logs[0].text.startsWith("$ "));
  assert.match(rec.log, /verify-t1-/);
});

suite("верификатор: verifyRetries 0 → человеку, set не вызван, unmerged", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {
      integration: [], verifyRetries: 0,
      verify: [[process.execPath, "-e", "process.exit(1)"]],
    },
    log, tasks, projectPath: repo, now: new Date(),
  });

  assert.equal(listik.calls.set.length, 0);
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.deepEqual(result.rejected, []);
  assert.equal(result.gate.reason, "unmerged");
  assert.equal(listik.calls.needsOwner.length, 1);
  const text = listik.calls.needsOwner[0].text;
  assert.ok(text.startsWith("рой: не влита — "));
  assert.match(text, /отклонена 1 раз/);
  assert.match(text, /тесты красные/);
  assert.match(text, /--clear/);
  assert.ok(listik.calls.comment[0].text.startsWith(REJECTED_MARK));
});

suite("верификатор: уже один REJECTED_MARK роя и verifyRetries 1 → человеку", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const prior = {
    author: SWARM_AUTHOR,
    text: `${REJECTED_MARK} ${JSON.stringify({reason: "red"})}`,
    created_at: "2026-01-01T00:00:00Z",
  };
  const listik = fakeListik({t1: {id: "t1", comments: [prior], write_scope: []}});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {
      integration: [], verifyRetries: 1,
      verify: [[process.execPath, "-e", "process.exit(1)"]],
    },
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.equal(listik.calls.set.length, 0);
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.deepEqual(result.rejected, []);
  assert.match(listik.calls.needsOwner[0].text, /отклонена 2 раз/);
});

suite("верификатор: тот же маркер от другого автора не считается → воркеру", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const prior = {
    author: "agent:other",
    text: `${REJECTED_MARK} ${JSON.stringify({reason: "red"})}`,
    created_at: "2026-01-01T00:00:00Z",
  };
  const listik = fakeListik({t1: {id: "t1", comments: [prior], write_scope: []}});
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {
      integration: [], verifyRetries: 1,
      verify: [[process.execPath, "-e", "process.exit(1)"]],
    },
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.rejected, ["t1"]);
  assert.deepEqual(result.unmerged, []);
  assert.equal(listik.calls.set.length, 1);
  assert.equal(listik.calls.needsOwner.length, 0);
});

suite("пустой дифф: дерево есть, ветка = HEAD, без MERGED_MARK → rejected empty", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const log = makeLog();
  const before = await git.headSha(repo);
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.equal(await git.headSha(repo), before);
  assert.deepEqual(result.rejected, ["t1"]);
  assert.deepEqual(result.unmerged, []);
  const rec = parseRejected(listik, "t1");
  assert.equal(rec.reason, "empty");
  assert.equal(listik.calls.set.length, 1);
  assert.equal(listik.calls.comment.some(c => c.text.startsWith(MERGED_MARK)), false);
});

suite("верификатор: verify [] и красная integration → команд верификатора нет, задача влита", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const logDir = tmpLogDir();
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {integration: [[process.execPath, "-e", "process.exit(1)"]], verify: []},
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.ok(result.merged.includes("t1"));
  assert.deepEqual(result.rejected, []);
  assert.equal(result.integration, "red");
  assert.equal(verifyLogFiles(logDir, "t1").length, 0);
  assert.ok(log.lines.some(l => l === "верификатор: команд нет"));
});

suite("верификатор: команда в cwd дерева (t1.txt есть)", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const logDir = tmpLogDir();
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {
      integration: [],
      verify: [[process.execPath, "-e",
        "process.exit(require('fs').existsSync('t1.txt')?0:1)"]],
    },
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.ok(result.merged.includes("t1"));
  const logs = verifyLogFiles(logDir, "t1");
  assert.equal(logs.length, 1);
  assert.ok(logs[0].text.includes("$ "));
});

suite("верификатор: LISTIK_DEV_PORT из метки; без метки на карточке — unset", async () => {
  const repo = initRepo();
  const treeA = addWorktree(repo, "ta");
  const treeB = addWorktree(repo, "tb");
  commitFile(treeA, "a.txt", "a\n", "ta");
  commitFile(treeB, "b.txt", "b\n", "tb");
  const printPort = [process.execPath, "-e",
    "process.stdout.write((process.env.LISTIK_DEV_PORT ?? 'unset') + '\\n'); process.exit(0)"];
  const prev = process.env.LISTIK_DEV_PORT;
  process.env.LISTIK_DEV_PORT = "9999";
  try {
    const tasks = [
      doneTask("ta", treeA, {labels: ["port:4242"]}),
      doneTask("tb", treeB, {labels: ["port:2"]}),
    ];
    const listik = fakeListik({
      ta: {id: "ta", comments: [], write_scope: [], labels: ["port:4242"]},
      tb: {id: "tb", comments: [], write_scope: [], labels: []},
    });
    const logDir = tmpLogDir();
    const log = makeLog();
    const result = await runBarrier({
      listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
      swarmConfig: {integration: [], verify: [printPort]},
      log, tasks, projectPath: repo, now: new Date(),
    });
    assert.deepEqual(result.mergedNow.sort(), ["ta", "tb"]);
    const logA = verifyLogFiles(logDir, "ta")[0].text;
    const logB = verifyLogFiles(logDir, "tb")[0].text;
    assert.match(logA, /4242/);
    assert.match(logB, /unset/);
  } finally {
    if (prev === undefined) delete process.env.LISTIK_DEV_PORT;
    else process.env.LISTIK_DEV_PORT = prev;
  }
});

suite("верификатор: dryRun — ни comment ни set, команда не запускалась, лог проверить", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const marker = join(tree, "ran.txt");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const log = makeLog();
  await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: true, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {verify: [[process.execPath, "-e",
      `require('fs').writeFileSync(${JSON.stringify(marker)}, 'x')`]]},
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.equal(listik.calls.comment.length, 0);
  assert.equal(listik.calls.set.length, 0);
  assert.equal(nodeFs.existsSync(marker), false);
  assert.ok(log.lines.some(l => l.includes("[dry-run] проверить t1:")));
});

suite("верификатор: две задачи — первая отклонена, вторая влита", async () => {
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  const treeT2 = addWorktree(repo, "t2");
  commitFile(treeT1, "fail.txt", "x\n", "t1");
  commitFile(treeT2, "ok.txt", "y\n", "t2");
  const tasks = [doneTask("t1", treeT1), doneTask("t2", treeT2)];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], write_scope: []},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {
      integration: [],
      verify: [[process.execPath, "-e",
        "process.exit(require('fs').existsSync('fail.txt')?1:0)"]],
    },
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.merged, ["t2"]);
  assert.deepEqual(result.rejected, ["t1"]);
  assert.deepEqual(result.unmerged, []);
  assert.equal(result.gate, null);
  const subjects = sh(repo, "log", "--format=%s").trim().split("\n");
  assert.ok(subjects.includes("t2"));
  assert.equal(subjects.includes("t1"), false);
});

suite("пустой дифф: коммит + revert → empty", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "gone.txt", "x\n", "add");
  sh(tree, "revert", "--no-edit", "HEAD");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const log = makeLog();
  const before = await git.headSha(repo);
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: []}, log, tasks, projectPath: repo, now: new Date(),
  });
  assert.equal(await git.headSha(repo), before);
  assert.deepEqual(result.rejected, ["t1"]);
  const rec = parseRejected(listik, "t1");
  assert.equal(rec.reason, "empty");
});

suite("верификатор: первая красная — вторая не запускается, в логе одна строка $", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const marker = join(tree, "second.txt");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const logDir = tmpLogDir();
  const log = makeLog();
  await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {
      integration: [],
      verify: [
        [process.execPath, "-e", "process.exit(1)"],
        [process.execPath, "-e", `require('fs').writeFileSync(${JSON.stringify(marker)}, 'x')`],
      ],
    },
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.equal(nodeFs.existsSync(marker), false);
  const text = verifyLogFiles(logDir, "t1")[0].text;
  const dollars = text.split("\n").filter(l => l.startsWith("$ "));
  assert.equal(dollars.length, 1);
});

suite("верификатор: таймаут sleep 10 при verifyTimeout 1 → red timed_out, не ждёт 10с", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const log = makeLog();
  const start = Date.now();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: [], verify: [["sleep", "10"]], verifyTimeout: 1},
    log, tasks, projectPath: repo, now: new Date(),
  });
  const elapsed = Date.now() - start;
  assert.ok(elapsed < 8000, `барьер вернулся не рано: ${elapsed}мс`);
  assert.deepEqual(result.rejected, ["t1"]);
  const rec = parseRejected(listik, "t1");
  assert.equal(rec.reason, "red");
  assert.equal(rec.timed_out, true);
});

suite("верификатор: отклонённая t1 не размораживает frozen-by:t1", async () => {
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  const treeT2 = addWorktree(repo, "t2");
  commitFile(treeT1, "t1.txt", "a\n", "t1");
  const tasks = [
    doneTask("t1", treeT1),
    {id: "t2", status: "open", worktree: treeT2, branch: "task/t2",
      labels: ["port:5171", "frozen-by:t1"]},
  ];
  const listik = fakeListik({
    t1: {id: "t1", comments: [], write_scope: []},
    t2: {id: "t2", comments: [], labels: ["port:5171", "frozen-by:t1"]},
  });
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: [], verify: [[process.execPath, "-e", "process.exit(1)"]]},
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.rejected, ["t1"]);
  assert.deepEqual(result.unfrozen, []);
  assert.ok(tasks.find(t => t.id === "t2").labels.includes("frozen-by:t1"));
  assert.equal(listik.calls.comment.some(c => c.id === "t2" && c.text.startsWith(UNFROZEN_MARK)), false);
});

suite("верификатор: отказ set → без исключения, unmerged содержит id", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik(
    {t1: {id: "t1", comments: [], write_scope: []}},
    {setFail: () => true},
  );
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir: tmpLogDir()},
    swarmConfig: {integration: [], verify: [[process.execPath, "-e", "process.exit(1)"]]},
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.deepEqual(result.unmerged, ["t1"]);
  assert.deepEqual(result.rejected, []);
  assert.ok(log.lines.some(l => l.includes("set t1 ошибка")));
});

suite("верификатор не задан, integration красная → в дереве ничего не запускалось", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  commitFile(tree, "t1.txt", "a\n", "t1");
  const tasks = [doneTask("t1", tree)];
  const listik = fakeListik({t1: {id: "t1", comments: [], write_scope: []}});
  const logDir = tmpLogDir();
  const log = makeLog();
  const result = await runBarrier({
    listik, git, fs: nodeFs, config: {dryRun: false, project: "demo", logDir},
    swarmConfig: {integration: [[process.execPath, "-e", "process.exit(1)"]]},
    log, tasks, projectPath: repo, now: new Date(),
  });
  assert.ok(result.merged.includes("t1"));
  assert.equal(result.integration, "red");
  assert.equal(nodeFs.readdirSync(logDir).filter(f => f.startsWith("verify-")).length, 0);
  assert.ok(log.lines.some(l => l === "верификатор: команд нет"));
});

