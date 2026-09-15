#!/usr/bin/env bash
# dsh-run.sh — единая точка запуска DeepSeek Harness (dsh) из Claude Code.
#
#   dsh-run.sh check [--json]
#   dsh-run.sh run [опции] < prompt.txt
#   dsh-run.sh status [--json] [--all] [--running] [job-id]
#   dsh-run.sh result <job-id> [--wait [сек]]
#   dsh-run.sh logs <job-id> [--tail N]
#   dsh-run.sh cancel <job-id|--all>
#   dsh-run.sh clean [--older-than <дней>] [--all]
#   dsh-run.sh transcript [job-id]
#
# Инвариант, на котором держится вся обвязка: в stdout подкоманд `run`
# (foreground) и `result` попадает РОВНО финальный ответ dsh и ничего больше.
# Всё служебное — прогресс, диагностика, ошибки запуска — идёт в stderr или в
# файлы джобы. Вызывающий может отдавать этот stdout пользователю дословно.
#
# Фоновая джоба — самостоятельная сущность с собственным идентификатором:
# её можно опрашивать (`status`, `logs`), забирать (`result`), убивать
# (`cancel`). Прогон переживает завершение вызвавшего его Bash-инструмента,
# поэтому долгие задачи не упираются в его десятиминутный потолок.
set -euo pipefail

STATE_DIR="${DSH_CLAUDE_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/dsh-claude}"
JOBS_DIR="$STATE_DIR/jobs"
DSH_HOME_DIR="${DSH_HOME:-$HOME/.dsh}"
# Foreground держим заметно ниже потолка Bash-инструмента (600с): скрипт
# должен успеть вернуть осмысленную ошибку раньше, чем его оборвут снаружи.
DEFAULT_TIMEOUT=540
# Фону этот потолок не писан — он живёт вне вызова Bash. Два часа с запасом
# на большую задачу; 0 отключает лимит совсем.
DEFAULT_BG_TIMEOUT=7200
# Сессия Claude Code, из которой запущена джоба. В окружении Bash-инструмента
# CLAUDE_SESSION_ID не появляется, поэтому полагаться на неё как на
# единственную границу нельзя — она уточняет фильтр, когда её всё же передали.
SESSION_ID="${DSH_CLAUDE_SESSION:-${CLAUDE_SESSION_ID:-}}"

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
  [[ -n "$val" && "$val" != -* ]] || die 2 "$opt требует значение"
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
  dsh-run.sh check [--json]
  dsh-run.sh run [--write] [--model pro|flash|vision|<id>] [--effort <level>]
                 [--provider <маршрут>] [--cwd <dir>] [--timeout <сек>]
                 [--background] [--label <текст>]
                 < prompt.txt
  dsh-run.sh status [--json] [--all] [--running] [job-id]
  dsh-run.sh result <job-id> [--wait [сек]]
  dsh-run.sh logs <job-id> [--tail <строк>]
  dsh-run.sh cancel <job-id|--all>
  dsh-run.sh clean [--older-than <дней>] [--all]
  dsh-run.sh transcript [job-id]
USAGE
  exit 2
}

# --- поиск бинаря -----------------------------------------------------------
# Порядок: явный DSH_BIN → PATH. Абсолютный путь важен для фоновых запусков:
# отвязанный процесс не наследует изменения PATH, сделанные после старта.
resolve_dsh() {
  local bin="${DSH_BIN:-}"
  if [[ -n "$bin" ]]; then
    command -v "$bin" >/dev/null 2>&1 || die 2 "DSH_BIN указывает на '$bin', но такого исполняемого файла нет"
    command -v "$bin"
    return 0
  fi
  command -v dsh >/dev/null 2>&1 \
    || die 2 "dsh не найден в PATH — установи DeepSeek Harness или укажи путь через переменную DSH_BIN"
  command -v dsh
}

pick_timeout_bin() {
  # coreutils timeout не гарантирован на macOS. Без него запускаем без лимита:
  # у foreground-вызова лимит всё равно ставит вызывающий Bash-инструмент.
  if command -v timeout >/dev/null 2>&1; then echo timeout
  elif command -v gtimeout >/dev/null 2>&1; then echo gtimeout
  else echo ""; fi
}

# --- чтение user-слоя настроек ----------------------------------------------
# Значение подключа верхнеуровневой секции `agent-default-model:`. Границей
# секции служит первая строка, начатая не с пробела: полноценного YAML-парсера
# в зависимостях нет, а секция по формату всегда плоская.
settings_field() {
  local key="$1" settings="$DSH_HOME_DIR/settings.yaml"
  [[ -f "$settings" ]] || return 0
  awk -v key="$key" '
    /^agent-default-model:/ { f=1; next }
    f && /^[^ ]/ { f=0 }
    f && $1 == key ":" { sub(/^[[:space:]]*[^:]+:[[:space:]]*/, ""); print; exit }
  ' "$settings"
}

# Имена маршрутов из секции `llm-pi-ai: providers:` — то, какие провайдеры
# вообще подняты. Нужны для диагностики: provider из agent-default-model,
# которого нет ни здесь, ни среди нативных, — это UNKNOWN_MODEL на прогоне.
settings_providers() {
  local settings="$DSH_HOME_DIR/settings.yaml"
  [[ -f "$settings" ]] || return 0
  awk '
    /^llm-pi-ai:/ { top=1; next }
    top && /^[^ ]/ { top=0 }
    top && /^[[:space:]]+providers:/ { inp=1; indent=match($0, /[^ ]/); next }
    inp && /^[[:space:]]*#/ { next }
    inp && /^[[:space:]]*$/ { next }
    inp {
      col = match($0, /[^ ]/)
      if (col <= indent) { inp=0; next }
      if (col == indent + 2 && $1 ~ /:$/) { name=$1; sub(/:$/, "", name); print name }
    }
  ' "$settings" | paste -sd, - || true
}

# --- check ------------------------------------------------------------------
cmd_check() {
  local as_json=0
  case "${1:-}" in
    --json)    as_json=1 ;;
    -h|--help) usage ;;
    "")        ;;
    *)         die 2 "неизвестная опция '$1'" ;;
  esac

  local bin="" bin_status="missing" version="" model="" provider="" effort="" auth="unknown"
  if [[ -n "${DSH_BIN:-}" ]] && command -v "${DSH_BIN}" >/dev/null 2>&1; then
    bin="$(command -v "${DSH_BIN}")"
  elif command -v dsh >/dev/null 2>&1; then
    bin="$(command -v dsh)"
  fi

  if [[ -n "$bin" ]]; then
    if version="$("$bin" --version 2>/dev/null)"; then
      bin_status="ok"
    else
      bin_status="broken"
      version=""
    fi
  fi

  # Модель читаем из user-слоя настроек: именно он перекрывает всё остальное,
  # включая --patch overlay (проверено вживую — см. SKILL.md).
  model="$(settings_field model)"
  provider="$(settings_field provider)"
  effort="$(settings_field reasoningEffort)"

  # Маршруты мульти-провайдерного адаптера: они поднимаются только из секции
  # `llm-pi-ai:`, поэтому пустой список при provider не из этой секции — норма,
  # а при нём — ровно та поломка, которую надо увидеть.
  local routes=""
  routes="$(settings_providers)"

  # Признак выполненного входа: локальное хранилище учётных данных харнесса.
  # Содержимое не читаем и наружу не отдаём — только факт существования.
  if [[ -s "$DSH_HOME_DIR/credentials.json" ]] || [[ -d "$DSH_HOME_DIR/storages" && -n "$(ls -A "$DSH_HOME_DIR/storages" 2>/dev/null)" ]]; then
    auth="present"
  else
    auth="absent"
  fi

  local profiles=""
  if [[ -d "$DSH_HOME_DIR/profiles" ]]; then
    profiles="$(ls "$DSH_HOME_DIR/profiles" 2>/dev/null | grep -v '^node_modules$' | paste -sd, - || true)"
  fi

  local running=0
  running="$(count_running_jobs)"

  local ready="no"
  [[ "$bin_status" == "ok" && "$profiles" == *headless* ]] && ready="yes"

  if [[ $as_json -eq 1 ]]; then
    printf '{"ready":"%s","binary":"%s","binary_status":"%s","version":"%s","profiles":"%s","provider":"%s","model":"%s","effort":"%s","credentials":"%s","pi_ai_routes":"%s","dsh_home":"%s","running_jobs":%s}\n' \
      "$(json_escape "$ready")" "$(json_escape "$bin")" "$(json_escape "$bin_status")" \
      "$(json_escape "$version")" "$(json_escape "$profiles")" "$(json_escape "$provider")" \
      "$(json_escape "$model")" "$(json_escape "$effort")" "$(json_escape "$auth")" \
      "$(json_escape "$routes")" \
      "$(json_escape "$DSH_HOME_DIR")" "$running"
  else
    echo "готовность:   $ready"
    echo "бинарь:       ${bin:-не найден} ($bin_status)"
    echo "версия:       ${version:-—}"
    echo "профили:      ${profiles:-—}"
    echo "модель:       ${provider:-—} / ${model:-—} (effort: ${effort:-—})"
    echo "маршруты pi-ai: ${routes:-—}"
    echo "учётные данные: $auth"   # вход в веб-интерфейс; на API-ключе бывает absent — это не поломка
    echo "фоновых задач в работе: $running"
    echo "DSH_HOME:     $DSH_HOME_DIR"
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

# --- сборка overlay для выбора модели --------------------------------------
# `--patch` НЕ переопределяет модель: user-слой settings.yaml накладывается
# поверх любого overlay. Рабочий обход — подменить сам путь, из которого
# settings-плагин читает документ (проверено: так поднимается deepseek-v4-pro).
#
# Подменяется ПУТЬ, а не одна секция, поэтому overlay обязан нести весь
# пользовательский документ. Файл из одного `agent-default-model:` выкидывал бы
# заодно `llm-deepseek:` и `llm-pi-ai:` — ссылку на ключ и профили провайдеров —
# и прогон молча уезжал на дефолтный ключ (DEEPSEEK_API_KEY) и нативный
# маршрут, независимо от того, что человек настроил. Поэтому копируем документ
# целиком и переписываем в нём ровно одну секцию.
write_settings_overlay() {
  local model="$1" effort="$2" provider="$3" dir="$4"
  local user_settings="$DSH_HOME_DIR/settings.yaml"
  local settings_file="$dir/settings.yaml" patch_file="$dir/patch.yml"

  if [[ -f "$user_settings" ]]; then
    # Вырезаем прежнюю секцию целиком: она верхнеуровневая и кончается на
    # первой строке, начатой не с пробела.
    awk '
      /^agent-default-model:/ { f=1; next }
      f && /^[[:space:]]*$/ { next }
      f && /^[^ ]/ { f=0 }
      !f { print }
    ' "$user_settings" > "$settings_file"
  else
    : > "$settings_file"
  fi

  {
    echo "agent-default-model:"
    echo "  provider: $provider"
    echo "  model: $model"
    [[ -n "$effort" ]] && echo "  reasoningEffort: $effort"
  } >> "$settings_file"
  {
    echo "- id: settings"
    echo "  config:"
    echo "    path: $settings_file"
  } > "$patch_file"
  echo "$patch_file"
}

# Алиасы моделей провайдер-зависимы: pro/flash/vision — это каталог DeepSeek.
# На чужом маршруте (openrouter, zai, свой шлюз) таких id нет, и молчаливая
# подстановка deepseek-v4-pro стоила бы прогона: в лучшем случае UNKNOWN_MODEL,
# в худшем — тихий уход на другого провайдера. Поэтому либо переопределение
# через DSH_MODEL_PRO / DSH_MODEL_FLASH / DSH_MODEL_VISION, либо полный id.
expand_model_alias() {
  local alias="$1" provider="$2" override=""
  case "$alias" in
    pro)    override="${DSH_MODEL_PRO:-}" ;;
    flash)  override="${DSH_MODEL_FLASH:-}" ;;
    vision) override="${DSH_MODEL_VISION:-}" ;;
    *)      printf '%s' "$alias"; return 0 ;;
  esac
  if [[ -n "$override" ]]; then printf '%s' "$override"; return 0; fi
  case "$provider" in
    deepseek-official|"")
      case "$alias" in
        pro)    printf '%s' "deepseek-v4-pro" ;;
        flash)  printf '%s' "deepseek-v4-flash" ;;
        vision) printf '%s' "deepseek-v4-flash-vision-exp" ;;
      esac ;;
    *)
      die 2 "алиас '$alias' определён только для провайдера deepseek-official, а текущий — '$provider'. Назови модель полным id (--model <id>) или задай переменную DSH_MODEL_$(printf '%s' "$alias" | tr '[:lower:]' '[:upper:]')" ;;
  esac
}

# --- run --------------------------------------------------------------------
cmd_run() {
  local write=0 model="" effort="" workdir="" timeout_s="" background=0 label="" provider_opt=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --write)      write=1; shift ;;
      --model)      model="$(need_value --model "${2:-}")"; shift 2 ;;
      --provider)   provider_opt="$(need_value --provider "${2:-}")"; shift 2 ;;
      --effort)     effort="${2:-}"; [[ -z "$effort" ]] && die 2 "--effort требует значение"
                    case "$effort" in low|medium|high|xhigh|max) ;; *) die 2 "--effort принимает low, medium, high, xhigh или max" ;; esac
                    shift 2 ;;
      --cwd)        workdir="$(need_value --cwd "${2:-}")"; shift 2 ;;
      --timeout)    timeout_s="${2:-}"; [[ -z "$timeout_s" ]] && die 2 "--timeout требует значение"; shift 2 ;;
      --label)      label="${2:-}"; [[ -z "$label" ]] && die 2 "--label требует значение"; shift 2 ;;
      --background) background=1; shift ;;
      -h|--help)    usage ;;
      *)            die 2 "неизвестная опция '$1' (промпт передаётся на stdin, не аргументом)" ;;
    esac
  done

  # Потолок зависит от режима: у фона нет внешнего ограничителя, у foreground
  # он есть, и там дефолт специально ниже.
  if [[ -z "$timeout_s" ]]; then
    if [[ $background -eq 1 ]]; then timeout_s="$DEFAULT_BG_TIMEOUT"; else timeout_s="$DEFAULT_TIMEOUT"; fi
  fi
  [[ "$timeout_s" =~ ^[0-9]+$ ]] || die 2 "--timeout принимает целое число секунд (0 — без ограничения)"

  local prompt
  prompt="$(cat)"
  [[ -z "${prompt//[[:space:]]/}" ]] && die 2 "пустой промпт на stdin"

  # Задача уходит одним элементом argv (dsh читает только позиционный
  # аргумент, stdin он не смотрит). Лимит ARG_MAX — 1 МиБ на macOS;
  # держим порог ниже с запасом на окружение процесса.
  local prompt_bytes
  prompt_bytes=$(printf '%s' "$prompt" | wc -c | tr -d ' ')
  if [[ "$prompt_bytes" -gt 262144 ]]; then
    die 2 "промпт $prompt_bytes байт — слишком длинный для argv. Положи материал в файл внутри рабочего каталога и сошлись на него из задачи."
  fi

  workdir="${workdir:-$PWD}"
  [[ -d "$workdir" ]] || die 2 "каталог '$workdir' не существует"
  workdir="$(cd "$workdir" && pwd)"

  local bin; bin="$(resolve_dsh)"

  local mode="read-only"
  [[ $write -eq 1 ]] && mode="workspace-write"

  local tmpdir=""
  local args=(--profile headless)
  local provider="" eff_model=""
  if [[ -n "$model" || -n "$effort" || -n "$provider_opt" ]]; then
    # Провайдер: явный флаг → выбор человека в settings.yaml → нативный
    # маршрут DeepSeek последним дефолтом. Своего мнения о провайдере у
    # обвязки нет: она правит секцию, а не решает, куда ходить.
    provider="${provider_opt:-$(settings_field provider)}"
    provider="${provider:-deepseek-official}"

    if [[ -n "$model" ]]; then
      eff_model="$(expand_model_alias "$model" "$provider")" || exit $?
    else
      # Только --effort или только --provider: модель остаётся выбранной
      # человеком, её нужно перенести в overlay как есть.
      eff_model="$(settings_field model)"
      [[ -n "$eff_model" ]] || die 2 "модель не выбрана ни флагом, ни в settings.yaml — добавь --model <id>"
    fi

    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/dsh-overlay.XXXXXX")"
    chmod 700 "$tmpdir"
    CLEANUP_PATHS+=("$tmpdir")
    local patch_file
    patch_file="$(write_settings_overlay "$eff_model" "$effort" "$provider" "$tmpdir")"
    args+=(--patch "$patch_file")
  fi

  if [[ $background -eq 1 ]]; then
    # Overlay переживает выход этого процесса: его убирает сам воркер.
    [[ -n "$tmpdir" ]] && CLEANUP_PATHS=()
    run_background "$bin" "$mode" "$workdir" "$timeout_s" "$prompt" "$tmpdir" \
      "$eff_model" "$effort" "$provider" "$label" "${args[@]}"
    return 0
  fi

  run_foreground "$bin" "$mode" "$workdir" "$timeout_s" "$prompt" "${args[@]}"
}

run_foreground() {
  local bin="$1" mode="$2" workdir="$3" timeout_s="$4" prompt="$5"; shift 5
  local args=("$@")
  local tb; tb="$(pick_timeout_bin)"
  local out err_file rc=0
  err_file="$(mktemp "${TMPDIR:-/tmp}/dsh-stderr.XXXXXX")"
  CLEANUP_PATHS+=("$err_file")

  # Потоки разводим строго: stdout — только ответ модели, диагностика dsh
  # остаётся в stderr. Слить их здесь значило бы отдать прогресс харнесса
  # пользователю как содержательный ответ.
  if [[ -n "$tb" ]]; then
    out="$(cd "$workdir" && DSH_PERMISSION_MODE="$mode" "$tb" "$timeout_s" "$bin" "${args[@]}" "$prompt" 2>"$err_file")" || rc=$?
  else
    out="$(cd "$workdir" && DSH_PERMISSION_MODE="$mode" "$bin" "${args[@]}" "$prompt" 2>"$err_file")" || rc=$?
  fi

  local err_text=""
  [[ -s "$err_file" ]] && err_text="$(cat "$err_file")"

  if [[ $rc -eq 124 ]]; then
    [[ -n "$out" ]] && printf '%s\n' "$out"
    die 6 "dsh: таймаут ${timeout_s}с. Задача слишком большая для одного прогона — перезапусти её с --background, тогда потолок снимается."
  fi
  if [[ $rc -ne 0 ]]; then
    # Содержательный кусок ответа, если он успел появиться, всё равно отдаём:
    # он полезнее кода возврата. Причина отказа идёт в stderr, к die.
    [[ -n "$out" ]] && printf '%s\n' "$out"
    die 6 "dsh: прогон завершился с кодом $rc${err_text:+ — $err_text}"
  fi
  [[ -z "$out" ]] && die 6 "dsh: пустой ответ — проверь готовность командой check${err_text:+. stderr: $err_text}"
  printf '%s\n' "$out"
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
    id="dsh-$stamp-$$-$RANDOM"
    dir="$JOBS_DIR/$id"
    mkdir "$dir" 2>/dev/null && { printf '%s' "$id"; return 0; }
    i=$((i+1))
    [[ $i -ge 100 ]] && die 5 "не удалось выделить идентификатор задачи в $JOBS_DIR"
  done
}

run_background() {
  local bin="$1" mode="$2" workdir="$3" timeout_s="$4" prompt="$5" tmpdir="$6"
  local model="$7" effort="$8" provider="$9"; shift 9
  local label="$1"; shift
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
    echo "timeout=$(if [[ -n "$(pick_timeout_bin)" ]]; then echo "$timeout_s"; else echo "none (нет coreutils timeout)"; fi)"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "started_epoch=$(date +%s)"
    echo "overlay=${tmpdir:-—}"
  } > "$job_dir/meta"

  # Отвязанный воркер: он же убирает overlay-каталог и проставляет итог.
  # dsh запускается фоном внутри воркера, чтобы его pid попал в meta — без
  # этого джобу нечем убить: kill воркера оставил бы харнесс работать сиротой.
  # Своя процесс-группа: без неё сигнал, посланный по группе вызывающей
  # оболочки (таймаут или прерывание Claude Code), уносит и воркер, и харнесс.
  set -m
  (
    local tb rc=0 inner
    # Закрытие терминала или выход вызвавшей сессии не должны уносить прогон:
    # ради этого джоба и делалась фоновой.
    trap '' HUP INT TERM
    # Пишет в meta только воркер — параллельная запись из родителя уже
    # приводила к потере статуса и осиротевшим задачам.
    # Свой pid субшелл в bash 3.2 иначе не узнаёт: $$ там принадлежит родителю.
    meta_set worker_pid "$(sh -c 'echo $PPID')" "$job_dir/meta"
    tb="$(pick_timeout_bin)"
    cd "$workdir" || exit 1
    if [[ -n "$tb" ]]; then
      DSH_PERMISSION_MODE="$mode" "$tb" "$timeout_s" "$bin" "${args[@]}" "$prompt" \
        > "$job_dir/output.txt" 2> "$job_dir/stderr.txt" &
    else
      DSH_PERMISSION_MODE="$mode" "$bin" "${args[@]}" "$prompt" \
        > "$job_dir/output.txt" 2> "$job_dir/stderr.txt" &
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
    # Время окончания пишем ПЕРВЫМ: без него elapsed_of считает от «сейчас», и
    # давно мёртвая задача показывает растущее время работы.
    meta_set finished_epoch "$(date +%s)" "$job_dir/meta"
    meta_set finished "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$job_dir/meta"
    meta_set exit "$rc" "$job_dir/meta"
    meta_set status "$final" "$job_dir/meta"
    [[ -n "$tmpdir" ]] && rm -rf "$tmpdir"
  ) >/dev/null 2>&1 &
  disown 2>/dev/null || true
  set +m

  echo "$job_id"
}

# --- общее для работы с джобами --------------------------------------------
job_dir_of() {
  local job_id="$1"
  [[ -n "$job_id" ]] || die 2 "нужен job-id (список — dsh-run.sh status)"
  # Идентификатор идёт в путь, поэтому его форма проверяется строго: иначе
  # `result ../../что-то` читает и переписывает каталоги вне JOBS_DIR.
  case "$job_id" in
    */*|*..*) die 2 "недопустимый job-id '$job_id'" ;;
  esac
  local dir="$JOBS_DIR/$job_id"
  [[ -d "$dir" ]] || die 2 "нет задачи с id '$job_id' (список — dsh-run.sh status --all)"
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
    # DSH_BIN может называться как угодно.
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
# Сессия уточняет ответ, когда её идентификатор передан через DSH_CLAUDE_SESSION;
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
  printf '%dм%02dс' $(( s / 60 )) $(( s % 60 ))
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
      -*)        die 2 "неизвестная опция '$1'" ;;
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

  [[ -d "$JOBS_DIR" ]] || { echo "фоновых задач нет" >&2; [[ $as_json -eq 1 ]] && echo '[]'; exit 1; }

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
      printf '%-34s %-10s %-8s %-14s %s\n' \
        "$dir" "$st" "$(elapsed_of "$d")" \
        "$(meta_get model "$d/meta")" \
        "$(meta_get label "$d/meta")" || exit 0
    fi
    if [[ $shown -ge 30 ]]; then
      [[ $as_json -eq 1 ]] || echo "… показаны первые 30; остальные — status --all" >&2
      break
    fi
  done

  if [[ $as_json -eq 1 ]]; then
    printf '[%s]\n' "${out%,}"
    return 0
  fi
  if [[ $shown -eq 0 ]]; then
    if [[ $all -eq 0 ]]; then
      echo "здесь фоновых задач нет (все задачи на машине — status --all)" >&2
    else
      echo "фоновых задач нет" >&2
    fi
    exit 1
  fi
}

job_json() {
  local dir="$1" st="$2"
  printf '{"id":"%s","status":"%s","meta_status":"%s","label":"%s","cwd":"%s","mode":"%s","model":"%s","provider":"%s","effort":"%s","session":"%s","started":"%s","elapsed":"%s","timeout":"%s","exit":"%s","output_bytes":%s}' \
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
      -*)        die 2 "неизвестная опция '$1'" ;;
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
      die 5 "задача ещё выполняется ($(elapsed_of "$dir") с $(meta_get started "$dir/meta")); опроси позже: dsh-run.sh status $job_id"
      ;;
    orphaned)
      [[ -s "$dir/output.txt" ]] && cat "$dir/output.txt"
      die 6 "воркер задачи исчез, не проставив итог (перезагрузка или kill -9); выше — то, что успело записаться"
      ;;
  esac

  # Частичный вывод оборванной или упавшей задачи печатаем в stdout ДО die:
  # он и есть самое ценное, что от такой задачи осталось.
  [[ -s "$dir/output.txt" ]] && cat "$dir/output.txt"

  case "$st" in
    timeout)
      die 6 "задача оборвалась по таймауту ($(meta_get timeout "$dir/meta")с); выше — то, что успело прийти"
      ;;
    canceled)
      die 6 "задача снята вручную ($(elapsed_of "$dir") работы); выше — то, что успело прийти"
      ;;
    failed)
      die 6 "задача завершилась с ошибкой (код $(meta_get exit "$dir/meta"))$( [[ -s "$dir/stderr.txt" ]] && printf ' — %s' "$(tail -c 500 "$dir/stderr.txt")" )"
      ;;
  esac

  if [[ ! -s "$dir/output.txt" ]]; then
    [[ -s "$dir/stderr.txt" ]] && tail -c 2000 "$dir/stderr.txt" >&2
    die 6 "пустой ответ"
  fi
}

# --- logs -------------------------------------------------------------------
# Прогресс живой задачи: stderr харнесса. Это диагностика, а не ответ, поэтому
# смешивать её с выводом `result` нельзя — но посмотреть, шевелится ли dsh и
# какие файлы он читает, иначе нечем.
cmd_logs() {
  local job_id="" tail_n=40
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tail)    tail_n="${2:-}"; [[ "$tail_n" =~ ^[0-9]+$ ]] || die 2 "--tail принимает число строк"; shift 2 ;;
      -h|--help) usage ;;
      -*)        die 2 "неизвестная опция '$1'" ;;
      *)         job_id="$1"; shift ;;
    esac
  done
  local dir; dir="$(job_dir_of "$job_id")"
  local st; st="$(job_status_of "$dir")"

  echo "статус:  $st ($(elapsed_of "$dir"))"
  echo "ответ:   $(file_bytes "$dir/output.txt") байт накоплено"
  if [[ -s "$dir/stderr.txt" ]]; then
    echo "--- последние $tail_n строк stderr ---"
    tail -n "$tail_n" "$dir/stderr.txt"
  elif [[ "$st" == "running" ]]; then
    echo "stderr пуст. Для dsh это норма: прогресс он наружу не транслирует,"
    echo "признак работы — сам статус running и растущее время."
  else
    echo "stderr пуст"
  fi
}

# --- cancel -----------------------------------------------------------------
# Убиваем всё дерево: dsh — node-процесс, который порождает подпроцессы
# (grep, тесты, сам провайдер). kill только по верхнему pid оставил бы их жить.
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
    echo "$job_id: уже $st, снимать нечего"
    return 0
  fi

  # Маркер ставим первым: воркер прочитает его при финализации и запишет
  # canceled вместо failed.
  : > "$dir/canceled"
  kill_tree "$(meta_get pid "$dir/meta")" TERM

  # Даём воркеру дописать итог; если харнесс не реагирует на TERM — добиваем
  # его, но НЕ воркер: воркер в этот момент пишет meta, и KILL посреди записи
  # оставлял задачу без finished_epoch, а overlay — на диске навсегда.
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
    # Overlay убирал воркер; раз его нет — убираем сами.
    local ov; ov="$(meta_get overlay "$dir/meta")"
    [[ -n "$ov" && "$ov" != "—" && "$ov" == */dsh-overlay.* ]] && rm -rf "$ov"
  fi

  rm -f "$dir/canceled"
  local final; final="$(job_status_of "$dir")"
  case "$final" in
    canceled) echo "$job_id: снята ($(elapsed_of "$dir") работы)" ;;
    running)  echo "$job_id: снять не удалось — процесс не отвечает; посмотри status $job_id" ;;
    *)        echo "$job_id: успела завершиться сама до отмены ($final)" ;;
  esac
}

cmd_cancel() {
  local target="${1:-}"
  [[ "$target" == "-h" || "$target" == "--help" ]] && usage
  [[ -n "$target" ]] || die 2 "нужен job-id или --all (список — dsh-run.sh status)"
  if [[ "$target" == "--all" ]]; then
    local any=0 dir
    [[ -d "$JOBS_DIR" ]] || die 1 "фоновых задач нет"
    for dir in "$JOBS_DIR"/*/; do
      [[ -f "$dir/meta" ]] || continue
      # --all в пределах своих задач: чужие снимать молча нельзя.
      job_is_mine "${dir%/}" || continue
      [[ "$(job_status_of "$dir")" == "running" ]] || continue
      any=1
      cancel_one "${dir%/}"
    done
    [[ $any -eq 1 ]] || { echo "работающих задач нет" >&2; exit 1; }
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
      --older-than) days="${2:-}"; [[ "$days" =~ ^[0-9]+$ ]] || die 2 "--older-than принимает число дней"
                    [[ "$days" -eq 0 ]] && all=1
                    shift 2 ;;
      --all)        all=1; shift ;;
      -h|--help)    usage ;;
      *)            die 2 "неизвестная опция '$1'" ;;
    esac
  done
  [[ -d "$JOBS_DIR" ]] || { echo "фоновых задач нет"; return 0; }

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
  echo "удалено задач: $removed (работающие не трогались${skipped:+; чужих пропущено: $skipped})"
}

# --- transcript -------------------------------------------------------------
# Полный ход рассуждений и вызовов инструментов dsh пишет в свою сессию;
# наружу он отдаёт только финальное сообщение. Разбор нужен, когда ответ
# выглядит неправдоподобно и надо посмотреть, что харнесс делал на самом деле.
cmd_transcript() {
  local job_id="${1:-}" workdir="$PWD"
  [[ "$job_id" == "-h" || "$job_id" == "--help" ]] && usage
  if [[ -n "$job_id" ]]; then
    local dir; dir="$(job_dir_of "$job_id")"
    workdir="$(meta_get cwd "$dir/meta")"
  fi

  local sessions_root="$DSH_HOME_DIR/sessions"
  [[ -d "$sessions_root" ]] || die 2 "нет каталога сессий: $sessions_root"

  # Каталог сессий именуется по рабочему каталогу: разделители пути заменены
  # на дефисы, имя обрамлено двойными дефисами.
  local slug
  slug="--$(printf '%s' "$workdir" | sed 's|^/||; s|/|-|g')--"
  local dir2="$sessions_root/$slug"
  [[ -d "$dir2" ]] || die 2 "для каталога '$workdir' сессий не найдено (искал $dir2)"

  local latest
  latest="$(ls -1td "$dir2"/session-* 2>/dev/null | head -1 || true)"
  [[ -n "$latest" ]] || die 2 "в '$dir2' нет сессий"

  local file="$latest/session.jsonl.zstd"
  if [[ -f "$file" ]]; then
    command -v zstd >/dev/null 2>&1 || die 2 "нужен zstd, чтобы распаковать $file"
    zstd -dc "$file"
  elif [[ -f "$latest/session.jsonl" ]]; then
    cat "$latest/session.jsonl"
  else
    die 2 "в '$latest' нет ни session.jsonl.zstd, ни session.jsonl"
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
  status)     cmd_status "$@" ;;
  result)     cmd_result "$@" ;;
  logs)       cmd_logs "$@" ;;
  cancel)     cmd_cancel "$@" ;;
  clean)      cmd_clean "$@" ;;
  transcript) cmd_transcript "$@" ;;
  -h|--help)  usage ;;
  *)          die 2 "неизвестная подкоманда '$sub'" ;;
esac
