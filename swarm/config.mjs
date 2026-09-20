// Разбор флагов роя (`node:util.parseArgs`, только stdlib). Никакого состояния —
// чистая функция argv -> config, включая дефолты и проверки.
import {parseArgs} from "node:util";
import {fileURLToPath} from "node:url";
import path from "node:path";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const KNOWN_LEVELS = ["xhigh", "high", "medium", "low", "xlow", "direct"];
const DEFAULT_WEIGHTS = {xhigh: 3, high: 2, medium: 1, low: 1, xlow: 1, direct: 1};

export class ConfigError extends Error {}

export class HelpRequested extends Error {}

const OPTIONS = {
  project: {type: "string"},
  parallel: {type: "string"},
  weights: {type: "string"},
  "port-base": {type: "string"},
  "port-count": {type: "string"},
  interval: {type: "string"},
  once: {type: "boolean"},
  "dry-run": {type: "boolean"},
  listik: {type: "string"},
  "listik-host": {type: "string"},
  "listik-port": {type: "string"},
  "cli-timeout": {type: "string"},
  "log-dir": {type: "string"},
  actor: {type: "string"},
  "stale-minutes": {type: "string"},
  "timeout-minutes": {type: "string"},
  "max-restarts": {type: "string"},
  help: {type: "boolean"},
};

export const HELP_TEXT = `listik-swarm — каркас роя: план, needs-owner, партия, дерево, порт, запуск

  --project <slug>          проект (обязателен)
  --parallel <N>             ёмкость партии в единицах веса (по умолчанию 3)
  --weights xhigh=3,high=2,medium=1,low=1,xlow=1,direct=1
                              частичное переопределение весов уровня маршрута
  --port-base <N>             начало диапазона портов (по умолчанию 5170)
  --port-count <N>            размер диапазона портов (по умолчанию 100)
  --interval <сек>            пауза между тиками (по умолчанию 30)
  --once                      один тик и выход
  --dry-run                   один тик без записи, только план и намерения
  --listik <путь или имя>     бинарь listik (по умолчанию "listik" из PATH)
  --listik-host <host>        --host для вызовов listik
  --listik-port <port>        --port для вызовов listik
  --cli-timeout <сек>         предел одного вызова listik (по умолчанию 120)
  --log-dir <путь>            каталог лога роя
  --actor <actor>             актёр для пишущих вызовов (по умолчанию agent:listik-swarm)
  --stale-minutes <N>         (порция c) по умолчанию 20
  --timeout-minutes <N>       (порция c) по умолчанию 0
  --max-restarts <N>          (порция c) по умолчанию 1
  --help                      эта справка
`;

function parseNumber(name, raw) {
  const n = Number(raw);
  if (!Number.isFinite(n)) {
    throw new ConfigError(`--${name} ожидало число, получено ${JSON.stringify(raw)}`);
  }
  return n;
}

function parseWeights(raw) {
  const weights = {...DEFAULT_WEIGHTS};
  if (!raw) return weights;
  for (const pair of raw.split(",")) {
    const trimmed = pair.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    if (eq < 0) {
      throw new ConfigError(`--weights ожидало level=N, получено ${trimmed}`);
    }
    const level = trimmed.slice(0, eq);
    const valueRaw = trimmed.slice(eq + 1);
    if (!KNOWN_LEVELS.includes(level)) {
      throw new ConfigError(`--weights: неизвестный уровень ${level} (из ${KNOWN_LEVELS.join(", ")})`);
    }
    const value = Number(valueRaw);
    if (!Number.isFinite(value)) {
      throw new ConfigError(`--weights: ${level} ожидало число, получено ${valueRaw}`);
    }
    weights[level] = value;
  }
  return weights;
}

export function parseConfig(argv) {
  let values;
  try {
    ({values} = parseArgs({args: argv, options: OPTIONS, allowPositionals: false}));
  } catch (err) {
    throw new ConfigError(err.message);
  }

  if (values.help) {
    throw new HelpRequested(HELP_TEXT);
  }

  if (!values.project) {
    throw new ConfigError("нужен --project <slug>");
  }

  const config = {
    project: values.project,
    parallel: values.parallel !== undefined ? parseNumber("parallel", values.parallel) : 3,
    weights: parseWeights(values.weights),
    portBase: values["port-base"] !== undefined ? parseNumber("port-base", values["port-base"]) : 5170,
    portCount: values["port-count"] !== undefined ? parseNumber("port-count", values["port-count"]) : 100,
    interval: values.interval !== undefined ? parseNumber("interval", values.interval) : 30,
    once: !!values.once,
    dryRun: !!values["dry-run"],
    listikBin: values.listik ?? "listik",
    listikHost: values["listik-host"],
    listikPort: values["listik-port"],
    cliTimeout: values["cli-timeout"] !== undefined ? parseNumber("cli-timeout", values["cli-timeout"]) : 120,
    logDir: values["log-dir"] ?? (process.env.LISTIK_HOME
      ? path.join(process.env.LISTIK_HOME, "logs")
      : path.join(REPO_ROOT, "logs")),
    actor: values.actor ?? "agent:listik-swarm",
    staleMinutes: values["stale-minutes"] !== undefined ? parseNumber("stale-minutes", values["stale-minutes"]) : 20,
    timeoutMinutes: values["timeout-minutes"] !== undefined ? parseNumber("timeout-minutes", values["timeout-minutes"]) : 0,
    maxRestarts: values["max-restarts"] !== undefined ? parseNumber("max-restarts", values["max-restarts"]) : 1,
  };

  return config;
}
