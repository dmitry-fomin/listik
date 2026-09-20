#!/usr/bin/env bash
# pi-run.sh — единая точка запуска CLI-агента pi (`pi --mode rpc`) из Claude Code.
#
#   pi-run.sh check [--json] [--no-probe] [--probe-timeout <сек>]
#   pi-run.sh run [опции] < prompt.txt
#   pi-run.sh resume <имя-сессии|job-id> [опции] < prompt.txt
#   pi-run.sh status [--json] [--all] [--running] [job-id]
#   pi-run.sh result <job-id> [--wait [сек]]
#   pi-run.sh logs <job-id> [--tail N]
#   pi-run.sh cancel <job-id|--all>        # kill — синоним cancel
#   pi-run.sh clean [--older-than <дней>] [--all]
#   pi-run.sh sessions [--json]
#   pi-run.sh transcript <job-id> | transcript --session <имя>
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
# --- Отличия от opencode-run.sh ---------------------------------------------
#
# 1. Каждый прогон — это не сам `pi`, а обвязка над ним из соседнего файла
#    `pi-rpc.py` (порция a): `python3 plugins/pi/scripts/pi-rpc.py …`. Она и
#    ведёт диалог RPC с pi, и печатает в свой stdout ровно финальный ответ —
#    этот скрипт занимается только job-management вокруг неё (job-id,
#    meta-файл, фоновый воркер, kill_tree, status/result/logs/cancel/clean),
#    как и `opencode-run.sh` вокруг `opencode run`.
#
# 2. Сессия адресуется ФАЙЛОМ, а не uuid. `pi --session <uuid>` ищет сессию
#    только в каталоге запуска; `pi --session <путь к файлу>` работает из
#    любого каталога — поэтому обвязка всегда продолжает сессию по пути файла
#    (`pi_session_file` из `--meta` клиента), а не по id. Это не «упрощение
#    зря», это единственный вариант, который переживает смену рабочего
#    каталога между run и resume.
#
# 3. Прав уровня ОС у pi нет (как и у opencode). Граница — allowlist
#    инструментов через `--tools` клиента: `read,grep,find,ls` для чтения,
#    плюс `bash` для read-bash, без ограничения вовсе для write.
#
# 4. Готовность канала проверяется ПРОБНЫМ ПРОГОНОМ, а не `pi auth check`:
#    `pi auth check --provider b-ai-glm` врёт (`not_ready`/`provider_not_found`),
#    хотя прогон на этом канале проходит — провайдер регистрируется
#    расширением, а не статичной конфигурацией, которую видит `auth check`.
#
# 5. Флага `--rpc` у pi нет — правильно `pi --mode rpc` (клиент собирает это
#    сам, здесь просто отмечено, чтобы не «исправляли» на несуществующий флаг).
#
# 6. Расширения НЕ отключаются: `~/.pi/agent/extensions/b-ai.ts` регистрирует
#    провайдеров `b-ai-glm`/`b-ai-deepseek`, и `--no-extensions` убрал бы их
#    из списка провайдеров вовсе.
#
# 7. Канал GLM (`b-ai-glm/glm-5.3-flash`) иногда зависает до первого токена
#    (>120с без ответа) — это не сбой обвязки, у пробы `check` поэтому есть
#    отдельный статус «таймаут», а не падение с ошибкой.
#
# 8. Foreground `run` БЕЗ `--session` идёт с `--no-session` (сессия не
#    создаётся на диске вовсе — иначе она осталась бы там навсегда без
#    способа её продолжить). Foreground с `--session <имя>` и любой `run
#    --background` всегда заводят именованную/адресуемую сессию.
set -euo pipefail

STATE_DIR="${PI_CLAUDE_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/pi-claude}"
JOBS_DIR="$STATE_DIR/jobs"
# Имя сессии → путь к файлу сессии pi. Один файл на имя, формат тот же
# key=value, что и у meta джобы.
NAMES_DIR="$STATE_DIR/sessions"
# rpc-клиент лежит рядом со скриптом (порция a), путь абсолютный — нужен и
# фоновому воркеру, который может пережить смену PWD вызывающего.
RPC="$(cd "$(dirname "$0")" && pwd)/pi-rpc.py"

# Каналы: короткое имя → полный идентификатор provider/model. Пара
# «имя:модель» на строку, порядок — порядок печати в check; первый канал
# считается каналом по умолчанию (решение автора: glm).
CHANNELS=(
  "glm:b-ai-glm/glm-5.3-flash"
  "deepseek:b-ai-deepseek/deepseek-v4.1-flash"
)

THINKING_LEVELS=(off minimal low medium high xhigh max)

# Короткое имя канала → полный идентификатор. Всё, что не короткое имя,
# возвращается как есть.
expand_channel() {
  local want="$1" pair
  for pair in "${CHANNELS[@]}"; do
    [[ "$want" == "${pair%%:*}" ]] && { printf '%s' "${pair#*:}"; return 0; }
  done
  printf '%s' "$want"
}

is_channel_name() {
  local want="$1" pair
  for pair in "${CHANNELS[@]}"; do
    [[ "$want" == "${pair%%:*}" ]] && return 0
  done
  return 1
}

channel_list_str() {
  local names="" pair
  for pair in "${CHANNELS[@]}"; do names="${names:+$names, }${pair%%:*}"; done
  printf '%s' "$names"
}

# Полный идентификатор → короткое имя канала, если он совпадает с одним из
# каналов; иначе пусто (это просто provider/model без короткого имени).
model_channel_name() {
  local m="$1" pair
  for pair in "${CHANNELS[@]}"; do
    [[ "${pair#*:}" == "$m" ]] && { printf '%s' "${pair%%:*}"; return 0; }
  done
  printf ''
}

# --model: короткое имя канала раскрываем, provider/model пропускаем как есть.
resolve_model() {
  local want="$1" resolved
  resolved="$(expand_channel "$want")"
  case "$resolved" in
    */*) printf '%s' "$resolved"; return 0 ;;
  esac
  die 2 "unknown channel '$want' - short names: $(channel_list_str); or a full provider/model id"
}

# --channel: принимает ТОЛЬКО короткие имена каналов.
resolve_channel_only() {
  local want="$1"
  is_channel_name "$want" || die 2 "unknown channel '$want' - short names: $(channel_list_str)"
  printf '%s' "$want"
}

# provider/model → провайдер (до первого /), модель (всё после). У openrouter
# модели сами содержат /, поэтому только первый разделитель значим.
split_provider_model() {
  local combined="$1"
  PROV="${combined%%/*}"
  MOD="${combined#*/}"
}

check_thinking() {
  local lvl="$1" t
  for t in "${THINKING_LEVELS[@]}"; do [[ "$t" == "$lvl" ]] && return 0; done
  die 2 "invalid --thinking '$lvl' - allowed values: ${THINKING_LEVELS[*]}"
}

# Модель по умолчанию: канал или полный идентификатор из переменной среды,
# иначе первый канал списка (glm).
DEFAULT_MODEL="$(expand_channel "${PI_CLAUDE_DEFAULT_MODEL:-${CHANNELS[0]#*:}}")"

# Foreground держим заметно ниже потолка Bash-инструмента (600с): скрипт
# должен успеть вернуть осмысленную ошибку раньше, чем его оборвут снаружи.
DEFAULT_TIMEOUT=540
# Фону этот потолок не писан — он живёт вне вызова Bash. Два часа с запасом
# на большую задачу; 0 отключает лимит совсем.
DEFAULT_BG_TIMEOUT=7200
DEFAULT_PROBE_TIMEOUT=120
# Сессия Claude Code, из которой запущена джоба.
SESSION_ID="${PI_CLAUDE_SESSION:-${CLAUDE_SESSION_ID:-}}"
# Файл сессии pi для продолжения (resume) — не путать с SESSION_ID выше.
RESUME_SESSION_FILE=""
RESUME_FROM=""

# Пути, которые надо убрать при любом выходе.
CLEANUP_PATHS=()
cleanup() {
  local rc=$?
  [[ ${#CLEANUP_PATHS[@]} -eq 0 ]] || rm -rf "${CLEANUP_PATHS[@]}"
  exit "$rc"
}
trap cleanup EXIT

# Значение поля meta: ключи разбираем по ПЕРВОМУ "=".
meta_get() {
  local key="$1" file="$2"
  [[ -f "$file" ]] || return 0
  awk -v k="$key" 'index($0, k "=")==1 { print substr($0, length(k)+2); exit }' "$file"
}

# Перезапись поля meta без sed -i: read-modify-write по всему файлу, поэтому
# двум писателям нужен взаимный замок. mkdir атомарен на любой ФС.
meta_set() {
  local key="$1" value="$2" file="$3"
  local lock="$file.lock" i=0
  while ! mkdir "$lock" 2>/dev/null; do
    i=$((i+1))
    [[ $i -ge 50 ]] && { rm -rf "$lock"; continue; }
    sleep 0.1
  done
  local tmp; tmp="$(mktemp "$file.tmp.XXXXXX")"
  if awk -v k="$key" -v v="$value" '
    index($0, k "=")==1 { if (!done) { print k "=" v; done=1 } ; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$file" > "$tmp"; then mv "$tmp" "$file"; else rm -f "$tmp"; fi
  rmdir "$lock" 2>/dev/null || true
}

json_escape() {
  local v="$1"
  v="${v//\\/\\\\}"
  v="${v//\"/\\\"}"
  v="${v//$'\n'/\\n}"
  v="${v//$'\t'/\\t}"
  v="${v//$'\r'/\\r}"
  printf '%s' "$v"
}

file_bytes() {
  local f="$1"
  [[ -s "$f" ]] || { echo 0; return 0; }
  wc -c "$f" 2>/dev/null | awk '{print $1; exit}'
}

# Строка таблицы status, поля через '\t': id, статус, время, модель, имя
# сессии, метка. printf '%-Ns' в bash 3.2 без гарантированной UTF-8-локали
# считает ширину в БАЙТАХ, а не в символах — «—» (3 байта, 1 символ) и
# «0м03с» (7 байт, 5 символов, кириллица) получают неверный отступ и столбцы
# расходятся. python3 считает символы верно независимо от локали процесса;
# без python3 (уже недостижимо для run/status с фоновыми задачами, но status
# сам по себе может звать и без них) — печать через printf как запасной путь,
# менее аккуратная, но не падающая.
status_row() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c '
import signal, sys
# Список строк идёт построчно, а status | head — обычный способ его читать;
# без сброса SIGPIPE к системному поведению ранний обрыв пайпа печатал бы
# трейсбек BrokenPipeError в stderr на каждой оставшейся строке.
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
cols = sys.argv[1:7]
widths = [33, 10, 8, 34, 16, 0]
padded = [c + " " * max(0, w - len(c)) for c, w in zip(cols, widths)]
sys.stdout.write(" ".join(padded) + "\n")
' "$1" "$2" "$3" "$4" "$5" "$6" 2>/dev/null
  else
    printf '%-33s %-10s %-8s %-34s %-16s %s\n' "$1" "$2" "$3" "$4" "$5" "$6"
  fi
}

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
  cat >&2 <<USAGE
usage:
  pi-run.sh check [--json] [--no-probe] [--probe-timeout <sec>]
  pi-run.sh run [--session <name>] [--permission read|bash|write]
               [--channel <channel>] [--model <channel|provider/model>]
               [--thinking <level>] [--cwd <dir>] [--timeout <sec>]
               [--background] [--label <text>]
               < prompt.txt
  pi-run.sh resume <session-name|job-id> [same options as run] < prompt.txt
  pi-run.sh status [--json] [--all] [--running] [job-id]
  pi-run.sh result <job-id> [--wait [sec]]
  pi-run.sh logs <job-id> [--tail <lines>]
  pi-run.sh cancel <job-id|--all>        (kill is a synonym)
  pi-run.sh clean [--older-than <days>] [--all]
  pi-run.sh sessions [--json]
  pi-run.sh transcript <job-id> | transcript --session <name>

--permission: read (default, read/grep/find/ls only), bash (plus commands, no
  edit tools), write (everything). --write and --bash are kept as aliases for
  --permission write / --permission bash.
channels (value of --channel/--model): glm = b-ai-glm/glm-5.3-flash (default),
  deepseek = b-ai-deepseek/deepseek-v4.1-flash; --model also takes a full
  provider/model id, --channel takes the short name only.
--thinking: off, minimal, low, medium, high, xhigh, max
USAGE
  exit 2
}

# --- бинарь pi ---------------------------------------------------------------
# Порядок: явный PI_CLAUDE_BIN → PATH. Абсолютный путь важен для фоновых
# запусков: отвязанный процесс не наследует изменения PATH после старта.
resolve_pi() {
  local bin="${PI_CLAUDE_BIN:-}"
  if [[ -n "$bin" ]]; then
    command -v "$bin" >/dev/null 2>&1 || die 2 "PI_CLAUDE_BIN points at '$bin', which is not an executable"
    command -v "$bin"
    return 0
  fi
  command -v pi >/dev/null 2>&1 \
    || die 2 "pi not found in PATH - install pi or set PI_CLAUDE_BIN"
  command -v pi
}

# Явный PI_CLAUDE_BIN, указывающий на несуществующий файл, — это не «бинаря
# нет, возьмём из PATH», а поломанная настройка: молчаливый откат на системный
# pi замаскировал бы её под «готовность: yes» и в check, и в проверке каналов.
find_pi_bin() {
  if [[ -n "${PI_CLAUDE_BIN:-}" ]]; then
    command -v "${PI_CLAUDE_BIN}" 2>/dev/null
  else
    command -v pi 2>/dev/null
  fi
  # Ничего не нашли — не отказ функции: под set -e команда, чей код
  # возврата уходит прямиком в `bin="$(find_pi_bin)"`, оборвала бы весь
  # скрипт до единой строки вывода (проверено: без этого `return 0`
  # `check` с недоступным pi печатает вообще ничего вместо "не найден").
  return 0
}

new_session_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr 'A-Z' 'a-z'
  else
    python3 -c 'import uuid; print(uuid.uuid4())'
  fi
}

# Очистка \r и ANSI (CSI/OSC/одиночные escape) по всей строке — тем же
# набором, что и в pi-rpc.py (порция a). Байта 0x1b после этого быть не
# должно нигде, что попадает в отчёт check или в JSON.
strip_control() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c '
import re, sys
t = sys.stdin.buffer.read().decode("utf-8", "replace")
t = t.replace("\r", "")
t = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", t)
t = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", t)
t = re.sub(r"\x1b[=>]", "", t)
sys.stdout.write(t)
'
  else
    tr -d '\r\033'
  fi
}

# --- права --------------------------------------------------------------------
# Песочницы уровня ОС у pi нет — граница только allowlist инструментов
# (`--tools` клиента pi-rpc.py). write — без ограничения вовсе (пусто
# означает «--tools не передавать»).
tools_for_mode() {
  case "$1" in
    write)     printf '' ;;
    read-bash) printf 'read,grep,find,ls,bash' ;;
    *)         printf 'read,grep,find,ls' ;;
  esac
}

mode_label() {
  case "$1" in
    write)     echo "full access (edits and commands)" ;;
    read-bash) echo "read and commands, edits blocked" ;;
    *)         echo "read-only (edits and bash blocked)" ;;
  esac
}

# --permission <read|bash|write> — единый флаг прав; --write/--bash остаются
# синонимами ради маршрутов Listik и пресетов конвейера, которые их уже шлют.
mode_from_permission() {
  case "$1" in
    read|read-only) printf 'read-only' ;;
    bash|read-bash) printf 'read-bash' ;;
    write)          printf 'write' ;;
    *) die 2 "invalid --permission '$1' - allowed values: read, bash, write" ;;
  esac
}

# --- имена сессий -------------------------------------------------------------
name_slug() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'
}

check_session_name() {
  local name="$1"
  [[ -n "$name" ]] || die 2 "--session needs a session name"
  case "$name" in
    */*|*..*) die 2 "invalid session name '$name' (no / and no ..)" ;;
  esac
  [[ ${#name} -le 120 ]] || die 2 "session name longer than 120 characters"
}

name_file() {
  printf '%s/%s' "$NAMES_DIR" "$(name_slug "$1")"
}

model_or_dash() {
  local v="$1"
  [[ -n "$v" ]] && printf '%s' "$v" || printf '%s' "—"
}

# name → id/file/cwd/model/thinking/updated.
remember_session_name() {
  local name="$1" id="$2" file="$3" cwd="${4:-}" model="${5:-}" thinking="${6:-}" nf
  [[ -n "$name" ]] || return 0
  mkdir -p "$NAMES_DIR" 2>/dev/null || return 0
  chmod 700 "$STATE_DIR" "$NAMES_DIR" 2>/dev/null || true
  nf="$(name_file "$name")"
  [[ -n "$model" ]] || model="$(meta_get model "$nf")"
  [[ -n "$thinking" ]] || thinking="$(meta_get thinking "$nf")"
  {
    echo "name=$name"
    echo "id=${id:-}"
    echo "file=${file:-}"
    echo "cwd=${cwd:-}"
    echo "model=${model:-}"
    echo "thinking=${thinking:-}"
    echo "updated=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$nf"
}

# Каталог сессий pi для данного cwd: "--<cwd без ведущего / c / → ->--".
cwd_session_dir_name() {
  local cwd="$1" trimmed
  trimmed="${cwd#/}"
  printf -- '--%s--' "${trimmed//\//-}"
}

# Модель/thinking, восстановленные из самого jsonl: последние model_change/
# thinking_level_change. Нет таких записей — пустые строки.
session_info_from_file() {
  local file="$1"
  command -v python3 >/dev/null 2>&1 || return 0
  python3 - "$file" <<'PY'
import json, sys
path = sys.argv[1]
model = ""
thinking = ""
try:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            t = ev.get("type")
            if t == "model_change":
                prov = ev.get("provider", "")
                mid = ev.get("modelId", "")
                if prov and mid:
                    model = "%s/%s" % (prov, mid)
            elif t == "thinking_level_change":
                thinking = ev.get("thinkingLevel", "") or thinking
except OSError:
    pass
print(model)
print(thinking)
PY
}

# Запасной поиск файла сессии по имени: каталоги
# ${PI_CODING_AGENT_SESSION_DIR:-~/.pi/agent/sessions}/*/, первым — каталог
# текущего cwd, затем остальные; внутри — *.jsonl от свежих к старым; читаются
# только первые 5 строк каждого файла (дёшево). Весь обход и сравнение — ОДИН
# процесс python3 на вызов, а не один на файл: при десятках сессий на машине
# порождение процесса на файл (а первая же проверка нового имени обходит их
# все, раз совпадений нет) заметно замедляло run/resume — задача, вызывающая
# скилом свою обвязку, не должна платить за это второй лишней секундой.
fallback_find_session_file() {
  local name="$1" workdir="${2:-$PWD}" root cwd_dir_name
  command -v python3 >/dev/null 2>&1 || return 0
  root="${PI_CODING_AGENT_SESSION_DIR:-$HOME/.pi/agent/sessions}"
  [[ -d "$root" ]] || return 0
  cwd_dir_name="$(cwd_session_dir_name "$workdir")"
  python3 - "$root" "$cwd_dir_name" "$name" <<'PY'
import json, os, sys

root, cwd_dir_name, name = sys.argv[1], sys.argv[2], sys.argv[3]


def matches(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 5:
                    break
                line = line.strip()
                if not line or '"type":"session_info"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("type") == "session_info" and ev.get("name") == name:
                    return True
    except OSError:
        pass
    return False


try:
    entries = sorted(os.listdir(root))
except OSError:
    entries = []
dirs = [d for d in entries if os.path.isdir(os.path.join(root, d))]
ordered = ([cwd_dir_name] if cwd_dir_name in dirs else []) + [d for d in dirs if d != cwd_dir_name]

for d in ordered:
    dpath = os.path.join(root, d)
    try:
        files = [f for f in os.listdir(dpath) if f.endswith(".jsonl")]
    except OSError:
        continue
    files.sort(key=lambda f: os.path.getmtime(os.path.join(dpath, f)), reverse=True)
    for f in files:
        fpath = os.path.join(dpath, f)
        if matches(fpath):
            sys.stdout.write(fpath)
            sys.exit(0)
sys.exit(1)
PY
}

# Имя → файл сессии pi. Сначала своё состояние, затем — запасной поиск.
resolve_session_name() {
  local name="$1" workdir="${2:-}" nf stored file
  nf="$(name_file "$name")"
  if [[ -f "$nf" ]]; then
    stored="$(meta_get name "$nf")"
    file="$(meta_get file "$nf")"
    if [[ "$stored" == "$name" && -n "$file" && -f "$file" ]]; then
      printf '%s' "$file"
      return 0
    fi
  fi
  file="$(fallback_find_session_file "$name" "$workdir" || true)"
  [[ -n "$file" ]] || return 0
  local info model thinking
  info="$(session_info_from_file "$file" || true)"
  model="$(printf '%s\n' "$info" | sed -n '1p')"
  thinking="$(printf '%s\n' "$info" | sed -n '2p')"
  remember_session_name "$name" "" "$file" "$workdir" "$model" "$thinking"
  printf '%s' "$file"
}

# --- check ---------------------------------------------------------------------
catalog_label() {
  case "$1" in
    yes) echo "yes" ;;
    no)  echo "no" ;;
    *)   echo "could not check" ;;
  esac
}

probe_label() {
  case "$1" in
    ok)      echo "answered in ${2}s" ;;
    timeout) echo "timeout ${2}s (channel stalled before the first token - retry later or use the other channel)" ;;
    error)   echo "error: $3" ;;
    *)       echo "skipped" ;;
  esac
}

# «В каталоге» — строка pi --list-models, у которой первая колонка ==
# provider и вторая == model (колонки разделены двумя и более пробелами).
# Пустой/битый вывод (нет строки-заголовка с provider/model) — «проверить не
# удалось», а не «нет».
channel_in_catalog() {
  local prov="$1" mod="$2" out="$3" header
  [[ -n "$out" ]] || { echo unknown; return 0; }
  header="$(printf '%s\n' "$out" | head -1)"
  if ! printf '%s\n' "$header" | grep -q 'provider' || ! printf '%s\n' "$header" | grep -q 'model'; then
    echo unknown
    return 0
  fi
  if printf '%s\n' "$out" | awk -v p="$prov" -v m="$mod" '
    NR==1 { next }
    {
      n = split($0, cols, /  +/)
      if (cols[1] == p && cols[2] == m) { found = 1; exit }
    }
    END { exit !found }
  '; then
    echo yes
  else
    echo no
  fi
}

# Проба готовности: тот же rpc-клиент, --no-session, пробный промпт, свой
# временный cwd/events/meta. Пишет rc/elapsed/err в $4 (каталог) для разбора
# после wait — вызывается фоново, параллельно по каналам.
probe_one() {
  local prov="$1" mod="$2" timeout_s="$3" dir="$4" bin="$5"
  local cwd ev meta rc=0 start end
  cwd="$(mktemp -d)"; ev="$(mktemp)"; meta="$(mktemp)"
  start=$(date +%s)
  printf 'Reply with one word: pong' | python3 "$RPC" --pi "$bin" --provider "$prov" --model "$mod" \
    --no-session --pi-arg=--no-tools --pi-arg=--no-context-files --thinking low --timeout "$timeout_s" \
    --events "$ev" --meta "$meta" --cwd "$cwd" >"$dir/out" 2>"$dir/err_raw" || rc=$?
  end=$(date +%s)
  echo "$rc" > "$dir/rc"
  echo "$((end-start))" > "$dir/elapsed"
  strip_control < "$dir/err_raw" > "$dir/err" 2>/dev/null || cp "$dir/err_raw" "$dir/err"
  rm -rf "$cwd" "$ev" "$meta" "$dir/err_raw" 2>/dev/null || true
}

cmd_check() {
  local as_json=0 no_probe=0 probe_timeout=$DEFAULT_PROBE_TIMEOUT
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)           as_json=1; shift ;;
      --no-probe)        no_probe=1; shift ;;
      --probe-timeout)   probe_timeout="${2:-}"
                         [[ "$probe_timeout" =~ ^[0-9]+$ ]] || die 2 "--probe-timeout takes a whole number of seconds"
                         shift 2 ;;
      -h|--help)         usage ;;
      *)                 die 2 "unknown option '$1'" ;;
    esac
  done

  local bin="" bin_status="missing" version=""
  bin="$(find_pi_bin)"
  if [[ -n "$bin" ]]; then
    local vout
    if vout="$("$bin" --version 2>/dev/null)"; then
      bin_status="ok"
      version="$(printf '%s' "$vout" | strip_control | head -1)"
    else
      bin_status="broken"
    fi
  fi

  local rpc_status="missing"
  [[ -f "$RPC" ]] && rpc_status="ok"
  local py3=""
  py3="$(command -v python3 2>/dev/null || true)"

  local default_channel="${CHANNELS[0]%%:*}" dc_name
  dc_name="$(model_channel_name "$DEFAULT_MODEL")"
  [[ -n "$dc_name" ]] && default_channel="$dc_name"

  local models_out="" catalog_rc=1
  if [[ "$bin_status" == "ok" ]]; then
    models_out="$(PI_OFFLINE=1 "$bin" --list-models 2>/dev/null | strip_control)" && catalog_rc=0 || catalog_rc=$?
  fi

  # Пробовать вообще нечем — нет бинаря, python3 или rpc-клиента: обе строки
  # каналов идут как «пропущена», а не «ошибка», это не отказ канала, а
  # невозможность его спросить.
  local can_probe=0
  [[ "$bin_status" == "ok" && -n "$py3" && "$rpc_status" == "ok" ]] && can_probe=1

  # --- пробы каналов: параллельно, чтобы check не длился дольше одного
  # таймаута суммарно по обоим каналам ---
  local probe_dirs=() pair idx=0
  for pair in "${CHANNELS[@]}"; do
    local cmodel="${pair#*:}" cdir prov mod
    cdir="$(mktemp -d)"
    probe_dirs[$idx]="$cdir"
    if [[ $no_probe -eq 0 && $can_probe -eq 1 ]]; then
      split_provider_model "$cmodel"; prov="$PROV"; mod="$MOD"
      ( probe_one "$prov" "$mod" "$probe_timeout" "$cdir" "$bin" ) &
    fi
    idx=$((idx+1))
  done
  [[ $no_probe -eq 0 && $can_probe -eq 1 ]] && wait

  local channels_json="" channels_lines="" any_ready=0
  idx=0
  for pair in "${CHANNELS[@]}"; do
    local cname="${pair%%:*}" cmodel="${pair#*:}" cdir="${probe_dirs[$idx]}" prov mod
    split_provider_model "$cmodel"; prov="$PROV"; mod="$MOD"

    local cat_status="unknown"
    [[ $catalog_rc -eq 0 ]] && cat_status="$(channel_in_catalog "$prov" "$mod" "$models_out")"

    local pstatus="skipped" pseconds="" perror=""
    if [[ $no_probe -eq 0 && $can_probe -eq 1 ]]; then
      local rc=0 el=0 errtxt=""
      [[ -f "$cdir/rc" ]] && rc="$(cat "$cdir/rc")"
      [[ -f "$cdir/elapsed" ]] && el="$(cat "$cdir/elapsed")"
      [[ -f "$cdir/err" ]] && errtxt="$(cat "$cdir/err")"
      if [[ "$rc" == "0" && -s "$cdir/out" ]]; then
        pstatus="ok"; pseconds="$el"
      elif [[ "$rc" == "124" ]]; then
        # Таймаут печатается настроенным значением, а не измеренным временем:
        # измеренное — это время до убийства процесса (abort + ожидание +
        # SIGKILL), оно систематически больше --probe-timeout на несколько
        # секунд, а чек-лист сверяет ровно заданное число.
        pstatus="timeout"; pseconds="$probe_timeout"
      else
        pstatus="error"
        perror="$(printf '%s' "$errtxt" | head -c 120)"
        [[ -z "$perror" ]] && perror="exit code $rc"
      fi
    fi
    rm -rf "$cdir" 2>/dev/null || true

    [[ "$pstatus" == "ok" ]] && any_ready=1
    if [[ $no_probe -eq 1 && ( "$cat_status" == "yes" || "$cat_status" == "unknown" ) ]]; then
      any_ready=1
    fi

    local mark="" mark_json=false
    if [[ "$cname" == "$default_channel" ]]; then mark=", default"; mark_json=true; fi

    channels_json="${channels_json:+$channels_json,}$(printf '{"name":"%s","provider":"%s","model":"%s","catalog":"%s","probe":"%s","probe_seconds":%s,"probe_error":"%s","default":%s}' \
      "$(json_escape "$cname")" "$(json_escape "$prov")" "$(json_escape "$mod")" \
      "$(json_escape "$cat_status")" "$(json_escape "$pstatus")" \
      "${pseconds:-null}" "$(json_escape "$perror")" "$mark_json")"

    channels_lines="${channels_lines}${channels_lines:+$'\n'}$cname -> $cmodel (in catalog: $(catalog_label "$cat_status"); probe: $(probe_label "$pstatus" "$pseconds" "$perror")$mark)"
    idx=$((idx+1))
  done

  local ready="no"
  if [[ "$bin_status" == "ok" && -n "$py3" && "$rpc_status" == "ok" && $any_ready -eq 1 ]]; then
    ready="yes"
  fi

  local running; running="$(count_running_jobs)"
  local named=0
  [[ -d "$NAMES_DIR" ]] && named="$(ls -1 "$NAMES_DIR" 2>/dev/null | wc -l | tr -d ' ' || true)"

  if [[ $as_json -eq 1 ]]; then
    printf '{"ready":"%s","binary":"%s","binary_status":"%s","version":"%s","rpc_client":"%s","rpc_client_status":"%s","python3":"%s","default_channel":"%s","probe_timeout":%s,"channels":[%s],"running_jobs":%s,"named_sessions":%s,"state_dir":"%s"}\n' \
      "$(json_escape "$ready")" "$(json_escape "${bin:-}")" "$(json_escape "$bin_status")" \
      "$(json_escape "$version")" "$(json_escape "$RPC")" "$(json_escape "$rpc_status")" \
      "$(json_escape "${py3:-}")" "$(json_escape "$default_channel")" "$probe_timeout" \
      "$channels_json" "$running" "${named:-0}" "$(json_escape "$STATE_DIR")"
  else
    echo "ready:            $ready"
    echo "binary:           ${bin:-not found} ($bin_status)"
    echo "version:          ${version:--}"
    echo "rpc client:       $RPC ($( [[ "$rpc_status" == "ok" ]] && echo ok || echo "file missing" )) · python3: ${py3:-missing}"
    echo "default channel:  $default_channel -> $(expand_channel "$default_channel")"
    printf 'channels:         %s\n' "$(printf '%s' "$channels_lines" | sed '2,$s/^/                  /')"
    echo "note: \"in catalog\" lists models with configured auth - \"no\" does not mean the model does not exist; the probe is the final word"
    echo "default permission: $(mode_label read-only) (--tools $(tools_for_mode read-only))"
    echo "background jobs running: $running"
    echo "named sessions:   ${named:-0}"
    echo "state directory:  $STATE_DIR"
  fi
  [[ "$ready" == "yes" ]] || exit 1
}

count_running_jobs() {
  local n=0 dir
  [[ -d "$JOBS_DIR" ]] || { echo 0; return 0; }
  for dir in "$JOBS_DIR"/*/; do
    [[ -f "$dir/meta" ]] || continue
    [[ "$(job_status_of "${dir%/}")" == "running" ]] && n=$((n+1))
  done
  echo "$n"
}

# --- run ------------------------------------------------------------------
cmd_run() {
  local mode="read-only" model="" model_given=0 channel_given=0 \
        thinking="" workdir="" timeout_s="" background=0 label="" name=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --session)    name="$(need_value --session "${2:-}")"; shift 2 ;;
      --permission) mode="$(mode_from_permission "$(need_value --permission "${2:-}")")"; shift 2 ;;
      --write)      mode="write"; shift ;;
      --bash)       [[ "$mode" == "write" ]] || mode="read-bash"; shift ;;
      --model)      model="$(need_value --model "${2:-}")"; model_given=1; shift 2 ;;
      --channel)    model="$(need_value --channel "${2:-}")"; channel_given=1; shift 2 ;;
      --thinking)   thinking="$(need_value --thinking "${2:-}")"; check_thinking "$thinking"; shift 2 ;;
      --cwd)        workdir="$(need_value --cwd "${2:-}")"; shift 2 ;;
      --timeout)    timeout_s="${2:-}"; [[ -z "$timeout_s" ]] && die 2 "--timeout needs a value"; shift 2 ;;
      --label)      label="${2:-}"; [[ -z "$label" ]] && die 2 "--label needs a value"; shift 2 ;;
      --background) background=1; shift ;;
      -h|--help)    usage ;;
      *)            die 2 "unknown option '$1' (the prompt goes on stdin, not as an argument)" ;;
    esac
  done
  [[ $model_given -eq 1 && $channel_given -eq 1 ]] && die 2 "--model and --channel are mutually exclusive"

  local full_model="" channel_name=""
  if [[ $channel_given -eq 1 ]]; then
    channel_name="$(resolve_channel_only "$model")"
    full_model="$(expand_channel "$channel_name")"
  elif [[ $model_given -eq 1 ]]; then
    full_model="$(resolve_model "$model")"
    channel_name="$(model_channel_name "$full_model")"
  else
    full_model="$DEFAULT_MODEL"
    channel_name="$(model_channel_name "$full_model")"
  fi

  workdir="${workdir:-$PWD}"
  [[ -d "$workdir" ]] || die 2 "directory '$workdir' does not exist"
  workdir="$(cd "$workdir" && pwd)"

  if [[ -n "$name" ]]; then
    check_session_name "$name"
    local existing; existing="$(resolve_session_name "$name" "$workdir" || true)"
    [[ -n "$existing" ]] && die 2 "session '$name' already exists - continue it with: pi-run.sh resume --session '$name'; a new session needs another name"
  fi

  start_run "$mode" "$full_model" "$channel_name" "$thinking" "$workdir" "$timeout_s" \
            "$background" "$label" "$name"
}

# Общая часть run и resume: собрать вызов клиента и отправить его в foreground
# или в фон. RESUME_SESSION_FILE к этому моменту уже проставлен (или пуст).
start_run() {
  local mode="$1" model="$2" channel="$3" thinking="$4" workdir="$5" timeout_s="$6"
  local background="$7" label="$8" name="$9"

  if [[ -z "$timeout_s" ]]; then
    if [[ $background -eq 1 ]]; then timeout_s="$DEFAULT_BG_TIMEOUT"; else timeout_s="$DEFAULT_TIMEOUT"; fi
  fi
  [[ "$timeout_s" =~ ^[0-9]+$ ]] || die 2 "--timeout takes a whole number of seconds (0 = no limit)"

  [[ -f "$RPC" ]] || die 2 "rpc client '$RPC' not found - reinstall the pi plugin"
  command -v python3 >/dev/null 2>&1 || die 2 "python3 is required - it runs the pi-rpc.py client"

  local prompt
  prompt="$(cat)"
  [[ -z "${prompt//[[:space:]]/}" ]] && die 2 "empty prompt on stdin"

  local bin; bin="$(resolve_pi)"
  local tools; tools="$(tools_for_mode "$mode")"
  split_provider_model "$model"
  local prov="$PROV" mod="$MOD"

  local sess_mode="" sess_uuid="" sess_file=""
  if [[ -n "$RESUME_SESSION_FILE" ]]; then
    sess_mode="file"; sess_file="$RESUME_SESSION_FILE"
  elif [[ -n "$name" ]]; then
    sess_mode="id"; sess_uuid="$(new_session_uuid)"
  elif [[ $background -eq 1 ]]; then
    sess_mode="id"; sess_uuid="$(new_session_uuid)"
  else
    sess_mode="none"
  fi

  if [[ $background -eq 1 ]]; then
    run_background "$bin" "$mode" "$tools" "$prov" "$mod" "$model" "$channel" "$thinking" \
      "$workdir" "$timeout_s" "$prompt" "$label" "$name" "$sess_mode" "$sess_uuid" "$sess_file"
    return 0
  fi

  run_foreground "$bin" "$mode" "$tools" "$prov" "$mod" "$model" "$channel" "$thinking" \
    "$workdir" "$timeout_s" "$prompt" "$name" "$sess_mode" "$sess_uuid" "$sess_file"
}

run_foreground() {
  local bin="$1" mode="$2" tools="$3" prov="$4" mod="$5" model="$6" channel="$7" thinking="$8"
  local workdir="$9" timeout_s="${10}" prompt="${11}" name="${12}" sess_mode="${13}" sess_uuid="${14}" sess_file="${15}"

  local events_file err_file out_file meta_file rc=0
  events_file="$(mktemp "${TMPDIR:-/tmp}/pi-events.XXXXXX")"
  err_file="$(mktemp "${TMPDIR:-/tmp}/pi-stderr.XXXXXX")"
  out_file="$(mktemp "${TMPDIR:-/tmp}/pi-out.XXXXXX")"
  meta_file="$(mktemp "${TMPDIR:-/tmp}/pi-meta.XXXXXX")"
  CLEANUP_PATHS+=("$events_file" "$err_file" "$out_file" "$meta_file")

  local args=(--pi "$bin" --provider "$prov" --model "$mod")
  [[ -n "$thinking" ]] && args+=(--thinking "$thinking")
  [[ -n "$tools" ]] && args+=(--tools "$tools")
  case "$sess_mode" in
    id)   args+=(--session-id "$sess_uuid"); [[ -n "$name" ]] && args+=(--name "$name") ;;
    file) args+=(--session-file "$sess_file") ;;
    *)    args+=(--no-session) ;;
  esac
  args+=(--cwd "$workdir" --timeout "$timeout_s" --events "$events_file" --meta "$meta_file")

  (cd "$workdir" && printf '%s' "$prompt" | python3 "$RPC" "${args[@]}") >"$out_file" 2>"$err_file" || rc=$?

  # Имя сессии запоминаем даже при неудаче прогона — сессия уже создана.
  local pi_session_file; pi_session_file="$(meta_get pi_session_file "$meta_file")"
  if [[ -n "$name" && -n "$pi_session_file" ]]; then
    remember_session_name "$name" "$sess_uuid" "$pi_session_file" "$workdir" "$model" "$thinking"
  fi

  local err_text=""
  [[ -s "$err_file" ]] && err_text="$(tail -c 500 "$err_file")"

  case "$rc" in
    0)
      cat "$out_file"
      ;;
    124)
      [[ -s "$out_file" ]] && cat "$out_file"
      die 6 "pi: timed out after ${timeout_s}s - rerun with --background to lift the ceiling${err_text:+; stderr: $err_text}"
      ;;
    2)
      die 2 "${err_text:-pi-rpc.py call failed (exit code $rc)}"
      ;;
    *)
      [[ -s "$out_file" ]] && cat "$out_file"
      die 6 "pi: run exited with code $rc${err_text:+ - $err_text}"
      ;;
  esac
}

# Идентификатор задачи — он же имя каталога и замок: занятое отбрасывается и
# берётся следующее.
claim_job_dir() {
  local stamp id dir i=0
  stamp="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$JOBS_DIR"
  while :; do
    id="pi-$stamp-$$-$RANDOM"
    dir="$JOBS_DIR/$id"
    mkdir "$dir" 2>/dev/null && { printf '%s' "$id"; return 0; }
    i=$((i+1))
    [[ $i -ge 100 ]] && die 5 "could not allocate a job id in $JOBS_DIR"
  done
}

run_background() {
  local bin="$1" mode="$2" tools="$3" prov="$4" mod="$5" model="$6" channel="$7" thinking="$8"
  local workdir="$9" timeout_s="${10}" prompt="${11}" label="${12}" name="${13}"
  local sess_mode="${14}" sess_uuid="${15}" sess_file="${16}"

  local job_id job_dir
  job_id="$(claim_job_dir)"
  job_dir="$JOBS_DIR/$job_id"
  chmod 700 "$STATE_DIR" "$JOBS_DIR" 2>/dev/null || true
  chmod 700 "$job_dir"

  printf '%s' "$prompt" > "$job_dir/prompt.txt"
  : > "$job_dir/events.jsonl"
  : > "$job_dir/output.txt"
  : > "$job_dir/stderr.txt"
  : > "$job_dir/pi.meta"
  {
    echo "id=$job_id"
    echo "status=running"
    echo "cwd=$workdir"
    echo "mode=$mode"
    echo "model=${model:-—}"
    echo "channel=${channel:-—}"
    echo "thinking=${thinking:-—}"
    echo "label=${label:-—}"
    echo "session=${SESSION_ID:-—}"
    echo "session_name=${name:-—}"
    # Уже известное на старте: новая сессия — свой uuid (--session-id), resume
    # по файлу — его путь. И то, и другое видно в status с первого опроса, а
    # не только после завершения задачи.
    echo "pi_session=${sess_uuid:-—}"
    echo "pi_session_file=${sess_file:-—}"
    echo "resumed_from=${RESUME_FROM:-—}"
    echo "timeout=$timeout_s"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "started_epoch=$(date +%s)"
  } > "$job_dir/meta"

  local args=(--pi "$bin" --provider "$prov" --model "$mod")
  [[ -n "$thinking" ]] && args+=(--thinking "$thinking")
  [[ -n "$tools" ]] && args+=(--tools "$tools")
  case "$sess_mode" in
    id)   args+=(--session-id "$sess_uuid"); [[ -n "$name" ]] && args+=(--name "$name") ;;
    file) args+=(--session-file "$sess_file") ;;
    *)    args+=(--no-session) ;;
  esac
  args+=(--cwd "$workdir" --timeout "$timeout_s" --events "$job_dir/events.jsonl" --meta "$job_dir/pi.meta")

  # Отвязанный воркер, своя процесс-группа — как у opencode-run.sh: клиент
  # python3 (pi-rpc.py) запускается фоном внутри воркера, его pid попадает в
  # meta (по нему cancel бьёт kill_tree; клиент по TERM сам гасит группу pi).
  set -m
  (
    local rc=0
    trap '' HUP INT TERM
    meta_set worker_pid "$(sh -c 'echo $PPID')" "$job_dir/meta"
    cat "$job_dir/prompt.txt" | python3 "$RPC" "${args[@]}" \
      > "$job_dir/output.txt" 2> "$job_dir/stderr.txt" &
    local inner=$!
    meta_set pid "$inner" "$job_dir/meta"
    wait "$inner" || rc=$?

    local pi_session_file pi_session_id stop_reason error_message
    pi_session_file="$(meta_get pi_session_file "$job_dir/pi.meta")"
    pi_session_id="$(meta_get pi_session_id "$job_dir/pi.meta")"
    stop_reason="$(meta_get stop_reason "$job_dir/pi.meta")"
    error_message="$(meta_get error_message "$job_dir/pi.meta")"

    [[ -n "$pi_session_file" ]] && meta_set pi_session_file "$pi_session_file" "$job_dir/meta"
    [[ -n "$pi_session_id" ]] && meta_set pi_session "$pi_session_id" "$job_dir/meta"
    [[ -n "$stop_reason" ]] && meta_set stop_reason "$stop_reason" "$job_dir/meta"
    [[ -n "$error_message" ]] && meta_set error "$error_message" "$job_dir/meta"

    if [[ -n "$name" && -n "$pi_session_file" ]]; then
      remember_session_name "$name" "${sess_uuid:-$pi_session_id}" "$pi_session_file" "$workdir" "$model" "$thinking"
    fi

    local final="completed"
    [[ $rc -eq 124 ]] && final="timeout"
    [[ $rc -ne 0 && $rc -ne 124 ]] && final="failed"
    [[ -f "$job_dir/canceled" ]] && final="canceled"
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
  [[ -n "$job_id" ]] || die 2 "a job-id is required (list them with: pi-run.sh status)"
  case "$job_id" in
    */*|*..*) die 2 "invalid job-id '$job_id'" ;;
  esac
  local dir="$JOBS_DIR/$job_id"
  [[ -d "$dir" ]] || die 2 "no job with id '$job_id' (list them with: pi-run.sh status --all)"
  echo "$dir"
}

job_status_of() {
  local dir="$1"
  local st pid
  st="$(meta_get status "$dir/meta")"
  [[ "$st" != "running" ]] && { echo "$st"; return 0; }
  pid="$(meta_get pid "$dir/meta")"
  if [[ -n "$pid" && "$pid" != "—" ]] && kill -0 "$pid" 2>/dev/null; then
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
  # Выход клиента — не конец задачи, пока воркер ещё дописывает meta.
  local wp start
  wp="$(meta_get worker_pid "$dir/meta")"
  if [[ -n "$wp" && "$wp" != "—" ]] && kill -0 "$wp" 2>/dev/null; then echo running; return 0; fi
  if [[ -z "$pid" || "$pid" == "—" ]]; then
    start="$(meta_get started_epoch "$dir/meta")"
    if [[ -n "$start" ]] && (( $(date +%s) - start < 60 )); then echo running; return 0; fi
  fi
  st="$(meta_get status "$dir/meta")"
  [[ "$st" != "running" ]] && { echo "$st"; return 0; }
  echo orphaned
}

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
      # 33 — ширина идентификатора с учётом даты/pid/RANDOM; 34 — под самую
      # длинную модель канала (b-ai-deepseek/deepseek-v4.1-flash, 33 символа).
      status_row \
        "$dir" "$st" "$(elapsed_of "$d")" \
        "$(meta_get model "$d/meta")" \
        "$(meta_get session_name "$d/meta")" \
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
  printf '{"id":"%s","status":"%s","meta_status":"%s","label":"%s","cwd":"%s","mode":"%s","model":"%s","channel":"%s","thinking":"%s","session":"%s","session_name":"%s","pi_session":"%s","pi_session_file":"%s","resumed_from":"%s","stop_reason":"%s","error":"%s","started":"%s","elapsed":"%s","timeout":"%s","exit":"%s","output_bytes":%s}' \
    "$(json_escape "$(meta_get id "$dir/meta")")" \
    "$(json_escape "$st")" \
    "$(json_escape "$(meta_get status "$dir/meta")")" \
    "$(json_escape "$(meta_get label "$dir/meta")")" \
    "$(json_escape "$(meta_get cwd "$dir/meta")")" \
    "$(json_escape "$(meta_get mode "$dir/meta")")" \
    "$(json_escape "$(meta_get model "$dir/meta")")" \
    "$(json_escape "$(meta_get channel "$dir/meta")")" \
    "$(json_escape "$(meta_get thinking "$dir/meta")")" \
    "$(json_escape "$(meta_get session "$dir/meta")")" \
    "$(json_escape "$(meta_get session_name "$dir/meta")")" \
    "$(json_escape "$(meta_get pi_session "$dir/meta")")" \
    "$(json_escape "$(meta_get pi_session_file "$dir/meta")")" \
    "$(json_escape "$(meta_get resumed_from "$dir/meta")")" \
    "$(json_escape "$(meta_get stop_reason "$dir/meta")")" \
    "$(json_escape "$(meta_get error "$dir/meta")")" \
    "$(json_escape "$(meta_get started "$dir/meta")")" \
    "$(json_escape "$(elapsed_of "$dir")")" \
    "$(json_escape "$(meta_get timeout "$dir/meta")")" \
    "$(json_escape "$(meta_get exit "$dir/meta")")" \
    "$(file_bytes "$dir/output.txt")"
}

# --- result -----------------------------------------------------------------
fallback_answer_from_events() {
  local events="$1"
  command -v python3 >/dev/null 2>&1 || return 0
  [[ -s "$events" ]] || return 0
  python3 - "$events" <<'PY'
import json, sys
last = None
with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") != "message_end":
            continue
        msg = ev.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        last = msg
if last is not None:
    parts = [c.get("text", "") for c in (last.get("content") or []) if isinstance(c, dict) and c.get("type") == "text"]
    sys.stdout.write("".join(parts))
PY
}

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
    local waited=0
    while [[ "$st" == "running" && $waited -lt $wait_s ]]; do
      sleep 3
      waited=$((waited+3))
      st="$(job_status_of "$dir")"
    done
  fi

  case "$st" in
    running)
      die 5 "job still running ($(elapsed_of "$dir") since $(meta_get started "$dir/meta")); poll later: pi-run.sh status $job_id"
      ;;
    orphaned)
      [[ -s "$dir/output.txt" ]] || fallback_answer_from_events "$dir/events.jsonl" > "$dir/output.txt" 2>/dev/null || true
      [[ -s "$dir/output.txt" ]] && cat "$dir/output.txt"
      die 6 "the job worker vanished without recording an outcome (reboot or kill -9); above is whatever got written"
      ;;
  esac

  [[ -s "$dir/output.txt" ]] && cat "$dir/output.txt"

  case "$st" in
    timeout)
      die 6 "job hit its timeout ($(meta_get timeout "$dir/meta")s); above is whatever arrived"
      ;;
    canceled)
      die 6 "job was cancelled manually (after $(elapsed_of "$dir")); above is whatever arrived"
      ;;
    failed)
      local em; em="$(meta_get error "$dir/meta")"
      if [[ -n "$em" && "$em" != "—" ]]; then
        die 6 "job failed (exit $(meta_get exit "$dir/meta")) - $em"
      else
        die 6 "job failed (exit $(meta_get exit "$dir/meta"))$( [[ -s "$dir/stderr.txt" ]] && printf ' — %s' "$(tail -c 500 "$dir/stderr.txt")" )"
      fi
      ;;
  esac

  if [[ ! -s "$dir/output.txt" ]]; then
    [[ -s "$dir/stderr.txt" ]] && tail -c 2000 "$dir/stderr.txt" >&2
    die 6 "empty answer"
  fi
}

# --- logs -------------------------------------------------------------------
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
  echo "events:  $(file_bytes "$dir/events.jsonl") bytes accumulated"
  echo "answer:  $(file_bytes "$dir/output.txt") bytes"
  if [[ -s "$dir/stderr.txt" ]]; then
    echo "--- client stderr ---"
    tail -n 10 "$dir/stderr.txt"
  fi
  if [[ -s "$dir/events.jsonl" ]]; then
    echo "--- last $tail_n events ---"
    render_events "$dir/events.jsonl" "$tail_n"
  elif [[ "$st" == "running" ]]; then
    echo "no events yet. That is normal in the first seconds of a run -"
    echo "the signs of life are the running status and a growing elapsed time."
  else
    echo "no events"
  fi
}

render_events() {
  local events="$1" tail_n="$2"
  if command -v python3 >/dev/null 2>&1; then
    tail -n "$tail_n" "$events" | python3 -c '
import json, sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except Exception:
        print(line[:200])
        continue
    t = ev.get("type", "?")
    if t == "tool_execution_start":
        args = ev.get("args") or {}
        brief = "; ".join("%s=%s" % (k, str(v)[:80]) for k, v in list(args.items())[:3])
        print("[tool] %s %s" % (ev.get("toolName", "?"), brief))
    elif t == "tool_execution_end":
        status = "error" if ev.get("isError") else "ok"
        print("[tool] %s -> %s" % (ev.get("toolName", "?"), status))
    elif t == "message_end":
        msg = ev.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        if msg.get("stopReason") == "error":
            print("[answer] error: %s" % (msg.get("errorMessage") or "")[:200])
        else:
            content = msg.get("content") or []
            text = "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
            print("[answer] %s" % text.replace("\n", " ")[:200])
    elif t == "agent_start":
        print("[agent] start")
    elif t == "agent_settled":
        print("[agent] done")
    elif t == "auto_retry_start":
        print("[retry] attempt %s of %s: %s" % (ev.get("attempt", "?"), ev.get("maxAttempts", "?"), (ev.get("errorMessage") or "")[:120]))
    elif t == "compaction_start":
        print("[context compaction] started (%s)" % ev.get("reason", "?"))
    elif t == "compaction_end":
        print("[context compaction] finished (%s)" % ev.get("reason", "?"))
    elif t == "pi_rpc_start":
        print("[client] start %s/%s" % (ev.get("provider", "?"), ev.get("model", "?")))
    elif t == "pi_rpc_end":
        print("[client] exit %s" % ev.get("exit", "?"))
    elif t in ("message_start", "turn_start", "turn_end", "agent_end", "entry_appended", "queue_update"):
        continue
    else:
        print("[%s]" % t)
' 2>/dev/null || tail -n "$tail_n" "$events"
  else
    tail -n "$tail_n" "$events"
  fi
}

# --- cancel -----------------------------------------------------------------
kill_tree() {
  local pid="$1" sig="${2:-TERM}" child
  [[ -n "$pid" && "$pid" != "—" ]] || return 0
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

  : > "$dir/canceled"
  kill_tree "$(meta_get pid "$dir/meta")" TERM

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
    canceled) echo "$job_id: cancelled (after $(elapsed_of "$dir"))" ;;
    running)  echo "$job_id: could not cancel - the process is not responding; see status $job_id" ;;
    *)        echo "$job_id: finished on its own before the cancel ($final)" ;;
  esac
}

cmd_cancel() {
  local target="${1:-}"
  [[ "$target" == "-h" || "$target" == "--help" ]] && usage
  [[ -n "$target" ]] || die 2 "a job-id or --all is required (list them with: pi-run.sh status)"
  if [[ "$target" == "--all" ]]; then
    local any=0 dir
    [[ -d "$JOBS_DIR" ]] || die 1 "no background jobs"
    for dir in "$JOBS_DIR"/*/; do
      [[ -f "$dir/meta" ]] || continue
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
  echo "jobs removed: $removed (running jobs untouched${skipped:+; other sessions skipped: $skipped})"
  echo "pi sessions were not deleted - their files live in ~/.pi/agent/sessions (delete via: pi -r, Ctrl+D)"
}

# --- resume -----------------------------------------------------------------
cmd_resume() {
  local target="" name="" mode="" model="" model_given=0 channel_given=0 \
        thinking="" workdir="" timeout_s="" background=0 label=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --session)    name="$(need_value --session "${2:-}")"; shift 2 ;;
      --permission) mode="$(mode_from_permission "$(need_value --permission "${2:-}")")"; shift 2 ;;
      --write)      mode="write"; shift ;;
      --bash)       [[ "$mode" == "write" ]] || mode="read-bash"; shift ;;
      --model)      model="$(need_value --model "${2:-}")"; model_given=1; shift 2 ;;
      --channel)    model="$(need_value --channel "${2:-}")"; channel_given=1; shift 2 ;;
      --thinking)   thinking="$(need_value --thinking "${2:-}")"; check_thinking "$thinking"; shift 2 ;;
      --cwd)        workdir="$(need_value --cwd "${2:-}")"; shift 2 ;;
      --timeout)    timeout_s="${2:-}"; [[ -z "$timeout_s" ]] && die 2 "--timeout needs a value"; shift 2 ;;
      --label)      label="${2:-}"; [[ -z "$label" ]] && die 2 "--label needs a value"; shift 2 ;;
      --background) background=1; shift ;;
      -h|--help)    usage ;;
      -*)           die 2 "unknown option '$1' (the prompt goes on stdin, not as an argument)" ;;
      *)            [[ -n "$target" ]] && die 2 "unexpected argument '$1'"
                    target="$1"; shift ;;
    esac
  done
  [[ $model_given -eq 1 && $channel_given -eq 1 ]] && die 2 "--model and --channel are mutually exclusive"

  [[ -n "$target" || -n "$name" ]] \
    || die 2 "a session name or job-id is required: pi-run.sh resume --session <name> (list them with: pi-run.sh sessions)"

  local job_dir=""
  if [[ -n "$target" ]]; then
    case "$target" in
      */*|*..*) die 2 "invalid argument '$target'" ;;
    esac
    if [[ -d "$JOBS_DIR/$target" ]]; then
      job_dir="$JOBS_DIR/$target"
    elif [[ -z "$name" ]]; then
      name="$target"
    else
      die 2 "both job-id '$target' and --session '$name' given - use one of them"
    fi
  fi

  local sess_file="" src_cwd="" src_mode="" src_model="" src_thinking="" src_name=""
  if [[ -n "$job_dir" ]]; then
    local st; st="$(job_status_of "$job_dir")"
    [[ "$st" == "running" ]] && die 2 "job '$target' is still running - resume it after it finishes, or fall back to a fresh run"
    sess_file="$(meta_get pi_session_file "$job_dir/meta")"
    [[ -n "$sess_file" && "$sess_file" != "—" && -f "$sess_file" ]] \
      || die 2 "job '$target' has no pi session file on disk - start a fresh run: pi-run.sh run"
    src_cwd="$(meta_get cwd "$job_dir/meta")"
    src_mode="$(meta_get mode "$job_dir/meta")"
    src_model="$(meta_get model "$job_dir/meta")"
    src_thinking="$(meta_get thinking "$job_dir/meta")"
    src_name="$(meta_get session_name "$job_dir/meta")"
    [[ "$src_name" == "—" ]] && src_name=""
    RESUME_FROM="$target"
    [[ -n "$name" ]] || name="$src_name"
  else
    check_session_name "$name"
    local probe_cwd="${workdir:-$PWD}"
    [[ -d "$probe_cwd" ]] && probe_cwd="$(cd "$probe_cwd" && pwd)"
    sess_file="$(resolve_session_name "$name" "$probe_cwd" || true)"
    [[ -n "$sess_file" && -f "$sess_file" ]] \
      || die 2 "no session named '$name' - start one: pi-run.sh run --session '$name'"
    src_cwd="$(meta_get cwd "$(name_file "$name")")"
    src_model="$(meta_get model "$(name_file "$name")")"
    src_thinking="$(meta_get thinking "$(name_file "$name")")"
  fi

  [[ -n "$workdir" ]] || workdir="$src_cwd"
  [[ -n "$workdir" && -d "$workdir" ]] || workdir="$PWD"
  workdir="$(cd "$workdir" && pwd)"

  local full_model="" channel_name=""
  if [[ $channel_given -eq 1 ]]; then
    channel_name="$(resolve_channel_only "$model")"
    full_model="$(expand_channel "$channel_name")"
  elif [[ $model_given -eq 1 ]]; then
    full_model="$(resolve_model "$model")"
    channel_name="$(model_channel_name "$full_model")"
  elif [[ -n "$src_model" && "$src_model" != "—" ]]; then
    full_model="$src_model"
    channel_name="$(model_channel_name "$full_model")"
  else
    full_model="$DEFAULT_MODEL"
    channel_name="$(model_channel_name "$full_model")"
  fi

  [[ -n "$thinking" ]] || { [[ -n "$src_thinking" && "$src_thinking" != "—" ]] && thinking="$src_thinking"; }

  if [[ -z "$mode" ]]; then
    case "$src_mode" in
      write|read-bash|read-only) mode="$src_mode" ;;
      *)                         mode="read-only" ;;
    esac
  fi
  [[ -n "$label" ]] || label="resume of ${name:-$target}"

  RESUME_SESSION_FILE="$sess_file"
  start_run "$mode" "$full_model" "$channel_name" "$thinking" "$workdir" "$timeout_s" \
            "$background" "$label" "${name:-}"
}

# --- sessions -----------------------------------------------------------------
cmd_sessions() {
  local as_json=0
  case "${1:-}" in
    --json)    as_json=1 ;;
    -h|--help) usage ;;
    "")        ;;
    *)         die 2 "unknown option '$1'" ;;
  esac

  if [[ ! -d "$NAMES_DIR" ]] || [[ -z "$(ls -1 "$NAMES_DIR" 2>/dev/null)" ]]; then
    [[ $as_json -eq 1 ]] && { echo '[]'; return 0; }
    echo "no named sessions (a name is given at launch: run --session <name>)" >&2
    exit 1
  fi

  local f out="" shown=0
  for f in "$NAMES_DIR"/*; do
    [[ -f "$f" ]] || continue
    shown=$((shown+1))
    if [[ $as_json -eq 1 ]]; then
      out+="$(printf '{"name":"%s","id":"%s","file":"%s","cwd":"%s","model":"%s","thinking":"%s","updated":"%s"}' \
        "$(json_escape "$(meta_get name "$f")")" \
        "$(json_escape "$(meta_get id "$f")")" \
        "$(json_escape "$(meta_get file "$f")")" \
        "$(json_escape "$(meta_get cwd "$f")")" \
        "$(json_escape "$(meta_get model "$f")")" \
        "$(json_escape "$(meta_get thinking "$f")")" \
        "$(json_escape "$(meta_get updated "$f")")"),"
    else
      printf '%-28s %-38s %-26s %-22s %s\n' \
        "$(meta_get name "$f")" "$(model_or_dash "$(meta_get id "$f")")" \
        "$(model_or_dash "$(meta_get model "$f")")" \
        "$(meta_get updated "$f")" "$(meta_get cwd "$f")"
    fi
  done
  [[ $as_json -eq 1 ]] && printf '[%s]\n' "${out%,}"
  return 0
}

# --- transcript -----------------------------------------------------------------
cmd_transcript() {
  local job_id="" name=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --session) name="$(need_value --session "${2:-}")"; shift 2 ;;
      -h|--help) usage ;;
      -*)        die 2 "unknown option '$1'" ;;
      *)         [[ -n "$job_id" ]] && die 2 "unexpected argument '$1'"
                 job_id="$1"; shift ;;
    esac
  done

  local file=""
  if [[ -n "$job_id" ]]; then
    local dir; dir="$(job_dir_of "$job_id")"
    file="$(meta_get pi_session_file "$dir/meta")"
    [[ -n "$file" && "$file" != "—" ]] \
      || die 2 "job '$job_id' has no pi session file yet (the run has not started, no session was created, or no events were written)"
  elif [[ -n "$name" ]]; then
    check_session_name "$name"
    file="$(resolve_session_name "$name" "$PWD" || true)"
    [[ -n "$file" ]] || die 2 "no session named '$name' - list them with: pi-run.sh sessions"
  else
    die 2 "give a job-id or --session <name>"
  fi

  [[ -f "$file" ]] || die 2 "session file '$file' is gone from disk - the session was deleted"
  cat "$file"
}

# --- диспетчер ----------------------------------------------------------------
[[ $# -eq 0 ]] && usage
sub="$1"; shift
case "$sub" in
  check)              cmd_check "$@" ;;
  run)                cmd_run "$@" ;;
  resume)             cmd_resume "$@" ;;
  status)             cmd_status "$@" ;;
  result)             cmd_result "$@" ;;
  logs)               cmd_logs "$@" ;;
  cancel|kill)        cmd_cancel "$@" ;;
  clean)              cmd_clean "$@" ;;
  sessions)           cmd_sessions "$@" ;;
  transcript)         cmd_transcript "$@" ;;
  -h|--help)          usage ;;
  *)                  die 2 "unknown subcommand '$sub'" ;;
esac
