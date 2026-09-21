import {test} from "node:test";
import assert from "node:assert/strict";
import {parseConfig, parseSwarmConfig, swarmConfigFor, ConfigError, HelpRequested,
  DEFAULT_QUESTION_TIMEOUT} from "../config.mjs";

test("дефолты", () => {
  const config = parseConfig(["--project", "listik"]);
  assert.equal(config.listikBin, "listik");
  assert.equal(config.cliTimeout, 120);
  assert.equal(config.parallel, 3);
  assert.equal(config.portBase, 5170);
  assert.equal(config.portCount, 100);
  assert.equal(config.interval, 30);
  assert.equal(config.actor, "agent:listik-swarm");
  assert.deepEqual(config.weights, {xhigh: 3, high: 2, medium: 1, low: 1, xlow: 1, direct: 1});
});

test("--weights частично переопределяет один уровень", () => {
  const config = parseConfig(["--project", "listik", "--weights", "xhigh=5"]);
  assert.deepEqual(config.weights, {xhigh: 5, high: 2, medium: 1, low: 1, xlow: 1, direct: 1});
});

test("--weights foo=1 — ошибка", () => {
  assert.throws(() => parseConfig(["--project", "listik", "--weights", "foo=1"]), ConfigError);
});

test("--parallel abc — ошибка", () => {
  assert.throws(() => parseConfig(["--project", "listik", "--parallel", "abc"]), ConfigError);
});

test("без --project — ошибка", () => {
  assert.throws(() => parseConfig([]), ConfigError);
});

test("--help — HelpRequested", () => {
  assert.throws(() => parseConfig(["--help"]), HelpRequested);
});

test("--config задаёт configPath, без флага — null", () => {
  const withFlag = parseConfig(["--project", "p", "--config", "/x/y.json"]);
  assert.equal(withFlag.configPath, "/x/y.json");
  const withoutFlag = parseConfig(["--project", "p"]);
  assert.equal(withoutFlag.configPath, null);
});

// --- swarm.json: parseSwarmConfig / swarmConfigFor ---

const FULL_EXAMPLE = JSON.stringify({
  integration_timeout: 99,
  arbiter: ["claude", "--dangerously-skip-permissions", "-p", "{prompt}"],
  projects: {
    listik: {
      arbiter_timeout: 42,
      integration: [
        ["python3", "-m", "unittest", "discover", "tests"],
        ["npm", "--prefix", "web", "run", "typecheck"],
      ],
    },
  },
});

test("parseSwarmConfig(null) — {}", () => {
  assert.deepEqual(parseSwarmConfig(null), {});
});

test("полный пример разбирается, swarmConfigFor даёт нужные поля по проекту и дефолт", () => {
  const cfg = parseSwarmConfig(FULL_EXAMPLE);
  const listik = swarmConfigFor(cfg, "listik");
  assert.deepEqual(listik.integration, [
    ["python3", "-m", "unittest", "discover", "tests"],
    ["npm", "--prefix", "web", "run", "typecheck"],
  ]);
  assert.deepEqual(listik.arbiter, ["claude", "--dangerously-skip-permissions", "-p", "{prompt}"]);
  assert.equal(listik.integrationTimeout, 99);
  assert.equal(listik.arbiterTimeout, 42);

  const other = swarmConfigFor(cfg, "other");
  assert.equal(other.integration, null);
  assert.deepEqual(other.arbiter, ["claude", "--dangerously-skip-permissions", "-p", "{prompt}"]);
  assert.equal(other.integrationTimeout, 99);
  assert.equal(other.arbiterTimeout, 1200);
});

test("объект без ключей таймаутов — дефолты 1800/1200", () => {
  const cfg = swarmConfigFor(parseSwarmConfig("{}"), "any");
  assert.equal(cfg.integrationTimeout, 1800);
  assert.equal(cfg.arbiterTimeout, 1200);
});

test('{"integration": []} — [] (не null)', () => {
  const cfg = swarmConfigFor(parseSwarmConfig(JSON.stringify({integration: []})), "any");
  assert.deepEqual(cfg.integration, []);
});

test("swarm.json: невалидные варианты — ConfigError", () => {
  const invalidJson = ["not json", "null", "[]"];
  for (const text of invalidJson) {
    assert.throws(() => parseSwarmConfig(text), ConfigError, text);
  }
  assert.throws(() => parseSwarmConfig(JSON.stringify({integration: "npm test"})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({integration: ["npm", "test"]})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({arbiter: ["claude", "-p", "prompt"]})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({integration_timeout: 0})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({integraton: ["x"]})), ConfigError);
});

test("verify разбирается и переопределяется по проекту; дефолты 1800/1", () => {
  const cfg = parseSwarmConfig(JSON.stringify({
    verify: [["echo", "top"]],
    verify_timeout: 10,
    verify_retries: 3,
    projects: {
      listik: {
        verify: [["echo", "proj"]],
        verify_timeout: 20,
        verify_retries: 0,
      },
    },
  }));
  const listik = swarmConfigFor(cfg, "listik");
  assert.deepEqual(listik.verify, [["echo", "proj"]]);
  assert.equal(listik.verifyTimeout, 20);
  assert.equal(listik.verifyRetries, 0);
  const other = swarmConfigFor(cfg, "other");
  assert.deepEqual(other.verify, [["echo", "top"]]);
  assert.equal(other.verifyTimeout, 10);
  assert.equal(other.verifyRetries, 3);

  const empty = swarmConfigFor(parseSwarmConfig("{}"), "any");
  assert.equal(empty.verify, null);
  assert.equal(empty.verifyTimeout, 1800);
  assert.equal(empty.verifyRetries, 1);
});

test('{"verify": []} — [] (не null)', () => {
  const cfg = swarmConfigFor(parseSwarmConfig(JSON.stringify({verify: []})), "any");
  assert.deepEqual(cfg.verify, []);
});

test("swarm.json: verify невалидные — ConfigError", () => {
  assert.throws(() => parseSwarmConfig(JSON.stringify({verify: ["x"]})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({verify: null})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({verify_timeout: 0})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({verify_retries: -1})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({verify_retries: 1.5})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({verfy: [["x"]]})), ConfigError);
});

test("question_timeout: дефолт 30, проектное переопределение, 0 и 0.05 ок, -1 и строка — ConfigError", () => {
  assert.equal(DEFAULT_QUESTION_TIMEOUT, 30);
  const empty = swarmConfigFor(parseSwarmConfig("{}"), "any");
  assert.equal(empty.questionTimeout, 30);

  const cfg = parseSwarmConfig(JSON.stringify({
    question_timeout: 10,
    projects: {listik: {question_timeout: 5}},
  }));
  assert.equal(swarmConfigFor(cfg, "listik").questionTimeout, 5);
  assert.equal(swarmConfigFor(cfg, "other").questionTimeout, 10);

  assert.equal(swarmConfigFor(parseSwarmConfig(JSON.stringify({question_timeout: 0})), "any").questionTimeout, 0);
  assert.equal(swarmConfigFor(parseSwarmConfig(JSON.stringify({question_timeout: 0.05})), "any").questionTimeout, 0.05);

  assert.throws(() => parseSwarmConfig(JSON.stringify({question_timeout: -1})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({question_timeout: "30"})), ConfigError);
});
