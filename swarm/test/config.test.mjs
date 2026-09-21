import {test} from "node:test";
import assert from "node:assert/strict";
import {parseConfig, parseSwarmConfig, swarmConfigFor, ConfigError, HelpRequested} from "../config.mjs";

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
