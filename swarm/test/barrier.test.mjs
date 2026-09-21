import {test} from "node:test";
import assert from "node:assert/strict";
import {
  covers, outsideScope, parseMarked, mergedRecord, sortForMerge, mergeCandidates, haltCards,
  frozenBy, tailLines, MERGED_MARK, FIRST_CHANGE_MARK, HALT_LABEL, SWARM_AUTHOR,
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
