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
  config: {type: "string"},
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
  --config <путь>              swarm.json (по умолчанию <data_dir>/swarm.json)
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
    configPath: values.config ?? null,
  };

  return config;
}

// --- swarm.json (барьер: тесты интеграции + арбитр слияния) ---
// Читается в тике (порция c) — здесь только чистый разбор текста.

const SWARM_TOP_KEYS = new Set(["integration", "arbiter", "integration_timeout", "arbiter_timeout",
  "verify", "verify_timeout", "verify_retries", "question_timeout", "projects"]);
const SWARM_PROJECT_KEYS = new Set(["integration", "arbiter", "integration_timeout", "arbiter_timeout",
  "verify", "verify_timeout", "verify_retries", "question_timeout"]);
const DEFAULT_INTEGRATION_TIMEOUT = 1800;
const DEFAULT_ARBITER_TIMEOUT = 1200;
const DEFAULT_VERIFY_TIMEOUT = 1800;
const DEFAULT_VERIFY_RETRIES = 1;
export const DEFAULT_QUESTION_TIMEOUT = 30;

function isPlainObject(v) {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isNonEmptyStringArray(v) {
  return Array.isArray(v) && v.length > 0 && v.every(s => typeof s === "string" && s.length > 0);
}

function validateIntegration(value, where, key = "integration") {
  if (!Array.isArray(value) || !value.every(isNonEmptyStringArray)) {
    throw new ConfigError(`swarm.json: ${where}${key} ожидал массив команд ` +
      `(каждая — непустой массив непустых строк)`);
  }
}

function validateArbiter(value, where) {
  if (!isNonEmptyStringArray(value)) {
    throw new ConfigError(`swarm.json: ${where}arbiter ожидал непустой массив непустых строк`);
  }
  if (!value.some(s => s.includes("{prompt}"))) {
    throw new ConfigError(`swarm.json: ${where}arbiter — ни один элемент не содержит {prompt}`);
  }
}

function validateTimeout(value, key, where) {
  if (typeof value !== "number" || !(value > 0)) {
    throw new ConfigError(`swarm.json: ${where}${key} ожидал число > 0`);
  }
}

function validateRetries(value, key, where) {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ConfigError(`swarm.json: ${where}${key} ожидал целое ≥ 0`);
  }
}

function validateQuestionTimeout(value, where) {
  if (typeof value !== "number" || !(value >= 0)) {
    throw new ConfigError(`swarm.json: ${where}question_timeout ожидал число ≥ 0`);
  }
}

function validateSettings(obj, allowedKeys, where) {
  for (const key of Object.keys(obj)) {
    if (!allowedKeys.has(key)) {
      throw new ConfigError(`swarm.json: неизвестный ключ ${where}${key}`);
    }
  }
  if ("integration" in obj) validateIntegration(obj.integration, where);
  if ("verify" in obj) validateIntegration(obj.verify, where, "verify");
  if ("arbiter" in obj) validateArbiter(obj.arbiter, where);
  if ("integration_timeout" in obj) validateTimeout(obj.integration_timeout, "integration_timeout", where);
  if ("arbiter_timeout" in obj) validateTimeout(obj.arbiter_timeout, "arbiter_timeout", where);
  if ("verify_timeout" in obj) validateTimeout(obj.verify_timeout, "verify_timeout", where);
  if ("verify_retries" in obj) validateRetries(obj.verify_retries, "verify_retries", where);
  if ("question_timeout" in obj) validateQuestionTimeout(obj.question_timeout, where);
}

export function parseSwarmConfig(text) {
  if (text === null) return {};

  let raw;
  try {
    raw = JSON.parse(text);
  } catch (err) {
    throw new ConfigError(`swarm.json: невалидный JSON — ${err.message}`);
  }
  if (!isPlainObject(raw)) {
    throw new ConfigError("swarm.json: ожидал объект верхнего уровня");
  }

  validateSettings(raw, SWARM_TOP_KEYS, "");

  if ("projects" in raw) {
    if (!isPlainObject(raw.projects)) {
      throw new ConfigError("swarm.json: projects ожидал объект (slug → настройки)");
    }
    for (const [slug, proj] of Object.entries(raw.projects)) {
      if (!isPlainObject(proj)) {
        throw new ConfigError(`swarm.json: projects.${slug} ожидал объект`);
      }
      validateSettings(proj, SWARM_PROJECT_KEYS, `projects.${slug}.`);
    }
  }

  return raw;
}

export function swarmConfigFor(parsed, slug) {
  const top = parsed || {};
  const proj = (top.projects && top.projects[slug]) || {};

  function pick(key, fallback) {
    if (key in proj) return proj[key];
    if (key in top) return top[key];
    return fallback;
  }

  return {
    integration: pick("integration", null),
    arbiter: pick("arbiter", null),
    integrationTimeout: pick("integration_timeout", DEFAULT_INTEGRATION_TIMEOUT),
    arbiterTimeout: pick("arbiter_timeout", DEFAULT_ARBITER_TIMEOUT),
    verify: pick("verify", null),
    verifyTimeout: pick("verify_timeout", DEFAULT_VERIFY_TIMEOUT),
    verifyRetries: pick("verify_retries", DEFAULT_VERIFY_RETRIES),
    questionTimeout: pick("question_timeout", DEFAULT_QUESTION_TIMEOUT),
  };
}
