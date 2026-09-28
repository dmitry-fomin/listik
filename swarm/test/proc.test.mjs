import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {runWithTimeout, killGroup} from "../proc.mjs";

// Запуск с логом во временный файл; fd закрывает тест. Возвращает результат и текст лога.
async function run(opts) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "swarm-proc-"));
  const logPath = path.join(dir, "out.log");
  const logFd = fs.openSync(logPath, "a");
  try {
    const res = await runWithTimeout({cwd: dir, timeoutSec: 30, ...opts, logFd});
    return {res, log: fs.readFileSync(logPath, "utf8")};
  } finally {
    fs.closeSync(logFd);
  }
}

test("runWithTimeout: код выхода и ms", async () => {
  const {res} = await run({argv: [process.execPath, "-e", "process.exit(3)"]});
  assert.equal(res.code, 3);
  assert.equal(res.timedOut, false);
  assert.equal(typeof res.ms, "number");
  assert.ok(res.ms >= 0);
});

test("runWithTimeout: несуществующий бинарь → code 1", async () => {
  const {res} = await run({argv: ["/nonexistent/swarm-proc-bin"]});
  assert.equal(res.code, 1);
  assert.equal(res.timedOut, false);
});

test("runWithTimeout: зависание → SIGTERM, через 5 с SIGKILL", async () => {
  const {res} = await run({argv: [process.execPath, "-e", "setInterval(()=>{},1000)"], timeoutSec: 1});
  assert.equal(res.timedOut, true);
  assert.equal(res.code, null);
  assert.ok(res.ms >= 5900, `ms=${res.ms}`);
  assert.throws(() => process.kill(res.pid, 0));
});

test("runWithTimeout: env пробрасывается", async () => {
  const {log} = await run({
    argv: [process.execPath, "-e", "process.stdout.write(process.env.PROC_TEST_MARK)"],
    env: {...process.env, PROC_TEST_MARK: "m1"},
  });
  assert.match(log, /m1/);
});

test("runWithTimeout: env по умолчанию — process.env", async () => {
  process.env.PROC_TEST_MARK = "m2";
  try {
    const {log} = await run({argv: [process.execPath, "-e", "process.stdout.write(process.env.PROC_TEST_MARK)"]});
    assert.match(log, /m2/);
  } finally {
    delete process.env.PROC_TEST_MARK;
  }
});

test("killGroup: пустой pid и завершившийся процесс не бросают", async () => {
  const {res} = await run({argv: [process.execPath, "-e", "process.exit(0)"]});
  assert.doesNotThrow(() => killGroup(undefined, "SIGTERM"));
  assert.doesNotThrow(() => killGroup("", "SIGKILL"));
  assert.doesNotThrow(() => killGroup(res.pid, "SIGTERM"));
});
