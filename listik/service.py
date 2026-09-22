"""Автозапуск сервера Listik: `listik service install|uninstall|status`.

Платформа выбирается по `sys.platform`: `darwin` — launchd (`~/Library/LaunchAgents`),
`linux` — systemd `--user` (`~/.config/systemd/user`). Всё остальное —
`errors.ListikError(code=UNSUPPORTED)`.

Все вызовы `launchctl`/`systemctl` идут через единственную функцию `run()`: тесты
подменяют её (и `sys.platform`), поэтому настоящие системные команды в юнит-тестах
никогда не выполняются. Юнит запускает `<bin> serve --quiet` с `LISTIK_HOME=<DATA_DIR>`
и PATH для харнессов, пишет лог в `<LOGS_DIR>/service.log`.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from . import errors, paths

LABEL = "listik.server"
LEGACY_LABEL = "dev.listik.server"
SYSTEMD_UNIT_NAME = "listik.service"


def run(argv: list[str]) -> tuple[int | None, str]:
    """Выполнить `launchctl`/`systemctl`: (код, stdout+stderr).

    Код — `None`, если бинарь запустить не удалось вовсе (его нет в PATH и т.п.),
    а не потому, что команда сама вернула ненулевой код. Тесты подменяют эту
    функцию целиком, поэтому реальные `launchctl`/`systemctl` в тестах не зовутся.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except OSError as exc:
        return None, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def platform_kind() -> str:
    """`"launchd"` на macOS, `"systemd"` на Linux — иначе UNSUPPORTED."""
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux"):
        return "systemd"
    raise errors.ListikError(
        "автозапуск поддерживается на macOS и Linux",
        code=errors.UNSUPPORTED,
    )


def unit_path(plat: str | None = None) -> Path:
    plat = plat or platform_kind()
    if plat == "launchd":
        return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    return Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME


def _legacy_unit_path(plat: str | None = None) -> Path | None:
    """Путь к launchd-плисту до переименования метки службы."""
    plat = plat or platform_kind()
    if plat != "launchd":
        return None
    return Path.home() / "Library" / "LaunchAgents" / f"{LEGACY_LABEL}.plist"


def log_path() -> Path:
    return paths.LOGS_DIR / "service.log"


def _service_path() -> str:
    """PATH для юнита: окружение установки плюс стандартные CLI-каталоги.

    launchd и systemd не читают интерактивный shell-профиль, поэтому одного
    PATH процесса, который установил сервис, может быть недостаточно после
    перезапуска. Каталоги добавляются даже до их создания: это позволяет
    установить сервис до установки конкретного харнесса.
    """
    candidates = [
        *(os.environ.get("PATH") or os.defpath).split(os.pathsep),
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / "bin"),
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/local/sbin",
        "/home/linuxbrew/.linuxbrew/bin",
        "/home/linuxbrew/.linuxbrew/sbin",
    ]
    seen: set[str] = set()
    entries: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            entries.append(item)
    return os.pathsep.join(entries)


def _systemd_environment(name: str, value: str) -> str:
    """Строка `Environment=` с корректным quoting для путей с пробелами."""
    if any(char.isspace() or char in '\\"' for char in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'Environment="{name}={escaped}"\n'
    return f"Environment={name}={value}\n"


def resolve_bin(bin_arg: str | None) -> Path:
    """Какой бинарь пишется в юнит: `--bin`, иначе `LISTIK_WRAPPER`, иначе `ROOT_DIR/bin/listik`."""
    if bin_arg:
        resolved = Path(bin_arg).expanduser().resolve()
        if not resolved.is_file():
            raise errors.ListikError(
                f"файла нет: {resolved}",
                code=errors.BAD_ARGUMENT,
                hint="укажите существующий бинарь: --bin <путь>",
            )
        return resolved
    wrapper = (os.environ.get("LISTIK_WRAPPER") or "").strip()
    if wrapper and Path(wrapper).is_file():
        return Path(wrapper)
    return paths.ROOT_DIR / "bin" / "listik"


def _launchd_target(label: str = LABEL) -> str:
    return f"gui/{os.getuid()}/{label}"


def _wait_launchd_gone(target: str, timeout: float = 10.0) -> bool:
    """Дождаться, пока `bootout` уберёт сервис из домена.

    `launchctl bootout` возвращается сразу, а сервис с KeepAlive ещё ~0.1 с
    снимается; `bootstrap` в это окно отвечает кодом 5 (EIO). Опрашиваем
    `launchctl print`, пока сервис не перестанет находиться. True — сервис
    был в домене (и исчез или не дождались); False — его там сразу не было.
    """
    deadline = time.monotonic() + timeout
    seen = False
    while time.monotonic() < deadline:
        code, _ = run(["launchctl", "print", target])
        if code != 0:
            return seen
        seen = True
        time.sleep(0.05)
    return seen


def _remove_legacy_unit(*, unload: bool) -> bool:
    """Выгрузить и удалить старый launchd-плист, если он остался."""
    path = _legacy_unit_path("launchd")
    if path is None or not path.is_file():
        return False
    if unload:
        run(["launchctl", "bootout", _launchd_target(LEGACY_LABEL)])  # ошибка игнорируется
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return True


def is_loaded(plat: str | None = None) -> bool | None:
    """Загружен ли юнит: True/False по коду возврата, None — раннер не смог выполниться."""
    plat = plat or platform_kind()
    if plat == "launchd":
        code, _ = run(["launchctl", "print", _launchd_target()])
    else:
        code, _ = run(["systemctl", "--user", "is-active", SYSTEMD_UNIT_NAME])
    if code is None:
        return None
    return code == 0


def _unit_text(plat: str, bin_path: Path) -> str:
    log = log_path()
    return (
        "[Unit]\n"
        "Description=Listik server\n"
        "\n"
        "[Service]\n"
        f"ExecStart={bin_path} serve --quiet\n"
        f"Environment=LISTIK_HOME={paths.DATA_DIR}\n"
        f"{_systemd_environment('PATH', _service_path())}"
        "Restart=on-failure\n"
        f"StandardOutput=append:{log}\n"
        f"StandardError=append:{log}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    ) if plat == "systemd" else ""


def write_unit(plat: str, bin_path: Path) -> Path:
    """Записать файл юнита (перезапись идемпотентна)."""
    path = unit_path(plat)
    path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path()
    if plat == "launchd":
        data = {
            "Label": LABEL,
            "ProgramArguments": [str(bin_path), "serve", "--quiet"],
            "EnvironmentVariables": {
                "LISTIK_HOME": str(paths.DATA_DIR),
                "PATH": _service_path(),
            },
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }
        with path.open("wb") as fh:
            plistlib.dump(data, fh)
    else:
        path.write_text(_unit_text(plat, bin_path), encoding="utf-8")
    return path


def bin_from_unit(path: Path, plat: str) -> str | None:
    """Первый аргумент запуска из уже записанного файла юнита (для `status`)."""
    if plat == "launchd":
        try:
            with path.open("rb") as fh:
                data = plistlib.load(fh)
        except (OSError, ValueError, plistlib.InvalidFileException):
            return None
        args = data.get("ProgramArguments") or []
        return str(args[0]) if args else None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ExecStart="):
            parts = line[len("ExecStart="):].strip().split()
            return parts[0] if parts else None
    return None


def _port_state() -> tuple[int, int | None, tuple[int, str] | None]:
    """(порт из конфига, pid живого сервера из pid-файла, кто слушает порт по lsof)."""
    from . import config as config_mod
    from . import server as server_mod

    cfg = config_mod.load()
    port = int(cfg["server"]["port"])
    return port, server_mod.read_pid(), server_mod.port_holder(port)


def _foreign_server_pid(state: tuple[int, int | None, tuple[int, str] | None]) -> int | None:
    """Живой `listik serve`, не поднятый этим сервисом: pid-файл или занятый порт."""
    from . import server as server_mod

    _port, pid, holder = state
    if pid:
        return pid
    if holder and server_mod.is_listik_serve(holder[1]):
        return holder[0]
    return None


def _port_intruder(state: tuple[int, int | None, tuple[int, str] | None]) \
        -> tuple[int, int, str] | None:
    """Порт Listik занят процессом, который не `listik serve`: (порт, pid, команда).

    Это единственный случай, который остаётся отказом и при `--stop`: свой сервер
    установщик вправе снять, чужой процесс — не вправе ни снять, ни занять его порт.
    """
    from . import server as server_mod

    port, pid, holder = state
    if not holder or server_mod.is_listik_serve(holder[1]):
        return None
    if pid and holder[0] == pid:
        return None
    return port, holder[0], holder[1]


def _raise_runner_failed(step: str, code: int | None, out: str) -> None:
    raise errors.ListikError(
        f"{step} упал (код {code}): {out.strip()}",
        code=errors.CONFLICT,
    )


def _stop_foreign_server(pid: int, timeout: float = 10.0) -> None:
    """SIGTERM уже запущенному вручную `listik serve` и ожидание его смерти."""
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise errors.ListikError(
            f"не удалось остановить сервер Listik (pid {pid}): {exc}",
            code=errors.CONFLICT,
            hint="listik stop",
        ) from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except OSError:
            return
        time.sleep(0.2)
    raise errors.ListikError(
        f"сервер Listik (pid {pid}) не остановился за {int(timeout)} с",
        code=errors.CONFLICT,
        hint="listik stop",
    )


def install(bin_arg: str | None, no_load: bool, stop: bool = False) -> dict:
    """`listik service install`: см. требования порции — пункты 1–2 перед записью юнита.

    С `--no-load` раннер (`launchctl`/`systemctl`) не вызывается вообще, поэтому проверка
    «юнит уже загружен» (которая сама требует раннера) пропускается — остаётся только
    проверка чужого процесса через `server.read_pid`/`server.port_holder`.

    С `stop=True` (`--stop`, им пользуется `install.sh`) запущенный вручную сервер не повод
    отказать: его останавливают и ставят сервис поверх — обновление рабочей установки идёт
    при живом `listik serve --daemon`, и это обычный случай, а не конфликт. Отказ остаётся
    только за действительно чужим процессом на порту (`_port_intruder`) — его не снимает
    и `--stop`.
    """
    plat = platform_kind()
    loaded = False if no_load else is_loaded(plat)
    if plat == "launchd":
        _remove_legacy_unit(unload=not no_load)
    stopped_pid = None
    if not loaded:
        state = _port_state()
        intruder = _port_intruder(state)
        if intruder:
            i_port, i_pid, i_cmd = intruder
            raise errors.ListikError(
                f"порт {i_port} занят чужим процессом (pid {i_pid}): {i_cmd}",
                code=errors.CONFLICT,
                hint=f"освободи порт {i_port} или смени его в config.toml ([server].port)",
            )
        pid = _foreign_server_pid(state)
        if pid and stop:
            _stop_foreign_server(pid)
            stopped_pid = pid
        elif pid:
            raise errors.ListikError(
                f"сервер Listik уже запущен отдельно от сервиса (pid {pid})",
                code=errors.CONFLICT,
                hint="listik service install --stop (или listik stop)",
            )
    bin_path = resolve_bin(bin_arg)
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = write_unit(plat, bin_path)
    if not no_load:
        if plat == "launchd":
            run(["launchctl", "bootout", _launchd_target()])  # ошибка игнорируется
            code, out = run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
            if code != 0 and _wait_launchd_gone(_launchd_target()):
                # bootstrap попал в окно асинхронного bootout (код 5, EIO) —
                # сервис уже ушёл из домена, повторяем.
                code, out = run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
            if code != 0:
                _raise_runner_failed("launchctl bootstrap", code, out)
        else:
            code, out = run(["systemctl", "--user", "daemon-reload"])
            if code != 0:
                _raise_runner_failed("systemctl daemon-reload", code, out)
            code, out = run(["systemctl", "--user", "enable", SYSTEMD_UNIT_NAME])
            if code != 0:
                _raise_runner_failed("systemctl enable", code, out)
            code, out = run(["systemctl", "--user", "restart", SYSTEMD_UNIT_NAME])
            if code != 0:
                _raise_runner_failed("systemctl restart", code, out)
    return {"platform": plat, "unit_path": str(path), "bin": str(bin_path),
            "loaded_before": bool(loaded), "stopped_pid": stopped_pid}


def _manual_unload_hint(plat: str) -> str:
    if plat == "launchd":
        return f"launchctl bootout {_launchd_target()}"
    return f"systemctl --user disable --now {SYSTEMD_UNIT_NAME}"


def uninstall(no_load: bool) -> dict:
    """`listik service uninstall`: данные (`DATA_DIR`) не трогает."""
    plat = platform_kind()
    path = unit_path(plat)
    legacy_path = _legacy_unit_path(plat)
    existed = path.is_file() or bool(legacy_path and legacy_path.is_file())
    warning = None
    if no_load:
        if plat == "launchd":
            _remove_legacy_unit(unload=False)
        warning = ("загруженный сервис продолжит работать до ручной выгрузки: "
                   + _manual_unload_hint(plat))
    else:
        if plat == "launchd":
            _remove_legacy_unit(unload=True)
            run(["launchctl", "bootout", _launchd_target()])  # ошибка игнорируется
        else:
            run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME])
    if existed:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return {"platform": plat, "unit_path": str(path), "existed": existed, "warning": warning}


def status() -> dict:
    """Состояние сервиса — то же, что печатает `listik service status --json`."""
    plat = platform_kind()
    if plat == "launchd":
        _remove_legacy_unit(unload=True)
    path = unit_path(plat)
    installed = path.is_file()
    loaded = is_loaded(plat)
    bin_value = bin_from_unit(path, plat) if installed else None
    return {
        "platform": plat,
        "installed": installed,
        "unit_path": str(path),
        "loaded": loaded,
        "bin": bin_value,
        "data_dir": str(paths.DATA_DIR),
    }
