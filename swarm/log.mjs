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
    const silenceById = new Map((report.silence || []).map(s => [s.id, s.minutes]));
    const runningDesc = (report.running || [])
      .map(r => {
        const base = `${r.id} ${r.route ?? "-"} ${r.worktree ?? "-"} :${r.port ?? "-"}`;
        const mins = silenceById.get(r.id);
        return mins != null ? `${base} молчит ${mins} мин` : base;
      }).join(", ");
    const launchedDesc = (report.launch || []).join(", ");
    const needsOwnerDesc = (report.needsOwner || [])
      .map(n => `${n.id} ${n.reason === "unroutable" ? "без маршрута" : "без области"}`).join(", ");
    const skippedDesc = (report.skipped || [])
      .map(s => `${s.id} ${
        s.reason === "held" ? `держит ${s.holder ?? "другого"}`
          : s.reason === "frozen" ? "заморожена"
          : s.reason === "gated" ? "гейт"
          : s.reason === "budget" ? "бюджет"
          : "не влезла"
      }`)
      .join(", ");
    const restartDesc = (report.restart || [])
      .map(r => `${r.id} ${r.reason} → поколение ${r.generation}`).join(", ");
    const crashedDesc = (report.crashed || [])
      .map(c => `${c.id} код ${c.exitCode == null ? "неизвестен" : c.exitCode}`).join(", ");
    const stopOnlyDesc = (report.stopOnly || []).join(", ");
    const mergedDesc = (report.merged || []).join(", ");
    const unmergedDesc = (report.unmerged || []).join(", ");
    const rejectedDesc = (report.rejected || []).join(", ");
    const defaultsDesc = (report.defaults || []).join(", ");
    const unfrozenDesc = (report.unfrozen || []).join(", ");
    const integrationDesc = report.integration === "green" ? "зелёная"
      : report.integration === "red" ? "красная" : "—";
    const rollbackDesc = (report.rollbacks || []).join(", ");
    const parkedDesc = (report.parked || []).join(", ");
    const rollbackMinutes = report.rollbackMinutes ?? 0;
    const text = `[${hhmmss}] ${report.project} · волна 0: ${report.waveSize} · ` +
      `бежит ${(report.running || []).length} (${runningDesc}) · ` +
      `запущено сейчас ${(report.launch || []).length} (${launchedDesc}) · ` +
      `перезапущено ${(report.restart || []).length} (${restartDesc}) · ` +
      `упали ${(report.crashed || []).length} (${crashedDesc}) · ` +
      `оставлены человеку ${(report.giveUp || []).length} (${(report.giveUp || []).join(", ")}) · ` +
      `сняты процессы закрытых ${(report.stopOnly || []).length} (${stopOnlyDesc}) · ` +
      `ждут человека ${(report.needsOwner || []).length} (${needsOwnerDesc}) · ` +
      `пропущено ${(report.skipped || []).length} (${skippedDesc}) · ` +
      `стоят ${report.blocked ?? 0} · дальше волн ${report.wavesLeft ?? 0} · ` +
      `откаты ${(report.rollbacks || []).length} (${rollbackDesc}) · ` +
      `на откаты ${rollbackMinutes} мин · ` +
      `по пределу ${(report.parked || []).length} (${parkedDesc}) · ` +
      `влито ${(report.merged || []).length} (${mergedDesc}) · ` +
      `не влиты ${(report.unmerged || []).length} (${unmergedDesc}) · ` +
      `не приняты ${(report.rejected || []).length} (${rejectedDesc}) · ` +
      `дефолт ${(report.defaults || []).length} (${defaultsDesc}) · ` +
      `разморожено ${(report.unfrozen || []).length} (${unfrozenDesc}) · ` +
      `интеграция ${integrationDesc} · ` +
      `стоп ${report.halt ?? "—"} · ` +
      `бюджет ${report.budget && report.budget.exhausted ? "исчерпан" : "есть"}`;
    fs.writeSync(fd, `[${stampLine(now)}] ${text}\n`);
    process.stdout.write(text + "\n");
    return text;
  }

  function close() {
    fs.closeSync(fd);
  }

  return {path: logPath, line, action, summary: summaryLine, close};
}
