// Клиент-субпроцесс: каждый метод — один вызов установленного бинаря `listik`.
// Никакого HTTP напрямую, никакого чтения config.toml — только CLI (`--json`).
import {execFile} from "node:child_process";

export class ListikError extends Error {
  constructor(code, message, hint) {
    super(message);
    this.code = code;
    this.hint = hint;
  }
}

function run(bin, argv, timeoutMs) {
  return new Promise((resolve, reject) => {
    execFile(bin, argv, {timeout: timeoutMs, killSignal: "SIGKILL", maxBuffer: 64 * 1024 * 1024},
      (err, stdout, stderr) => {
        resolve({err, stdout: stdout ?? "", stderr: stderr ?? ""});
      });
  });
}

export class Listik {
  constructor({bin = "listik", host, port, actor, cliTimeout = 120} = {}) {
    this.bin = bin;
    this.host = host;
    this.port = port;
    this.actor = actor;
    this.cliTimeout = cliTimeout;
  }

  async _call(subArgs, {write = false} = {}) {
    const argv = [];
    if (this.host) argv.push("--host", String(this.host));
    if (this.port) argv.push("--port", String(this.port));
    argv.push(...subArgs);
    argv.push("--json");
    if (write) argv.push("--actor", this.actor);

    const {err, stdout, stderr} = await run(this.bin, argv, this.cliTimeout * 1000);

    if (err && (err.killed || err.signal)) {
      throw new ListikError("timeout", `таймаут ${this.cliTimeout}s: listik ${subArgs.join(" ")}`);
    }

    let parsed = null;
    try {
      parsed = stdout ? JSON.parse(stdout) : null;
    } catch {
      parsed = null;
    }

    if (!err) {
      return parsed;
    }

    if (parsed && typeof parsed === "object") {
      if (parsed.error) {
        throw new ListikError(parsed.error.code, parsed.error.message, parsed.error.hint);
      }
      // stdout — валидный JSON без ключа error (status: down, waves без --apply при цикле).
      return parsed;
    }

    throw new ListikError("cli", (stderr || stdout || err.message || "").trim());
  }

  status() {
    return this._call(["status"]);
  }

  async waves(project, {apply = false} = {}) {
    if (!apply) {
      return this._call(["waves", "--project", project]);
    }
    try {
      const res = await this._call(["waves", "--project", project, "--apply"], {write: true});
      const plan = res.waves;
      // Рёбра, записанные `--apply` — не часть плана, но лог тика должен видеть исход
      // пишущего вызова; вешаем как доп.поле, decide() эти поля не читает.
      plan.applyResult = {added: res.added || [], removed: res.removed || [], kept: res.kept || 0};
      return plan;
    } catch (err) {
      if (err instanceof ListikError && err.code === "conflict" &&
          typeof err.message === "string" &&
          err.message.startsWith("ресурсные рёбра не записаны: в зависимостях цикл")) {
        return this._call(["waves", "--project", project]);
      }
      throw err;
    }
  }

  list(project) {
    return this._call(["list", "-p", project, "-a", "-n", "1000"]);
  }

  routes() {
    return this._call(["routes"]);
  }

  projects() {
    return this._call(["projects"]);
  }

  comment(id, text, kind = "journal") {
    return this._call(["comment", id, text, "-k", kind], {write: true});
  }

  create({title, project, type, labels, discoveredFrom, description} = {}) {
    const argv = ["new", title, "-p", project, "-t", type];
    for (const label of labels || []) argv.push("-l", label);
    if (discoveredFrom) argv.push("--discovered-from", discoveredFrom);
    if (description) argv.push("-d", description);
    return this._call(argv, {write: true});
  }

  set(id, fields) {
    const argv = ["set", id];
    for (const [k, v] of Object.entries(fields || {})) {
      const value = Array.isArray(v) ? v.join(",") : (v == null ? "" : v);
      argv.push(`${k}=${value}`);
    }
    return this._call(argv, {write: true});
  }

  show(id) {
    return this._call(["show", id]);
  }

  needsOwner(id, text) {
    return this._call(["needs-owner", id, text], {write: true});
  }

  worktree(id) {
    return this._call(["worktree", id], {write: true});
  }

  setLabels(id, labels) {
    return this._call(["set", id, `labels=${labels.join(",")}`], {write: true});
  }

  launch(id, env) {
    const envArgs = Object.entries(env || {}).flatMap(([k, v]) => ["--env", `${k}=${v}`]);
    return this._call(["launch", id, ...envArgs], {write: true});
  }

  revoke(id, note) {
    return this._call(["revoke", id, "--note", note], {write: true});
  }
}
