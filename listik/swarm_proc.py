"""Рой как дочерний процесс сервера.

`listik serve` держит один `bin/listik-swarm` без `--project`: тот сам обходит
проекты, у которых есть работа, и спит 30 секунд между кругами. Флаг —
`[swarm] enabled` в config.toml. Сервер перечитывает его и поднимает процесс
заново, если тот умер. Занятый `swarm.pid` даёт код 2: сервер пишет об этом
один раз и пробует снова через полминуты, карточки второй раз не запускает.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config as config_mod
from . import paths

#: Пауза между тиками, её же рой принимает флагом `--interval`.
TICK_SECONDS = 30
#: Как часто перечитывать `[swarm] enabled`, пока процесс жив.
POLL_SECONDS = 5.0
#: После падения — прежде чем поднять снова.
RESTART_SECONDS = 2.0
#: Код 2 — рой уже занят другим процессом; node нет — то же ожидание.
BUSY_SECONDS = 30.0

_current: Supervisor | None = None


def build_argv(host: str, port: int) -> list[str] | None:
    """Аргументы роя на все проекты. Нет `node` или скрипта — None."""
    node = shutil.which("node")
    script = paths.ROOT_DIR / "bin" / "listik-swarm"
    listik_bin = paths.ROOT_DIR / "bin" / "listik"
    if not node or not script.is_file() or not listik_bin.is_file():
        return None
    return [
        node, str(script),
        "--listik", str(listik_bin),
        "--listik-host", host,
        "--listik-port", str(port),
        "--log-dir", str(paths.LOGS_DIR),
        "--interval", str(TICK_SECONDS),
    ]


def dispatcher_pid(log_dir: Path | None = None) -> int | None:
    """Pid живого роя по `swarm.pid` в каталоге логов. Мёртвый pid — None."""
    path = Path(log_dir or paths.LOGS_DIR) / "swarm.pid"
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def stop_current() -> None:
    """Остановить рой, который поднял этот процесс."""
    sup = _current
    if sup is not None:
        sup.stop()


def runtime() -> dict:
    """Состояние для `/api/health` и `listik status`.

    Пока в этом процессе есть супервизор, pid — только его ребёнок. Чужой
    `swarm.pid` сюда не подставляется: иначе ожидание занятого лока выглядело
    бы как «наш рой работает». Без супервизора (команда `status` с другой
    стороны) живой pid из файла — это и есть запущенный рой.
    """
    try:
        enabled = config_mod.swarm_enabled()
    except ValueError:
        enabled = False
    sup = _current
    if sup is not None:
        running = sup.running()
        return {"enabled": enabled, "running": running, "pid": sup.pid() if running else None}
    pid = dispatcher_pid() if enabled else None
    return {"enabled": enabled, "running": pid is not None, "pid": pid}


def _log(text: str) -> None:
    print(f"рой: {text}", flush=True)


def _terminate(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


class Supervisor:
    """Поток сервера: по флагу конфига держит один процесс роя."""

    def __init__(self, host: str, port: int, *, popen=None, argv=None,
                 poll_seconds: float = POLL_SECONDS,
                 restart_seconds: float = RESTART_SECONDS,
                 busy_seconds: float = BUSY_SECONDS, log=None):
        self.host = host
        self.port = int(port)
        self._popen = popen or subprocess.Popen
        self._argv_fn = argv if argv is not None else (lambda: build_argv(self.host, self.port))
        self._poll = poll_seconds
        self._restart = restart_seconds
        self._busy = busy_seconds
        self._log = log or _log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        global _current
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="listik-swarm", daemon=True)
        self._thread.start()
        _current = self

    def stop(self) -> None:
        global _current
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._poll + self._restart + 2)
        proc = self._alive()
        if proc is not None:
            _terminate(proc)
            with self._lock:
                if self._proc is proc:
                    self._proc = None
        if _current is self:
            _current = None

    def running(self) -> bool:
        return self._alive() is not None

    def pid(self) -> int | None:
        proc = self._alive()
        if proc is None:
            return None
        return proc.pid

    def _alive(self) -> subprocess.Popen | None:
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return None
        return proc

    def _wait(self, proc: subprocess.Popen, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self._stop.is_set():
            if proc.poll() is not None:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._stop.wait(min(0.5, remaining))

    def _loop(self) -> None:
        missing_logged = False
        busy_logged = False
        while not self._stop.is_set():
            try:
                want = config_mod.swarm_enabled()
            except ValueError as exc:
                self._log(str(exc))
                want = False
            proc = self._alive()
            if not want:
                if proc is not None:
                    self._log("выключен в config.toml — останавливаю")
                    _terminate(proc)
                    with self._lock:
                        if self._proc is proc:
                            self._proc = None
                missing_logged = False
                busy_logged = False
                self._stop.wait(self._poll)
                continue
            if proc is None:
                argv = self._argv_fn()
                if not argv:
                    if not missing_logged:
                        self._log("включён, но нет node или bin/listik-swarm")
                        missing_logged = True
                    self._stop.wait(self._busy)
                    continue
                missing_logged = False
                try:
                    proc = self._popen(
                        argv, cwd=str(paths.ROOT_DIR), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except OSError as exc:
                    self._log(f"не запустился: {exc}")
                    self._stop.wait(self._restart)
                    continue
                with self._lock:
                    self._proc = proc
                # Код 2 приходит сразу: лок занят, процесс даже не начал тик.
                # «запущен» здесь был бы ложью и повторялся бы каждые полминуты.
                if proc.poll() is None:
                    busy_logged = False
                    self._log(f"запущен (pid {proc.pid})")
            self._wait(proc, self._poll)
            if self._stop.is_set():
                break
            if proc.poll() is not None:
                code = proc.returncode
                with self._lock:
                    if self._proc is proc:
                        self._proc = None
                if code == 2:
                    if not busy_logged:
                        self._log("уже запущен другим процессом, подожду")
                        busy_logged = True
                    self._stop.wait(self._busy)
                else:
                    busy_logged = False
                    self._log(f"завершился с кодом {code}, перезапуск")
                    self._stop.wait(self._restart)
        proc = self._alive()
        if proc is not None:
            _terminate(proc)
            with self._lock:
                if self._proc is proc:
                    self._proc = None
