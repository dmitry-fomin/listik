#!/usr/bin/env bash
# codex-run.sh — единая точка запуска OpenAI Codex CLI (`codex exec`) из Claude Code.
#
#   codex-run.sh check [--json]
#   codex-run.sh run [опции] < prompt.txt
#   codex-run.sh status [--json] [--all] [--running] [job-id]
#   codex-run.sh result <job-id> [--wait [сек]]
#   codex-run.sh logs <job-id> [--tail N]
#   codex-run.sh cancel <job-id|--all>
#   codex-run.sh clean [--older-than <дней>] [--all]
#   codex-run.sh transcript [job-id]
#   codex-run.sh resume <job-id> [опции] < prompt.txt
#
# Инвариант, на котором держится вся обвязка: в stdout подкоманд `run`
# (foreground) и `result` попадает РОВНО финальный ответ codex и ничего больше.
# Всё служебное — прогресс, диагностика, ошибки запуска — идёт в stderr или в
# файлы джобы. Вызывающий может отдавать этот stdout пользователю дословно.
#
# Фоновая джоба — самостоятельная сущность с собственным идентификатором:
# её можно опрашивать (`status`, `logs`), забирать (`result`), убивать
# (`cancel`). Прогон переживает завершение вызвавшего его Bash-инструмента,
# поэтому долгие задачи не упираются в его десятиминутный потолок.
#
# --- Отличия от dsh-run.sh (аналога для DeepSeek Harness) --------------------
#
# 1. `codex exec` сам по себе НЕ фоновый: он блокирует, пока агент не закончит.
#    Весь job-management (job-id, meta-файл, отвязанный воркер, kill_tree,
#    status/result/logs/cancel/clean) обвязка реализует сама — этот код почти
#    не codex-специфичен и перенесён из dsh-run.sh почти без изменений.
#
# 2. Промпт идёт на STDIN, а не позиционным аргументом. `codex exec` умеет то
#    и другое (аргумент, либо `-`, либо стандартный ввод, если аргумент не
#    дан), но именно stdin — штатный способ без ограничений ARG_MAX, поэтому
#    dsh-эквивалентный обход через мелкий argv-лимит (256 КиБ) здесь не нужен
#    вовсе: лимита на размер промпта эта обвязка не ставит.
#
# 3. Чистый ответ агента ловим через `-o/--output-last-message <файл>` —
#    штатный флаг codex, который пишет туда ровно финальное сообщение и
#    ничего больше. У dsh такого флага нет — там ответом считался весь stdout
#    процесса. Собственный stdout/stderr codex (человекочитаемый прогресс —
#    рассуждения, вызовы инструментов) идёт в отдельный файл-журнал и наружу
#    как «ответ» никогда не попадает.
#
# 4. Выбор модели/провайдера/эффорта — не оверлей файла настроек (как у dsh,
#    которому нужно было обходить то, что `--patch` не переопределяет модель),
#    а прямой `-c key=value` поверх ~/.codex/config.toml: у codex `-c`
#    действительно переопределяет то, что просят, накладывать через подмену
#    целого документа настроек не нужно.
#
# 5. `check` не собирает диагностику по кускам (bin/version/profiles/auth),
#    а разбирает готовый отчёт `codex doctor --json` — codex уже считает
#    всё нужное сам.
#
# 6. `transcript` — не zstd-архив по слагу рабочего каталога, а обычный JSONL
#    (`~/.codex/sessions/<год>/<месяц>/<день>/rollout-*.jsonl`), в котором
#    первая строка (`session_meta`) несёт поле `cwd`. Обвязка ищет среди всех
#    сессий самую свежую с подходящим `cwd` — специального инструмента для
#    распаковки не нужно вовсе.
set -euo pipefail

STATE_DIR="${CODEX_CLAUDE_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/codex-claude}"
JOBS_DIR="$STATE_DIR/jobs"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
# Foreground держим заметно ниже потолка Bash-инструмента (600с): скрипт
# должен успеть вернуть осмысленную ошибку раньше, чем его оборвут снаружи.
DEFAULT_TIMEOUT=540
# Фону этот потолок не писан — он живёт вне вызова Bash. Два часа с запасом
# на большую задачу; 0 отключает лимит совсем.
DEFAULT_BG_TIMEOUT=7200
# Сессия Claude Code, из которой запущена джоба. В окружении Bash-инструмента
# CLAUDE_SESSION_ID не появляется, поэтому полагаться на неё как на
# единственную границу нельзя — она уточняет фильтр, когда её всё же передали.
SESSION_ID="${CODEX_CLAUDE_SESSION:-${CLAUDE_SESSION_ID:-}}"
# Id сессии Codex CLI (UUID из session_meta), не путать с SESSION_ID выше —
# тот тегирует джобы сессией Claude Code. Resume читает только это поле.
RESUME_SID=""
RESUME_FROM=""

# Пути, которые надо убрать при любом выходе. die() уходит через exit, поэтому
# уборка в конце функции недостижима — только trap.
CLEANUP_PATHS=()
cleanup() {
  local rc=$?
  [[ ${#CLEANUP_PATHS[@]} -eq 0 ]] || rm -rf "${CLEANUP_PATHS[@]}"
  exit "$rc"
}
trap cleanup EXIT

# Значение поля meta: ключи разбираем по ПЕРВОМУ "=", иначе путь со знаком
# равенства в имени обрезается на нём.
meta_get() {
  local key="$1" file="$2"
  [[ -f "$file" ]] || return 0
  awk -v k="$key" 'index($0, k "=")==1 { print substr($0, length(k)+2); exit }' "$file"
}

# Перезапись поля meta без sed -i: значение может содержать слэши и & .
# Запись — read-modify-write по всему файлу, поэтому двум писателям нужен
# взаимный замок: без него параллельные meta_set теряют целые наборы полей
# (воспроизводится стабильно). mkdir атомарен на любой файловой системе.
meta_set() {
  local key="$1" value="$2" file="$3"
  local lock="$file.lock" i=0
  while ! mkdir "$lock" 2>/dev/null; do
    i=$((i+1))
    # Замок мёртвого процесса не должен вешать всех остальных навсегда.
    [[ $i -ge 50 ]] && { rm -rf "$lock"; continue; }
    sleep 0.1
  done
  # $$ в субшелле равен pid родителя, поэтому воркер и вызывающий процесс
  # получили бы один и тот же временный файл. BASHPID тут не помощник:
  # в bash 3.2 (системный на macOS) его нет.
  local tmp; tmp="$(mktemp "$file.tmp.XXXXXX")"
  if awk -v k="$key" -v v="$value" '
    index($0, k "=")==1 { if (!done) { print k "=" v; done=1 } ; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$file" > "$tmp"; then mv "$tmp" "$file"; else rm -f "$tmp"; fi
  rmdir "$lock" 2>/dev/null || true
}

# Экранирование значения для строки JSON.
json_escape() {
  local v="$1"
  v="${v//\\/\\\\}"
  v="${v//\"/\\\"}"
  v="${v//$'\n'/\\n}"
  v="${v//$'\t'/\\t}"
  v="${v//$'\r'/\\r}"
  printf '%s' "$v"
}

# `wc -c < file` печатает ошибку редиректа раньше, чем сработает 2>/dev/null,
# и отдаёт пустую строку там, где ждут число.
file_bytes() {
  local f="$1"
  [[ -s "$f" ]] || { echo 0; return 0; }
  wc -c "$f" 2>/dev/null | awk '{print $1; exit}'
}

# Значение опции, начинающееся с дефиса, — почти всегда забытый аргумент:
# `--model --write` иначе молча уедет в имя модели, а --write не применится.
need_value() {
  local opt="$1" val="${2:-}"
  [[ -n "$val" && "$val" != -* ]] || die 2 "$opt needs a value"
  printf '%s' "$val"
}

die() {
  local code="$1"; shift
  echo "error: $*" >&2
  exit "$code"
}

usage() {
  cat >&2 <<'USAGE'
usage:
  codex-run.sh check [--json]
  codex-run.sh run [--permission read|bash|write] [--model <id>] [--effort <level>]
                    [--provider <route>] [--cwd <dir>] [--timeout <sec>]
                    [--background] [--label <text>]
                    < prompt.txt
  codex-run.sh status [--json] [--all] [--running] [job-id]
  codex-run.sh result <job-id> [--wait [sec]]
  codex-run.sh logs <job-id> [--tail <lines>]
  codex-run.sh cancel <job-id|--all>
  codex-run.sh clean [--older-than <days>] [--all]
  codex-run.sh transcript [job-id]
  codex-run.sh resume <job-id> [--background] [--timeout <sec>] [--label <text>]
                    < prompt.txt

--permission: read (default) and bash both map to the codex sandbox -s read-only
  (commands run, writes are denied); write maps to -s workspace-write. --write is
  kept as an alias for --permission write.
USAGE
  exit 2
}

# --- поиск бинаря -----------------------------------------------------------
# Порядок: явный CODEX_BIN → PATH. Абсолютный путь важен для фоновых запусков:
# отвязанный процесс не наследует изменения PATH, сделанные после старта.
resolve_codex() {
  local bin="${CODEX_BIN:-}"
  if [[ -n "$bin" ]]; then
    command -v "$bin" >/dev/null 2>&1 || die 2 "CODEX_BIN points at '$bin', which is not an executable"
    command -v "$bin"
    return 0
  fi
  command -v codex >/dev/null 2>&1 \
    || die 2 "codex not found in PATH - install OpenAI Codex CLI or set CODEX_BIN"
  command -v codex
}

# --- режим прав -------------------------------------------------------------
# --permission <read|bash|write> — единый флаг; --write остаётся синонимом
# --permission write: его уже шлют маршруты Listik и пресеты конвейера.
# read и bash дают одну и ту же песочницу: у codex она файловая, а не
# по-инструментная — в read-only команды выполняются, но запись запрещена,
# отдельного «запрета bash» у codex exec нет.
mode_from_permission() {
  case "$1" in
    read|read-only|bash) printf 'read-only' ;;
    write|workspace-write) printf 'workspace-write' ;;
    *) die 2 "invalid --permission '$1' - allowed values: read, bash, write" ;;
  esac
}

mode_label() {
  case "$1" in
    workspace-write) echo "workspace-write (edits inside --cwd allowed)" ;;
    *)               echo "read-only (commands run, writes denied)" ;;
  esac
}

pick_timeout_bin() {
  # coreutils timeout не гарантирован на macOS. Без него запускаем без лимита:
  # у foreground-вызова лимит всё равно ставит вызывающий Bash-инструмент.
  if command -v timeout >/dev/null 2>&1; then echo timeout
  elif command -v gtimeout >/dev/null 2>&1; then echo gtimeout
  else echo ""; fi
}

# --- разбор `codex doctor --json` --------------------------------------------
# jq — предпочтительный путь; python3 — фолбэк почти на любой машине; grep —
# последний рубеж, если нет ни того ни другого (даёт только часть полей).
# Печатает key=value построчно, тем же форматом, что и meta-файлы.
doctor_parse() {
  local json="$1"
  if [[ -z "$json" ]]; then return 0; fi
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$json" | jq -r '
      "overall_status=" + (.overallStatus // "unknown"),
      "auth_status=" + (.checks["auth.credentials"].status // "unknown"),
      "auth_summary=" + (.checks["auth.credentials"].summary // ""),
      "auth_env_var=" + (.checks["auth.credentials"].details["provider auth env var"] // ""),
      "codex_home=" + (.checks["config.load"].details["CODEX_HOME"] // ""),
      "config_path=" + (.checks["config.load"].details["config.toml"] // ""),
      "config_model=" + (.checks["config.load"].details["model"] // ""),
      "config_provider=" + (.checks["config.load"].details["model provider"] // ""),
      "app_server_status=" + (.checks["app_server.status"].status // "unknown")
    ' 2>/dev/null
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$json" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
checks = d.get("checks", {})
auth = checks.get("auth.credentials", {})
auth_d = auth.get("details", {}) or {}
cfg = checks.get("config.load", {}).get("details", {}) or {}
app = checks.get("app_server.status", {})
print("overall_status=" + str(d.get("overallStatus", "unknown")))
print("auth_status=" + str(auth.get("status", "unknown")))
print("auth_summary=" + str(auth.get("summary", "")))
print("auth_env_var=" + str(auth_d.get("provider auth env var", "")))
print("codex_home=" + str(cfg.get("CODEX_HOME", "")))
print("config_path=" + str(cfg.get("config.toml", "")))
print("config_model=" + str(cfg.get("model", "")))
print("config_provider=" + str(cfg.get("model provider", "")))
print("app_server_status=" + str(app.get("status", "unknown")))
' 2>/dev/null
    return 0
  fi
  # Без jq и python3 — грубый разбор двух самых важных полей регуляркой.
  printf '%s' "$json" | grep -o '"overallStatus":"[^"]*"' | head -1 | sed 's/"overallStatus":"/overall_status=/; s/"$//'
  printf '%s' "$json" | grep -o '"status":"[^"]*"' | head -1 | sed 's/"status":"/auth_status=/; s/"$//'
}

# --- check ------------------------------------------------------------------
cmd_check() {
  local as_json=0
  case "${1:-}" in
    --json)    as_json=1 ;;
    -h|--help) usage ;;
    "")        ;;
    *)         die 2 "unknown option '$1'" ;;
  esac

  local bin="" bin_status="missing" version=""
  if [[ -n "${CODEX_BIN:-}" ]] && command -v "${CODEX_BIN}" >/dev/null 2>&1; then
    bin="$(command -v "${CODEX_BIN}")"
  elif command -v codex >/dev/null 2>&1; then
    bin="$(command -v codex)"
  fi

  if [[ -n "$bin" ]]; then
    if version="$("$bin" --version 2>/dev/null)"; then
      bin_status="ok"
    else
      bin_status="broken"
      version=""
    fi
  fi

  local doctor_json="" overall_status="unknown" auth_status="unknown" auth_summary="" \
        auth_env_var="" codex_home="" config_path="" config_model="" config_provider="" \
        app_server_status="unknown"
  if [[ "$bin_status" == "ok" ]]; then
    doctor_json="$("$bin" doctor --json 2>/dev/null)" || doctor_json=""
  fi
  if [[ -n "$doctor_json" ]]; then
    local line k v
    while IFS= read -r line; do
      k="${line%%=*}"; v="${line#*=}"
      case "$k" in
        overall_status)     overall_status="$v" ;;
        auth_status)         auth_status="$v" ;;
        auth_summary)         auth_summary="$v" ;;
        auth_env_var)         auth_env_var="$v" ;;
        codex_home)         codex_home="$v" ;;
        config_path)         config_path="$v" ;;
        config_model)         config_model="$v" ;;
        config_provider)     config_provider="$v" ;;
        app_server_status)     app_server_status="$v" ;;
      esac
    done <<< "$(doctor_parse "$doctor_json")"
  fi

  local running=0
  running="$(count_running_jobs)"

  local ready="no"
  [[ "$bin_status" == "ok" && "$overall_status" == "ok" ]] && ready="yes"

  if [[ $as_json -eq 1 ]]; then
    printf '{"ready":"%s","binary":"%s","binary_status":"%s","version":"%s","overall_status":"%s","auth_status":"%s","auth_summary":"%s","auth_env_var":"%s","model":"%s","provider":"%s","app_server_status":"%s","codex_home":"%s","config_path":"%s","running_jobs":%s}\n' \
      "$(json_escape "$ready")" "$(json_escape "$bin")" "$(json_escape "$bin_status")" \
      "$(json_escape "$version")" "$(json_escape "$overall_status")" "$(json_escape "$auth_status")" \
      "$(json_escape "$auth_summary")" "$(json_escape "$auth_env_var")" \
      "$(json_escape "$config_model")" "$(json_escape "$config_provider")" \
      "$(json_escape "$app_server_status")" \
      "$(json_escape "${codex_home:-$CODEX_HOME_DIR}")" "$(json_escape "$config_path")" "$running"
  else
    echo "ready:            $ready"
    echo "binary:           ${bin:-not found} ($bin_status)"
    echo "version:          ${version:--}"
    echo "codex doctor:     $overall_status"
    echo "model:            ${config_provider:--} / ${config_model:--}"
    echo "app-server:       $app_server_status"
    echo "credentials:      ${auth_status} (${auth_summary:--})"
    [[ -n "$auth_env_var" ]] && echo "key env var:      $auth_env_var"
    echo "default permission: $(mode_label read-only)"
    echo "background jobs running: $running"
    echo "CODEX_HOME:       ${codex_home:-$CODEX_HOME_DIR}"
    [[ -n "$config_path" ]] && echo "config.toml:      $config_path"
  fi
  [[ "$ready" == "yes" ]] || exit 1
}

count_running_jobs() {
  local n=0 dir
  [[ -d "$JOBS_DIR" ]] || { echo 0; return 0; }
  for dir in "$JOBS_DIR"/*/; do
    [[ -f "$dir/meta" ]] || continue
    # По meta нельзя: осиротевшая джоба навсегда осталась бы «в работе».
    [[ "$(job_status_of "${dir%/}")" == "running" ]] && n=$((n+1))
  done
  echo "$n"
}

# --- run --------------------------------------------------------------------
cmd_run() {
  local mode="read-only" model="" effort="" provider_opt="" workdir="" timeout_s="" background=0 label=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --permission) mode="$(mode_from_permission "$(need_value --permission "${2:-}")")"; shift 2 ;;
      --write)      mode="workspace-write"; shift ;;
      --model)      model="$(need_value --model "${2:-}")"; shift 2 ;;
      --provider)   provider_opt="$(need_value --provider "${2:-}")"; shift 2 ;;
      --effort)     effort="$(need_value --effort "${2:-}")"; shift 2 ;;
      --cwd)        workdir="$(need_value --cwd "${2:-}")"; shift 2 ;;
      --timeout)    timeout_s="${2:-}"; [[ -z "$timeout_s" ]] && die 2 "--timeout needs a value"; shift 2 ;;
      --label)      label="${2:-}"; [[ -z "$label" ]] && die 2 "--label needs a value"; shift 2 ;;
      --background) background=1; shift ;;
      -h|--help)    usage ;;
      *)            die 2 "unknown option '$1' (the prompt goes on stdin, not as an argument)" ;;
    esac
  done
  # --effort не валидируется по фиксированному списку: допустимые значения
  # model_reasoning_effort зависят от модели (в каталоге codex встречаются
  # minimal/low/medium/high/xhigh/max/ultra в разных сочетаниях) — жёсткий
  # список здесь означал бы либо неверный отказ, либо молчаливо устаревший.

  # Потолок зависит от режима: у фона нет внешнего ограничителя, у foreground
  # он есть, и там дефолт специально ниже.
  if [[ -z "$timeout_s" ]]; then
    if [[ $background -eq 1 ]]; then timeout_s="$DEFAULT_BG_TIMEOUT"; else timeout_s="$DEFAULT_TIMEOUT"; fi
  fi
  [[ "$timeout_s" =~ ^[0-9]+$ ]] || die 2 "--timeout takes a whole number of seconds (0 = no limit)"

  local prompt
  prompt="$(cat)"
  [[ -z "${prompt//[[:space:]]/}" ]] && die 2 "empty prompt on stdin"

  workdir="${workdir:-$PWD}"
  [[ -d "$workdir" ]] || die 2 "directory '$workdir' does not exist"
  workdir="$(cd "$workdir" && pwd)"

  local bin; bin="$(resolve_codex)"

  # --skip-git-repo-check всегда: обвязка не требует, чтобы рабочий каталог
  # был git-репозиторием (dsh такого требования тоже не ставит).
  local args=(--skip-git-repo-check -s "$mode" -C "$workdir")
  [[ -n "$model" ]] && args+=(-m "$model")
  # -c ждёт TOML-значение; кавычки делают строку валидным TOML-строковым
  # литералом. Без них bare-слово тоже сработало бы (codex откатывается на
  # литерал, если TOML не парсится), но так однозначнее и совпадает с
  # примером из --help.
  [[ -n "$effort" ]] && args+=(-c "model_reasoning_effort=\"$effort\"")
  [[ -n "$provider_opt" ]] && args+=(-c "model_provider=\"$provider_opt\"")

  if [[ $background -eq 1 ]]; then
    run_background "$bin" "$mode" "$workdir" "$timeout_s" "$prompt" \
      "$model" "$effort" "$provider_opt" "$label" "${args[@]}"
    return 0
  fi

  run_foreground "$bin" "$workdir" "$timeout_s" "$prompt" "${args[@]}"
}

run_foreground() {
  local bin="$1" workdir="$2" timeout_s="$3" prompt="$4"; shift 4
  local args=("$@")
  local tb; tb="$(pick_timeout_bin)"
  local out_file err_file rc=0
  out_file="$(mktemp "${TMPDIR:-/tmp}/codex-out.XXXXXX")"
  err_file="$(mktemp "${TMPDIR:-/tmp}/codex-stderr.XXXXXX")"
  CLEANUP_PATHS+=("$out_file" "$err_file")

  # Ответ агента ловим через -o в отдельный файл — это и есть "чистый ответ".
  # Собственный stdout/stderr codex (прогресс, рассуждения, вызовы
  # инструментов) уходит в err_file и наружу как ответ никогда не идёт —
  # тот же принцип разведения потоков, что и в dsh-run.sh, только здесь его
  # обеспечивает не перенаправление, а выделенный флаг CLI.
  local cmd=("$bin" exec "${args[@]}")
  if [[ -n "${RESUME_SID:-}" ]]; then
    cmd+=(resume "$RESUME_SID" -)
  fi
  cmd+=(-o "$out_file")
  if [[ -n "$tb" ]]; then
    (cd "$workdir" && printf '%s' "$prompt" | "$tb" "$timeout_s" "${cmd[@]}") >"$err_file" 2>&1 || rc=$?
  else
    (cd "$workdir" && printf '%s' "$prompt" | "${cmd[@]}") >"$err_file" 2>&1 || rc=$?
  fi

  local err_text=""
  [[ -s "$err_file" ]] && err_text="$(tail -c 500 "$err_file")"

  if [[ $rc -eq 124 ]]; then
    [[ -s "$out_file" ]] && cat "$out_file"
    die 6 "codex: timed out after ${timeout_s}s - the task is too big for one foreground run; rerun with --background to lift the ceiling"
  fi
  if [[ $rc -ne 0 ]]; then
    # Содержательный кусок ответа, если он успел появиться, всё равно отдаём:
    # он полезнее кода возврата. Причина отказа идёт в stderr, к die.
    [[ -s "$out_file" ]] && cat "$out_file"
    die 6 "codex: run exited with code $rc${err_text:+ - $err_text}"
  fi
  [[ -s "$out_file" ]] || die 6 "codex: empty answer - check readiness with: codex-run.sh check${err_text:+. stderr: $err_text}"
  cat "$out_file"
}

# Идентификатор задачи — он же имя её каталога, поэтому он же и замок: имя
# захватывается атомарным mkdir, занятое просто отбрасывается и берётся
# следующее. Одних date и $$ мало — они совпадают у двух фоновых запусков из
# одного процесса-скрипта в одну секунду, и вторая задача затёрла бы первой
# prompt, output и meta. Захват снимает вопрос о достаточности энтропии
# вообще: уникальность обеспечивает файловая система, а не удачный суффикс.
claim_job_dir() {
  local stamp id dir i=0
  stamp="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$JOBS_DIR"
  while :; do
    id="codex-$stamp-$$-$RANDOM"
    dir="$JOBS_DIR/$id"
    mkdir "$dir" 2>/dev/null && { printf '%s' "$id"; return 0; }
    i=$((i+1))
    [[ $i -ge 100 ]] && die 5 "could not allocate a job id in $JOBS_DIR"
  done
}

run_background() {
  local bin="$1" mode="$2" workdir="$3" timeout_s="$4" prompt="$5"
  local model="$6" effort="$7" provider="$8" label="$9"; shift 9
  local args=("$@")
  local job_id job_dir
  job_id="$(claim_job_dir)"
  job_dir="$JOBS_DIR/$job_id"
  chmod 700 "$STATE_DIR" "$JOBS_DIR" 2>/dev/null || true
  chmod 700 "$job_dir"

  printf '%s' "$prompt" > "$job_dir/prompt.txt"
  : > "$job_dir/output.txt"
  : > "$job_dir/stderr.txt"
  {
    echo "id=$job_id"
    echo "status=running"
    echo "cwd=$workdir"
    echo "mode=$mode"
    echo "model=${model:-—}"
    echo "provider=${provider:-—}"
    echo "effort=${effort:-—}"
    echo "label=${label:-—}"
    echo "session=${SESSION_ID:-—}"
    echo "codex_session=${RESUME_SID:-—}"
    echo "resumed_from=${RESUME_FROM:-—}"
    echo "timeout=$(if [[ -n "$(pick_timeout_bin)" ]]; then echo "$timeout_s"; else echo "none (no coreutils timeout)"; fi)"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "started_epoch=$(date +%s)"
  } > "$job_dir/meta"

  # Отвязанный воркер: он же проставляет итог. codex запускается фоном
  # внутри воркера, чтобы его pid попал в meta — без этого джобу нечем убить:
  # kill воркера оставил бы харнесс работать сиротой. Своя процесс-группа:
  # без неё сигнал, посланный по группе вызывающей оболочки (таймаут или
  # прерывание Claude Code), уносит и воркер, и харнесс.
  set -m
  (
    local tb rc=0 inner sid cmd
    # Закрытие терминала или выход вызвавшей сессии не должны уносить прогон:
    # ради этого джоба и делалась фоновой.
    trap '' HUP INT TERM
    # Пишет в meta только воркер — параллельная запись из родителя уже
    # приводила к потере статуса и осиротевшим задачам (см. dsh-run.sh).
    # Свой pid субшелл в bash 3.2 иначе не узнаёт: $$ там принадлежит родителю.
    meta_set worker_pid "$(sh -c 'echo $PPID')" "$job_dir/meta"
    tb="$(pick_timeout_bin)"
    cd "$workdir" || exit 1
    cmd=("$bin" exec "${args[@]}")
    if [[ -n "${RESUME_SID:-}" ]]; then
      cmd+=(resume "$RESUME_SID" -)
    fi
    cmd+=(-o "$job_dir/output.txt")
    if [[ -n "$tb" ]]; then
      cat "$job_dir/prompt.txt" | "$tb" "$timeout_s" "${cmd[@]}" \
        > "$job_dir/stderr.txt" 2>&1 &
    else
      cat "$job_dir/prompt.txt" | "${cmd[@]}" \
        > "$job_dir/stderr.txt" 2>&1 &
    fi
    inner=$!
    meta_set pid "$inner" "$job_dir/meta"
    wait "$inner" || rc=$?

    local final="completed"
    [[ $rc -eq 124 ]] && final="timeout"
    [[ $rc -ne 0 && $rc -ne 124 ]] && final="failed"
    # Маркер отмены ставит cancel ДО убийства процесса: иначе гонка выдала бы
    # снятую вручную задачу за упавшую.
    [[ -f "$job_dir/canceled" ]] && final="canceled"
    # Time окончания пишем ПЕРВЫМ: без него elapsed_of считает от «сейчас», и
    # давно мёртвая задача показывает растущее время работы.
    meta_set finished_epoch "$(date +%s)" "$job_dir/meta"
    meta_set finished "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$job_dir/meta"
    meta_set exit "$rc" "$job_dir/meta"
    meta_set status "$final" "$job_dir/meta"
    if [[ -z "${RESUME_SID:-}" || "${RESUME_SID}" == "—" ]]; then
      sid="$(discover_codex_session "$job_dir")"
      [[ -n "$sid" ]] && meta_set codex_session "$sid" "$job_dir/meta"
    fi
  ) >/dev/null 2>&1 &
  disown 2>/dev/null || true
  set +m

  echo "$job_id"
}

# --- общее для работы с джобами --------------------------------------------
job_dir_of() {
  local job_id="$1"
  [[ -n "$job_id" ]] || die 2 "a job-id is required (list them with: codex-run.sh status)"
  # Идентификатор идёт в путь, поэтому его форма проверяется строго: иначе
  # `result ../../что-то` читает и переписывает каталоги вне JOBS_DIR.
  case "$job_id" in
    */*|*..*) die 2 "invalid job-id '$job_id'" ;;
  esac
  local dir="$JOBS_DIR/$job_id"
  [[ -d "$dir" ]] || die 2 "no job with id '$job_id' (list them with: codex-run.sh status --all)"
  echo "$dir"
}

# Живость процесса — единственный способ отличить работающую джобу от той,
# чей воркер убили извне (перезагрузка, kill -9): meta в таком случае навсегда
# осталась бы в состоянии running.
job_status_of() {
  local dir="$1"
  local st pid
  st="$(meta_get status "$dir/meta")"
  [[ "$st" != "running" ]] && { echo "$st"; return 0; }
  pid="$(meta_get pid "$dir/meta")"
  if [[ -n "$pid" && "$pid" != "—" ]] && kill -0 "$pid" 2>/dev/null; then
    # Одного kill -0 мало: pid переиспользуются, и старая meta после
    # перезагрузки показывала бы вечный running, а cancel бил бы по чужому
    # процессу. Сверяем родство с воркером — по имени бинаря сверять нельзя,
    # CODEX_BIN может называться как угодно.
    local wp ppid
    wp="$(meta_get worker_pid "$dir/meta")"
    if [[ -n "$wp" && "$wp" != "—" ]]; then
      ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
      if [[ "$ppid" == "$wp" ]]; then echo running; return 0; fi
      echo orphaned
      return 0
    fi
    echo running
    return 0
  fi
  # pid ещё не проставлен — воркер стартует, это доли секунды. Минуты без
  # pid означают, что воркер не поднялся вообще.
  if [[ -z "$pid" || "$pid" == "—" ]]; then
    local wp start
    wp="$(meta_get worker_pid "$dir/meta")"
    if [[ -n "$wp" ]] && kill -0 "$wp" 2>/dev/null; then echo running; return 0; fi
    start="$(meta_get started_epoch "$dir/meta")"
    if [[ -n "$start" ]] && (( $(date +%s) - start < 60 )); then echo running; return 0; fi
  fi
  echo orphaned
}

# «Своя» задача — запущенная из этого рабочего каталога или его поддерева.
# Сессия уточняет ответ, когда её идентификатор передан через CODEX_CLAUDE_SESSION;
# сам Claude Code в окружении Bash-инструмента его не отдаёт.
job_is_mine() {
  local dir="$1"
  if [[ -n "$SESSION_ID" ]]; then
    local js; js="$(meta_get session "$dir/meta")"
    [[ -n "$js" && "$js" != "—" ]] && { [[ "$js" == "$SESSION_ID" ]]; return $?; }
  fi
  local jc; jc="$(meta_get cwd "$dir/meta")"
  [[ -n "$jc" ]] || return 1
  [[ "$jc" == "$PWD" || "$jc" == "$PWD"/* || "$PWD" == "$jc"/* ]]
}

elapsed_of() {
  local dir="$1"
  local start end now
  start="$(meta_get started_epoch "$dir/meta")"
  [[ -n "$start" ]] || { echo "—"; return 0; }
  end="$(meta_get finished_epoch "$dir/meta")"
  now="${end:-$(date +%s)}"
  local s=$(( now - start ))
  printf '%dm%02ds' $(( s / 60 )) $(( s % 60 ))
}

# --- status -----------------------------------------------------------------
cmd_status() {
  local as_json=0 all=0 only_running=0 job_id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)    as_json=1; shift ;;
      --all)     all=1; shift ;;
      --running) only_running=1; shift ;;
      -h|--help) usage ;;
      -*)        die 2 "unknown option '$1'" ;;
      *)         job_id="$1"; shift ;;
    esac
  done

  if [[ -n "$job_id" ]]; then
    local dir; dir="$(job_dir_of "$job_id")"
    local st; st="$(job_status_of "$dir")"
    if [[ $as_json -eq 1 ]]; then
      job_json "$dir" "$st"
    else
      cat "$dir/meta"
      echo "actual_status=$st"
      echo "elapsed=$(elapsed_of "$dir")"
      echo "output_bytes=$(file_bytes "$dir/output.txt")"
    fi
    return 0
  fi

  [[ -d "$JOBS_DIR" ]] || { echo "no background jobs" >&2; [[ $as_json -eq 1 ]] && echo '[]'; exit 1; }

  local ids=() dir name
  for dir in $(ls -1t "$JOBS_DIR" 2>/dev/null); do
    name="$JOBS_DIR/$dir"
    [[ -f "$name/meta" ]] || continue
    # По умолчанию показываем свои задачи: список общий на машину, и чужие
    # прогоны легко принять за свои. --all снимает фильтр.
    if [[ $all -eq 0 ]]; then
      job_is_mine "$name" || continue
    fi
    ids+=("$dir")
  done

  local shown=0 out=""
  for dir in "${ids[@]:-}"; do
    [[ -n "$dir" ]] || continue
    local d="$JOBS_DIR/$dir" st
    st="$(job_status_of "$d")"
    [[ $only_running -eq 1 && "$st" != "running" ]] && continue
    shown=$((shown+1))
    if [[ $as_json -eq 1 ]]; then
      out+="$(job_json "$d" "$st"),"
    else
      # 34 — ширина идентификатора со случайным суффиксом; уже него колонки
      # разъезжаются, как только в списке окажется задача нового формата.
      printf '%-34s %-10s %-8s %-18s %s\n' \
        "$dir" "$st" "$(elapsed_of "$d")" \
        "$(meta_get model "$d/meta")" \
        "$(meta_get label "$d/meta")" || exit 0
    fi
    if [[ $shown -ge 30 ]]; then
      [[ $as_json -eq 1 ]] || echo "... first 30 shown; the rest are in status --all" >&2
      break
    fi
  done

  if [[ $as_json -eq 1 ]]; then
    printf '[%s]\n' "${out%,}"
    return 0
  fi
  if [[ $shown -eq 0 ]]; then
    if [[ $all -eq 0 ]]; then
      echo "no background jobs here (every job on this machine: status --all)" >&2
    else
      echo "no background jobs" >&2
    fi
    exit 1
  fi
}

job_json() {
  local dir="$1" st="$2"
  printf '{"id":"%s","status":"%s","meta_status":"%s","label":"%s","cwd":"%s","mode":"%s","model":"%s","provider":"%s","effort":"%s","session":"%s","codex_session":"%s","resumed_from":"%s","started":"%s","elapsed":"%s","timeout":"%s","exit":"%s","output_bytes":%s}' \
    "$(json_escape "$(meta_get id "$dir/meta")")" \
    "$(json_escape "$st")" \
    "$(json_escape "$(meta_get status "$dir/meta")")" \
    "$(json_escape "$(meta_get label "$dir/meta")")" \
    "$(json_escape "$(meta_get cwd "$dir/meta")")" \
    "$(json_escape "$(meta_get mode "$dir/meta")")" \
    "$(json_escape "$(meta_get model "$dir/meta")")" \
    "$(json_escape "$(meta_get provider "$dir/meta")")" \
    "$(json_escape "$(meta_get effort "$dir/meta")")" \
    "$(json_escape "$(meta_get session "$dir/meta")")" \
    "$(json_escape "$(meta_get codex_session "$dir/meta")")" \
    "$(json_escape "$(meta_get resumed_from "$dir/meta")")" \
    "$(json_escape "$(meta_get started "$dir/meta")")" \
    "$(json_escape "$(elapsed_of "$dir")")" \
    "$(json_escape "$(meta_get timeout "$dir/meta")")" \
    "$(json_escape "$(meta_get exit "$dir/meta")")" \
    "$(file_bytes "$dir/output.txt")"
}

# --- result -----------------------------------------------------------------
cmd_result() {
  local job_id="" wait_s=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --wait)    wait_s="${2:-}"
                 if [[ "$wait_s" =~ ^[0-9]+$ ]]; then shift 2; else wait_s=300; shift; fi ;;
      -h|--help) usage ;;
      -*)        die 2 "unknown option '$1'" ;;
      *)         job_id="$1"; shift ;;
    esac
  done
  local dir; dir="$(job_dir_of "$job_id")"

  local st; st="$(job_status_of "$dir")"
  if [[ "$st" == "running" && $wait_s -gt 0 ]]; then
    # Ожидание — вежливость к вызывающему, а не механизм: держать Bash-вызов
    # дольше пары минут смысла нет, задача на то и фоновая.
    local waited=0
    while [[ "$st" == "running" && $waited -lt $wait_s ]]; do
      sleep 3
      waited=$((waited+3))
      st="$(job_status_of "$dir")"
    done
  fi

  case "$st" in
    running)
      die 5 "job still running ($(elapsed_of "$dir") since $(meta_get started "$dir/meta")); poll later: codex-run.sh status $job_id"
      ;;
    orphaned)
      [[ -s "$dir/output.txt" ]] && cat "$dir/output.txt"
      die 6 "the job worker vanished without recording an outcome (reboot or kill -9); above is whatever got written"
      ;;
  esac

  # Частичный вывод оборванной или упавшей задачи печатаем в stdout ДО die:
  # он и есть самое ценное, что от такой задачи осталось.
  [[ -s "$dir/output.txt" ]] && cat "$dir/output.txt"

  case "$st" in
    timeout)
      die 6 "job hit its timeout ($(meta_get timeout "$dir/meta")s); above is whatever arrived before that"
      ;;
    canceled)
      die 6 "job was cancelled ($(elapsed_of "$dir") of work); above is whatever arrived before that"
      ;;
    failed)
      die 6 "job failed (exit code $(meta_get exit "$dir/meta"))$( [[ -s "$dir/stderr.txt" ]] && printf ' - %s' "$(tail -c 500 "$dir/stderr.txt")" )"
      ;;
  esac

  if [[ ! -s "$dir/output.txt" ]]; then
    [[ -s "$dir/stderr.txt" ]] && tail -c 2000 "$dir/stderr.txt" >&2
    die 6 "empty answer"
  fi
}

# --- logs -------------------------------------------------------------------
# Прогресс живой задачи: собственный stdout/stderr codex (в отличие от dsh,
# codex в человекочитаемом режиме транслирует рассуждения и вызовы
# инструментов по мере работы, поэтому здесь это не только диагностика, но и
# живая картина хода дела). Смешивать это с выводом `result` всё равно
# нельзя — там должен остаться только чистый ответ из -o.
cmd_logs() {
  local job_id="" tail_n=40
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tail)    tail_n="${2:-}"; [[ "$tail_n" =~ ^[0-9]+$ ]] || die 2 "--tail takes a number of lines"; shift 2 ;;
      -h|--help) usage ;;
      -*)        die 2 "unknown option '$1'" ;;
      *)         job_id="$1"; shift ;;
    esac
  done
  local dir; dir="$(job_dir_of "$job_id")"
  local st; st="$(job_status_of "$dir")"

  echo "status:  $st ($(elapsed_of "$dir"))"
  echo "answer:  $(file_bytes "$dir/output.txt") bytes accumulated"
  if [[ -s "$dir/stderr.txt" ]]; then
    echo "--- last $tail_n lines of codex output ---"
    tail -n "$tail_n" "$dir/stderr.txt"
  elif [[ "$st" == "running" ]]; then
    echo "codex output is still empty - normal in the first seconds of a run;"
    echo "the signs of work are the running status and the growing elapsed time."
  else
    echo "codex output is empty"
  fi
}

# --- cancel -----------------------------------------------------------------
# Убиваем всё дерево: codex — процесс, который порождает подпроцессы (shell-
# команды модели, тесты, инструменты). kill только по верхнему pid оставил бы
# их жить.
kill_tree() {
  local pid="$1" sig="${2:-TERM}" child
  [[ -n "$pid" && "$pid" != "—" ]] || return 0
  # Пока перечисляем детей, живой родитель успевает породить новых. STOP
  # замораживает его на время обхода; CONT нужен, чтобы он смог обработать
  # сигнал и умереть.
  kill -STOP "$pid" 2>/dev/null || true
  if command -v pgrep >/dev/null 2>&1; then
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do kill_tree "$child" "$sig"; done
  fi
  kill -"$sig" "$pid" 2>/dev/null || true
  kill -CONT "$pid" 2>/dev/null || true
}

cancel_one() {
  local dir="$1"
  local job_id; job_id="$(meta_get id "$dir/meta")"
  local st; st="$(job_status_of "$dir")"
  if [[ "$st" != "running" ]]; then
    echo "$job_id: already $st, nothing to cancel"
    return 0
  fi

  # Маркер ставим первым: воркер прочитает его при финализации и запишет
  # canceled вместо failed.
  : > "$dir/canceled"
  kill_tree "$(meta_get pid "$dir/meta")" TERM

  # Даём воркеру дописать итог; если codex не реагирует на TERM — добиваем
  # его, но НЕ воркер: воркер в этот момент пишет meta, и KILL посреди записи
  # оставлял задачу без finished_epoch.
  local waited=0
  while [[ $waited -lt 10 ]]; do
    sleep 1; waited=$((waited+1))
    [[ "$(meta_get status "$dir/meta")" == "running" ]] || break
  done
  if [[ "$(meta_get status "$dir/meta")" == "running" ]]; then
    kill_tree "$(meta_get pid "$dir/meta")" KILL
    waited=0
    while [[ $waited -lt 5 ]]; do
      sleep 1; waited=$((waited+1))
      [[ "$(meta_get status "$dir/meta")" == "running" ]] || break
    done
  fi

  # Итог проставляем сами, только если воркера уже нет: иначе это второй
  # писатель в meta и потерянные поля.
  local wp; wp="$(meta_get worker_pid "$dir/meta")"
  if [[ "$(meta_get status "$dir/meta")" == "running" ]] \
     && { [[ -z "$wp" || "$wp" == "—" ]] || ! kill -0 "$wp" 2>/dev/null; }; then
    meta_set finished_epoch "$(date +%s)" "$dir/meta"
    meta_set finished "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$dir/meta"
    meta_set status canceled "$dir/meta"
  fi

  rm -f "$dir/canceled"
  local final; final="$(job_status_of "$dir")"
  case "$final" in
    canceled) echo "$job_id: cancelled ($(elapsed_of "$dir") of work)" ;;
    running)  echo "$job_id: could not cancel - the process does not respond; see status $job_id" ;;
    *)        echo "$job_id: finished on its own before the cancel landed ($final)" ;;
  esac
}

cmd_cancel() {
  local target="${1:-}"
  [[ "$target" == "-h" || "$target" == "--help" ]] && usage
  [[ -n "$target" ]] || die 2 "a job-id or --all is required (list them with: codex-run.sh status)"
  if [[ "$target" == "--all" ]]; then
    local any=0 dir
    [[ -d "$JOBS_DIR" ]] || die 1 "no background jobs"
    for dir in "$JOBS_DIR"/*/; do
      [[ -f "$dir/meta" ]] || continue
      # --all в пределах своих задач: чужие снимать молча нельзя.
      job_is_mine "${dir%/}" || continue
      [[ "$(job_status_of "$dir")" == "running" ]] || continue
      any=1
      cancel_one "${dir%/}"
    done
    [[ $any -eq 1 ]] || { echo "no running jobs" >&2; exit 1; }
    return 0
  fi
  local dir; dir="$(job_dir_of "$target")"
  cancel_one "$dir"
}

# --- clean ------------------------------------------------------------------
# Джобы хранят промпт и ответ открытым текстом и сами не исчезают. Уборка —
# явная команда, потому что удалять чужой результат молча нельзя.
cmd_clean() {
  local days=7 all=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --older-than) days="${2:-}"; [[ "$days" =~ ^[0-9]+$ ]] || die 2 "--older-than takes a number of days"
                    [[ "$days" -eq 0 ]] && all=1
                    shift 2 ;;
      --all)        all=1; shift ;;
      -h|--help)    usage ;;
      *)            die 2 "unknown option '$1'" ;;
    esac
  done
  [[ -d "$JOBS_DIR" ]] || { echo "no background jobs"; return 0; }

  local now removed=0 skipped=0 dir
  now="$(date +%s)"
  for dir in "$JOBS_DIR"/*/; do
    [[ -f "$dir/meta" ]] || continue
    [[ "$(job_status_of "${dir%/}")" == "running" ]] && continue
    # Удаление безвозвратно, поэтому граница та же, что у status: свои задачи —
    # запущенные отсюда. Чужой результат, который ещё никто не забрал, чистить
    # молча нельзя.
    if ! job_is_mine "${dir%/}"; then skipped=$((skipped+1)); continue; fi
    if [[ $all -eq 0 ]]; then
      local fin; fin="$(meta_get finished_epoch "$dir/meta")"
      [[ -n "$fin" ]] || fin="$(meta_get started_epoch "$dir/meta")"
      [[ -n "$fin" ]] || continue
      (( now - fin < days * 86400 )) && continue
    fi
    rm -rf "${dir%/}"
    removed=$((removed+1))
  done
  echo "jobs removed: $removed (running jobs untouched${skipped:+; other owners skipped: $skipped})"
}

# --- transcript -------------------------------------------------------------
# У codex нет каталога сессий по слагу рабочей директории (как у dsh) —
# сессии организованы по дате: ~/.codex/sessions/<год>/<месяц>/<день>/
# rollout-*.jsonl, обычный JSONL без сжатия. Первая строка каждого файла —
# событие session_meta с полями payload.cwd и payload.originator.
#
# Отбор ТОЛЬКО по cwd недостаточен: интерактивные сессии codex TUI (originator
# codex-tui) пишутся в тот же каталог сессий и с тем же cwd, что и прогон
# задачи из того же рабочего каталога. Без job-id различить их нечем в
# принципе — совпадение по cwd там единственный критерий, и вернётся просто
# самая свежая сессия для каталога, о чём предупреждаем в stderr. С job-id
# добавляется временное окно: сессия задачи не может стартовать раньше её
# `started_epoch` и не может стартовать позже `finished_epoch` (плюс запас
# на задержку записи) — этого достаточно, чтобы не подобрать чужую
# интерактивную сессию, запущенную в том же каталоге до или после задачи,
# даже не зная точного значения originator, которое `codex exec` пишет
# (не проверялось вживую — реальный прогон стоит денег).
# rollout-*.jsonl этой задачи: cwd совпал и mtime попал в окно started…finished.
# Пусто, если сессия ещё не записалась или CODEX_HOME другой.
find_codex_rollout() {
  local workdir="$1" since_epoch="${2:-}" until_epoch="${3:-}"
  local sessions_root="$CODEX_HOME_DIR/sessions"
  [[ -d "$sessions_root" ]] || return 0
  local f mt
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    [[ "$(session_meta_cwd "$f")" == "$workdir" ]] || continue
    if [[ -n "$since_epoch" ]]; then
      mt="$(file_mtime_epoch "$f")"
      [[ -n "$mt" ]] || continue
      (( mt >= since_epoch - 2 )) || continue
      [[ -n "$until_epoch" ]] && ! (( mt <= until_epoch + 300 )) && continue
    fi
    printf '%s' "$f"
    return 0
  done < <(find "$sessions_root" -type f -name 'rollout-*.jsonl' -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null)
}

discover_codex_session() {
  local dir="$1" f
  f="$(find_codex_rollout "$(meta_get cwd "$dir/meta")" "$(meta_get started_epoch "$dir/meta")" "$(meta_get finished_epoch "$dir/meta")")"
  [[ -n "$f" ]] || return 0
  session_meta_field "$f" session_id
}

# Продолжить сессию Codex задачи: те же --write/--model/--effort/--cwd, новый промпт.
# Нет id сессии или задача ещё running — код 2, оркестратор откатывается на run.
cmd_resume() {
  local job_id="" background=0 timeout_s="" label=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --background) background=1; shift ;;
      --timeout)    timeout_s="${2:-}"; [[ -z "$timeout_s" ]] && die 2 "--timeout needs a value"; shift 2 ;;
      --label)      label="${2:-}"; [[ -z "$label" ]] && die 2 "--label needs a value"; shift 2 ;;
      -h|--help)    usage ;;
      -*)           die 2 "unknown option '$1' (the prompt goes on stdin, not as an argument)" ;;
      *)            [[ -n "$job_id" ]] && die 2 "unexpected extra argument '$1'"
                    job_id="$1"; shift ;;
    esac
  done
  local dir; dir="$(job_dir_of "$job_id")"
  local st; st="$(job_status_of "$dir")"
  [[ "$st" == "running" ]] && die 2 "job '$job_id' is still running - resume it after it finishes, otherwise fall back to a fresh run"

  local sid
  sid="$(meta_get codex_session "$dir/meta")"
  if [[ -z "$sid" || "$sid" == "—" ]]; then
    sid="$(discover_codex_session "$dir")"
    [[ -n "$sid" ]] && meta_set codex_session "$sid" "$dir/meta"
  fi
  [[ -n "$sid" && "$sid" != "—" ]] || die 2 "job '$job_id' has no Codex session id - fall back to a fresh run (codex-run.sh run)"

  local prompt
  prompt="$(cat)"
  [[ -z "${prompt//[[:space:]]/}" ]] && die 2 "empty prompt on stdin"

  local workdir mode model effort provider
  workdir="$(meta_get cwd "$dir/meta")"
  mode="$(meta_get mode "$dir/meta")"
  model="$(meta_get model "$dir/meta")"
  effort="$(meta_get effort "$dir/meta")"
  provider="$(meta_get provider "$dir/meta")"
  [[ -d "$workdir" ]] || die 2 "directory '$workdir' recorded on job '$job_id' does not exist"

  if [[ -z "$timeout_s" ]]; then
    if [[ $background -eq 1 ]]; then timeout_s="$DEFAULT_BG_TIMEOUT"; else timeout_s="$DEFAULT_TIMEOUT"; fi
  fi
  [[ "$timeout_s" =~ ^[0-9]+$ ]] || die 2 "--timeout takes a whole number of seconds (0 = no limit)"

  local bin; bin="$(resolve_codex)"
  [[ -n "$mode" && "$mode" != "—" ]] || mode="read-only"
  local args=(--skip-git-repo-check -s "$mode" -C "$workdir")
  [[ -n "$model" && "$model" != "—" ]] && args+=(-m "$model")
  [[ -n "$effort" && "$effort" != "—" ]] && args+=(-c "model_reasoning_effort=\"$effort\"")
  [[ -n "$provider" && "$provider" != "—" ]] && args+=(-c "model_provider=\"$provider\"")
  [[ -n "$label" ]] || label="resume $job_id"

  RESUME_SID="$sid"
  RESUME_FROM="$job_id"
  if [[ $background -eq 1 ]]; then
    run_background "$bin" "$mode" "$workdir" "$timeout_s" "$prompt" \
      "$model" "$effort" "$provider" "$label" "${args[@]}"
  else
    run_foreground "$bin" "$workdir" "$timeout_s" "$prompt" "${args[@]}"
  fi
}

cmd_transcript() {
  local job_id="${1:-}" workdir="$PWD"
  [[ "$job_id" == "-h" || "$job_id" == "--help" ]] && usage
  local since_epoch="" until_epoch="" dir=""
  if [[ -n "$job_id" ]]; then
    dir="$(job_dir_of "$job_id")"
    workdir="$(meta_get cwd "$dir/meta")"
    since_epoch="$(meta_get started_epoch "$dir/meta")"
    until_epoch="$(meta_get finished_epoch "$dir/meta")"
  fi

  local sessions_root="$CODEX_HOME_DIR/sessions"
  [[ -d "$sessions_root" ]] || die 2 "no sessions directory: $sessions_root (--ephemeral runs write no session file, but this bridge never uses --ephemeral)"

  local matched="" matched_originator=""
  matched="$(find_codex_rollout "$workdir" "$since_epoch" "$until_epoch")"
  [[ -n "$matched" ]] && matched_originator="$(session_meta_originator "$matched")"

  if [[ -z "$matched" ]]; then
    if [[ -n "$job_id" ]]; then
      die 2 "no codex session found for job '$job_id' (directory '$workdir', window $(meta_get started "$dir/meta")...$(meta_get finished "$dir/meta")) - the run may have written no session (its own CODEX_HOME, or a sub-2s race), or this CODEX_HOME is unreadable"
    fi
    die 2 "no codex session found for directory '$workdir' in $sessions_root (searched every rollout-*.jsonl by the cwd field of session_meta, no match)"
  fi

  if [[ -z "$job_id" ]]; then
    echo "warning: no job-id given - showing the newest session for this directory (originator=${matched_originator:--}); this may be your own interactive codex session rather than a job run" >&2
  fi
  cat "$matched"
}

file_mtime_epoch() {
  local f="$1"
  stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null
}

session_meta_cwd() { session_meta_field "$1" cwd; }
session_meta_originator() { session_meta_field "$1" originator; }

session_meta_field() {
  local f="$1" key="$2" line
  line="$(head -1 "$f" 2>/dev/null)"
  [[ -n "$line" ]] || return 0
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$line" | jq -r --arg k "$key" '.payload[$k] // empty' 2>/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$line" | python3 -c '
import json, sys
key = sys.argv[1]
try:
    d = json.load(sys.stdin)
    print(d.get("payload", {}).get(key, ""))
except Exception:
    pass
' "$key" 2>/dev/null
  else
    printf '%s' "$line" | grep -o "\"$key\":\"[^\"]*\"" | head -1 | sed "s/^\"$key\":\"//; s/\"\$//"
  fi
}

# --- диспетчер --------------------------------------------------------------
[[ $# -eq 0 ]] && usage
sub="$1"; shift
# -h разбирает каждая подкоманда сама: сканировать все аргументы нельзя, иначе
# `run --label -h` печатает справку вместо запуска задачи.
case "$sub" in
  check)      cmd_check "$@" ;;
  run)        cmd_run "$@" ;;
  resume)     cmd_resume "$@" ;;
  status)     cmd_status "$@" ;;
  result)     cmd_result "$@" ;;
  logs)       cmd_logs "$@" ;;
  cancel)     cmd_cancel "$@" ;;
  clean)      cmd_clean "$@" ;;
  transcript) cmd_transcript "$@" ;;
  -h|--help)  usage ;;
  *)          die 2 "unknown subcommand '$sub'" ;;
esac
