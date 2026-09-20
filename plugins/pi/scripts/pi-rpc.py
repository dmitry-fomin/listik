#!/usr/bin/env python3
"""pi-rpc.py — один прогон CLI-агента pi (--mode rpc) от промпта до финального ответа.

Только стандартная библиотека. Читает промпт целиком из stdin, запускает
`pi --mode rpc` в отдельной группе процессов, ведёт диалог по протоколу RPC
(см. docs/rpc.md пакета pi-coding-agent) и печатает в stdout РОВНО текст
финального ответа модели — ничего больше. Вся диагностика идёт в stderr.

Эта обвязка — только нижний слой (один прогон). Всё про фоновые задачи,
идентификаторы, status/result/cancel — задача обвязки более высокого уровня,
которая будет вызывать этот скрипт.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

try:
    import queue
except ImportError:  # pragma: no cover — python2 не поддерживается, но на всякий случай
    import Queue as queue  # type: ignore


# ---------------------------------------------------------------------------
# Очистка ANSI: все вхождения CSI/OSC/одиночных escape-байтов по всей строке.
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"       # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[=>]"                        # одиночные
)


def strip_ansi(text):
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Кольцевой буфер последних N байт stderr pi — печатается после error:-строки
# в сценариях отказа.
# ---------------------------------------------------------------------------

class RingBuffer(object):
    def __init__(self, maxlen=500):
        self.maxlen = maxlen
        self._buf = bytearray()
        self._lock = threading.Lock()

    def add(self, text):
        data = text.encode("utf-8", errors="replace")
        with self._lock:
            self._buf.extend(data)
            if len(self._buf) > self.maxlen:
                del self._buf[: len(self._buf) - self.maxlen]

    def dump(self):
        with self._lock:
            return bytes(self._buf).decode("utf-8", errors="replace")


STDERR_LOCK = threading.Lock()


def eprint(text):
    """Печать в stderr скрипта с общей блокировкой (потоки stdout/stderr pi
    и основной поток пишут в один stderr конкурентно)."""
    with STDERR_LOCK:
        sys.stderr.write(text)
        if not text.endswith("\n"):
            sys.stderr.write("\n")
        sys.stderr.flush()


def fail2(message):
    """Ошибка разбора/проверки опций до запуска pi — код 2."""
    with STDERR_LOCK:
        sys.stderr.write("error: %s\n" % message)
        sys.stderr.flush()
    sys.exit(2)


class ArgParser(argparse.ArgumentParser):
    def error(self, message):
        with STDERR_LOCK:
            sys.stderr.write("error: %s\n" % message)
            sys.stderr.flush()
        sys.exit(2)


def parse_args(argv):
    p = ArgParser(add_help=True)
    p.add_argument("--pi", default="pi")
    p.add_argument("--provider", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--thinking", default=None)
    p.add_argument("--tools", default=None)
    p.add_argument("--cwd", default=None)
    p.add_argument("--session-id", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--session-file", default=None)
    p.add_argument("--no-session", action="store_true")
    p.add_argument("--events", required=True)
    p.add_argument("--meta", default=None)
    p.add_argument("--timeout", type=int, default=540)
    p.add_argument("--pi-arg", action="append", default=[], dest="pi_arg")
    return p.parse_args(argv)


def validate_args(args):
    modes = [bool(args.session_id), bool(args.session_file), bool(args.no_session)]
    if sum(1 for m in modes if m) != 1:
        fail2("exactly one of --session-id, --session-file, --no-session is required")
    if args.name is not None and not args.session_id:
        fail2("--name is only allowed together with --session-id")
    if args.timeout < 0:
        fail2("--timeout cannot be negative")

    cwd = args.cwd if args.cwd is not None else os.getcwd()
    if not os.path.isdir(cwd):
        fail2("directory not found: %s" % cwd)
    args.cwd = os.path.abspath(cwd)

    if args.session_file is not None and not os.path.isfile(args.session_file):
        fail2("session file not found: %s" % args.session_file)

    pi_path = args.pi
    if os.sep in pi_path or ("/" in pi_path):
        if not (os.path.isfile(pi_path) and os.access(pi_path, os.X_OK)):
            fail2("pi binary not found or not executable: %s" % pi_path)
        args.pi = os.path.abspath(pi_path)
    else:
        found = shutil.which(pi_path)
        if not found:
            fail2("pi binary not found in PATH: %s" % pi_path)
        args.pi = found

    return args


def read_prompt():
    raw = sys.stdin.buffer.read()
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        fail2("empty prompt")
    return text


def build_argv(args):
    argv = [args.pi, "--mode", "rpc", "--provider", args.provider, "--model", args.model]
    if args.thinking:
        argv += ["--thinking", args.thinking]
    if args.tools:
        argv += ["--tools", args.tools]
    if args.session_id:
        argv += ["--session-id", args.session_id]
        if args.name:
            argv += ["--name", args.name]
    elif args.session_file:
        argv += ["--session", args.session_file]
    else:
        argv += ["--no-session"]
    argv += list(args.pi_arg)
    return argv


# ---------------------------------------------------------------------------
# Состояние прогона
# ---------------------------------------------------------------------------

class RunState(object):
    def __init__(self):
        self.session_file = None
        self.session_id = None
        self.session_name = None
        self.model_provider = None
        self.model_id = None
        self.thinking_level = None

        self.had_assistant_message = False
        self.last_stop_reason = None
        self.last_error_message = None
        self.last_message_text = ""

        self.delta_buffer = {}

        self.sent_last_request = False


EOF_SENTINEL = object()


def make_stdout_reader(stream, out_queue, ring_unused=None):
    def run():
        try:
            for raw_line in iter(stream.readline, b""):
                line = raw_line.decode("utf-8", errors="replace")
                if line.endswith("\n"):
                    line = line[:-1]
                if line.endswith("\r"):
                    line = line[:-1]
                line = strip_ansi(line)
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    eprint("pi: %s" % line)
                    continue
                out_queue.put(obj)
        except (OSError, ValueError):
            pass
        finally:
            out_queue.put(EOF_SENTINEL)
    return run


def make_stderr_reader(stream, ring):
    def run():
        try:
            for raw_line in iter(stream.readline, b""):
                line = raw_line.decode("utf-8", errors="replace")
                if line.endswith("\n"):
                    line = line[:-1]
                if line.endswith("\r"):
                    line = line[:-1]
                line = strip_ansi(line)
                if not line:
                    continue
                ring.add(line + "\n")
                eprint(line)
        except (OSError, ValueError):
            pass
    return run


def main():
    args = parse_args(sys.argv[1:])
    args = validate_args(args)
    prompt = read_prompt()

    # Файл событий: дозапись, создаётся, если нет; существующее не трогается.
    events_path = args.events
    events_file = open(events_path, "a", encoding="utf-8")

    def write_event(obj):
        try:
            events_file.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
            events_file.write("\n")
            events_file.flush()
        except (OSError, ValueError):
            pass

    argv = build_argv(args)

    write_event({
        "type": "pi_rpc_start",
        "provider": args.provider,
        "model": args.model,
        "thinking": args.thinking,
        "cwd": args.cwd,
        "argv": argv,
    })

    start_time = time.monotonic()
    state = RunState()
    ring = RingBuffer(500)

    proc = subprocess.Popen(
        argv,
        cwd=args.cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        bufsize=0,
    )

    out_q = queue.Queue()

    # Чтение stdout и stderr запускается ДО первой записи в stdin.
    stdout_thread = threading.Thread(target=make_stdout_reader(proc.stdout, out_q), daemon=True)
    stderr_thread = threading.Thread(target=make_stderr_reader(proc.stderr, ring), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    stop_event = threading.Event()
    signal_exit_code = [None]

    def signal_handler(code):
        def handler(signum, frame):
            signal_exit_code[0] = code
            stop_event.set()
        return handler

    signal.signal(signal.SIGTERM, signal_handler(143))
    signal.signal(signal.SIGHUP, signal_handler(143))
    signal.signal(signal.SIGINT, signal_handler(130))

    def close_stdin_quiet():
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except (OSError, ValueError):
            pass

    def safe_send(cmd):
        line = json.dumps(cmd, ensure_ascii=False) + "\n"
        try:
            proc.stdin.write(line.encode("utf-8"))
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            close_stdin_quiet()
            return False

    def kill_group(first_sig, wait_first, second_sig=signal.SIGKILL, wait_second=5):
        try:
            os.killpg(proc.pid, first_sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=wait_first)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, second_sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=wait_second)
        except subprocess.TimeoutExpired:
            pass

    def join_and_close():
        close_stdin_quiet()
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        for f in (proc.stdout, proc.stderr):
            try:
                if f and not f.closed:
                    f.close()
            except (OSError, ValueError):
                pass

    def sanitize(v):
        if v is None:
            return ""
        return str(v).replace("\n", " ").replace("\r", " ")

    def write_meta(exit_code, stop_reason, error_message, elapsed):
        if not args.meta:
            return
        err = sanitize(error_message)[:500]
        lines = [
            "pi_session_file=%s" % sanitize(state.session_file),
            "pi_session_id=%s" % sanitize(state.session_id),
            "pi_session_name=%s" % sanitize(state.session_name),
            "provider=%s" % sanitize(state.model_provider),
            "model=%s" % sanitize(state.model_id),
            "thinking=%s" % sanitize(state.thinking_level),
            "stop_reason=%s" % sanitize(stop_reason),
            "error_message=%s" % err,
            "exit=%s" % exit_code,
            "elapsed_sec=%.1f" % elapsed,
        ]
        try:
            with open(args.meta, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass

    def emit_stdout(text):
        if not text:
            return
        if not text.endswith("\n"):
            text = text + "\n"
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (OSError, ValueError):
            pass

    def finish(exit_code, stdout_text=None, stop_reason=None, error_message=None):
        if stdout_text:
            emit_stdout(stdout_text)
        elapsed = time.monotonic() - start_time
        write_event({
            "type": "pi_rpc_end",
            "exit": exit_code,
            "stop_reason": stop_reason,
            "elapsed_sec": round(elapsed, 1),
        })
        write_meta(exit_code, stop_reason, error_message, elapsed)
        try:
            events_file.close()
        except OSError:
            pass
        join_and_close()
        sys.exit(exit_code)

    def handle_pi_died():
        rc = proc.wait()
        eprint("error: pi exited before the run finished (code %d)" % rc)
        buf = ring.dump()
        if buf:
            eprint(buf)
        finish(6, stop_reason="premature_exit")

    # Первые команды: get_state, затем prompt.
    if not safe_send({"id": "state", "type": "get_state"}):
        handle_pi_died()
        return
    if not safe_send({"id": "prompt", "type": "prompt", "message": prompt}):
        handle_pi_died()
        return

    deadline = None if args.timeout == 0 else start_time + args.timeout
    poll_interval = 0.5

    while True:
        if stop_event.is_set():
            code = signal_exit_code[0]
            kill_group(signal.SIGTERM, 3)
            finish(code, stop_reason="terminated" if code == 143 else "interrupted")
            return

        now = time.monotonic()
        if deadline is not None and now >= deadline:
            # Таймаут: отправить abort (ошибку записи проглотить), подождать
            # до 5с, убить группу процессов, код 124.
            try:
                safe_send({"type": "abort"})
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            eprint("error: timed out after %ss - no answer from pi" % args.timeout)
            # Что успело прийти: текст message_end этого прогона, иначе накопленные
            # дельты текущего сообщения, иначе ничего.
            partial = state.last_message_text if state.had_assistant_message else ""
            if not partial and state.delta_buffer:
                partial = "".join(
                    state.delta_buffer[k] for k in sorted(state.delta_buffer.keys())
                )
            finish(124, stdout_text=partial, stop_reason="timeout")
            return

        wait_for = poll_interval
        if deadline is not None:
            remaining = deadline - now
            wait_for = max(0.0, min(poll_interval, remaining))

        try:
            item = out_q.get(timeout=wait_for)
        except queue.Empty:
            continue

        if item is EOF_SENTINEL:
            handle_pi_died()
            return

        ev = item
        etype = ev.get("type")

        if etype == "extension_ui_request":
            method = ev.get("method")
            if method in ("select", "confirm", "input", "editor"):
                ok = safe_send({
                    "type": "extension_ui_response",
                    "id": ev.get("id"),
                    "cancelled": True,
                })
                if not ok:
                    handle_pi_died()
                    return
            continue

        if etype == "response":
            rid = ev.get("id")
            if rid == "state":
                data = ev.get("data") or {}
                state.session_file = data.get("sessionFile")
                state.session_id = data.get("sessionId")
                state.session_name = data.get("sessionName")
                model = data.get("model") or {}
                state.model_provider = model.get("provider")
                state.model_id = model.get("id")
                state.thinking_level = data.get("thinkingLevel")
            elif rid == "prompt":
                if ev.get("success") is False:
                    eprint("error: pi rejected the prompt: %s" % ev.get("error"))
                    finish(6, stop_reason="rejected", error_message=ev.get("error"))
                    return
            elif rid == "last":
                data = ev.get("data") or {}
                last_text = data.get("text")

                # Закрыть stdin, дождаться выхода (до 10с, иначе убить группу).
                close_stdin_quiet()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass

                if not state.had_assistant_message:
                    eprint("error: empty answer - pi produced no assistant message in this run")
                    finish(6, stop_reason=None, error_message=None)
                    return

                if state.last_stop_reason in ("error", "aborted"):
                    eprint("error: pi: %s" % (state.last_error_message or "run aborted"))
                    finish(
                        6,
                        stdout_text=(last_text or state.last_message_text or ""),
                        stop_reason=state.last_stop_reason,
                        error_message=state.last_error_message,
                    )
                    return

                answer = last_text if last_text else state.last_message_text
                if not answer:
                    eprint("error: empty answer")
                    finish(6, stop_reason=state.last_stop_reason, error_message=state.last_error_message)
                    return

                finish(0, stdout_text=answer, stop_reason=state.last_stop_reason, error_message=state.last_error_message)
                return
            continue

        # Внутренняя обработка состояния для остальных типов событий.
        if etype == "message_start":
            msg = ev.get("message") or {}
            if msg.get("role") == "assistant":
                state.delta_buffer = {}
        elif etype == "message_update":
            ame = ev.get("assistantMessageEvent") or {}
            if ame.get("type") == "text_delta":
                idx = ame.get("contentIndex")
                state.delta_buffer[idx] = state.delta_buffer.get(idx, "") + (ame.get("delta") or "")
        elif etype == "message_end":
            msg = ev.get("message") or {}
            if msg.get("role") == "assistant":
                state.had_assistant_message = True
                state.last_stop_reason = msg.get("stopReason")
                state.last_error_message = msg.get("errorMessage")
                content = msg.get("content") or []
                parts = [
                    c.get("text", "")
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                ]
                state.last_message_text = "".join(parts)

        if etype not in ("response", "extension_ui_request", "message_update", "tool_execution_update"):
            write_event(ev)

        if etype == "agent_settled" and not state.sent_last_request:
            state.sent_last_request = True
            if not safe_send({"id": "last", "type": "get_last_assistant_text"}):
                handle_pi_died()
                return


if __name__ == "__main__":
    main()
