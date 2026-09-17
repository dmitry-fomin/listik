#!/usr/bin/env bash
# opencode-run.sh — единая точка запуска opencode (`opencode run`) из Claude Code.
#
#   opencode-run.sh check [--json]
#   opencode-run.sh run [опции] < prompt.txt
#   opencode-run.sh resume <имя-сессии|job-id> [опции] < prompt.txt
#   opencode-run.sh status [--json] [--all] [--running] [job-id]
#   opencode-run.sh result <job-id> [--wait [сек]]
#   opencode-run.sh logs <job-id> [--tail N]
#   opencode-run.sh cancel <job-id|--all>
#   opencode-run.sh clean [--older-than <дней>] [--all]
#   opencode-run.sh sessions [--json]
#   opencode-run.sh transcript [job-id|--session <имя>]
#
# Инвариант, на котором держится вся обвязка: в stdout подкоманд `run`/`resume`
# (foreground) и `result` попадает РОВНО финальный ответ модели и ничего больше.
# Всё служебное — прогресс, диагностика, ошибки запуска — идёт в stderr или в
# файлы джобы. Вызывающий может отдавать этот stdout пользователю дословно.
#
# Фоновая джоба — самостоятельная сущность с собственным идентификатором:
# её можно опрашивать (`status`, `logs`), забирать (`result`), убивать
# (`cancel`). Прогон переживает завершение вызвавшего его Bash-инструмента,
# поэтому долгие задачи не упираются в его десятиминутный потолок.
#
# --- Отличия от codex-run.sh и dsh-run.sh -----------------------------------
#
# 1. `opencode run` сам по себе НЕ фоновый: он блокирует, пока агент не
#    закончит. Весь job-management (job-id, meta-файл, отвязанный воркер,
#    kill_tree, status/result/logs/cancel/clean) обвязка реализует сама — этот
#    код почти не opencode-специфичен и перенесён из codex-run.sh.
#
# 2. У opencode нет флага «напиши финальный ответ в файл» (аналога codex
#    `-o/--output-last-message`). Чистый ответ добываем из потока событий
#    `--format json`: это JSONL, где у каждого события есть sessionID, а текст
#    ответа лежит в событиях type=text. Ответом считаются текстовые части
#    ПОСЛЕДНЕГО сообщения — промежуточные реплики между вызовами инструментов
#    в ответ не попадают. Разбор требует python3 или jq (см. check).
#
# 3. Сессия адресуется ИМЕНЕМ, а не только job-id. `opencode run --title <имя>`
#    заводит сессию с этим заголовком, `opencode run --session <ses_...>`
#    продолжает её. Сам opencode ищет сессию только по id (`--session <имя>`
#    отвечает "Session not found"), поэтому имя→id держит обвязка: каталог
#    $STATE_DIR/sessions, плюс поиск по `opencode session list --format json`
#    как запасной путь, если состояние потеряно.
#
# 4. Права: песочницы уровня ОС, как `-s read-only` у codex, у opencode нет.
#    Режим чтения ставится на уровне инструментов — OPENCODE_PERMISSION с
#    `edit=deny` убирает инструменты правки из набора модели вовсе. Запись
#    через bash это не запрещает: граница здесь слабее, чем у codex, и об этом
#    сказано в README и скилах.
#
# 5. Модель по умолчанию задана обвязкой (b.ai/glm-5.3-flash), а не берётся из
#    настроек пользователя: у opencode нет одной «активной модели», модель
#    выбирается на каждый запуск. Переопределяется флагом --model или
#    переменной OPENCODE_DEFAULT_MODEL.
#
# 6. Каналов два: `glm` (b.ai/glm-5.3-flash, по умолчанию) и `deepseek`
#    (b.ai/deepseek-v4.1-flash). Короткое имя канала раскрывается обвязкой в
#    полный идентификатор; полный идентификатор принимается как есть, поэтому
#    любая другая модель провайдера доступна без правки скрипта.
set -euo pipefail

STATE_DIR="${OPENCODE_CLAUDE_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/opencode-claude}"
JOBS_DIR="$STATE_DIR/jobs"
# Имя сессии → id сессии opencode. Один файл на имя, формат тот же key=value,
# что и у meta джобы.
NAMES_DIR="$STATE_DIR/sessions"
# Каналы: короткое имя → полный идентификатор модели. Пара «имя:модель» на
# строку, порядок — порядок печати в check; первый канал считается каналом по
# умолчанию. Короткие имена нужны, чтобы ни человеку, ни скилу не приходилось
# помнить версию модели, а смена версии оставалась правкой одной строки.
CHANNELS=(
  "glm:b.ai/glm-5.3-flash"
  "deepseek:b.ai/deepseek-v4.1-flash"
)

# Короткое имя канала → полный идентификатор. Всё, что не короткое имя,
# возвращается как есть: полный идентификатор провайдера обвязка не
# перепроверяет и не сужает списком.
expand_channel() {
  local want="$1" pair
  for pair in "${CHANNELS[@]}"; do
    [[ "$want" == "${pair%%:*}" ]] && { printf '%s' "${pair#*:}"; return 0; }
  done
  printf '%s' "$want"
}

# Модель по умолчанию. У opencode нет «текущей модели» в настройках, которую
# можно было бы просто унаследовать, — её передают на каждый запуск.
# OPENCODE_DEFAULT_MODEL принимает и короткое имя канала, и полный
# идентификатор.
DEFAULT_MODEL="$(expand_channel "${OPENCODE_DEFAULT_MODEL:-${CHANNELS[0]#*:}}")"

# Значение --model: короткое имя канала раскрываем, полный идентификатор
# пропускаем. Голое имя без провайдера — почти всегда опечатка в названии
# канала, и opencode на него ответит своей невнятной ошибкой уже после запуска,
# поэтому отбиваем здесь.
resolve_model() {
  local want="$1" resolved names="" pair
  resolved="$(expand_channel "$want")"
  case "$resolved" in
    */*) printf '%s' "$resolved"; return 0 ;;
  esac
  for pair in "${CHANNELS[@]}"; do names="${names:+$names, }${pair%%:*}"; done
  die 2 "неизвестный канал '$want' — короткие имена: $names; либо полный идентификатор вида provider/model"
}

# Агент opencode по умолчанию: основной `build`. Режим чтения обеспечивает не
# он, а OPENCODE_PERMISSION (см. permission_json).
DEFAULT_AGENT="${OPENCODE_DEFAULT_AGENT:-build}"
# Foreground держим заметно ниже потолка Bash-инструмента (600с): скрипт
# должен успеть вернуть осмысленную ошибку раньше, чем его оборвут снаружи.
DEFAULT_TIMEOUT=540
# Фону этот потолок не писан — он живёт вне вызова Bash. Два часа с запасом
# на большую задачу; 0 отключает лимит совсем.
DEFAULT_BG_TIMEOUT=7200
# Сессия Claude Code, из которой запущена джоба. В окружении Bash-инструмента
# CLAUDE_SESSION_ID не появляется, поэтому полагаться на неё как на
# единственную границу нельзя — она уточняет фильтр, когда её всё же передали.
SESSION_ID="${OPENCODE_CLAUDE_SESSION:-${CLAUDE_SESSION_ID:-}}"
# Id сессии opencode для продолжения (ses_...), не путать с SESSION_ID выше —
# тот тегирует джобы сессией Claude Code.
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
# взаимный замок: без него параллельные meta_set теряют целые наборы полей.
# mkdir атомарен на любой файловой системе.
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
  opencode-run.sh check [--json]
  opencode-run.sh run [--session <имя>] [--write] [--bash] [--model <provider/model>]
                      [--agent <имя>] [--variant <level>] [--cwd <dir>]
                      [--timeout <сек>] [--background] [--label <текст>]
                      < prompt.txt
  opencode-run.sh resume <имя-сессии|job-id> [--session <имя>] [--write] [--bash]
                      [--model <provider/model>] [--agent <имя>] [--variant <level>]
                      [--cwd <dir>] [--timeout <сек>] [--background] [--label <текст>]
                      < prompt.txt
  opencode-run.sh status [--json] [--all] [--running] [job-id]
  opencode-run.sh result <job-id> [--wait [сек]]
  opencode-run.sh logs <job-id> [--tail <строк>]
  opencode-run.sh cancel <job-id|--all>
  opencode-run.sh clean [--older-than <дней>] [--all]
  opencode-run.sh sessions [--json]
  opencode-run.sh transcript [job-id] [--session <имя>]

каналы (значение --model): glm = b.ai/glm-5.3-flash (по умолчанию),
  deepseek = b.ai/deepseek-v4.1-flash; принимается и полный provider/model
USAGE
  exit 2
}

# --- поиск бинаря -----------------------------------------------------------
# Порядок: явный OPENCODE_BIN → PATH. Абсолютный путь важен для фоновых
# запусков: отвязанный процесс не наследует изменения PATH, сделанные после
# старта.
resolve_opencode() {
  local bin="${OPENCODE_BIN:-}"
  if [[ -n "$bin" ]]; then
    command -v "$bin" >/dev/null 2>&1 || die 2 "OPENCODE_BIN указывает на '$bin', но такого исполняемого файла нет"
    command -v "$bin"
    return 0
  fi
  command -v opencode >/dev/null 2>&1 \
    || die 2 "opencode не найден в PATH — установи opencode или укажи путь через переменную OPENCODE_BIN"
  command -v opencode
}

find_opencode_bin() {
  if [[ -n "${OPENCODE_BIN:-}" ]] && command -v "${OPENCODE_BIN}" >/dev/null 2>&1; then
    command -v "${OPENCODE_BIN}"
  elif command -v opencode >/dev/null 2>&1; then
    command -v opencode
  fi
}

pick_timeout_bin() {
  # coreutils timeout не гарантирован на macOS. Без него запускаем без лимита:
  # у foreground-вызова лимит всё равно ставит вызывающий Bash-инструмент.
  if command -v timeout >/dev/null 2>&1; then echo timeout
  elif command -v gtimeout >/dev/null 2>&1; then echo gtimeout
  else echo ""; fi
}

# Чем разбирать поток событий. Без python3 и jq ответ из JSONL не собрать
# честно: в тексте бывают экранированные кавычки и переводы строк, и грубый
# grep вернул бы покоцанный ответ вместо ответа.
json_tool() {
  if command -v python3 >/dev/null 2>&1; then echo python3
  elif command -v jq >/dev/null 2>&1; then echo jq
  else echo ""; fi
}

# --- права ------------------------------------------------------------------
# Песочницы уровня ОС у opencode нет (это главное отличие от codex с его
# `-s read-only`). Единственная настоящая граница — OPENCODE_PERMISSION:
# запрещённый инструмент не попадает в набор модели вовсе.
#
# Отсюда три режима, а не два:
#   read-only — deny и на правку, и на bash. Только это и есть настоящее
#               чтение: с разрешённым bash модель спокойно пишет файл через
#               `printf ... > файл` (проверено — так и сделала). Инструментов
#               read/grep/glob/list для обхода кода хватает.
#   read-bash — правка запрещена, bash разрешён: нужен `git log`, тесты,
#               сборка. Записи это не запрещает, гарантии «только чтение»
#               здесь нет — флаг на то и отдельный.
#   write     — полный доступ.
permission_json() {
  local mode="$1"
  case "$mode" in
    write)     printf '%s' '{"edit":"allow","bash":"allow"}' ;;
    read-bash) printf '%s' '{"edit":"deny"}' ;;
    *)         printf '%s' '{"edit":"deny","bash":"deny"}' ;;
  esac
}

mode_ru() {
  case "$1" in
    write)     echo "полный доступ (правка и команды)" ;;
    read-bash) echo "чтение и команды, правка запрещена" ;;
    *)         echo "только чтение (правка и bash запрещены)" ;;
  esac
}

# --- разбор потока событий ---------------------------------------------------
# Ответ = текстовые части последнего сообщения. Промежуточные реплики (модель
# говорит между вызовами инструментов) в ответ не идут — иначе «дословный
# stdout» превратился бы в стенограмму.
extract_answer() {
  local events="$1" tool
  [[ -s "$events" ]] || return 0
  tool="$(json_tool)"
  case "$tool" in
    python3)
      python3 - "$events" <<'PY'
import json, sys

texts, last = {}, None
with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("type") != "text":
            continue
        part = event.get("part") or {}
        text = part.get("text") or ""
        if not text:
            continue
        mid = part.get("messageID") or ""
        texts.setdefault(mid, []).append(text)
        last = mid
if last is not None:
    sys.stdout.write("".join(texts[last]))
PY
      ;;
    jq)
      jq -rs '
        map(select(.type == "text" and ((.part.text // "") != "")))
        | if length == 0 then ""
          else (last.part.messageID) as $m
               | map(select(.part.messageID == $m) | .part.text) | join("")
          end
      ' "$events" 2>/dev/null
      ;;
    *)
      return 0
      ;;
  esac
}

# Id сессии opencode из потока событий. Он есть в каждом событии, поэтому
# хватает первой строки и обычного grep — формат `"sessionID":"ses_..."`
# экранирования не содержит.
session_id_from_events() {
  local events="$1"
  [[ -s "$events" ]] || return 0
  grep -o '"sessionID":"[^"]*"' "$events" 2>/dev/null | head -1 | cut -d'"' -f4
}

# --- имена сессий -----------------------------------------------------------
# Имя пользователя произвольное (в том числе кириллица), имя файла — нет.
# Слаг детерминированный; настоящее имя лежит внутри файла и сверяется, чтобы
# два разных имени с одним слагом не выдавали сессию друг друга.
name_slug() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'
}

check_session_name() {
  local name="$1"
  [[ -n "$name" ]] || die 2 "--session требует имя сессии"
  case "$name" in
    */*|*..*) die 2 "недопустимое имя сессии '$name' (без / и ..)" ;;
  esac
  [[ ${#name} -le 120 ]] || die 2 "имя сессии длиннее 120 символов"
}

name_file() {
  printf '%s/%s' "$NAMES_DIR" "$(name_slug "$1")"
}

# Имя → id сессии opencode. Сначала своё состояние, затем — список сессий
# самого opencode: состояние могли почистить, а сессия жива.
resolve_session_name() {
  local name="$1" workdir="${2:-}" file id stored
  file="$(name_file "$name")"
  if [[ -f "$file" ]]; then
    stored="$(meta_get name "$file")"
    id="$(meta_get id "$file")"
    if [[ "$stored" == "$name" && -n "$id" ]]; then
      printf '%s' "$id"
      return 0
    fi
  fi
  id="$(lookup_session_by_title "$name" "$workdir" || true)"
  [[ -n "$id" ]] || return 0
  remember_session_name "$name" "$id" "$workdir"
  printf '%s' "$id"
}

# Запасной путь: спросить сам opencode. title — это и есть имя, которым мы
# метили сессию при запуске (--title). Совпадений может быть несколько —
# берём самое свежее, при прочих равных из того же каталога.
lookup_session_by_title() {
  local name="$1" workdir="${2:-}" bin tool json
  bin="$(find_opencode_bin)"
  [[ -n "$bin" ]] || return 0
  tool="$(json_tool)"
  [[ -n "$tool" ]] || return 0
  json="$("$bin" session list --format json 2>/dev/null)" || return 0
  [[ -n "$json" ]] || return 0
  if [[ "$tool" == "python3" ]]; then
    printf '%s' "$json" | python3 -c '
import json, sys

name, workdir = sys.argv[1], sys.argv[2]
try:
    rows = json.load(sys.stdin)
except Exception:
    sys.exit(0)
hits = [r for r in rows if r.get("title") == name]
if not hits:
    sys.exit(0)
same = [r for r in hits if workdir and r.get("directory") == workdir]
pick = max(same or hits, key=lambda r: r.get("updated") or 0)
print(pick.get("id", ""))
' "$name" "$workdir" 2>/dev/null
  else
    printf '%s' "$json" | jq -r --arg n "$name" --arg d "$workdir" '
      [.[] | select(.title == $n)] as $hits
      | if ($hits | length) == 0 then empty
        else ([$hits[] | select(.directory == $d)]) as $same
             | (if ($same | length) > 0 then $same else $hits end)
             | sort_by(.updated) | last | .id
        end
    ' 2>/dev/null
  fi
}

# Модель запоминается вместе с именем: resume по имени (в отличие от resume по
# job-id) больше неоткуда её взять, а молча уехать с deepseek обратно на модель
# по умолчанию продолжение той же сессии не должно. Вызов без модели её не
# затирает — так запасной путь (сессия найдена по title в самом opencode)
# не теряет то, что обвязка уже знала.
remember_session_name() {
  local name="$1" id="$2" workdir="${3:-}" model="${4:-}" file
  [[ -n "$name" && -n "$id" ]] || return 0
  mkdir -p "$NAMES_DIR" 2>/dev/null || return 0
  chmod 700 "$STATE_DIR" "$NAMES_DIR" 2>/dev/null || true
  file="$(name_file "$name")"
  [[ -n "$model" ]] || model="$(meta_get model "$file")"
  {
    echo "name=$name"
    echo "id=$id"
    echo "cwd=${workdir:-}"
    echo "model=${model:-}"
    echo "updated=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$file"
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

  local bin="" bin_status="missing" version=""
  bin="$(find_opencode_bin)"
  if [[ -n "$bin" ]]; then
    # Никаких трейсбеков и падений, даже если бинарь битый: версия просто не
    # определилась, а отчёт всё равно печатается до конца.
    if version="$("$bin" --version 2>/dev/null)"; then
      bin_status="ok"
      version="$(printf '%s' "$version" | head -1 | tr -d '\r')"
    else
      bin_status="broken"
      version=""
    fi
  fi

  local model="$DEFAULT_MODEL" provider="${DEFAULT_MODEL%%/*}"
  local model_status="unknown" models_out=""
  if [[ "$bin_status" == "ok" ]]; then
    models_out="$("$bin" models 2>/dev/null)" || models_out=""
  fi
  model_status="$(model_status_in "$model" "$models_out")"

  # Каналов больше одного, и «готов ли opencode» — это доступность каждого:
  # прогон на deepseek не спасёт то, что glm на месте. Каталог моделей
  # спрашиваем один раз, а разбираем по каналам.
  local chan_json="" chan_lines="" pair cname cmodel cstatus cmark
  for pair in "${CHANNELS[@]}"; do
    cname="${pair%%:*}"; cmodel="${pair#*:}"
    cstatus="$(model_status_in "$cmodel" "$models_out")"
    cmark=""
    [[ "$cmodel" == "$DEFAULT_MODEL" ]] && cmark=", по умолчанию"
    chan_json="${chan_json:+$chan_json,}$(printf '{"name":"%s","model":"%s","status":"%s","default":"%s"}' \
      "$(json_escape "$cname")" "$(json_escape "$cmodel")" "$(json_escape "$cstatus")" \
      "$(if [[ -n "$cmark" ]]; then echo yes; else echo no; fi)")"
    chan_lines="${chan_lines}${chan_lines:+$'\n'}$cname → $cmodel ($(model_status_ru "$cstatus")$cmark)"
  done

  local auth_status="unknown"
  if [[ "$bin_status" == "ok" ]]; then
    local auth_out
    # Вывод providers list — человекочитаемый, с ANSI-кодами; ключи в нём не
    # печатаются, только имена провайдеров, поэтому его можно смотреть.
    auth_out="$("$bin" providers list 2>/dev/null | tr -d '\r' | sed $'s/\033\[[0-9;]*m//g')" || auth_out=""
    if [[ -n "$auth_out" ]]; then
      if printf '%s\n' "$auth_out" | grep -qiF "$provider"; then
        auth_status="ok"
      else
        auth_status="missing"
      fi
    fi
  fi

  local parser; parser="$(json_tool)"
  local running; running="$(count_running_jobs)"
  local named=0
  [[ -d "$NAMES_DIR" ]] && named="$(ls -1 "$NAMES_DIR" 2>/dev/null | wc -l | tr -d ' ' || true)"

  local ready="no"
  [[ "$bin_status" == "ok" && "$model_status" != "missing" && -n "$parser" ]] && ready="yes"

  if [[ $as_json -eq 1 ]]; then
    printf '{"ready":"%s","binary":"%s","binary_status":"%s","version":"%s","model":"%s","model_status":"%s","channels":[%s],"provider":"%s","auth_status":"%s","agent":"%s","json_parser":"%s","running_jobs":%s,"named_sessions":%s,"state_dir":"%s"}\n' \
      "$(json_escape "$ready")" "$(json_escape "$bin")" "$(json_escape "$bin_status")" \
      "$(json_escape "$version")" "$(json_escape "$model")" "$(json_escape "$model_status")" \
      "$chan_json" "$(json_escape "$provider")" "$(json_escape "$auth_status")" \
      "$(json_escape "$DEFAULT_AGENT")" "$(json_escape "${parser:-нет}")" \
      "$running" "${named:-0}" "$(json_escape "$STATE_DIR")"
  else
    echo "готовность:   $ready"
    echo "бинарь:       ${bin:-не найден} ($bin_status)"
    echo "версия:       ${version:-—}"
    echo "модель:       $model ($(model_status_ru "$model_status"))"
    printf 'каналы:       %s\n' "$(printf '%s' "$chan_lines" | sed '2,$s/^/              /')"
    echo "провайдер:    $provider (учётные данные: $(auth_status_ru "$auth_status"))"
    echo "агент:        $DEFAULT_AGENT (по умолчанию $(mode_ru read-only))"
    echo "разбор ответа: ${parser:-нет (нужен python3 или jq)}"
    echo "фоновых задач в работе: $running"
    echo "именованных сессий: ${named:-0}"
    echo "каталог состояния: $STATE_DIR"
  fi
  [[ "$ready" == "yes" ]] || exit 1
}

# Статус модели по каталогу `opencode models`. Пустой каталог — это «спросить
# не удалось» (нет сети, битый бинарь), а не «модели нет».
model_status_in() {
  local model="$1" models_out="$2"
  [[ -n "$models_out" ]] || { echo unknown; return 0; }
  if printf '%s\n' "$models_out" | grep -qxF "$model"; then echo ok; else echo missing; fi
}

model_status_ru() {
  case "$1" in
    ok)      echo "доступна" ;;
    missing) echo "нет в каталоге моделей" ;;
    *)       echo "проверить не удалось" ;;
  esac
}

auth_status_ru() {
  case "$1" in
    ok)      echo "есть" ;;
    missing) echo "не найдены" ;;
    *)       echo "проверить не удалось" ;;
  esac
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
  local mode="read-only" model="" agent="" variant="" workdir="" timeout_s="" background=0 label="" name=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --session)    name="$(need_value --session "${2:-}")"; shift 2 ;;
      --write)      mode="write"; shift ;;
      --bash)       [[ "$mode" == "write" ]] || mode="read-bash"; shift ;;
      --model)      model="$(need_value --model "${2:-}")" || exit $?
                    model="$(resolve_model "$model")" || exit $?
                    shift 2 ;;
      --agent)      agent="$(need_value --agent "${2:-}")"; shift 2 ;;
      --variant)    variant="$(need_value --variant "${2:-}")"; shift 2 ;;
      --cwd)        workdir="$(need_value --cwd "${2:-}")"; shift 2 ;;
      --timeout)    timeout_s="${2:-}"; [[ -z "$timeout_s" ]] && die 2 "--timeout требует значение"; shift 2 ;;
      --label)      label="${2:-}"; [[ -z "$label" ]] && die 2 "--label требует значение"; shift 2 ;;
      --background) background=1; shift ;;
      -h|--help)    usage ;;
      *)            die 2 "неизвестная опция '$1' (промпт передаётся на stdin, не аргументом)" ;;
    esac
  done
  # --variant не валидируется по фиксированному списку: допустимые значения
  # (high, max, minimal и т.п.) зависят от модели и провайдера.

  workdir="${workdir:-$PWD}"
  [[ -d "$workdir" ]] || die 2 "каталог '$workdir' не существует"
  workdir="$(cd "$workdir" && pwd)"

  if [[ -n "$name" ]]; then
    check_session_name "$name"
    local existing; existing="$(resolve_session_name "$name" "$workdir" || true)"
    [[ -n "$existing" ]] && die 2 "сессия с именем '$name' уже есть ($existing) — продолжить её: opencode-run.sh resume --session '$name'; новая сессия требует другого имени"
  fi

  start_run "$mode" "$model" "$agent" "$variant" "$workdir" "$timeout_s" \
            "$background" "$label" "$name"
}

# Общая часть run и resume: собрать команду opencode и отправить её в
# foreground или в фон. RESUME_SID к этому моменту уже проставлен (или пуст).
start_run() {
  local mode="$1" model="$2" agent="$3" variant="$4" workdir="$5" timeout_s="$6"
  local background="$7" label="$8" name="$9"

  # Потолок зависит от режима: у фона нет внешнего ограничителя, у foreground
  # он есть, и там дефолт специально ниже.
  if [[ -z "$timeout_s" ]]; then
    if [[ $background -eq 1 ]]; then timeout_s="$DEFAULT_BG_TIMEOUT"; else timeout_s="$DEFAULT_TIMEOUT"; fi
  fi
  [[ "$timeout_s" =~ ^[0-9]+$ ]] || die 2 "--timeout принимает целое число секунд (0 — без ограничения)"

  [[ -n "$(json_tool)" ]] || die 2 "нужен python3 или jq: ответ собирается из потока событий opencode (--format json)"

  local prompt
  prompt="$(cat)"
  [[ -z "${prompt//[[:space:]]/}" ]] && die 2 "пустой промпт на stdin"

  local bin; bin="$(resolve_opencode)"
  model="$(expand_channel "${model:-$DEFAULT_MODEL}")"
  agent="${agent:-$DEFAULT_AGENT}"

  # --auto обязателен: у headless-прогона нет интерактивного канала одобрения,
  # и запрос разрешения превратился бы в зависание. Настоящая граница прав —
  # OPENCODE_PERMISSION, а не отсутствие --auto.
  local args=(run --format json --auto --model "$model" --agent "$agent")
  [[ -n "$variant" ]] && args+=(--variant "$variant")
  if [[ -n "$RESUME_SID" ]]; then
    args+=(--session "$RESUME_SID")
  elif [[ -n "$name" ]]; then
    # Заголовок сессии — это и есть её имя: по нему её потом найдёт resume,
    # даже если каталог состояния потеряли.
    args+=(--title "$name")
  fi

  if [[ $background -eq 1 ]]; then
    run_background "$bin" "$mode" "$workdir" "$timeout_s" "$prompt" \
      "$model" "$agent" "$variant" "$label" "$name" "${args[@]}"
    return 0
  fi

  run_foreground "$bin" "$mode" "$workdir" "$timeout_s" "$prompt" "$name" "$model" "${args[@]}"
}

run_foreground() {
  local bin="$1" mode="$2" workdir="$3" timeout_s="$4" prompt="$5" name="$6" model="$7"; shift 7
  local args=("$@")
  local tb; tb="$(pick_timeout_bin)"
  local events_file err_file out_file rc=0
  events_file="$(mktemp "${TMPDIR:-/tmp}/opencode-events.XXXXXX")"
  err_file="$(mktemp "${TMPDIR:-/tmp}/opencode-stderr.XXXXXX")"
  out_file="$(mktemp "${TMPDIR:-/tmp}/opencode-out.XXXXXX")"
  CLEANUP_PATHS+=("$events_file" "$err_file" "$out_file")

  local perm; perm="$(permission_json "$mode")"
  local cmd=(env "OPENCODE_PERMISSION=$perm" "$bin" "${args[@]}")
  if [[ -n "$tb" ]]; then
    (cd "$workdir" && printf '%s' "$prompt" | "$tb" "$timeout_s" "${cmd[@]}") >"$events_file" 2>"$err_file" || rc=$?
  else
    (cd "$workdir" && printf '%s' "$prompt" | "${cmd[@]}") >"$events_file" 2>"$err_file" || rc=$?
  fi

  # Имя сессии запоминаем, даже если прогон упал: сессия уже создана, и
  # продолжать надо именно её.
  if [[ -n "$name" ]]; then
    local sid; sid="$(session_id_from_events "$events_file" || true)"
    [[ -n "$sid" ]] && remember_session_name "$name" "$sid" "$workdir" "$model"
  fi

  extract_answer "$events_file" > "$out_file" || true

  local err_text=""
  [[ -s "$err_file" ]] && err_text="$(tail -c 500 "$err_file")"

  if [[ $rc -eq 124 ]]; then
    [[ -s "$out_file" ]] && cat "$out_file"
    die 6 "opencode: таймаут ${timeout_s}с. Задача слишком большая для одного прогона — перезапусти её с --background, тогда потолок снимается."
  fi
  if [[ $rc -ne 0 ]]; then
    # Содержательный кусок ответа, если он успел появиться, всё равно отдаём:
    # он полезнее кода возврата. Причина отказа идёт в stderr, к die.
    [[ -s "$out_file" ]] && cat "$out_file"
    die 6 "opencode: прогон завершился с кодом $rc${err_text:+ — $err_text}"
  fi
  [[ -s "$out_file" ]] || die 6 "opencode: пустой ответ — проверь готовность командой check${err_text:+. stderr: $err_text}"
  cat "$out_file"
}

# Идентификатор задачи — он же имя её каталога, поэтому он же и замок: имя
# захватывается атомарным mkdir, занятое просто отбрасывается и берётся
# следующее. Одних date и $$ мало — они совпадают у двух фоновых запусков из
# одного процесса-скрипта в одну секунду, и вторая задача затёрла бы первой
# prompt, output и meta.
claim_job_dir() {
  local stamp id dir i=0
  stamp="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$JOBS_DIR"
  while :; do
    id="opencode-$stamp-$$-$RANDOM"
    dir="$JOBS_DIR/$id"
    mkdir "$dir" 2>/dev/null && { printf '%s' "$id"; return 0; }
    i=$((i+1))
    [[ $i -ge 100 ]] && die 5 "не удалось выделить идентификатор задачи в $JOBS_DIR"
  done
}

run_background() {
  local bin="$1" mode="$2" workdir="$3" timeout_s="$4" prompt="$5"
  local model="$6" agent="$7" variant="$8" label="$9" name="${10}"; shift 10
  local args=("$@")
  local job_id job_dir
  job_id="$(claim_job_dir)"
  job_dir="$JOBS_DIR/$job_id"
  chmod 700 "$STATE_DIR" "$JOBS_DIR" 2>/dev/null || true
  chmod 700 "$job_dir"

  printf '%s' "$prompt" > "$job_dir/prompt.txt"
  : > "$job_dir/events.jsonl"
  : > "$job_dir/output.txt"
  : > "$job_dir/stderr.txt"
  {
    echo "id=$job_id"
    echo "status=running"
    echo "cwd=$workdir"
    echo "mode=$mode"
    echo "model=${model:-—}"
    echo "agent=${agent:-—}"
    echo "variant=${variant:-—}"
    echo "label=${label:-—}"
    echo "session=${SESSION_ID:-—}"
    echo "session_name=${name:-—}"
    echo "opencode_session=${RESUME_SID:-—}"
    echo "resumed_from=${RESUME_FROM:-—}"
    echo "timeout=$(if [[ -n "$(pick_timeout_bin)" ]]; then echo "$timeout_s"; else echo "none (нет coreutils timeout)"; fi)"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "started_epoch=$(date +%s)"
  } > "$job_dir/meta"

  local perm; perm="$(permission_json "$mode")"

  # Отвязанный воркер: он же проставляет итог. opencode запускается фоном
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
    # приводила к потере статуса и осиротевшим задачам.
    # Свой pid субшелл в bash 3.2 иначе не узнаёт: $$ там принадлежит родителю.
    meta_set worker_pid "$(sh -c 'echo $PPID')" "$job_dir/meta"
    tb="$(pick_timeout_bin)"
    cd "$workdir" || exit 1
    cmd=(env "OPENCODE_PERMISSION=$perm" "$bin" "${args[@]}")
    if [[ -n "$tb" ]]; then
      cat "$job_dir/prompt.txt" | "$tb" "$timeout_s" "${cmd[@]}" \
        > "$job_dir/events.jsonl" 2> "$job_dir/stderr.txt" &
    else
      cat "$job_dir/prompt.txt" | "${cmd[@]}" \
        > "$job_dir/events.jsonl" 2> "$job_dir/stderr.txt" &
    fi
    inner=$!
    meta_set pid "$inner" "$job_dir/meta"
    wait "$inner" || rc=$?

    extract_answer "$job_dir/events.jsonl" > "$job_dir/output.txt" 2>/dev/null || true

    sid="$(session_id_from_events "$job_dir/events.jsonl" || true)"
    if [[ -n "$sid" ]]; then
      meta_set opencode_session "$sid" "$job_dir/meta"
      [[ -n "$name" ]] && remember_session_name "$name" "$sid" "$workdir" "$model"
    fi

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
  ) >/dev/null 2>&1 &
  disown 2>/dev/null || true
  set +m

  echo "$job_id"
}

# --- общее для работы с джобами --------------------------------------------
job_dir_of() {
  local job_id="$1"
  [[ -n "$job_id" ]] || die 2 "нужен job-id (список — opencode-run.sh status)"
  # Идентификатор идёт в путь, поэтому его форма проверяется строго: иначе
  # `result ../../что-то` читает и переписывает каталоги вне JOBS_DIR.
  case "$job_id" in
    */*|*..*) die 2 "недопустимый job-id '$job_id'" ;;
  esac
  local dir="$JOBS_DIR/$job_id"
  [[ -d "$dir" ]] || die 2 "нет задачи с id '$job_id' (список — opencode-run.sh status --all)"
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
    # OPENCODE_BIN может называться как угодно.
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
  # Сюда попадаем в двух разных случаях: процесс opencode ещё не стартовал или
  # он уже вышел. Различает их не pid, а воркер — и в обоих случаях ответ даёт
  # именно его живость.
  #
  # Выход opencode — НЕ конец задачи: после него воркер ещё собирает ответ из
  # потока событий (extract_answer) и дописывает пять полей meta
  # (opencode_session, finished_epoch, finished, exit, status). Всё это время
  # meta.status ещё `running`, а pid уже мёртв. Считать такую задачу
  # осиротевшей нельзя: `result` успешной задачи печатал верный ответ и тут же
  # падал кодом 6 с «воркер задачи исчез» — гонка ловилась примерно на каждом
  # четвёртом фоновом прогоне. Пока воркер жив, задача дописывает итог, то есть
  # всё ещё running.
  local wp start
  wp="$(meta_get worker_pid "$dir/meta")"
  if [[ -n "$wp" && "$wp" != "—" ]] && kill -0 "$wp" 2>/dev/null; then echo running; return 0; fi
  # Воркер не успел записать свой pid — это доли секунды после запуска. Минуты
  # без pid означают, что воркер не поднялся вообще.
  if [[ -z "$pid" || "$pid" == "—" ]]; then
    start="$(meta_get started_epoch "$dir/meta")"
    if [[ -n "$start" ]] && (( $(date +%s) - start < 60 )); then echo running; return 0; fi
  fi
  # Последняя проверка перед приговором: воркер мог дописать итог и завершиться
  # между чтением status в начале функции и проверкой живости — тогда задача не
  # осиротевшая, а нормально закончившаяся.
  st="$(meta_get status "$dir/meta")"
  [[ "$st" != "running" ]] && { echo "$st"; return 0; }
  echo orphaned
}

# «Своя» задача — запущенная из этого рабочего каталога или его поддерева.
# Сессия уточняет ответ, когда её идентификатор передан через
# OPENCODE_CLAUDE_SESSION; сам Claude Code в окружении Bash-инструмента его не
# отдаёт.
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
      # 37 — ширина идентификатора со случайным суффиксом; уже него колонки
      # разъезжаются, как только в списке окажется задача нового формата.
      printf '%-37s %-10s %-8s %-22s %-16s %s\n' \
        "$dir" "$st" "$(elapsed_of "$d")" \
        "$(meta_get model "$d/meta")" \
        "$(meta_get session_name "$d/meta")" \
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
  printf '{"id":"%s","status":"%s","meta_status":"%s","label":"%s","cwd":"%s","mode":"%s","model":"%s","agent":"%s","variant":"%s","session":"%s","session_name":"%s","opencode_session":"%s","resumed_from":"%s","started":"%s","elapsed":"%s","timeout":"%s","exit":"%s","output_bytes":%s}' \
    "$(json_escape "$(meta_get id "$dir/meta")")" \
    "$(json_escape "$st")" \
    "$(json_escape "$(meta_get status "$dir/meta")")" \
    "$(json_escape "$(meta_get label "$dir/meta")")" \
    "$(json_escape "$(meta_get cwd "$dir/meta")")" \
    "$(json_escape "$(meta_get mode "$dir/meta")")" \
    "$(json_escape "$(meta_get model "$dir/meta")")" \
    "$(json_escape "$(meta_get agent "$dir/meta")")" \
    "$(json_escape "$(meta_get variant "$dir/meta")")" \
    "$(json_escape "$(meta_get session "$dir/meta")")" \
    "$(json_escape "$(meta_get session_name "$dir/meta")")" \
    "$(json_escape "$(meta_get opencode_session "$dir/meta")")" \
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
      die 5 "задача ещё выполняется ($(elapsed_of "$dir") с $(meta_get started "$dir/meta")); опроси позже: opencode-run.sh status $job_id"
      ;;
    orphaned)
      # У осиротевшей задачи ответ мог не собраться: воркер не дошёл до разбора
      # событий. Собираем из того, что успело записаться.
      [[ -s "$dir/output.txt" ]] || extract_answer "$dir/events.jsonl" > "$dir/output.txt" 2>/dev/null || true
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
# Прогресс живой задачи. У opencode собственный поток событий (--format json)
# идёт в файл по мере работы, поэтому здесь видно не только «жив ли процесс»,
# но и какие инструменты он звал. Смешивать это с выводом `result` нельзя —
# там должен остаться только чистый ответ.
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
  echo "события: $(file_bytes "$dir/events.jsonl") байт накоплено"
  echo "ответ:   $(file_bytes "$dir/output.txt") байт"
  if [[ -s "$dir/stderr.txt" ]]; then
    echo "--- stderr opencode ---"
    tail -n 10 "$dir/stderr.txt"
  fi
  if [[ -s "$dir/events.jsonl" ]]; then
    echo "--- последние $tail_n событий ---"
    render_events "$dir/events.jsonl" "$tail_n"
  elif [[ "$st" == "running" ]]; then
    echo "событий пока нет. Это может быть норма в первые секунды прогона —"
    echo "признак работы — сам статус running и растущее время."
  else
    echo "событий нет"
  fi
}

# Человекочитаемый пересказ JSONL-потока: что за событие, какой инструмент и с
# чем его звали. Без python3/jq — просто хвост сырых строк.
render_events() {
  local events="$1" tail_n="$2" tool
  tool="$(json_tool)"
  if [[ "$tool" == "python3" ]]; then
    tail -n "$tail_n" "$events" | python3 -c '
import json, sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        event = json.loads(line)
    except Exception:
        print(line[:200])
        continue
    kind = event.get("type", "?")
    part = event.get("part") or {}
    if kind == "tool_use":
        state = part.get("state") or {}
        args = state.get("input") or {}
        brief = "; ".join("%s=%s" % (k, str(v)[:80]) for k, v in list(args.items())[:3])
        print("[инструмент] %s %s -> %s" % (part.get("tool", "?"), brief, state.get("status", "?")))
    elif kind == "text":
        print("[текст] %s" % (part.get("text", "").replace("\n", " ")[:200]))
    elif kind == "step_finish":
        tokens = part.get("tokens") or {}
        print("[шаг] %s (вход %s, выход %s)" % (part.get("reason", "?"), tokens.get("input", "?"), tokens.get("output", "?")))
    elif kind == "step_start":
        print("[шаг] начат")
    else:
        print("[%s]" % kind)
' 2>/dev/null || tail -n "$tail_n" "$events"
  elif [[ "$tool" == "jq" ]]; then
    tail -n "$tail_n" "$events" | jq -r '
      if .type == "tool_use" then "[инструмент] " + (.part.tool // "?") + " -> " + (.part.state.status // "?")
      elif .type == "text" then "[текст] " + ((.part.text // "") | gsub("\n"; " ") | .[0:200])
      elif .type == "step_finish" then "[шаг] " + (.part.reason // "?")
      elif .type == "step_start" then "[шаг] начат"
      else "[" + (.type // "?") + "]" end
    ' 2>/dev/null || tail -n "$tail_n" "$events"
  else
    tail -n "$tail_n" "$events"
  fi
}

# --- cancel -----------------------------------------------------------------
# Убиваем всё дерево: opencode поднимает собственный сервер и порождает
# подпроцессы (shell-команды модели, тесты, инструменты). kill только по
# верхнему pid оставил бы их жить.
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

  # Даём воркеру дописать итог; если opencode не реагирует на TERM — добиваем
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
    canceled) echo "$job_id: снята ($(elapsed_of "$dir") работы)" ;;
    running)  echo "$job_id: снять не удалось — процесс не отвечает; посмотри status $job_id" ;;
    *)        echo "$job_id: успела завершиться сама до отмены ($final)" ;;
  esac
}

cmd_cancel() {
  local target="${1:-}"
  [[ "$target" == "-h" || "$target" == "--help" ]] && usage
  [[ -n "$target" ]] || die 2 "нужен job-id или --all (список — opencode-run.sh status)"
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
# Джобы хранят промпт, поток событий и ответ открытым текстом и сами не
# исчезают. Уборка — явная команда, потому что удалять чужой результат молча
# нельзя. Сами сессии opencode этой командой не трогаются: они живут в его
# собственном хранилище, их чистит `opencode session delete`.
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
  echo "сессии opencode не удалялись — они живут в хранилище opencode (opencode session delete)"
}

# --- resume -----------------------------------------------------------------
# Продолжение сессии: по имени (--session <имя> или первым аргументом) или по
# job-id прошлой задачи. Промпт попадает в ТУ ЖЕ сессию opencode, поэтому вся
# её история модели видна. Сессии нет — код 2, вызывающий откатывается на run.
cmd_resume() {
  local target="" name="" mode="" model="" agent="" variant="" workdir="" \
        timeout_s="" background=0 label=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --session)    name="$(need_value --session "${2:-}")"; shift 2 ;;
      --write)      mode="write"; shift ;;
      --bash)       [[ "$mode" == "write" ]] || mode="read-bash"; shift ;;
      --model)      model="$(need_value --model "${2:-}")" || exit $?
                    model="$(resolve_model "$model")" || exit $?
                    shift 2 ;;
      --agent)      agent="$(need_value --agent "${2:-}")"; shift 2 ;;
      --variant)    variant="$(need_value --variant "${2:-}")"; shift 2 ;;
      --cwd)        workdir="$(need_value --cwd "${2:-}")"; shift 2 ;;
      --timeout)    timeout_s="${2:-}"; [[ -z "$timeout_s" ]] && die 2 "--timeout требует значение"; shift 2 ;;
      --label)      label="${2:-}"; [[ -z "$label" ]] && die 2 "--label требует значение"; shift 2 ;;
      --background) background=1; shift ;;
      -h|--help)    usage ;;
      -*)           die 2 "неизвестная опция '$1' (промпт передаётся на stdin, не аргументом)" ;;
      *)            [[ -n "$target" ]] && die 2 "лишний аргумент '$1'"
                    target="$1"; shift ;;
    esac
  done

  [[ -n "$target" || -n "$name" ]] \
    || die 2 "нужно имя сессии или job-id: opencode-run.sh resume --session <имя> (список — opencode-run.sh sessions)"

  # Позиционный аргумент может быть и job-id, и именем сессии: job-id узнаётся
  # по существующему каталогу задачи, всё остальное — имя.
  local job_dir=""
  if [[ -n "$target" ]]; then
    case "$target" in
      */*|*..*) die 2 "недопустимый аргумент '$target'" ;;
    esac
    if [[ -d "$JOBS_DIR/$target" ]]; then
      job_dir="$JOBS_DIR/$target"
    elif [[ -z "$name" ]]; then
      name="$target"
    else
      die 2 "указаны и job-id '$target', и --session '$name' — оставь что-то одно"
    fi
  fi

  local sid="" src_cwd="" src_mode="" src_model="" src_agent="" src_variant="" src_name=""
  if [[ -n "$job_dir" ]]; then
    local st; st="$(job_status_of "$job_dir")"
    [[ "$st" == "running" ]] && die 2 "задача '$target' ещё выполняется — resume после её окончания; иначе вызывающий откатывается на новый прогон"
    sid="$(meta_get opencode_session "$job_dir/meta")"
    if [[ -z "$sid" || "$sid" == "—" ]]; then
      sid="$(session_id_from_events "$job_dir/events.jsonl" || true)"
      [[ -n "$sid" ]] && meta_set opencode_session "$sid" "$job_dir/meta"
    fi
    src_cwd="$(meta_get cwd "$job_dir/meta")"
    src_mode="$(meta_get mode "$job_dir/meta")"
    src_model="$(meta_get model "$job_dir/meta")"
    src_agent="$(meta_get agent "$job_dir/meta")"
    src_variant="$(meta_get variant "$job_dir/meta")"
    src_name="$(meta_get session_name "$job_dir/meta")"
    [[ "$src_name" == "—" ]] && src_name=""
    [[ -n "$sid" && "$sid" != "—" ]] \
      || die 2 "у задачи '$target' нет id сессии opencode — вызывающий должен откатиться на новый прогон (opencode-run.sh run)"
    RESUME_FROM="$target"
    [[ -n "$name" ]] || name="$src_name"
  else
    check_session_name "$name"
    local probe_cwd="${workdir:-$PWD}"
    [[ -d "$probe_cwd" ]] && probe_cwd="$(cd "$probe_cwd" && pwd)"
    sid="$(resolve_session_name "$name" "$probe_cwd" || true)"
    [[ -n "$sid" ]] \
      || die 2 "сессии с именем '$name' нет ни в состоянии обвязки, ни в списке opencode — начни новую: opencode-run.sh run --session '$name'"
    src_cwd="$(meta_get cwd "$(name_file "$name")")"
    src_model="$(meta_get model "$(name_file "$name")")"
  fi

  # Каталог, модель и права по умолчанию наследуются от прошлого прогона —
  # продолжение той же работы не должно менять их молча.
  [[ -n "$workdir" ]] || workdir="$src_cwd"
  [[ -n "$workdir" && -d "$workdir" ]] || workdir="$PWD"
  workdir="$(cd "$workdir" && pwd)"
  [[ -n "$model" ]] || { [[ -n "$src_model" && "$src_model" != "—" ]] && model="$src_model"; }
  [[ -n "$agent" ]] || { [[ -n "$src_agent" && "$src_agent" != "—" ]] && agent="$src_agent"; }
  [[ -n "$variant" ]] || { [[ -n "$src_variant" && "$src_variant" != "—" ]] && variant="$src_variant"; }
  # Права наследуются от исходного прогона, если их не задали явно: продолжение
  # той же работы не должно молча ни расширять их, ни сужать.
  if [[ -z "$mode" ]]; then
    case "$src_mode" in
      write|read-bash|read-only) mode="$src_mode" ;;
      *)                         mode="read-only" ;;
    esac
  fi
  [[ -n "$label" ]] || label="продолжение ${name:-$target}"

  RESUME_SID="$sid"
  # Имя в meta нужно для карточки задачи, а перезаписывать файл имени незачем:
  # id сессии не поменялся.
  start_run "$mode" "$model" "$agent" "$variant" "$workdir" "$timeout_s" \
            "$background" "$label" "${name:-}"
}

# --- sessions ---------------------------------------------------------------
# Пустое значение в колонке — это «неизвестно», а не пустая строка в таблице.
model_or_dash() {
  local v="$1"
  [[ -n "$v" ]] && printf '%s' "$v" || printf '%s' "—"
}

cmd_sessions() {
  local as_json=0
  case "${1:-}" in
    --json)    as_json=1 ;;
    -h|--help) usage ;;
    "")        ;;
    *)         die 2 "неизвестная опция '$1'" ;;
  esac

  if [[ ! -d "$NAMES_DIR" ]] || [[ -z "$(ls -1 "$NAMES_DIR" 2>/dev/null)" ]]; then
    [[ $as_json -eq 1 ]] && { echo '[]'; return 0; }
    echo "именованных сессий нет (имя задаётся при запуске: run --session <имя>)" >&2
    exit 1
  fi

  local f out="" shown=0
  for f in "$NAMES_DIR"/*; do
    [[ -f "$f" ]] || continue
    shown=$((shown+1))
    if [[ $as_json -eq 1 ]]; then
      out+="$(printf '{"name":"%s","id":"%s","cwd":"%s","model":"%s","updated":"%s"}' \
        "$(json_escape "$(meta_get name "$f")")" \
        "$(json_escape "$(meta_get id "$f")")" \
        "$(json_escape "$(meta_get cwd "$f")")" \
        "$(json_escape "$(meta_get model "$f")")" \
        "$(json_escape "$(meta_get updated "$f")")"),"
    else
      # Канал печатается и здесь, а не только в --json: по человеческому
      # списку выбирают, какую сессию продолжать, а продолжение идёт на её
      # модели. Прочерк — сессия из состояния, записанного до появления
      # каналов; продолжится она на модели по умолчанию.
      printf '%-28s %-32s %-26s %-22s %s\n' \
        "$(meta_get name "$f")" "$(meta_get id "$f")" \
        "$(model_or_dash "$(meta_get model "$f")")" \
        "$(meta_get updated "$f")" "$(meta_get cwd "$f")"
    fi
  done
  [[ $as_json -eq 1 ]] && printf '[%s]\n' "${out%,}"
  return 0
}

# --- transcript -------------------------------------------------------------
# Ход сессии целиком отдаёт сам opencode: `opencode export <ses_...>` печатает
# JSON сессии в stdout (служебная строка «Exporting session…» идёт в stderr).
# Это и есть место, где видно, что продолжение легло в ту же сессию: id и
# список сообщений общий.
cmd_transcript() {
  local job_id="" name=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --session) name="$(need_value --session "${2:-}")"; shift 2 ;;
      -h|--help) usage ;;
      -*)        die 2 "неизвестная опция '$1'" ;;
      *)         [[ -n "$job_id" ]] && die 2 "лишний аргумент '$1'"
                 job_id="$1"; shift ;;
    esac
  done

  local bin; bin="$(resolve_opencode)"
  local sid=""
  if [[ -n "$job_id" ]]; then
    local dir; dir="$(job_dir_of "$job_id")"
    sid="$(meta_get opencode_session "$dir/meta")"
    if [[ -z "$sid" || "$sid" == "—" ]]; then
      sid="$(session_id_from_events "$dir/events.jsonl" || true)"
    fi
    [[ -n "$sid" && "$sid" != "—" ]] \
      || die 2 "у задачи '$job_id' ещё нет id сессии opencode (прогон не начался или события не записались)"
  elif [[ -n "$name" ]]; then
    check_session_name "$name"
    sid="$(resolve_session_name "$name" "$PWD" || true)"
    [[ -n "$sid" ]] || die 2 "сессии с именем '$name' нет — список: opencode-run.sh sessions"
  else
    sid="$(latest_session_for_cwd "$PWD" || true)"
    [[ -n "$sid" ]] || die 2 "для каталога '$PWD' сессий opencode не найдено — укажи job-id или --session <имя>"
    echo "внимание: ни job-id, ни имя не даны — показана последняя сессия этого каталога ($sid), она может быть и твоей интерактивной сессией opencode" >&2
  fi

  "$bin" export "$sid" 2>/dev/null || die 2 "opencode export не смог отдать сессию '$sid' (её могли удалить)"
}

latest_session_for_cwd() {
  local workdir="$1" bin tool json
  bin="$(find_opencode_bin)"
  [[ -n "$bin" ]] || return 0
  tool="$(json_tool)"
  [[ -n "$tool" ]] || return 0
  json="$("$bin" session list --format json 2>/dev/null)" || return 0
  [[ -n "$json" ]] || return 0
  if [[ "$tool" == "python3" ]]; then
    printf '%s' "$json" | python3 -c '
import json, sys

workdir = sys.argv[1]
try:
    rows = json.load(sys.stdin)
except Exception:
    sys.exit(0)
hits = [r for r in rows if r.get("directory") == workdir]
if hits:
    print(max(hits, key=lambda r: r.get("updated") or 0).get("id", ""))
' "$workdir" 2>/dev/null
  else
    printf '%s' "$json" | jq -r --arg d "$workdir" '
      [.[] | select(.directory == $d)]
      | if length == 0 then empty else (sort_by(.updated) | last | .id) end
    ' 2>/dev/null
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
  sessions)   cmd_sessions "$@" ;;
  transcript) cmd_transcript "$@" ;;
  -h|--help)  usage ;;
  *)          die 2 "неизвестная подкоманда '$sub'" ;;
esac
