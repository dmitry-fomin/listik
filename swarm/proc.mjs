// Запуск внешней команды роя с таймаутом — общий для арбитра (`arbiter.mjs`) и команд
// интеграции/верификатора (`barrier.mjs`). Процесс — лидер своей группы (`detached`),
// stdout+stderr сразу в готовый fd (не pipe — длинный вывод иначе дедлочит).
import {spawn, execFileSync} from "node:child_process";

// Сигнал всей группе процессов: SIGTERM по таймауту, SIGKILL через 5 с.
export function killGroup(pid, signal) {
  if (!pid) return;
  try { process.kill(-pid, signal); } catch { /* лидер мог уже выйти */ }
  try {
    execFileSync("kill", ["-s", signal === "SIGKILL" ? "KILL" : "TERM", `-${pid}`],
      {stdio: "ignore", timeout: 2000});
  } catch { /* группы уже нет */ }
}

// Результат ровно один раз. После таймаута `exit`/`error` игнорируются: промис
// разрешается только после SIGKILL. `logFd` не открывается и не закрывается здесь.
export function runWithTimeout({argv, cwd, env = process.env, logFd, timeoutSec}) {
  return new Promise((resolvePromise) => {
    const start = Date.now();
    const child = spawn(argv[0], argv.slice(1), {
      cwd, env, detached: true, stdio: ["ignore", logFd, logFd],
    });
    let settled = false;
    let timedOut = false;
    let killTimer = null;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(termTimer);
      if (killTimer) clearTimeout(killTimer);
      resolvePromise({...result, ms: Date.now() - start, pid: child.pid});
    };
    const termTimer = setTimeout(() => {
      timedOut = true;
      killGroup(child.pid, "SIGTERM");
      killTimer = setTimeout(() => {
        killGroup(child.pid, "SIGKILL");
        finish({code: null, timedOut: true});
      }, 5000);
    }, timeoutSec * 1000);
    child.on("exit", (code) => {
      if (timedOut) return;
      finish({code, timedOut: false});
    });
    child.on("error", () => {
      if (timedOut) return;
      finish({code: 1, timedOut: false});
    });
  });
}
