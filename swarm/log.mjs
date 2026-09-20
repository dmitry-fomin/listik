// Лог прогона: файл + stdout. Никаких прогресс-баров/цветов — читается из файла
// и из nohup.
import fs from "node:fs";
import path from "node:path";

function stampFile(d) {
  return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

function stampLine(d) {
  return d.toISOString();
}

export function open(dir, project) {
  fs.mkdirSync(dir, {recursive: true});
  const file = `swarm-${project}-${stampFile(new Date())}.log`;
  const logPath = path.join(dir, file);
  const fd = fs.openSync(logPath, "a");

  function line(text) {
    fs.writeSync(fd, `[${stampLine(new Date())}] ${text}\n`);
  }

  // Строка действия (пишущий вызов/его намерение в --dry-run, launch, needs-owner):
  // в файл с меткой времени, и то же — в stdout (без интерактива, читается из nohup).
  function action(text) {
    line(text);
    process.stdout.write(text + "\n");
  }

  function summaryLine(report) {
    const now = new Date();
    const hhmmss = now.toISOString().slice(11, 19);
    const runningDesc = (report.running || [])
      .map(r => `${r.id} ${r.route ?? "-"} ${r.worktree ?? "-"} :${r.port ?? "-"}`).join(", ");
    const launchedDesc = (report.launch || []).join(", ");
    const needsOwnerDesc = (report.needsOwner || [])
      .map(n => `${n.id} ${n.reason === "unroutable" ? "без маршрута" : "без области"}`).join(", ");
    const skippedDesc = (report.skipped || [])
      .map(s => `${s.id} ${s.reason === "held" ? `держит ${s.holder ?? "другого"}` : "не влезла"}`)
      .join(", ");
    const text = `[${hhmmss}] ${report.project} · волна 0: ${report.waveSize} · ` +
      `бежит ${(report.running || []).length} (${runningDesc}) · ` +
      `запущено сейчас ${(report.launch || []).length} (${launchedDesc}) · ` +
      `ждут человека ${(report.needsOwner || []).length} (${needsOwnerDesc}) · ` +
      `пропущено ${(report.skipped || []).length} (${skippedDesc}) · ` +
      `стоят ${report.blocked ?? 0} · дальше волн ${report.wavesLeft ?? 0}`;
    fs.writeSync(fd, `[${stampLine(now)}] ${text}\n`);
    process.stdout.write(text + "\n");
    return text;
  }

  function close() {
    fs.closeSync(fd);
  }

  return {path: logPath, line, action, summary: summaryLine, close};
}
