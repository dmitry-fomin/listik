// Точка входа роя. Рой не вызывает модель: каждый тик читает состояние проекта
// заново из Listik субпроцессами `listik … --json` и решает жадно по правилам
// (`decide.mjs`), поэтому не хранит состояния между запусками — падение
// переживается перезапуском, повторный тик ничего не задваивает.
import {parseConfig, ConfigError, HelpRequested} from "./config.mjs";
import {Listik} from "./listik.mjs";
import {tick} from "./run.mjs";
import {open as openLog} from "./log.mjs";

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function configLine(config) {
  const {listikHost, listikPort, ...rest} = config;
  return `конфиг: ${JSON.stringify({...rest, listikHost, listikPort})}`;
}

function waitingLine(result) {
  const parts = [];
  const report = result.report || {};
  for (const s of report.skipped || []) {
    parts.push(s.reason === "held" ? `${s.id} держит другой` : `${s.id} не влезла в партию`);
  }
  if (report.blocked) parts.push(`${report.blocked} стоят за блокерами`);
  return `ждут: ${parts.length ? parts.join(", ") : (result.open || []).join(", ")}`;
}

// п.8: причина вопроса — по последнему комментарию kind=="question". «рой: …» — своя
// короткая причина; иначе воркер сам о чём-то спросил.
function questionReason(text) {
  if (typeof text !== "string" || !text.startsWith("рой:")) return "вопрос воркера";
  if (text.includes("нет маршрута")) return "без маршрута";
  if (text.includes("нет write_scope")) return "без области";
  if (text.includes("процесс задачи завершился")) return "упала";
  if (text.includes("свободных портов")) return "нет портов";
  if (text.includes("задача зависла")) return "зависла";
  if (text.includes("не снят")) return "процесс не снят";
  return "вопрос воркера";
}

async function reportWaiting(listik, config, log, result) {
  const ids = result.needsOwnerOpen || [];
  for (const id of ids) {
    if (config.dryRun) {
      log.line(`${id} — ждёт человека`);
      continue;
    }
    try {
      const shown = await listik.show(id);
      const questions = (shown.comments || []).filter(c => c.kind === "question");
      const last = questions[questions.length - 1];
      log.line(`${id} — ${questionReason(last && last.text)}`);
    } catch (err) {
      log.line(`${id} — ждёт человека (show ошибка: ${err.code ?? "error"}/${err.message ?? err})`);
    }
  }
  log.line(waitingLine(result));
}

async function runOneTick(listik, config, log) {
  try {
    return await tick(listik, config, log);
  } catch (err) {
    log.line(`ошибка тика: ${err.code ?? "error"}/${err.message ?? err}/${err.hint ?? ""}`);
    return {error: true};
  }
}

export async function main(argv) {
  let config;
  try {
    config = parseConfig(argv);
  } catch (err) {
    if (err instanceof HelpRequested) {
      process.stdout.write(err.message);
      return 0;
    }
    if (err instanceof ConfigError) {
      process.stderr.write(`${err.message}\n`);
      return 2;
    }
    throw err;
  }

  const log = openLog(config.logDir, config.project);
  process.stdout.write(`${log.path}\n`);
  log.line(configLine(config));

  const listik = new Listik({
    bin: config.listikBin,
    host: config.listikHost,
    port: config.listikPort,
    actor: config.actor,
    cliTimeout: config.cliTimeout,
  });

  let signalExit = null;
  const onSignal = (name, code) => () => {
    log.line(`остановлен сигналом ${name}`);
    signalExit = code;
  };
  process.once("SIGINT", onSignal("SIGINT", 130));
  process.once("SIGTERM", onSignal("SIGTERM", 143));

  if (config.once || config.dryRun) {
    const result = await runOneTick(listik, config, log);
    if (signalExit != null) return signalExit;
    if (result.error) return 4;
    if (result.serverDown) return 3;
    if (result.cycles && result.cycles.length) return 1;
    return 0;
  }

  let firstOpen = null;
  let totalRestarts = 0;

  for (;;) {
    const result = await runOneTick(listik, config, log);
    if (signalExit != null) return signalExit;

    if (result.serverDown || result.error || (result.cycles && result.cycles.length)) {
      await sleep(config.interval * 1000);
      if (signalExit != null) return signalExit;
      continue;
    }

    if (firstOpen == null) firstOpen = result.open || [];
    totalRestarts += (result.restarted || []).length;

    const launchedNone = !result.launched || result.launched.length === 0;
    const restartedNone = !result.restarted || result.restarted.length === 0;
    const runningEmpty = !result.running || result.running.length === 0;
    if (launchedNone && restartedNone && runningEmpty) {
      const openList = result.open || [];
      if (openList.length === 0) return 0;
      await reportWaiting(listik, config, log, result);
      const lastOpen = new Set(openList);
      const closed = firstOpen.filter(id => !lastOpen.has(id));
      const leftToHuman = (result.needsOwnerOpen || []).length;
      log.line(`итог: закрыто ${closed.length} (${closed.join(", ")}), ` +
        `перезапусков ${totalRestarts}, оставлено человеку ${leftToHuman}`);
      return 2;
    }

    await sleep(config.interval * 1000);
    if (signalExit != null) return signalExit;
  }
}
