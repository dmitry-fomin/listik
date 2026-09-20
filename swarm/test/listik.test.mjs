import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {Listik, ListikError} from "../listik.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const FAKE_BIN = path.join(here, "fixtures", "fake-listik.mjs");

function setupFake(responses) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-test-"));
  const callsFile = path.join(dir, "calls.log");
  const responsesFile = path.join(dir, "responses.json");
  fs.writeFileSync(responsesFile, JSON.stringify(responses));
  process.env.LISTIK_FAKE_CALLS_FILE = callsFile;
  process.env.LISTIK_FAKE_RESPONSES_FILE = responsesFile;
  return {
    callsFile,
    calls() {
      return fs.readFileSync(callsFile, "utf8").trim().split("\n").filter(Boolean)
        .map(l => JSON.parse(l).argv);
    },
  };
}

test("launch: argv содержит --env, --json, --actor", async () => {
  const {calls} = setupFake({launch: {stdout: JSON.stringify({id: "a", generation: 1})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  await listik.launch("a", {LISTIK_DEV_PORT: "5170"});
  const [argv] = calls();
  assert.deepEqual(argv, ["launch", "a", "--env", "LISTIK_DEV_PORT=5170", "--json", "--actor", "agent:listik-swarm"]);
});

test("--port стоит до подкоманды", async () => {
  const {calls} = setupFake({status: {stdout: JSON.stringify({server: "up"})}});
  const listik = new Listik({bin: FAKE_BIN, port: 1234, cliTimeout: 5});
  await listik.status();
  const [argv] = calls();
  assert.deepEqual(argv.slice(0, 2), ["--port", "1234"]);
});

test("list зовётся с -a", async () => {
  const {calls} = setupFake({list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: []})}});
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 5});
  await listik.list("proj");
  const [argv] = calls();
  assert.ok(argv.includes("-a"));
});

test("ненулевой код с JSON {error} — ListikError с кодом", async () => {
  setupFake({show: {exitCode: 1, stdout: JSON.stringify({error: {code: "not_found", message: "нет", hint: "h"}})}});
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 5});
  await assert.rejects(() => listik.show("x"), (err) => {
    assert.ok(err instanceof ListikError);
    assert.equal(err.code, "not_found");
    return true;
  });
});

test("ненулевой код с JSON без error (status down) — успех", async () => {
  setupFake({status: {exitCode: 1, stdout: JSON.stringify({server: "down"})}});
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 5});
  const res = await listik.status();
  assert.equal(res.server, "down");
});

test("waves без --apply при цикле (ненулевой код) — успех, план с cycles", async () => {
  setupFake({waves: {exitCode: 1, stdout: JSON.stringify({cycles: [["a", "b"]], waves: []})}});
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 5});
  const plan = await listik.waves("proj", {apply: false});
  assert.deepEqual(plan.cycles, [["a", "b"]]);
});

test("waves --apply конфликт цикла — второй вызов без --apply, план из него", async () => {
  const {calls} = setupFake({
    waves: [
      {exitCode: 1, stdout: JSON.stringify({error: {
        code: "conflict",
        message: "ресурсные рёбра не записаны: в зависимостях цикл a → b → a",
      }})},
      {stdout: JSON.stringify({cycles: [["a", "b"]], waves: []})},
    ],
  });
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 5});
  const plan = await listik.waves("proj", {apply: true});
  assert.deepEqual(plan.cycles, [["a", "b"]]);
  const argvs = calls();
  assert.equal(argvs.length, 2);
  assert.ok(argvs[0].includes("--apply"));
  assert.ok(!argvs[1].includes("--apply"));
});

test("таймаут — ListikError c code timeout", async () => {
  setupFake({status: {sleepMs: 2000, stdout: JSON.stringify({server: "up"})}});
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 1});
  await assert.rejects(() => listik.status(), (err) => {
    assert.ok(err instanceof ListikError);
    assert.equal(err.code, "timeout");
    return true;
  });
});
