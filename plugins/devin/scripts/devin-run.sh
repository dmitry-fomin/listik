#!/usr/bin/env bash
# devin-run.sh — единая точка запуска терминального агента devin из Claude Code.
#
#   devin-run.sh check [--json] [--no-probe] [--probe-timeout <сек>]
#   devin-run.sh run [опции] < prompt.txt
#   devin-run.sh resume <имя-сессии|job-id|devin-session-id> [опции] < prompt.txt
#   devin-run.sh status [--json] [--all] [--running] [job-id]
#   devin-run.sh result <job-id> [--wait [сек]]
#   devin-run.sh logs <job-id> [--tail N]
#   devin-run.sh cancel <job-id|--all>        # kill — синоним cancel
#   devin-run.sh clean [--older-than <дней>] [--all]
#   devin-run.sh sessions [--json]
#   devin-run.sh transcript <job-id> | transcript --session <имя>
#
# Инвариант, на котором держится вся обвязка: в stdout подкоманд `run`/`resume`
# (foreground) и `result` попадает РОВНО финальный ответ devin и ничего больше.
# Всё служебное — прогресс, диагностика, ошибки запуска — идёт в stderr или в
# файлы джобы. Вызывающий может отдавать этот stdout пользователю дословно.
#
# Фоновая джоба — самостоятельная сущность с собственным идентификатором:
# её можно опрашивать (`status`, `logs`), забирать (`result`), убивать
# (`cancel`). Прогон переживает завершение вызвавшего его Bash-инструмента,
# поэтому долгие задачи не упираются в его десятиминутный потолок.
#
# --- Чем devin отличается от остальных мостов (pi, opencode, codex, dsh) -----
#
# 1. Промежуточного клиента нет: обвязка зовёт сам `devin … -p`. В
#    неинтерактивном режиме devin уже печатает в stdout ровно финальный ответ,
#    поэтому инвариант обеспечивается самим бинарём, а не парсером событий.
#
# 2. Промпт передаётся ФАЙЛОМ (`--prompt-file`), а не аргументом. Проверено на
#    живом прогоне: многострочный текст с кодом, бэктиками и `$HOME` проходит
#    без искажений, тогда как inline-промпт после `-p` пришлось бы защищать от
#    разбора командной строки.
#
# 3. Флага рабочего каталога у devin НЕТ. `--cwd` обвязки — это переход в
#    каталог перед запуском (`cd`), и ничто другое.
#
# 4. `devin list` — интерактивный TUI: он непригоден для скриптов и вдобавок
#    сбрасывает cwd вызывающего. Учёт фоновых задач ведёт сама обвязка, а
#    идентификатор сессии devin читается из его собственной sqlite-базы
#    ($DEVIN_HOME/sessions.db, таблица sessions) — только на чтение.
#
# 5. Канал ровно один: swe = семейство SWE-2. Уровень усилия выбирается
#    флагом `--thinking medium|high|max`, который раскрывается в модель
#    `swe-2-<уровень>`. Никаких других моделей обвязка не пускает.
#
# 6. У devin есть песочница уровня ОС (`--sandbox`), и она ВЫКЛЮЧЕНА по
#    умолчанию: агент должен уметь и кодить. Включается только явным
#    `--sandbox` обвязки.
#
# 7. Доверие к каталогу (`--respect-workspace-trust`): в неинтерактивном
#    режиме devin не может показать запрос доверия и падает в недоверенном
#    каталоге. Поэтому обвязка всегда передаёт `--respect-workspace-trust false`
#    (решение автора 25.09.2026); `--trust-workspace` оставлен для совместимости.
#
# 8. Фонового режима у devin нет вовсе — фон целиком на обвязке (воркер,
#    meta-файл, kill_tree, каталог состояния).
set -euo pipefail

STATE_DIR="${DEVIN_CLAUDE_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/devin-claude}"
JOBS_DIR="$STATE_DIR/jobs"
# Имя сессии → идентификатор сессии devin. Один файл на имя, формат тот же
# key=value, что и у meta джобы.
NAMES_DIR="$STATE_DIR/sessions"

# Домашний каталог самого devin и его база сессий. Обвязка читает её только на
# чтение: другого способа узнать идентификатор сессии у неинтерактивного
# прогона нет (`devin list` — TUI).
DEVIN_HOME="${DEVIN_CLAUDE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/devin/cli}"
SESSIONS_DB="${DEVIN_CLAUDE_SESSIONS_DB:-$DEVIN_HOME/sessions.db}"

# Единственный канал: короткое имя → семейство моделей. Второго канала нет
# (решение автора), поэтому список из одной строки, а не массив каналов.
CHANNEL_NAME="swe"
MODEL_FAMILY="swe-2"

# Уровни усилия = суффиксы моделей семейства swe-2. Всё, чего нет в списке,
# отклоняется кодом 2 ещё до запуска devin.
THINKING_LEVELS=(medium high max)
DEFAULT_THINKING="${DEVIN_CLAUDE_DEFAULT_THINKING:-medium}"

# Foreground держим заметно ниже потолка Bash-инструмента (600с): скрипт
# должен успеть вернуть осмысленную ошибку раньше, чем его оборвут снаружи.
DEFAULT_TIMEOUT=540
# Фону этот потолок не писан — он живёт вне вызова Bash. Два часа с запасом
# на большую задачу; 0 отключает лимит совсем.
DEFAULT_BG_TIMEOUT=7200
DEFAULT_PROBE_TIMEOUT=120

# Сессия Claude Code, из которой запущена джоба.
SESSION_ID="${DEVIN_CLAUDE_SESSION:-${CLAUDE_SESSION_ID:-}}"
# Идентификатор сессии devin для продолжения (resume) — не путать с SESSION_ID.
RESUME_SESSION_ID=""
RESUME_FROM=""

# Пути, которые надо убрать при любом выходе.
CLEANUP_PATHS=()
cleanup() {
  local rc=$?
  [[ ${#CLEANUP_PATHS[@]} -eq 0 ]] || rm -rf "${CLEANUP_PATHS[@]}"
  exit "$rc"
}
trap cleanup EXIT

die() {
  local code="$1"; shift
  echo "error: $*" >&2
  exit "$code"
}

usage() {
  cat >&2 <<USAGE
usage:
  devin-run.sh check [--json] [--no-probe] [--probe-timeout <sec>]
  devin-run.sh run [--session <name>] [--permission read|bash|write]
                  [--thinking medium|high|max] [--channel swe]
                  [--sandbox] [--trust-workspace]
                  [--cwd <dir>] [--timeout <sec>]
                  [--background] [--label <text>]
                  < prompt.txt
  devin-run.sh resume <session-name|job-id|devin-session-id> [same options as run] < prompt.txt
  devin-run.sh status [--json] [--all] [--running] [job-id]
  devin-run.sh result <job-id> [--wait [sec]]
  devin-run.sh logs <job-id> [--tail <lines>]
  devin-run.sh cancel <job-id|--all>        (kill is a synonym)
  devin-run.sh clean [--older-than <days>] [--all]
  devin-run.sh sessions [--json]
  devin-run.sh transcript <job-id> | transcript --session <name>

--permission: read (default, devin --permission-mode auto), bash (smart),
  write (dangerous). --write and --bash are kept as aliases for
  --permission write / --permission bash.
channel: swe = SWE-2 (the only channel); --thinking picks the effort level
  medium (default), high, max -> models swe-2-medium, swe-2-high, swe-2-max.
--sandbox: off by default; turns on devin's OS sandbox for the exec tool.
--trust-workspace: kept for compatibility; --respect-workspace-trust false
  is always passed to devin.
USAGE
  exit 2
}

need_value() {
  local opt="$1" val="${2:-}"
  [[ -n "$val" && "$val" != -* ]] || die 2 "$opt needs a value"
  printf '%s' "$val"
}

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

# Строка таблицы status. printf '%-Ns' в bash 3.2 без гарантированной
# UTF-8-локали считает ширину в БАЙТАХ, а не в символах, и колонки с «—»
# расходятся; python3 считает символы верно. Без python3 — запасной printf.
status_row() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c '
import signal, sys
# status | head — обычный способ читать список; без сброса SIGPIPE к
# системному поведению ранний обрыв пайпа печатал бы BrokenPipeError.
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
cols = sys.argv[1:7]
widths = [36, 10, 8, 14, 18, 0]
padded = [c + " " * max(0, w - len(c)) for c, w in zip(cols, widths)]
sys.stdout.write(" ".join(padded) + "\n")
' "$1" "$2" "$3" "$4" "$5" "$6" 2>/dev/null
  else
    printf '%-36s %-10s %-8s %-14s %-18s %s\n' "$1" "$2" "$3" "$4" "$5" "$6"
  fi
}

# Очистка \r и ANSI (CSI/OSC/одиночные escape): в отчёт check и в JSON байт
# 0x1b попадать не должен.
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

# --- модель и уровень усилия --------------------------------------------------
check_thinking() {
  local lvl="$1" t
  for t in "${THINKING_LEVELS[@]}"; do [[ "$t" == "$lvl" ]] && return 0; done
  die 2 "invalid --thinking '$lvl' - allowed values: ${THINKING_LEVELS[*]}"
}

# Уровень усилия → конкретная модель семейства. Другого способа выбрать
# модель у обвязки нет: канал один.
model_for_thinking() {
  printf '%s-%s' "$MODEL_FAMILY" "$1"
}

resolve_channel_only() {
  local want="$1"
  [[ "$want" == "$CHANNEL_NAME" ]] || die 2 "unknown channel '$want' - the only channel is $CHANNEL_NAME (SWE-2); effort level is --thinking medium|high|max"
  printf '%s' "$CHANNEL_NAME"
}

# --- бинарь devin -------------------------------------------------------------
# Порядок: явный DEVIN_CLAUDE_BIN → PATH. Абсолютный путь важен для фоновых
# запусков: отвязанный процесс не наследует изменения PATH после старта.
resolve_devin() {
  local bin="${DEVIN_CLAUDE_BIN:-}"
  if [[ -n "$bin" ]]; then
    command -v "$bin" >/dev/null 2>&1 || die 2 "DEVIN_CLAUDE_BIN points at '$bin', which is not an executable"
    command -v "$bin"
    return 0
  fi
  command -v devin >/dev/null 2>&1 \
    || die 2 "devin not found in PATH - install devin or set DEVIN_CLAUDE_BIN"
  command -v devin
}

# Явный DEVIN_CLAUDE_BIN, указывающий на несуществующий файл, — это не «бинаря
# нет, возьмём из PATH», а поломанная настройка: молчаливый откат на системный
# devin замаскировал бы её под «готовность: yes».
find_devin_bin() {
  if [[ -n "${DEVIN_CLAUDE_BIN:-}" ]]; then
    command -v "${DEVIN_CLAUDE_BIN}" 2>/dev/null
  else
    command -v devin 2>/dev/null
  fi
  # Ничего не нашли — не отказ функции: под set -e код возврата ушёл бы прямо
  # в `bin="$(find_devin_bin)"` и оборвал весь check до единой строки вывода.
  return 0
}

# --- права --------------------------------------------------------------------
# Три уровня обвязки → четыре режима devin. Совпадения один-в-один нет:
# лестница devin (auto ⊂ accept-edits ⊂ smart ⊂ dangerous) ставит правки
# РАНЬШЕ команд, а наша (read ⊂ bash ⊂ write) — наоборот. Отсюда отображение:
#   read  -> auto       — автоодобряются только читающие инструменты; всё
#                         остальное в режиме -p подтвердить некому, поэтому
#                         прогон честно упирается в запрет;
#   bash  -> smart      — самый низкий режим devin, где вообще выполняются
#                         команды; правки он тоже пропускает, так что «bash»
#                         здесь шире нашего обычного «команды без правок»;
#   write -> dangerous  — автоодобряется всё, как и задумано для write.
devin_permission_mode() {
  case "$1" in
    write)     printf 'dangerous' ;;
    read-bash) printf 'smart' ;;
    *)         printf 'auto' ;;
  esac
}

mode_label() {
  case "$1" in
    write)     echo "full access (edits and commands)" ;;
    read-bash) echo "commands and edits a fast model judges safe" ;;
    *)         echo "read-only (edits and commands blocked)" ;;
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

# --- база сессий devin (только чтение) ----------------------------------------
# devin держит sessions.db в режиме WAL и пишет в неё во время прогона. Read-only
# открытие такой базы иногда требует восстановления WAL и падает, поэтому у
# запроса два пути: сначала ro-подключение, при отказе — копия db/-wal/-shm во
# временный каталог. Возвращает то, что напечатал запрос; ошибки глушатся —
# отсутствие базы не должно ронять прогон.
devin_db_query() {
  local sql_name="$1"; shift
  command -v python3 >/dev/null 2>&1 || return 0
  [[ -f "$SESSIONS_DB" ]] || return 0
  python3 - "$SESSIONS_DB" "$sql_name" "$@" <<'PY' 2>/dev/null || true
import json, os, shutil, sqlite3, sys, tempfile

db_path, mode = sys.argv[1], sys.argv[2]
args = sys.argv[3:]


def connect():
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=5)
        con.execute("select count(*) from sessions").fetchone()
        return con, None
    except Exception:
        pass
    # WAL-база под живым писателем: читаем копию, а не оригинал.
    tmp = tempfile.mkdtemp(prefix="devin-db-")
    for suffix in ("", "-wal", "-shm"):
        src = db_path + suffix
        if os.path.exists(src):
            try:
                shutil.copy2(src, os.path.join(tmp, "sessions.db" + suffix))
            except OSError:
                pass
    try:
        con = sqlite3.connect(os.path.join(tmp, "sessions.db"), timeout=5)
        con.execute("select count(*) from sessions").fetchone()
        return con, tmp
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


try:
    con, tmp = connect()
except Exception:
    sys.exit(0)

try:
    if mode == "newest":
        # Сессия, созданная этим прогоном. Одного «самая свежая в этом
        # каталоге» мало: параллельные прогоны в одном каталоге дают
        # несколько сессий за те же секунды, и все они достались бы первому
        # спросившему. Поэтому сначала отбор по тексту промпта
        # (prompt_history), затем исключение идентификаторов, уже занятых
        # другими джобами, и только потом — самая ранняя из оставшихся.
        cwd, since, prompt_file = args[0], int(args[1]), args[2]
        taken = set(a for a in args[3:] if a)
        try:
            with open(prompt_file, encoding="utf-8", errors="replace") as fh:
                prompt = fh.read()
        except OSError:
            prompt = ""
        rows = con.execute(
            "select id from sessions where working_directory = ? and created_at >= ?"
            " order by created_at asc, last_activity_at asc",
            (cwd, since),
        ).fetchall()
        ids = [r[0] for r in rows if r[0] not in taken]
        matched = []
        if prompt:
            for sid in ids:
                hit = con.execute(
                    "select 1 from prompt_history where session_id = ? and content = ? limit 1",
                    (sid, prompt),
                ).fetchone()
                if hit:
                    matched.append(sid)
        pick = matched or ids
        if pick:
            print(pick[0])
    elif mode == "info":
        row = con.execute(
            "select id, working_directory, model, agent_mode, created_at, last_activity_at, title"
            " from sessions where id = ?",
            (args[0],),
        ).fetchone()
        if row:
            print("\n".join("" if v is None else str(v) for v in row))
    elif mode == "exists":
        row = con.execute("select 1 from sessions where id = ?", (args[0],)).fetchone()
        if row:
            print("yes")
    elif mode in ("events", "transcript"):
        # Ход разговора хранится деревом узлов; для отчёта достаточно порядка
        # вставки. Системные узлы (промпты, правила) пропускаем: они огромны и
        # не являются ходом диалога.
        rows = con.execute(
            "select chat_message from message_nodes where session_id = ? order by row_id",
            (args[0],),
        ).fetchall()
        seen = set()
        for (raw,) in rows:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            role = msg.get("role")
            if role == "system":
                continue
            mid = msg.get("message_id")
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            content = msg.get("content")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            calls = msg.get("tool_calls") or []
            if mode == "transcript":
                print(json.dumps(
                    {"role": role, "content": content, "tool_calls": calls},
                    ensure_ascii=False,
                ))
                continue
            for call in calls:
                call = call or {}
                # Вызов инструмента лежит либо плоско (name/arguments), либо
                # завёрнутым в function — devin пишет первый вариант, второй
                # оставлен на случай смены формата.
                fn = call.get("function") or {}
                name = fn.get("name") or call.get("name") or "?"
                raw_args = fn.get("arguments") or call.get("arguments") or ""
                if isinstance(raw_args, (dict, list)):
                    raw_args = json.dumps(raw_args, ensure_ascii=False)
                print("[tool] %s %s" % (name, str(raw_args).replace("\n", " ")[:160]))
            text = content.replace("\n", " ").strip()
            if text:
                label = "tool-result" if role == "tool" else (role or "?")
                limit = 120 if role == "tool" else 200
                print("[%s] %s" % (label, text[:limit]))
finally:
    con.close()
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
PY
}

# Сессия этого прогона: запрос к базе devin с исключением идентификаторов,
# которые уже присвоили себе другие джобы (свою собственную джобу не
# исключаем — иначе прогон никогда не нашёл бы свою же сессию повторно).
resolve_session_for_run() {
  local cwd="$1" since="$2" prompt_file="$3" self_dir="${4:-}"
  local taken=() d v
  if [[ -d "$JOBS_DIR" ]]; then
    for d in "$JOBS_DIR"/*/; do
      [[ -f "$d/meta" ]] || continue
      [[ -n "$self_dir" && "${d%/}" == "$self_dir" ]] && continue
      v="$(meta_get devin_session "$d/meta")"
      [[ -n "$v" && "$v" != "—" ]] && taken+=("$v")
    done
  fi
  devin_db_query newest "$cwd" "$since" "$prompt_file" "${taken[@]:-}"
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

value_or_dash() {
  local v="$1"
  [[ -n "$v" ]] && printf '%s' "$v" || printf '%s' "—"
}

# name → devin id/cwd/model/thinking/updated.
remember_session_name() {
  local name="$1" id="$2" cwd="${3:-}" model="${4:-}" thinking="${5:-}" nf
  [[ -n "$name" && -n "$id" ]] || return 0
  mkdir -p "$NAMES_DIR" 2>/dev/null || return 0
  chmod 700 "$STATE_DIR" "$NAMES_DIR" 2>/dev/null || true
  nf="$(name_file "$name")"
  {
    echo "name=$name"
    echo "id=$id"
    echo "cwd=${cwd:-}"
    echo "model=${model:-}"
    echo "thinking=${thinking:-}"
    echo "updated=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$nf"
}

# Имя → идентификатор сессии devin. Источник только свой: имена сессий
# существуют в обвязке, devin про них ничего не знает.
resolve_session_name() {
  local name="$1" nf stored id
  nf="$(name_file "$name")"
  [[ -f "$nf" ]] || return 0
  stored="$(meta_get name "$nf")"
  id="$(meta_get id "$nf")"
  [[ "$stored" == "$name" && -n "$id" ]] || return 0
  printf '%s' "$id"
}

# --- запуск devin с таймаутом ---------------------------------------------------
# `timeout(1)` на macOS нет в базовой системе, поэтому сторож свой: фоновый
# сон, по истечении которого дерево процессов прогона гасится, а прогон
# получает код 124 — тот же, что у GNU timeout.
DEVIN_RC=0
run_devin_with_timeout() {
  local timeout_s="$1" out_file="$2" err_file="$3" pid_file="$4"; shift 4
  local flag rc=0 pid watcher=""
  flag="$(mktemp "${TMPDIR:-/tmp}/devin-timeout.XXXXXX")"
  rm -f "$flag"

  # `trap - HUP INT TERM` внутри — не украшение: фоновый воркер глушит эти
  # сигналы у себя, а проигнорированный сигнал наследуется и через fork, и
  # через exec. Без сброса ни сторож, ни сам devin не отзывались бы на TERM,
  # и cancel добивал бы их только KILL, а сторож после прогона висел бы
  # полным таймаутом.
  ( trap - HUP INT TERM; exec "$@" ) >"$out_file" 2>"$err_file" &
  pid=$!
  [[ -n "$pid_file" ]] && printf '%s' "$pid" > "$pid_file"

  if [[ "$timeout_s" -gt 0 ]]; then
    (
      trap - HUP INT TERM
      sleep "$timeout_s"
      if kill -0 "$pid" 2>/dev/null; then
        : > "$flag"
        kill_tree "$pid" TERM
        sleep 5
        kill -0 "$pid" 2>/dev/null && kill_tree "$pid" KILL
      fi
    ) >/dev/null 2>&1 &
    watcher=$!
  fi

  wait "$pid" || rc=$?
  if [[ -n "$watcher" ]]; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
  fi
  [[ -f "$flag" ]] && rc=124
  rm -f "$flag"
  DEVIN_RC=$rc
  return 0
}

# --- check ---------------------------------------------------------------------
probe_label() {
  case "$1" in
    ok)      echo "answered in ${2}s" ;;
    timeout) echo "timeout ${2}s (no answer before the first token - retry later)" ;;
    error)   echo "error: $3" ;;
    *)       echo "skipped" ;;
  esac
}

# «В каталоге» — строка списка `devin models list`, начинающаяся с имени
# модели (в списке она идёт с отступом и описанием справа). Сравнение внутри
# bash, без grep: список большой, а `printf | grep -q` рвёт канал на первом же
# совпадении и печатает в stderr «write error: Broken pipe».
model_in_catalog() {
  local model="$1" out="$2"
  [[ -n "$out" ]] || { echo unknown; return 0; }
  local line trimmed
  while IFS= read -r line; do
    trimmed="${line#"${line%%[![:space:]]*}"}"
    case "$trimmed" in
      "$model "*|"$model") echo yes; return 0 ;;
    esac
  done <<< "$out"
  echo no
}

cmd_check() {
  local as_json=0 no_probe=0 probe_timeout=$DEFAULT_PROBE_TIMEOUT
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)          as_json=1; shift ;;
      --no-probe)      no_probe=1; shift ;;
      --probe-timeout) probe_timeout="${2:-}"
                       [[ "$probe_timeout" =~ ^[0-9]+$ ]] || die 2 "--probe-timeout takes a whole number of seconds"
                       shift 2 ;;
      -h|--help)       usage ;;
      *)               die 2 "unknown option '$1'" ;;
    esac
  done

  local bin="" bin_status="missing" version=""
  bin="$(find_devin_bin)"
  if [[ -n "$bin" ]]; then
    local vout
    if vout="$("$bin" version 2>/dev/null)"; then
      bin_status="ok"
      version="$(printf '%s' "$vout" | strip_control | head -1)"
    else
      bin_status="broken"
    fi
  fi

  local py3=""; py3="$(command -v python3 2>/dev/null || true)"
  local db_status="missing"
  [[ -f "$SESSIONS_DB" ]] && db_status="ok"

  local default_model; default_model="$(model_for_thinking "$DEFAULT_THINKING")"
  local models_out="" catalog_rc=1
  if [[ "$bin_status" == "ok" ]]; then
    models_out="$("$bin" models list 2>/dev/null | strip_control)" && catalog_rc=0 || catalog_rc=$?
  fi
  local cat_status="unknown"
  [[ $catalog_rc -eq 0 ]] && cat_status="$(model_in_catalog "$default_model" "$models_out")"

  # Проба готовности: настоящий прогон в ТЕКУЩЕМ каталоге. Временный каталог
  # тут не годится — он заведомо не доверен, и проба падала бы на доверии, а
  # не на готовности канала.
  local pstatus="skipped" pseconds="" perror=""
  if [[ $no_probe -eq 0 && "$bin_status" == "ok" ]]; then
    local pdir pf pout perr start end
    pdir="$(mktemp -d)"; CLEANUP_PATHS+=("$pdir")
    pf="$pdir/prompt.txt"; pout="$pdir/out"; perr="$pdir/err"
    printf 'Reply with one word: pong' > "$pf"
    start=$(date +%s)
    run_devin_with_timeout "$probe_timeout" "$pout" "$perr" "" \
      "$bin" --model "$default_model" --permission-mode auto --respect-workspace-trust false --prompt-file "$pf" -p
    end=$(date +%s)
    if [[ $DEVIN_RC -eq 0 && -s "$pout" ]]; then
      pstatus="ok"; pseconds="$((end-start))"
    elif [[ $DEVIN_RC -eq 124 ]]; then
      # Печатаем заданный таймаут, а не измеренное время: измеренное всегда
      # больше на время добивания процесса.
      pstatus="timeout"; pseconds="$probe_timeout"
    else
      pstatus="error"
      perror="$(strip_control < "$perr" 2>/dev/null | tr '\n' ' ' | head -c 160)"
      [[ -z "${perror// }" ]] && perror="exit code $DEVIN_RC"
    fi
  fi

  local ready="no"
  if [[ "$bin_status" == "ok" ]]; then
    if [[ "$pstatus" == "ok" ]]; then
      ready="yes"
    elif [[ $no_probe -eq 1 && "$cat_status" != "no" ]]; then
      ready="yes"
    fi
  fi

  local running; running="$(count_running_jobs)"
  local named=0
  [[ -d "$NAMES_DIR" ]] && named="$(ls -1 "$NAMES_DIR" 2>/dev/null | wc -l | tr -d ' ' || true)"

  if [[ $as_json -eq 1 ]]; then
    printf '{"ready":"%s","binary":"%s","binary_status":"%s","version":"%s","python3":"%s","sessions_db":"%s","sessions_db_status":"%s","channel":"%s","default_thinking":"%s","default_model":"%s","catalog":"%s","probe":"%s","probe_seconds":%s,"probe_error":"%s","probe_timeout":%s,"sandbox_default":"off","running_jobs":%s,"named_sessions":%s,"state_dir":"%s"}\n' \
      "$(json_escape "$ready")" "$(json_escape "${bin:-}")" "$(json_escape "$bin_status")" \
      "$(json_escape "$version")" "$(json_escape "${py3:-}")" \
      "$(json_escape "$SESSIONS_DB")" "$(json_escape "$db_status")" \
      "$(json_escape "$CHANNEL_NAME")" "$(json_escape "$DEFAULT_THINKING")" \
      "$(json_escape "$default_model")" "$(json_escape "$cat_status")" \
      "$(json_escape "$pstatus")" "${pseconds:-null}" "$(json_escape "$perror")" \
      "$probe_timeout" "$running" "${named:-0}" "$(json_escape "$STATE_DIR")"
  else
    echo "ready:              $ready"
    echo "binary:             ${bin:-not found} ($bin_status)"
    echo "version:            ${version:--}"
    echo "python3:            ${py3:-missing (status/logs/transcript degrade)}"
    echo "sessions db:        $SESSIONS_DB ($db_status)"
    echo "channel:            $CHANNEL_NAME -> $MODEL_FAMILY; effort levels: ${THINKING_LEVELS[*]}"
    echo "default model:      $default_model (in catalog: $cat_status)"
    echo "probe:              $(probe_label "$pstatus" "$pseconds" "$perror")"
    echo "default permission: $(mode_label read-only) (devin --permission-mode auto)"
    echo "OS sandbox:         off by default (turn on with --sandbox)"
    echo "workspace trust:    skipped (--respect-workspace-trust false always passed)"
    echo "background jobs running: $running"
    echo "named sessions:     ${named:-0}"
    echo "state directory:    $STATE_DIR"
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
  local mode="read-only" thinking="$DEFAULT_THINKING" workdir="" timeout_s="" \
        background=0 label="" name="" sandbox=0 trust_off=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --session)         name="$(need_value --session "${2:-}")"; shift 2 ;;
      --permission)      mode="$(mode_from_permission "$(need_value --permission "${2:-}")")"; shift 2 ;;
      --write)           mode="write"; shift ;;
      --bash)            [[ "$mode" == "write" ]] || mode="read-bash"; shift ;;
      --channel)         resolve_channel_only "$(need_value --channel "${2:-}")" >/dev/null; shift 2 ;;
      --thinking)        thinking="$(need_value --thinking "${2:-}")"; check_thinking "$thinking"; shift 2 ;;
      --sandbox)         sandbox=1; shift ;;
      --trust-workspace) trust_off=1; shift ;;
      --cwd)             workdir="$(need_value --cwd "${2:-}")"; shift 2 ;;
      --timeout)         timeout_s="${2:-}"; [[ -z "$timeout_s" ]] && die 2 "--timeout needs a value"; shift 2 ;;
      --label)           label="${2:-}"; [[ -z "$label" ]] && die 2 "--label needs a value"; shift 2 ;;
      --background)      background=1; shift ;;
      -h|--help)         usage ;;
      *)                 die 2 "unknown option '$1' (the prompt goes on stdin, not as an argument)" ;;
    esac
  done

  workdir="${workdir:-$PWD}"
  [[ -d "$workdir" ]] || die 2 "directory '$workdir' does not exist"
  workdir="$(cd "$workdir" && pwd)"

  if [[ -n "$name" ]]; then
    check_session_name "$name"
    local existing; existing="$(resolve_session_name "$name" || true)"
    [[ -n "$existing" ]] && die 2 "session '$name' already exists - continue it with: devin-run.sh resume --session '$name'; a new session needs another name"
  fi

  start_run "$mode" "$thinking" "$workdir" "$timeout_s" "$background" "$label" \
            "$name" "$sandbox" "$trust_off"
}

# Общая часть run и resume: собрать командную строку devin и отправить её в
# foreground или в фон. RESUME_SESSION_ID к этому моменту уже проставлен
# (или пуст).
DEVIN_ARGS=()
build_devin_args() {
  local bin="$1" mode="$2" model="$3" sandbox="$4" trust_off="$5" prompt_file="$6"
  DEVIN_ARGS=("$bin" --model "$model" --permission-mode "$(devin_permission_mode "$mode")")
  [[ "$sandbox" -eq 1 ]] && DEVIN_ARGS+=(--sandbox)
  # Проверку доверия снимаем только по явной просьбе: неинтерактивный прогон в
  # недоверенном каталоге падает, и решение «доверять» принимает человек.
  [[ "$trust_off" -eq 1 ]] && DEVIN_ARGS+=(--respect-workspace-trust false)
  [[ -n "$RESUME_SESSION_ID" ]] && DEVIN_ARGS+=(-r "$RESUME_SESSION_ID")
  DEVIN_ARGS+=(--prompt-file "$prompt_file" -p)
}

start_run() {
  local mode="$1" thinking="$2" workdir="$3" timeout_s="$4" background="$5" \
        label="$6" name="$7" sandbox="$8" trust_off="$9"

  if [[ -z "$timeout_s" ]]; then
    if [[ $background -eq 1 ]]; then timeout_s="$DEFAULT_BG_TIMEOUT"; else timeout_s="$DEFAULT_TIMEOUT"; fi
  fi
  [[ "$timeout_s" =~ ^[0-9]+$ ]] || die 2 "--timeout takes a whole number of seconds (0 = no limit)"

  local prompt
  prompt="$(cat)"
  [[ -z "${prompt//[[:space:]]/}" ]] && die 2 "empty prompt on stdin"

  local bin; bin="$(resolve_devin)"
  local model; model="$(model_for_thinking "$thinking")"

  if [[ $background -eq 1 ]]; then
    run_background "$bin" "$mode" "$model" "$thinking" "$workdir" "$timeout_s" \
      "$prompt" "$label" "$name" "$sandbox" "$trust_off"
    return 0
  fi
  run_foreground "$bin" "$mode" "$model" "$thinking" "$workdir" "$timeout_s" \
    "$prompt" "$name" "$sandbox" "$trust_off"
}

# Понятный текст вместо сырого отказа devin: доверие к каталогу — самая частая
# причина падения неинтерактивного прогона на новой машине.
trust_hint_if_needed() {
  local err_text="$1" workdir="$2"
  case "$err_text" in
    *untrusted*|*"workspace trust"*)
      printf ' - the directory %s was never trusted: start `devin` there interactively once, or rerun with --trust-workspace' "$workdir" ;;
  esac
}

run_foreground() {
  local bin="$1" mode="$2" model="$3" thinking="$4" workdir="$5" timeout_s="$6"
  local prompt="$7" name="$8" sandbox="$9" trust_off="${10}"

  local tmpdir out_file err_file prompt_file start_epoch
  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/devin-run.XXXXXX")"
  CLEANUP_PATHS+=("$tmpdir")
  out_file="$tmpdir/out"; err_file="$tmpdir/err"; prompt_file="$tmpdir/prompt.txt"
  printf '%s' "$prompt" > "$prompt_file"

  build_devin_args "$bin" "$mode" "$model" "$sandbox" "$trust_off" "$prompt_file"
  start_epoch=$(date +%s)
  ( cd "$workdir" && run_devin_with_timeout "$timeout_s" "$out_file" "$err_file" "" "${DEVIN_ARGS[@]}"; exit "$DEVIN_RC" ) && DEVIN_RC=0 || DEVIN_RC=$?

  # Имя сессии запоминаем даже при неудаче прогона — сессия уже создана.
  if [[ -n "$name" ]]; then
    local sid="$RESUME_SESSION_ID"
    [[ -n "$sid" ]] || sid="$(resolve_session_for_run "$workdir" "$start_epoch" "$prompt_file")"
    remember_session_name "$name" "$sid" "$workdir" "$model" "$thinking"
  fi

  local err_text=""
  [[ -s "$err_file" ]] && err_text="$(strip_control < "$err_file" | tr '\n' ' ' | tail -c 500)"

  case "$DEVIN_RC" in
    0)
      # Пустой ответ при нулевом коде — обычный исход запрещённого
      # инструмента: devin в неинтерактивном режиме отклоняет неодобренный
      # вызов и заканчивает ход вообще без текста. Молча вернуть пустой
      # stdout нельзя — вызывающий принял бы это за ответ.
      if [[ ! -s "$out_file" ]]; then
        die 6 "devin returned an empty answer${err_text:+ - $err_text}: in --permission read a tool it is not allowed to run ends the turn without text; rerun with --permission bash or write if the task really needs it"
      fi
      cat "$out_file"
      ;;
    124)
      [[ -s "$out_file" ]] && cat "$out_file"
      die 6 "devin: timed out after ${timeout_s}s - rerun with --background to lift the ceiling${err_text:+; stderr: $err_text}"
      ;;
    *)
      [[ -s "$out_file" ]] && cat "$out_file"
      die 6 "devin: run exited with code $DEVIN_RC${err_text:+ - $err_text}$(trust_hint_if_needed "$err_text" "$workdir")"
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
    id="devin-$stamp-$$-$RANDOM"
    dir="$JOBS_DIR/$id"
    mkdir "$dir" 2>/dev/null && { printf '%s' "$id"; return 0; }
    i=$((i+1))
    [[ $i -ge 100 ]] && die 5 "could not allocate a job id in $JOBS_DIR"
  done
}

# Строка события в свой поток: у devin -p потока событий нет, поэтому жизненный
# цикл прогона обвязка записывает сама, а ходы модели logs достаёт из базы
# сессий devin.
job_event() {
  local file="$1" type="$2" text="${3:-}"
  printf '{"ts":"%s","type":"%s","text":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(json_escape "$type")" "$(json_escape "$text")" >> "$file"
}

run_background() {
  local bin="$1" mode="$2" model="$3" thinking="$4" workdir="$5" timeout_s="$6"
  local prompt="$7" label="$8" name="$9" sandbox="${10}" trust_off="${11}"

  local job_id job_dir start_epoch
  job_id="$(claim_job_dir)"
  job_dir="$JOBS_DIR/$job_id"
  chmod 700 "$STATE_DIR" "$JOBS_DIR" 2>/dev/null || true
  chmod 700 "$job_dir"

  printf '%s' "$prompt" > "$job_dir/prompt.txt"
  : > "$job_dir/events.jsonl"
  : > "$job_dir/output.txt"
  : > "$job_dir/stderr.txt"

  build_devin_args "$bin" "$mode" "$model" "$sandbox" "$trust_off" "$job_dir/prompt.txt"
  start_epoch=$(date +%s)
  {
    echo "id=$job_id"
    echo "status=running"
    echo "cwd=$workdir"
    echo "mode=$mode"
    echo "permission_mode=$(devin_permission_mode "$mode")"
    echo "model=$model"
    echo "channel=$CHANNEL_NAME"
    echo "thinking=$thinking"
    echo "sandbox=$( [[ $sandbox -eq 1 ]] && echo on || echo off )"
    echo "trust_check=$( [[ $trust_off -eq 1 ]] && echo skipped || echo respected )"
    echo "label=${label:-—}"
    echo "session=${SESSION_ID:-—}"
    echo "session_name=${name:-—}"
    # Идентификатор сессии devin известен сразу только при resume; для нового
    # прогона его дописывает воркер, как только сессия появится в базе.
    echo "devin_session=${RESUME_SESSION_ID:-—}"
    echo "resumed_from=${RESUME_FROM:-—}"
    echo "timeout=$timeout_s"
    # Строка запуска целиком — по ней видно и режим прав, и наличие --sandbox,
    # и то, снималась ли проверка доверия.
    echo "cmdline=${DEVIN_ARGS[*]}"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "started_epoch=$start_epoch"
  } > "$job_dir/meta"
  job_event "$job_dir/events.jsonl" job_start "$model, permission-mode $(devin_permission_mode "$mode"), sandbox $( [[ $sandbox -eq 1 ]] && echo on || echo off ), cwd $workdir"

  # Отвязанный воркер в своей процесс-группе: devin запускается изнутри, его
  # pid попадает в meta (по нему cancel бьёт kill_tree).
  set -m
  (
    trap '' HUP INT TERM
    meta_set worker_pid "$(sh -c 'echo $PPID')" "$job_dir/meta"
    cd "$workdir" || exit 1

    # Спутник прогона: переносит pid devin в meta (по нему cancel бьёт дерево
    # процессов) и ищет сессию этого прогона в базе devin — её id нужен для
    # logs и resume ещё до того, как прогон закончится.
    (
      trap - HUP INT TERM
      local waited=0 sid="" tries=0
      while [[ $waited -lt 100 ]]; do
        [[ -s "$job_dir/pid" ]] && break
        sleep 0.1; waited=$((waited+1))
      done
      [[ -s "$job_dir/pid" ]] && meta_set pid "$(cat "$job_dir/pid")" "$job_dir/meta"
      [[ -n "$RESUME_SESSION_ID" ]] && exit 0
      while [[ $tries -lt 150 ]]; do
        sid="$(resolve_session_for_run "$workdir" "$start_epoch" "$job_dir/prompt.txt" "$job_dir")"
        if [[ -n "$sid" ]]; then
          meta_set devin_session "$sid" "$job_dir/meta"
          job_event "$job_dir/events.jsonl" session "devin session $sid"
          break
        fi
        sleep 2; tries=$((tries+1))
      done
    ) >/dev/null 2>&1 &
    local poller=$!

    run_devin_with_timeout "$timeout_s" "$job_dir/output.txt" "$job_dir/stderr.txt" \
      "$job_dir/pid" "${DEVIN_ARGS[@]}"
    local rc=$DEVIN_RC
    kill "$poller" 2>/dev/null || true
    wait "$poller" 2>/dev/null || true

    local sid; sid="$(meta_get devin_session "$job_dir/meta")"
    if [[ -z "$sid" || "$sid" == "—" ]]; then
      sid="${RESUME_SESSION_ID:-$(resolve_session_for_run "$workdir" "$start_epoch" "$job_dir/prompt.txt" "$job_dir")}"
      [[ -n "$sid" ]] && meta_set devin_session "$sid" "$job_dir/meta"
    fi
    if [[ -n "$name" && -n "$sid" && "$sid" != "—" ]]; then
      remember_session_name "$name" "$sid" "$workdir" "$model" "$thinking"
    fi

    local final="completed"
    [[ $rc -eq 124 ]] && final="timeout"
    [[ $rc -ne 0 && $rc -ne 124 ]] && final="failed"
    [[ -f "$job_dir/canceled" ]] && final="canceled"
    if [[ "$final" == "failed" && -s "$job_dir/stderr.txt" ]]; then
      meta_set error "$(strip_control < "$job_dir/stderr.txt" | tr '\n' ' ' | tail -c 300)" "$job_dir/meta"
    fi
    job_event "$job_dir/events.jsonl" job_end "exit $rc, $final, answer $(file_bytes "$job_dir/output.txt") bytes"
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
  [[ -n "$job_id" ]] || die 2 "a job-id is required (list them with: devin-run.sh status)"
  case "$job_id" in
    */*|*..*) die 2 "invalid job-id '$job_id'" ;;
  esac
  local dir="$JOBS_DIR/$job_id"
  [[ -d "$dir" ]] || die 2 "no job with id '$job_id' (list them with: devin-run.sh status --all)"
  echo "$dir"
}

job_status_of() {
  local dir="$1"
  local st pid
  st="$(meta_get status "$dir/meta")"
  [[ "$st" != "running" ]] && { echo "$st"; return 0; }
  pid="$(meta_get pid "$dir/meta")"
  if [[ -n "$pid" && "$pid" != "—" ]] && kill -0 "$pid" 2>/dev/null; then
    echo running
    return 0
  fi
  # Выход devin — не конец задачи, пока воркер ещё дописывает meta.
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
      # 36 — ширина идентификатора с датой/pid/RANDOM; 14 — под самую длинную
      # модель канала (swe-2-medium).
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
  printf '{"id":"%s","status":"%s","meta_status":"%s","label":"%s","cwd":"%s","mode":"%s","permission_mode":"%s","model":"%s","channel":"%s","thinking":"%s","sandbox":"%s","trust_check":"%s","session":"%s","session_name":"%s","devin_session":"%s","resumed_from":"%s","error":"%s","started":"%s","elapsed":"%s","timeout":"%s","exit":"%s","cmdline":"%s","output_bytes":%s}' \
    "$(json_escape "$(meta_get id "$dir/meta")")" \
    "$(json_escape "$st")" \
    "$(json_escape "$(meta_get status "$dir/meta")")" \
    "$(json_escape "$(meta_get label "$dir/meta")")" \
    "$(json_escape "$(meta_get cwd "$dir/meta")")" \
    "$(json_escape "$(meta_get mode "$dir/meta")")" \
    "$(json_escape "$(meta_get permission_mode "$dir/meta")")" \
    "$(json_escape "$(meta_get model "$dir/meta")")" \
    "$(json_escape "$(meta_get channel "$dir/meta")")" \
    "$(json_escape "$(meta_get thinking "$dir/meta")")" \
    "$(json_escape "$(meta_get sandbox "$dir/meta")")" \
    "$(json_escape "$(meta_get trust_check "$dir/meta")")" \
    "$(json_escape "$(meta_get session "$dir/meta")")" \
    "$(json_escape "$(meta_get session_name "$dir/meta")")" \
    "$(json_escape "$(meta_get devin_session "$dir/meta")")" \
    "$(json_escape "$(meta_get resumed_from "$dir/meta")")" \
    "$(json_escape "$(meta_get error "$dir/meta")")" \
    "$(json_escape "$(meta_get started "$dir/meta")")" \
    "$(json_escape "$(elapsed_of "$dir")")" \
    "$(json_escape "$(meta_get timeout "$dir/meta")")" \
    "$(json_escape "$(meta_get exit "$dir/meta")")" \
    "$(json_escape "$(meta_get cmdline "$dir/meta")")" \
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
    local waited=0
    while [[ "$st" == "running" && $waited -lt $wait_s ]]; do
      sleep 3
      waited=$((waited+3))
      st="$(job_status_of "$dir")"
    done
  fi

  case "$st" in
    running)
      die 5 "job still running ($(elapsed_of "$dir") since $(meta_get started "$dir/meta")); poll later: devin-run.sh status $job_id"
      ;;
    orphaned)
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
      die 6 "job failed (exit $(meta_get exit "$dir/meta"))${em:+ - $em}$(trust_hint_if_needed "$em" "$(meta_get cwd "$dir/meta")")"
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
  local sid; sid="$(meta_get devin_session "$dir/meta")"

  echo "status:  $st ($(elapsed_of "$dir"))"
  echo "session: ${sid:-—}"
  echo "answer:  $(file_bytes "$dir/output.txt") bytes"
  if [[ -s "$dir/stderr.txt" ]]; then
    echo "--- devin stderr ---"
    strip_control < "$dir/stderr.txt" | tail -n 10
  fi
  if [[ -s "$dir/events.jsonl" ]]; then
    echo "--- job events ---"
    tail -n "$tail_n" "$dir/events.jsonl"
  fi
  # Ходы модели и вызовы инструментов живут не у нас, а в базе сессий devin.
  if [[ -n "$sid" && "$sid" != "—" ]]; then
    local turns; turns="$(devin_db_query events "$sid" | tail -n "$tail_n")"
    if [[ -n "$turns" ]]; then
      echo "--- last $tail_n turns of devin session $sid ---"
      printf '%s\n' "$turns"
    else
      echo "no turns recorded in the devin session yet"
    fi
  elif [[ "$st" == "running" ]]; then
    echo "the devin session has not appeared in $SESSIONS_DB yet. That is normal in the"
    echo "first seconds of a run - the signs of life are the running status and a growing"
    echo "elapsed time."
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
  [[ -n "$target" ]] || die 2 "a job-id or --all is required (list them with: devin-run.sh status)"
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
  echo "devin sessions were not deleted - they live in $SESSIONS_DB (delete one with: devin rm <session-id>)"
}

# --- resume -----------------------------------------------------------------
cmd_resume() {
  local target="" name="" mode="" thinking="" workdir="" timeout_s="" \
        background=0 label="" sandbox=0 trust_off=0 sandbox_given=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --session)         name="$(need_value --session "${2:-}")"; shift 2 ;;
      --permission)      mode="$(mode_from_permission "$(need_value --permission "${2:-}")")"; shift 2 ;;
      --write)           mode="write"; shift ;;
      --bash)            [[ "$mode" == "write" ]] || mode="read-bash"; shift ;;
      --channel)         resolve_channel_only "$(need_value --channel "${2:-}")" >/dev/null; shift 2 ;;
      --thinking)        thinking="$(need_value --thinking "${2:-}")"; check_thinking "$thinking"; shift 2 ;;
      --sandbox)         sandbox=1; sandbox_given=1; shift ;;
      --trust-workspace) trust_off=1; shift ;;
      --cwd)             workdir="$(need_value --cwd "${2:-}")"; shift 2 ;;
      --timeout)         timeout_s="${2:-}"; [[ -z "$timeout_s" ]] && die 2 "--timeout needs a value"; shift 2 ;;
      --label)           label="${2:-}"; [[ -z "$label" ]] && die 2 "--label needs a value"; shift 2 ;;
      --background)      background=1; shift ;;
      -h|--help)         usage ;;
      -*)                die 2 "unknown option '$1' (the prompt goes on stdin, not as an argument)" ;;
      *)                 [[ -n "$target" ]] && die 2 "unexpected argument '$1'"
                         target="$1"; shift ;;
    esac
  done

  [[ -n "$target" || -n "$name" ]] \
    || die 2 "a session name, job-id or devin session id is required: devin-run.sh resume --session <name> (list them with: devin-run.sh sessions)"

  local job_dir="" sess_id="" src_cwd="" src_mode="" src_thinking="" src_sandbox="" src_name=""
  if [[ -n "$target" ]]; then
    case "$target" in
      */*|*..*) die 2 "invalid argument '$target'" ;;
    esac
    if [[ -d "$JOBS_DIR/$target" ]]; then
      job_dir="$JOBS_DIR/$target"
    elif [[ -n "$(resolve_session_name "$target" || true)" ]]; then
      [[ -n "$name" && "$name" != "$target" ]] && die 2 "both '$target' and --session '$name' given - use one of them"
      name="$target"
    elif [[ -n "$(devin_db_query exists "$target")" ]]; then
      # Прямой идентификатор сессии devin: продолжаем её, даже если задачу
      # заводила не обвязка (например, человек работал интерактивно).
      sess_id="$target"
      RESUME_FROM="$target"
    elif [[ -z "$name" ]]; then
      die 2 "no job, session name or devin session called '$target' - list them with: devin-run.sh sessions (or status --all)"
    else
      die 2 "both '$target' and --session '$name' given - use one of them"
    fi
  fi

  if [[ -n "$job_dir" ]]; then
    local st; st="$(job_status_of "$job_dir")"
    [[ "$st" == "running" ]] && die 2 "job '$target' is still running - resume it after it finishes, or fall back to a fresh run"
    sess_id="$(meta_get devin_session "$job_dir/meta")"
    [[ -n "$sess_id" && "$sess_id" != "—" ]] \
      || die 2 "job '$target' has no devin session id - start a fresh run: devin-run.sh run"
    src_cwd="$(meta_get cwd "$job_dir/meta")"
    src_mode="$(meta_get mode "$job_dir/meta")"
    src_thinking="$(meta_get thinking "$job_dir/meta")"
    src_sandbox="$(meta_get sandbox "$job_dir/meta")"
    src_name="$(meta_get session_name "$job_dir/meta")"
    [[ "$src_name" == "—" ]] && src_name=""
    RESUME_FROM="$target"
    [[ -n "$name" ]] || name="$src_name"
  elif [[ -z "$sess_id" ]]; then
    check_session_name "$name"
    sess_id="$(resolve_session_name "$name" || true)"
    [[ -n "$sess_id" ]] \
      || die 2 "no session named '$name' - start one: devin-run.sh run --session '$name'"
    src_cwd="$(meta_get cwd "$(name_file "$name")")"
    src_thinking="$(meta_get thinking "$(name_file "$name")")"
  fi

  [[ -n "$workdir" ]] || workdir="$src_cwd"
  [[ -n "$workdir" && -d "$workdir" ]] || workdir="$PWD"
  workdir="$(cd "$workdir" && pwd)"

  # Уровень усилия наследуется от исходного прогона: смена модели в середине
  # сессии — отдельное решение, а не побочный эффект resume.
  if [[ -z "$thinking" ]]; then
    if [[ -n "$src_thinking" && "$src_thinking" != "—" ]]; then thinking="$src_thinking"; else thinking="$DEFAULT_THINKING"; fi
  fi
  check_thinking "$thinking"

  if [[ -z "$mode" ]]; then
    case "$src_mode" in
      write|read-bash|read-only) mode="$src_mode" ;;
      *)                         mode="read-only" ;;
    esac
  fi
  if [[ $sandbox_given -eq 0 && "$src_sandbox" == "on" ]]; then sandbox=1; fi
  [[ -n "$label" ]] || label="resume of ${name:-${RESUME_FROM:-$sess_id}}"

  RESUME_SESSION_ID="$sess_id"
  start_run "$mode" "$thinking" "$workdir" "$timeout_s" "$background" "$label" \
            "${name:-}" "$sandbox" "$trust_off"
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

  local f out=""
  for f in "$NAMES_DIR"/*; do
    [[ -f "$f" ]] || continue
    if [[ $as_json -eq 1 ]]; then
      out+="$(printf '{"name":"%s","id":"%s","cwd":"%s","model":"%s","thinking":"%s","updated":"%s"}' \
        "$(json_escape "$(meta_get name "$f")")" \
        "$(json_escape "$(meta_get id "$f")")" \
        "$(json_escape "$(meta_get cwd "$f")")" \
        "$(json_escape "$(meta_get model "$f")")" \
        "$(json_escape "$(meta_get thinking "$f")")" \
        "$(json_escape "$(meta_get updated "$f")")"),"
    else
      printf '%-28s %-26s %-14s %-22s %s\n' \
        "$(meta_get name "$f")" "$(value_or_dash "$(meta_get id "$f")")" \
        "$(value_or_dash "$(meta_get model "$f")")" \
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

  local sid=""
  if [[ -n "$job_id" ]]; then
    local dir; dir="$(job_dir_of "$job_id")"
    sid="$(meta_get devin_session "$dir/meta")"
    [[ -n "$sid" && "$sid" != "—" ]] \
      || die 2 "job '$job_id' has no devin session id yet (the run has not started or no session was created)"
  elif [[ -n "$name" ]]; then
    check_session_name "$name"
    sid="$(resolve_session_name "$name" || true)"
    [[ -n "$sid" ]] || die 2 "no session named '$name' - list them with: devin-run.sh sessions"
  else
    die 2 "give a job-id or --session <name>"
  fi

  command -v python3 >/dev/null 2>&1 || die 2 "python3 is required to read the devin session database"
  [[ -f "$SESSIONS_DB" ]] || die 2 "devin session database '$SESSIONS_DB' not found"
  local text; text="$(devin_db_query transcript "$sid")"
  [[ -n "$text" ]] || die 2 "devin session '$sid' is gone from the database - it was deleted"
  printf '%s\n' "$text"
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
