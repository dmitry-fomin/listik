// Лог прогона: файл + stdout. Никаких прогресс-баров/цветов — читается из файла
// и из nohup.
import fs from "node:fs";
import path from "node:path";

export function stampFile(d) {
  return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

function stampLine(d) {
  return d.toISOString();
}

// Подписи причин — одна таблица для сводки тика и строки `ждут:` (main.mjs).
const SKIP_LABELS = {
  frozen: "заморожена", gated: "гейт", budget: "бюджет",
  sliced: "нарезана, ждёт порции", capacity: "не влезла в партию",
};
// Причины вопроса человеку: код → подпись и начало текста вопроса. `re` заякорены
// на начало и взаимоисключающие — порядок записей на `questionCode` не влияет.
// Тексты ставят decide.mjs, run.mjs, rollback.mjs, barrier.mjs и listik/*.py;
// сверка с ними — swarm/test/reasons.test.mjs.
export const QUESTION_REASONS = {
  unscoped: {label: "без области", re: /^рой: у задачи нет write_scope/u},
  sliced_stuck: {label: "порции не запускаются",
    re: /^рой: задача нарезана, но ни одна порция не запускается/u},
  crashed: {label: "упала", re: /^рой: процесс задачи завершился/u},
  no_ports: {label: "нет портов", re: /^рой: задача зависла, но перезапустить нечем/u},
  hung: {label: "зависла", re: /^рой: задача зависла \(бежит дольше/u},
  budget: {label: "бюджет", re: /^рой: бюджет прогона исчерпан/u},
  process_not_stopped: {label: "процесс не снят", re: /^рой: полномочия отозваны, но процесс/u},
  worktree_failed: {label: "нет рабочего дерева", re: /^рой: не удалось завести рабочее дерево/u},
  freeze_limit: {label: "предел откатов", re: /^рой: предел откатов/u},
  rejected: {label: "не принята", re: /^рой: не влита — отклонена \d+ раз/u},
  unmerged: {label: "не влита", re: /^рой: не влита — (?!отклонена \d+ раз)/u},
  halt: {label: "стоп роя", re: /^рой: интеграционные тесты /u},
  no_role_after: {label: "нет роли после этапа", re: /^рой: после \S+ роли нет/u},
  portions_no_role: {label: "порции без роли",
    re: /^рой: порции заведены, но после s1-spec роли нет/u},
  portions_cancelled: {label: "порции отменены", re: /^рой: все порции отменены/u},
  route_no_roles: {label: "маршрут без ролей", re: /^рой: у маршрута .*? нет роли с командой/u},
  no_criteria: {label: "нет критериев роли", re: /^рой: нет критериев роли/u},
  no_workdir: {label: "нет рабочего каталога", re: /^рой: нет рабочего каталога/u},
  claim_failed: {label: "не взята харнессом", re: /^рой: не взял карточку за /u},
  start_failed: {label: "этап не запустился", re: /^рой: этап \S+ \([^)]*\) не запустился:/u},
  not_delivered: {label: "работа не сдана", re: /^рой: этап \S+ \([^)]*\) не сдал работу/u},
  empty_question: {label: "вопрос без текста",
    re: /^рой: этап \S+ \([^)]*\) ответил «вопрос» без текста/u},
  outcome_error: {label: "исход не разобран", re: /^рой: не разобрал исход этапа/u},
  autostart_refused: {label: "автостарт не выполнен", re: /^автостарт не выполнен: /u},
};

// Код причины по тексту вопроса — только от начала текста; не подошло → null.
export function questionCode(text) {
  if (typeof text !== "string") return null;
  const head = text.trimStart();
  for (const [code, {re}] of Object.entries(QUESTION_REASONS)) {
    if (re.test(head)) return code;
  }
  return null;
}

export function skipLabel(s) {
  if (s.reason === "held") return `держит ${s.holder ?? "другой"}`;
  return Object.hasOwn(SKIP_LABELS, s.reason) ? SKIP_LABELS[s.reason] : s.reason;
}

export function needsOwnerLabel(n) {
  return Object.hasOwn(QUESTION_REASONS, n.reason) ? QUESTION_REASONS[n.reason].label : n.reason;
}

export function open(dir, project, out = process.stdout) {
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
    out.write(text + "\n");
  }

  function summaryLine(report) {
    const now = new Date();
    const hhmmss = now.toISOString().slice(11, 19);
    const runningDesc = (report.running || [])
      .map(r => `${r.id} ${r.route ?? "-"} ${r.worktree ?? "-"} :${r.port ?? "-"}`).join(", ");
    const launchedDesc = (report.launch || []).join(", ");
    const needsOwnerDesc = (report.needsOwner || [])
      .map(n => `${n.id} ${needsOwnerLabel(n)}`).join(", ");
    const skippedDesc = (report.skipped || [])
      .map(s => `${s.id} ${skipLabel(s)}`).join(", ");
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
    out.write(text + "\n");
    return text;
  }

  function close() {
    fs.closeSync(fd);
  }

  return {path: logPath, line, action, summary: summaryLine, close};
}
