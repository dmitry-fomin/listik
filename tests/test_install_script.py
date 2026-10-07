"""Шаг 12, порция c: `install.sh` (пункты 1–10 и 15).
Шаг 12, порция d (пункт 14): вопросы `--service`/`--mcp`/`--plugins`.

Сети наружу нет: архивы собираются во временном каталоге вручную (`tarfile`) в формате
порции b, а «сеть» изображает `http.server` из stdlib на `127.0.0.1:0`. Установка идёт
только во временные `HOME` и `LISTIK_BIN_DIR`; `LISTIK_HOME` не задан,
поэтому работает значение по умолчанию `$HOME/.listik`.

Скрипт ставит обёртку, которая запускает установленный код тем `python3`, который нашёлся
в `PATH`, — поэтому в сценариях с поддельным старым `python3` вокруг ничего не создаётся.

Пункт 14 гоняет настоящий `install.sh` в подпроцессе — поэтому `listik.service.run` в нём
не подменить, как в `tests/test_service.py`. Вместо этого поддельные `launchctl`,
`systemctl` и `claude` кладутся первыми в `PATH` (см. `make_fake_tools`): они пишут свой
argv в общий лог-файл и выходят с кодом, который задаёт сама переменная окружения теста
(`FAKE_LAUNCHCTL_EXIT`/`FAKE_SYSTEMCTL_EXIT`/`FAKE_CLAUDE_EXIT`/`FAKE_CLAUDE_MCP_GET_EXIT`).
Настоящие `launchctl`/`systemctl`/`claude` эти тесты не зовут ни разу: на хосте, где `claude`
уже стоит (сам харнесс), сценарий «нет claude в PATH» вычищает из `PATH` именно тот каталог,
где лежит настоящий `claude`, а не полагается на его отсутствие. У каждого сценария —
свой временный `LISTIK_PORT`: без него `listik service install` в подпроцессе видел бы порт
8787 живого сервера Listik (если он поднят на машине разработчика) как «чужой процесс».
"""
from __future__ import annotations

import errno
import fcntl
import functools
import hashlib
import http.server
import io
import json
import os
import pathlib
import pty
import re
import select
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tempfile
import termios
import threading
import time
import tomllib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
README = REPO_ROOT / "README.md"

#: Версия дерева и вторая версия — прибавлением единицы к патчу (номера не хардкодим).
VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
_major, _minor, _patch = VERSION.split(".")
NEXT_VERSION = f"{_major}.{_minor}.{int(_patch) + 1}"

#: Число маршрутов в routes.json дерева — ожидаемое «ввезено N» при реимпорте.
ROUTES_COUNT = len(json.loads((REPO_ROOT / "routes.json").read_text(encoding="utf-8"))["routes"])

SH = shutil.which("sh")
TAR = shutil.which("tar")

#: `bin/listik` в архиве со «сломанным init»: версия печатается, init выходит с кодом 3.
BROKEN_INIT = """#!/bin/sh
case "${1:-}" in
    --version) echo "listik @VERSION@" ; exit 0 ;;
    init) echo "тест: init сломан" >&2 ; exit 3 ;;
esac
exit 0
"""

#: Поддельные launchctl/systemctl/claude (пункт 14): пишут argv в общий $FAKE_LOG,
#: код выхода берут из своей переменной окружения (по умолчанию — успех).
#: Проверка «юнит уже загружен» (`launchctl print` / `systemctl is-active`) получает свой
#: код выхода: сценарий «сервис ещё не стоит, а сервер поднят вручную» отличается от
#: остальных шагов именно ею, а один общий код завалил бы заодно bootstrap/restart.
FAKE_LAUNCHCTL_SH = """#!/bin/sh
printf 'launchctl %s\\n' "$*" >> "$FAKE_LOG"
if [ "$1" = print ]; then
    exit "${FAKE_LAUNCHCTL_LOADED_EXIT:-${FAKE_LAUNCHCTL_EXIT:-0}}"
fi
exit "${FAKE_LAUNCHCTL_EXIT:-0}"
"""

FAKE_SYSTEMCTL_SH = """#!/bin/sh
printf 'systemctl %s\\n' "$*" >> "$FAKE_LOG"
if [ "$2" = is-active ]; then
    exit "${FAKE_SYSTEMCTL_LOADED_EXIT:-${FAKE_SYSTEMCTL_EXIT:-0}}"
fi
exit "${FAKE_SYSTEMCTL_EXIT:-0}"
"""

#: `claude mcp get` по умолчанию «не найдена» (1) — так `install.sh` не пытается `mcp remove`
#: лишний раз; тест на `mcp get` -> 0 подменяет `FAKE_CLAUDE_MCP_GET_EXIT`.
#: listik-jdo8: `FAKE_CLAUDE_FAIL_PLUGIN=<плагин>@listik` роняет его install/update,
#: `FAKE_CLAUDE_FAIL_MARKETPLACE=1` — `plugin marketplace update`. В /dev/tty не пишет.
FAKE_CLAUDE_SH = """#!/bin/sh
printf 'claude %s\\n' "$*" >> "$FAKE_LOG"
if [ "$1" = mcp ] && [ "$2" = get ]; then
    exit "${FAKE_CLAUDE_MCP_GET_EXIT:-1}"
fi
if [ "$1" = plugin ] && [ "$2" = marketplace ] && [ "$3" = update ] \\
        && [ "${FAKE_CLAUDE_FAIL_MARKETPLACE:-}" = 1 ]; then
    exit 1
fi
if [ "$1" = plugin ] && { [ "$2" = install ] || [ "$2" = update ]; } \\
        && [ -n "${FAKE_CLAUDE_FAIL_PLUGIN:-}" ] && [ "$3" = "$FAKE_CLAUDE_FAIL_PLUGIN" ]; then
    exit 1
fi
exit "${FAKE_CLAUDE_EXIT:-0}"
"""


def free_port() -> int:
    """Свободный порт: без него `listik service install` мог бы решить, что 8787 занят."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def path_without(dirs_containing: str, path: str | None = None) -> str:
    """`PATH` без каталогов, где лежит исполняемый файл `dirs_containing`.

    Нужно сценарию «нет claude в PATH»: на машине разработчика/харнесса `claude` уже
    стоит (это сам агент), поэтому недостаточно понадеяться на его отсутствие — каталог
    с ним нужно явно вырезать из `PATH`.
    """
    kept = []
    for d in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep):
        if not d or os.path.isfile(os.path.join(d, dirs_containing)):
            continue
        kept.append(d)
    return os.pathsep.join(kept)


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Заглушка релиза: отдаёт каталог и молчит в лог теста."""

    def log_message(self, *args) -> None:  # noqa: D102
        pass


@unittest.skipUnless(SH and TAR, "нужны sh и tar")
class InstallScriptTests(unittest.TestCase):
    """Сценарии `install.sh` во временном HOME."""

    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory(prefix="listik-install-test-")
        self.addCleanup(tmpdir.cleanup)
        self.tmp = pathlib.Path(tmpdir.name)
        self.home = self.tmp / "user-home"
        self.home.mkdir()
        self.bin_dir = self.tmp / "bin"
        self.archives = self.tmp / "archives"
        self.archives.mkdir()
        self.installed_home = self.home / ".listik"
        self.app = self.installed_home / "app"
        self.wrapper = self.bin_dir / "listik"
        self._servers: list[http.server.ThreadingHTTPServer] = []
        self.addCleanup(self._stop_servers)

    # --- вспомогательное -------------------------------------------------

    def env(self, **overrides: str) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith("LISTIK_")}
        env["HOME"] = str(self.home)
        # Конфиг Codex установщик ищет в CODEX_HOME: в тестах это только временный HOME,
        # иначе при заданном в окружении CODEX_HOME он смотрел бы в настоящий ~/.codex.
        env["CODEX_HOME"] = str(self.home / ".codex")
        env["LISTIK_BIN_DIR"] = str(self.bin_dir)
        env.update(overrides)
        return env

    def make_fake_tools(self) -> tuple[pathlib.Path, pathlib.Path]:
        """Каталог с поддельными launchctl/systemctl/claude и общий лог их вызовов.

        Кладётся первым в `PATH` (см. пункт 14): настоящие launchctl/systemctl/claude эти
        тесты не зовут — даже если они есть на машине.
        """
        fake_dir = self.tmp / "fake-tools"
        fake_dir.mkdir(exist_ok=True)
        log = self.tmp / "fake-tools.log"
        for name, script in (("launchctl", FAKE_LAUNCHCTL_SH),
                             ("systemctl", FAKE_SYSTEMCTL_SH),
                             ("claude", FAKE_CLAUDE_SH)):
            path = fake_dir / name
            path.write_text(script, encoding="utf-8")
            path.chmod(0o755)
        return fake_dir, log

    @staticmethod
    def _add_bytes(tf: tarfile.TarFile, arcname: str, data: bytes, mode: int = 0o644) -> None:
        info = tarfile.TarInfo(arcname)
        info.size = len(data)
        info.mode = mode
        info.mtime = int(time.time())
        tf.addfile(info, io.BytesIO(data))

    @staticmethod
    def _add_tree(tf: tarfile.TarFile, source: pathlib.Path, arcname: str) -> None:
        def skip(info: tarfile.TarInfo):
            parts = pathlib.PurePosixPath(info.name).parts
            if "__pycache__" in parts or info.name.endswith(".pyc"):
                return None
            return info

        tf.add(str(source), arcname=arcname, filter=skip, recursive=True)

    def make_archive(self, version: str, *, protocol: bytes | None = None,
                     broken_init: bool = False, sha: bool = True,
                     name: str | None = None) -> pathlib.Path:
        """Архив формата порции b: `listik-<ver>/` с кодом, routes.json и протоколом."""
        top = f"listik-{version}"
        archive = self.archives / (name or f"listik-{version}.tar.gz")
        if protocol is None:
            protocol = (REPO_ROOT / "docs" / "harness-protocol.md").read_bytes()
        with tarfile.open(archive, "w:gz") as tf:
            if broken_init:
                script = BROKEN_INIT.replace("@VERSION@", version).encode("utf-8")
                self._add_bytes(tf, f"{top}/bin/listik", script, mode=0o755)
            else:
                self._add_tree(tf, REPO_ROOT / "bin", f"{top}/bin")
                self._add_tree(tf, REPO_ROOT / "listik", f"{top}/listik")
            self._add_bytes(tf, f"{top}/VERSION", (version + "\n").encode("utf-8"))
            self._add_bytes(tf, f"{top}/routes.json", (REPO_ROOT / "routes.json").read_bytes())
            self._add_bytes(tf, f"{top}/docs/harness-protocol.md", protocol)
        if sha:
            digest = sha256_file(archive)
            (archive.parent / (archive.name + ".sha256")).write_text(
                f"{digest}  {archive.name}\n", encoding="utf-8")
        return archive

    def run_install(self, *args: str, env: dict | None = None, stdin=None,
                    session: bool = False, timeout: int = 240) -> subprocess.CompletedProcess:
        return subprocess.run([SH, str(INSTALL_SH), *args], env=env or self.env(),
                              capture_output=True, text=True, timeout=timeout,
                              stdin=stdin, start_new_session=session)

    def run_wrapper(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.wrapper), *args], env=env or self.env(),
                              capture_output=True, text=True, timeout=120)

    def assert_installed(self, version: str) -> None:
        self.assertTrue((self.app / version / "bin" / "listik").is_file(),
                        f"нет кода версии {version}")
        self.assertTrue((self.app / version / "VERSION").is_file())
        self.assertTrue((self.app / "current").is_symlink(), "current не ссылка")
        self.assertEqual(os.readlink(self.app / "current"), version,
                         "ссылка current должна быть относительной")
        self.assertTrue(self.wrapper.is_file(), "обёртка не записана")

    def assert_no_leftovers(self) -> None:
        names = [p.name for p in self.app.iterdir()
                 if p.name.startswith(".old-") or p.name.startswith(".new-")]
        self.assertEqual(names, [], "остались временные каталоги установки")

    def install(self, archive: pathlib.Path, *extra: str, env: dict | None = None):
        result = self.run_install("--archive", str(archive), "--yes",
                                  "--service", "no", "--mcp", "no", "--plugins", "no",
                                  *extra, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    # --- заглушка релиза -------------------------------------------------

    def start_stub(self, version: str, *, sha: bool = True) -> str:
        site = self.tmp / "site"
        (site / f"v{version}").mkdir(parents=True, exist_ok=True)
        (site / "latest").write_text(json.dumps({"tag_name": f"v{version}"}),
                                     encoding="utf-8")
        archive = self.make_archive(version)
        target = site / f"v{version}" / archive.name
        shutil.copy2(archive, target)
        if sha:
            shutil.copy2(archive.parent / (archive.name + ".sha256"),
                         site / f"v{version}" / (archive.name + ".sha256"))
        handler = functools.partial(QuietHandler, directory=str(site))
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self._servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def _stop_servers(self) -> None:
        for srv in self._servers:
            srv.shutdown()
            srv.server_close()

    # --- пункт 15: первая установка --------------------------------------

    def test_first_install_archive(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        self.assert_installed(VERSION)
        self.assert_no_leftovers()
        self.assertTrue((self.installed_home / "listik.db").is_file(), "нет базы данных")

        version_out = self.run_wrapper("--version")
        self.assertEqual(version_out.returncode, 0, version_out.stderr)
        self.assertEqual(version_out.stdout.strip(), f"listik {VERSION}")

        status = self.run_wrapper("--local", "status", "--json")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertEqual(json.loads(status.stdout)["data_dir"], str(self.installed_home))

        wrapper_text = self.wrapper.read_text(encoding="utf-8")
        self.assertIn(f'LISTIK_WRAPPER="{self.wrapper}"', wrapper_text)

    def test_reinstall_same_version_keeps_data(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        created = self.run_wrapper("--local", "new", "проба", "--project", "listik")
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        # `token` создаёт config.toml с токеном — установщик не должен его переписывать.
        token = self.run_wrapper("token")
        self.assertEqual(token.returncode, 0, token.stdout + token.stderr)
        config = self.installed_home / "config.toml"
        self.assertTrue(config.is_file(), "config.toml не создан")
        before = sha256_file(config)

        self.install(archive)

        self.assert_installed(VERSION)
        self.assert_no_leftovers()
        tasks = self.run_wrapper("--local", "list", "--json")
        self.assertEqual(tasks.returncode, 0, tasks.stdout + tasks.stderr)
        self.assertEqual(json.loads(tasks.stdout)["total"], 1, "задача пропала")
        self.assertEqual(sha256_file(config), before, "config.toml перезаписан установкой")

    def test_second_version_keeps_first(self) -> None:
        self.install(self.make_archive(VERSION))
        self.install(self.make_archive(NEXT_VERSION))
        self.assert_installed(NEXT_VERSION)
        self.assert_no_leftovers()
        self.assertTrue((self.app / VERSION / "bin" / "listik").is_file(),
                        "каталог первой версии удалён")
        version_out = self.run_wrapper("--version")
        self.assertEqual(version_out.stdout.strip(), f"listik {NEXT_VERSION}")

    # --- пункт 15: routes.json больше не трогается ------------------------

    def test_routes_flag_is_gone(self) -> None:
        """Маршруты живут только в таблице routes — установщик про них не знает."""
        archive = self.make_archive(VERSION)
        result = self.run_install("--archive", str(archive), "--routes", "keep",
                                  "--service", "no", "--mcp", "no", "--plugins", "no")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("неизвестный флаг: --routes", result.stderr)

    def test_install_does_not_write_routes_copy(self) -> None:
        self.install(self.make_archive(VERSION))
        self.assertFalse((self.home / ".config" / "listik" / "routes.json").exists(),
                         "установщик создал ~/.config/listik/routes.json")

    # --- пункт 15: протокол ----------------------------------------------

    def test_protocol_change_reminds(self) -> None:
        self.install(self.make_archive(VERSION))
        result = self.install(self.make_archive(NEXT_VERSION, protocol=b"# protocol v2\n"))
        self.assertIn("init-projects", result.stdout)

    def test_protocol_unchanged_silent(self) -> None:
        self.install(self.make_archive(VERSION))
        result = self.install(self.make_archive(NEXT_VERSION))
        self.assertNotIn("init-projects", result.stdout)

    def test_first_install_has_no_protocol_reminder(self) -> None:
        result = self.install(self.make_archive(VERSION))
        self.assertNotIn("init-projects", result.stdout)

    # --- пункт 15: ошибки ------------------------------------------------

    def test_bad_checksum_does_not_install(self) -> None:
        archive = self.make_archive(VERSION)
        sum_file = archive.parent / (archive.name + ".sha256")
        sum_file.write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")
        result = self.run_install("--archive", str(archive), "--yes",
                                  "--service", "no", "--mcp", "no", "--plugins", "no")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.installed_home.exists(), "каталог данных создан установкой")
        self.assertFalse(self.wrapper.exists(), "обёртка записана при плохой сумме")

    def test_bad_checksum_keeps_previous_install(self) -> None:
        self.install(self.make_archive(VERSION))
        wrapper_before = sha256_file(self.wrapper)
        archive = self.make_archive(NEXT_VERSION)
        (archive.parent / (archive.name + ".sha256")).write_text(
            f"{'1' * 64}  {archive.name}\n", encoding="utf-8")
        result = self.run_install("--archive", str(archive), "--yes",
                                  "--service", "no", "--mcp", "no", "--plugins", "no")
        self.assertNotEqual(result.returncode, 0)
        self.assert_installed(VERSION)
        self.assertEqual(sha256_file(self.wrapper), wrapper_before)
        self.assertFalse((self.app / NEXT_VERSION).exists())

    def test_broken_init_reports(self) -> None:
        archive = self.make_archive(NEXT_VERSION, broken_init=True)
        result = self.run_install("--archive", str(archive), "--yes",
                                  "--service", "no", "--mcp", "no", "--plugins", "no")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_installed(NEXT_VERSION)
        self.assertIn("listik init", result.stdout + result.stderr)

    def test_old_python3_does_not_touch_home(self) -> None:
        archive = self.make_archive(VERSION)
        fake_bin = self.tmp / "fake-bin"
        fake_bin.mkdir()
        fake_python = fake_bin / "python3"
        fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake_python.chmod(0o755)
        env = self.env(PATH=f"{fake_bin}:{os.environ.get('PATH', '')}")
        result = self.run_install("--archive", str(archive), "--yes",
                                  "--service", "no", "--mcp", "no", "--plugins", "no", env=env)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.installed_home.exists(), "$HOME/.listik создан")

    # --- пункт 15: сетевой путь ------------------------------------------

    def test_network_install_without_version(self) -> None:
        base = self.start_stub(VERSION)
        env = self.env(LISTIK_RELEASES_API=f"{base}/latest", LISTIK_DOWNLOAD_BASE=base)
        result = self.run_install("--yes", "--service", "no", "--mcp", "no", "--plugins", "no",
                                  env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_installed(VERSION)

    def test_network_flag_wins_over_env(self) -> None:
        base = self.start_stub(VERSION)
        env = self.env(LISTIK_RELEASES_API=f"{base}/latest", LISTIK_DOWNLOAD_BASE=base,
                       LISTIK_VERSION="9.9.9")
        result = self.run_install("--version", VERSION, "--yes",
                                  "--service", "no", "--mcp", "no", "--plugins", "no", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_installed(VERSION)
        self.assertFalse((self.app / "9.9.9").exists())

    def test_network_without_sha256_fails(self) -> None:
        base = self.start_stub(VERSION, sha=False)
        env = self.env(LISTIK_RELEASES_API=f"{base}/latest", LISTIK_DOWNLOAD_BASE=base)
        result = self.run_install("--yes", "--service", "no", "--mcp", "no", "--plugins", "no",
                                  env=env)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.installed_home.exists(), "что-то поставлено без суммы")
        self.assertFalse(self.wrapper.exists())

    # --- пункт 1 и 2: stdin-режим и справка ------------------------------

    def test_piped_to_sh(self) -> None:
        archive = self.make_archive(VERSION)
        result = subprocess.run(
            [SH, "-s", "--", "--archive", str(archive), "--yes",
             "--service", "no", "--mcp", "no", "--plugins", "no"],
            input=INSTALL_SH.read_text(encoding="utf-8"), env=self.env(),
            capture_output=True, text=True, timeout=240)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_installed(VERSION)

    def test_help(self) -> None:
        result = self.run_install("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("--version", "--archive", "--swarm", "LISTIK_DOWNLOAD_BASE", "LISTIK_HOME"):
            self.assertIn(text, result.stdout, f"в --help нет {text}")
        # Старый `--routes keep` и `LISTIK_ROUTES` убраны; `--routes-reimport` и
        # `LISTIK_ROUTES_REIMPORT` (listik-ttjm) — другие имена и гардом не ловятся.
        self.assertNotRegex(result.stdout, r"--routes(?![-\w])")
        self.assertNotRegex(result.stdout, r"LISTIK_ROUTES(?!_)")

    # --- шаг 12, порция d, пункт 14: --service/--mcp/--plugins -----------

    def test_yes_runs_service_mcp_plugins(self) -> None:
        fake_dir, log = self.make_fake_tools()
        archive = self.make_archive(VERSION)
        env = self.env(PATH=f"{fake_dir}:{os.environ.get('PATH', '')}",
                       FAKE_LOG=str(log), LISTIK_PORT=str(free_port()))
        result = self.run_install("--archive", str(archive), "--yes", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = log.read_text(encoding="utf-8") if log.exists() else ""
        if sys.platform == "darwin":
            self.assertIn("launchctl print", text)
            self.assertIn("launchctl bootstrap", text)
        else:
            self.assertIn("systemctl --user is-active", text)
            self.assertIn("systemctl --user restart", text)
        self.assertNotIn("mcp add", text)
        self.assertIn("MCP: пропущен", result.stdout)
        self.assertIn("plugin marketplace add dmitry-fomin/listik", text)
        self.assertIn("plugin install listik@listik", text)
        self.assertIn("plugin install feature-pipeline@listik", text)
        self.assertIn("plugin install dsh@listik", text)
        self.assertIn("plugin install codex@listik", text)
        self.assertIn("plugin install second-opinion@listik", text)
        self.assertIn("plugin install claude-codex@listik", text)
        self.assertIn("plugin marketplace update listik", text)
        for name in ("listik", "feature-pipeline", "pi", "devin", "opencode"):
            self.assertIn(f"plugin install {name}@listik", text)
            self.assertIn(f"plugin update {name}@listik", text)

    def test_mcp_get_zero_removes_before_add(self) -> None:
        fake_dir, log = self.make_fake_tools()
        archive = self.make_archive(VERSION)
        env = self.env(PATH=f"{fake_dir}:{os.environ.get('PATH', '')}",
                       FAKE_LOG=str(log), FAKE_CLAUDE_MCP_GET_EXIT="0",
                       LISTIK_PORT=str(free_port()))
        result = self.run_install("--archive", str(archive), "--yes", "--mcp", "yes",
                                  "--service", "no", "--plugins", "no", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = [ln for ln in log.read_text(encoding="utf-8").splitlines()
                if ln.startswith("claude mcp")]
        remove_idx = next(i for i, ln in enumerate(lines) if ln.startswith("claude mcp remove"))
        add_idx = next(i for i, ln in enumerate(lines) if ln.startswith("claude mcp add"))
        self.assertLess(remove_idx, add_idx, lines)

    def test_swarm_yes_writes_enabled_without_model_key(self) -> None:
        archive = self.make_archive(VERSION)
        result = self.install(archive, "--swarm", "yes")
        self.assertIn("рой: ok", result.stdout)
        parsed = tomllib.loads((self.installed_home / "config.toml").read_text(encoding="utf-8"))
        self.assertIs(parsed["swarm"]["enabled"], True)
        self.assertNotIn("api_key", parsed["swarm"])

    def test_swarm_no_writes_disabled(self) -> None:
        archive = self.make_archive(VERSION)
        result = self.install(archive, "--swarm", "no")
        self.assertIn("рой: выключен", result.stdout)
        parsed = tomllib.loads((self.installed_home / "config.toml").read_text(encoding="utf-8"))
        self.assertIs(parsed["swarm"]["enabled"], False)

    def test_all_no_skips_everything(self) -> None:
        fake_dir, log = self.make_fake_tools()
        archive = self.make_archive(VERSION)
        env = self.env(PATH=f"{fake_dir}:{os.environ.get('PATH', '')}",
                       FAKE_LOG=str(log), LISTIK_PORT=str(free_port()))
        result = self.run_install("--archive", str(archive), "--yes",
                                  "--service", "no", "--mcp", "no", "--plugins", "no", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(log.exists(), "раннер вызван, хотя все три шага пропущены")
        self.assertIn("автозапуск: пропущен", result.stdout)
        self.assertIn("MCP: пропущен", result.stdout)
        self.assertIn("плагины: пропущен", result.stdout)

    def test_no_claude_in_path(self) -> None:
        archive = self.make_archive(VERSION)
        env = self.env(PATH=path_without("claude"), LISTIK_PORT=str(free_port()))
        result = self.run_install("--archive", str(archive), "--yes", "--mcp", "yes",
                                  "--service", "no", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("claude mcp add", result.stdout)
        self.assertIn("/plugin install", result.stdout)
        self.assertIn("MCP: не удалось", result.stdout)
        self.assertIn("плагины: не удалось", result.stdout)

    def test_claude_fails_reports_but_continues(self) -> None:
        fake_dir, log = self.make_fake_tools()
        archive = self.make_archive(VERSION)
        env = self.env(PATH=f"{fake_dir}:{os.environ.get('PATH', '')}",
                       FAKE_LOG=str(log), FAKE_CLAUDE_EXIT="1",
                       LISTIK_PORT=str(free_port()))
        result = self.run_install("--archive", str(archive), "--yes", "--mcp", "yes", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stderr.strip(), "ожидалось предупреждение в stderr")
        self.assertIn("MCP: не удалось", result.stdout)
        self.assertIn("плагины: не удалось", result.stdout)
        self.assert_installed(VERSION)
        version_out = self.run_wrapper("--version", env=env)
        self.assertEqual(version_out.returncode, 0, version_out.stderr)
        self.assertEqual(version_out.stdout.strip(), f"listik {VERSION}")

    def test_reinstall_with_loaded_unit_reloads(self) -> None:
        fake_dir, log = self.make_fake_tools()
        env = self.env(PATH=f"{fake_dir}:{os.environ.get('PATH', '')}",
                       FAKE_LOG=str(log), FAKE_LAUNCHCTL_EXIT="0", FAKE_SYSTEMCTL_EXIT="0",
                       LISTIK_PORT=str(free_port()))
        first = self.make_archive(VERSION)
        result = self.run_install("--archive", str(first), "--yes",
                                  "--mcp", "no", "--plugins", "no", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        log.write_text("", encoding="utf-8")  # чистим лог первой установки

        second = self.make_archive(NEXT_VERSION)
        result = self.run_install("--archive", str(second), "--yes",
                                  "--mcp", "no", "--plugins", "no", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = log.read_text(encoding="utf-8")
        self.assertTrue("bootstrap" in text or "restart" in text, text)

    def test_reinstall_stops_manual_server_and_reports(self) -> None:
        """Обновление при поднятом вручную сервере: автозапуск встаёт, отчёт говорит о снятии.

        Сервер здесь настоящий (`listik serve --daemon` из установленной обёртки) — именно
        он раньше ломал шаг автозапуска; `launchctl`/`systemctl` по-прежнему поддельные.
        """
        fake_dir, log = self.make_fake_tools()
        env = self.env(PATH=f"{fake_dir}:{os.environ.get('PATH', '')}",
                       FAKE_LOG=str(log), FAKE_LAUNCHCTL_EXIT="0", FAKE_SYSTEMCTL_EXIT="0",
                       FAKE_LAUNCHCTL_LOADED_EXIT="1", FAKE_SYSTEMCTL_LOADED_EXIT="1",
                       LISTIK_PORT=str(free_port()))
        self.install(self.make_archive(VERSION), env=env)

        started = self.run_wrapper("serve", "--daemon", env=env)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        pid_path = self.installed_home / "listik.pid"
        pid = 0
        for _ in range(100):
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
                break
            except (OSError, ValueError):
                time.sleep(0.1)
        self.assertTrue(pid, "сервер не записал pid-файл")
        self.addCleanup(self._kill, pid)

        second = self.make_archive(NEXT_VERSION)
        result = self.run_install("--archive", str(second), "--yes",
                                  "--mcp", "no", "--plugins", "no", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("автозапуск: ok (ручной сервер остановлен)", result.stdout)
        self.assertFalse(self._alive(pid), "ручной сервер должен быть остановлен")
        self.assertTrue("bootstrap" in log.read_text(encoding="utf-8")
                        or "restart" in log.read_text(encoding="utf-8"),
                        log.read_text(encoding="utf-8"))

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _kill(self, pid: int) -> None:
        try:
            os.kill(pid, 15)
        except OSError:
            pass

    # --- порция a: Codex и network_access в песочнице --------------------

    @property
    def codex_config(self) -> pathlib.Path:
        """Тот же путь, что берёт установщик: `$CODEX_HOME/config.toml` (см. `env`)."""
        return self.home / ".codex" / "config.toml"

    def make_fake_codex(self) -> pathlib.Path:
        """Поддельный `codex` в PATH: установщику достаточно `command -v codex`."""
        fake_dir = self.tmp / "fake-codex"
        fake_dir.mkdir(exist_ok=True)
        path = fake_dir / "codex"
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return fake_dir

    def env_with_codex(self, **overrides: str) -> dict:
        path = os.pathsep.join([str(self.make_fake_codex()), os.environ.get("PATH", "")])
        return self.env(PATH=path, **overrides)

    def path_without_codex(self) -> str:
        """`PATH`, в котором нет исполняемого `codex`, но есть все остальные файлы.

        `path_without` выбросил бы каталог целиком, а `codex` на машине разработчика
        лежит рядом с brew-питоном (`/opt/homebrew/bin`) — вместе с ним из `PATH` ушёл бы
        и python3 3.11+. Поэтому каждый файл из `PATH` получает ссылку в отдельном
        каталоге, кроме самого `codex`.
        """
        farm = self.tmp / "path-without-codex"
        farm.mkdir(exist_ok=True)
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory or not os.path.isdir(directory):
                continue
            try:
                names = os.listdir(directory)
            except OSError:
                continue
            for name in names:
                link = farm / name
                if name == "codex" or link.exists() or link.is_symlink():
                    continue
                try:
                    link.symlink_to(os.path.join(directory, name))
                except OSError:
                    continue
        return str(farm)

    def test_codex_network_already_enabled_keeps_config(self) -> None:
        archive = self.make_archive(VERSION)
        self.codex_config.parent.mkdir(parents=True)
        before = 'model = "gpt-5"\n\n[sandbox_workspace_write]\nnetwork_access = true\n'
        self.codex_config.write_text(before, encoding="utf-8")
        result = self.install(archive, env=self.env_with_codex())
        self.assertEqual(self.codex_config.read_text(encoding="utf-8"), before)
        self.assertEqual(list(self.codex_config.parent.glob("config.toml.bak-*")), [],
                         "настройка уже есть, а копия конфига сделана")
        self.assertIn("Codex: уже включён", result.stdout)
        self.assertNotIn("не сможет", result.stdout + result.stderr)

    def test_codex_network_flag_yes_appends_and_keeps_backup(self) -> None:
        archive = self.make_archive(VERSION)
        self.codex_config.parent.mkdir(parents=True)
        before = 'model = "gpt-5"\n'
        self.codex_config.write_text(before, encoding="utf-8")
        result = self.install(archive, "--codex-network", "yes", env=self.env_with_codex())
        data = tomllib.loads(self.codex_config.read_text(encoding="utf-8"))
        self.assertIs(data["sandbox_workspace_write"]["network_access"], True)
        self.assertEqual(data["model"], "gpt-5", "остальной конфиг потерян")
        backups = list(self.codex_config.parent.glob("config.toml.bak-*"))
        self.assertEqual([b.read_text(encoding="utf-8") for b in backups], [before],
                         "нет резервной копии конфига")
        self.assertIn("Codex: включён", result.stdout)
        self.assertNotIn("не сможет", result.stdout + result.stderr)

    def test_codex_network_creates_config_when_missing(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive, "--codex-network", "yes", env=self.env_with_codex())
        data = tomllib.loads(self.codex_config.read_text(encoding="utf-8"))
        self.assertIs(data["sandbox_workspace_write"]["network_access"], True)

    def test_codex_network_keeps_mode_and_leaves_no_temp(self) -> None:
        archive = self.make_archive(VERSION)
        self.codex_config.parent.mkdir(parents=True)
        self.codex_config.write_text('model = "gpt-5"\n', encoding="utf-8")
        self.codex_config.chmod(0o600)
        self.install(archive, "--codex-network", "yes", env=self.env_with_codex())
        self.assertEqual(self.codex_config.stat().st_mode & 0o777, 0o600,
                         "права конфига не сохранены")
        self.assertEqual(list(self.codex_config.parent.glob("config.toml.listik-new-*")), [],
                         "остался временный файл правки")

    def test_codex_network_replaces_false_in_existing_section(self) -> None:
        archive = self.make_archive(VERSION)
        self.codex_config.parent.mkdir(parents=True)
        self.codex_config.write_text(
            '[sandbox_workspace_write]\nsandbox_mode = "workspace-write"\n'
            'network_access = false\n\n[other]\nkey = 1\n', encoding="utf-8")
        self.install(archive, "--codex-network", "yes", env=self.env_with_codex())
        text = self.codex_config.read_text(encoding="utf-8")
        data = tomllib.loads(text)
        self.assertIs(data["sandbox_workspace_write"]["network_access"], True)
        self.assertEqual(data["sandbox_workspace_write"]["sandbox_mode"], "workspace-write")
        self.assertEqual(data["other"], {"key": 1})
        self.assertEqual(text.count("network_access"), 1, "ключ задвоился — TOML сломан")

    def test_codex_network_adds_key_into_existing_section(self) -> None:
        archive = self.make_archive(VERSION)
        self.codex_config.parent.mkdir(parents=True)
        self.codex_config.write_text(
            '[sandbox_workspace_write]\nsandbox_mode = "workspace-write"\n\n[other]\nkey = 1\n',
            encoding="utf-8")
        self.install(archive, "--codex-network", "yes", env=self.env_with_codex())
        data = tomllib.loads(self.codex_config.read_text(encoding="utf-8"))
        self.assertIs(data["sandbox_workspace_write"]["network_access"], True)
        self.assertEqual(data["other"], {"key": 1}, "ключ ушёл в чужую секцию")

    def test_codex_network_flag_no_warns_and_keeps_config(self) -> None:
        archive = self.make_archive(VERSION)
        result = self.install(archive, "--codex-network", "no", env=self.env_with_codex())
        self.assertFalse(self.codex_config.exists(), "конфиг создан при --codex-network no")
        self.assertIn("127.0.0.1", result.stderr)
        self.assertIn("не сможет", result.stderr)
        self.assertIn("--codex-network yes", result.stderr)

    def test_codex_network_without_tty_keeps_config(self) -> None:
        archive = self.make_archive(VERSION)
        result = self.run_install("--archive", str(archive), "--service", "no", "--mcp", "no",
                                  "--plugins", "no", env=self.env_with_codex(),
                                  stdin=subprocess.DEVNULL, session=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.codex_config.exists(), "конфиг изменён без ответа пользователя")
        self.assertIn("не сможет", result.stderr)

    def test_codex_not_installed_skips(self) -> None:
        archive = self.make_archive(VERSION)
        env = self.env(PATH=self.path_without_codex())
        result = self.install(archive, "--codex-network", "yes", env=env)
        self.assertFalse(self.codex_config.exists(), "конфиг создан без codex в PATH")
        self.assertIn("Codex: пропущен", result.stdout)

    def test_codex_network_flag_invalid(self) -> None:
        result = self.run_install("--codex-network", "maybe", "--yes")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--codex-network", result.stderr)

    def test_codex_network_in_help(self) -> None:
        result = self.run_install("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--codex-network", result.stdout)
        self.assertIn("CODEX_HOME", result.stdout)

    # --- listik-ttjm: --routes-reimport ------------------------------------

    def edit_installed_route(self) -> None:
        import sqlite3
        conn = sqlite3.connect(self.installed_home / "listik.db")
        with conn:
            conn.execute("UPDATE routes SET title = 'Моя правка' WHERE key = 'low-pipeline'")
        conn.close()

    def installed_route_title(self) -> str:
        import sqlite3
        conn = sqlite3.connect(self.installed_home / "listik.db")
        try:
            return conn.execute("SELECT title FROM routes WHERE key = 'low-pipeline'").fetchone()[0]
        finally:
            conn.close()

    def test_routes_reimport_yes_rewrites(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        self.edit_installed_route()
        result = self.install(archive, "--routes-reimport", "yes")
        self.assertEqual(self.installed_route_title(), "low")
        self.assertIn("маршруты: перезаписаны", result.stdout)
        self.assertRegex(result.stdout, rf"ввезено {ROUTES_COUNT}(?!\d)")

    def test_routes_reimport_env_yes(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        self.edit_installed_route()
        self.install(archive, env=self.env(LISTIK_ROUTES_REIMPORT="yes"))
        self.assertEqual(self.installed_route_title(), "low")

    def test_routes_reimport_no_keeps(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        self.edit_installed_route()
        result = self.install(archive, "--routes-reimport", "no")
        self.assertEqual(self.installed_route_title(), "Моя правка")
        self.assertIn("маршруты: не тронуты", result.stdout)

    def test_routes_reimport_ask_without_tty_keeps(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        self.edit_installed_route()
        result = self.run_install("--archive", str(archive), "--service", "no", "--mcp", "no",
                                  "--plugins", "no", "--routes-reimport", "ask",
                                  stdin=subprocess.DEVNULL, session=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.installed_route_title(), "Моя правка")
        self.assertIn("маршруты: не тронуты", result.stdout)

    def test_routes_reimport_first_install_skipped(self) -> None:
        result = self.install(self.make_archive(VERSION), "--routes-reimport", "yes")
        self.assertIn("маршруты: пропущен (первая установка)", result.stdout)

    def test_routes_reimport_flag_invalid(self) -> None:
        result = self.run_install("--routes-reimport", "maybe", "--yes")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--routes-reimport", result.stderr)

    def test_routes_reimport_in_help(self) -> None:
        result = self.run_install("--help")
        self.assertIn("--routes-reimport", result.stdout)
        self.assertIn("LISTIK_ROUTES_REIMPORT", result.stdout)

    # --- listik-jdo8, порция b: плагины по выбору нейронок и харнессов ----

    ALL_PLUGINS = ("listik", "feature-pipeline", "claude-codex", "dsh", "codex", "opencode",
                   "pi", "devin", "second-opinion")
    PROMPT = "Номера через пробел или запятую; Enter — все, 0 — ни одной: "

    def fake_env(self, **overrides: str) -> tuple[dict, pathlib.Path]:
        fake_dir, log = self.make_fake_tools()
        env = self.env(PATH=f"{fake_dir}:{os.environ.get('PATH', '')}",
                       FAKE_LOG=str(log), LISTIK_PORT=str(free_port()), **overrides)
        return env, log

    @staticmethod
    def log_text(log: pathlib.Path) -> str:
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def installed_plugins(self, log: pathlib.Path) -> set[str]:
        text = self.log_text(log)
        return {p for p in self.ALL_PLUGINS if f"plugin install {p}@listik\n" in text}

    def route_visibility(self) -> dict[str, int]:
        conn = sqlite3.connect(self.installed_home / "listik.db")
        try:
            return dict(conn.execute("SELECT key, visible FROM routes").fetchall())
        finally:
            conn.close()

    def install_plugins(self, *extra: str, env: dict) -> subprocess.CompletedProcess:
        result = self.run_install("--archive", str(self.make_archive(VERSION)), "--yes",
                                  "--service", "no", "--mcp", "no", "--swarm", "no",
                                  "--plugins", "yes", *extra, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_lists_select_plugins(self) -> None:
        env, log = self.fake_env()
        result = self.install_plugins("--models", " deepseek , claude,,claude",
                                      "--harnesses=pi,claude,dsh", env=env)
        self.assertEqual(self.installed_plugins(log),
                         {"listik", "feature-pipeline", "dsh", "pi", "second-opinion"})
        self.assertNotIn("uninstall", self.log_text(log))
        self.assertIn("нейронки: claude, deepseek", result.stdout)
        self.assertIn("харнессы: claude, pi, dsh", result.stdout)
        self.assertIn("плагины: ok (listik, feature-pipeline, dsh, pi, second-opinion)",
                      result.stdout)
        self.assertNotIn("скрыты маршруты", result.stdout)
        self.assertNotIn("grok:", result.stdout)
        self.assertTrue(all(self.route_visibility().values()))

    def test_lists_order_of_plugins_and_claude_codex(self) -> None:
        env, log = self.fake_env()
        self.install_plugins("--models", "openai,claude,glm", "--harnesses",
                             "opencode,codex,claude", env=env)
        installs = [ln.split()[3] for ln in self.log_text(log).splitlines()
                    if ln.startswith("claude plugin install ")]
        self.assertEqual(installs, ["listik@listik", "feature-pipeline@listik",
                                    "claude-codex@listik", "codex@listik", "opencode@listik",
                                    "second-opinion@listik"])

    def test_without_claude_model_hides_pipeline_routes(self) -> None:
        env, log = self.fake_env()
        result = self.install_plugins("--models", "openai", "--harnesses", "claude,codex",
                                      env=env)
        self.assertEqual(self.installed_plugins(log), {"listik", "codex", "second-opinion"})
        visibility = self.route_visibility()
        self.assertEqual(len(visibility), ROUTES_COUNT)
        self.assertFalse(any(visibility.values()), visibility)
        self.assertIn("скрыты маршруты: ", result.stdout)
        self.assertIn("low-pipeline", result.stdout)

    def test_without_claude_harness_installs_nothing(self) -> None:
        env, log = self.fake_env()
        result = self.install_plugins("--models", "all", "--harnesses", "codex,grok", env=env)
        self.assertNotIn("claude plugin", self.log_text(log))
        self.assertIn("плагины: пропущен (не выбран claude)", result.stdout)
        self.assertIn("grok: плагина в marketplace listik нет, ставится отдельно", result.stdout)
        self.assertFalse(any(self.route_visibility().values()))

    def test_only_listik_when_claude_harness_alone(self) -> None:
        env, log = self.fake_env()
        result = self.install_plugins("--models", "none", "--harnesses", "claude", env=env)
        self.assertEqual(self.installed_plugins(log), {"listik"})
        self.assertIn("нейронки: —", result.stdout)
        self.assertIn("плагины: ok (listik)", result.stdout)

    def test_env_lists_and_flag_wins(self) -> None:
        env, log = self.fake_env(LISTIK_MODELS="claude", LISTIK_HARNESSES="claude,devin")
        result = self.install_plugins("--models", "claude,devin", env=env)
        self.assertEqual(self.installed_plugins(log), {"listik", "feature-pipeline", "devin"})
        self.assertIn("харнессы: claude, devin", result.stdout)

    def test_empty_env_is_unset(self) -> None:
        env, log = self.fake_env(LISTIK_MODELS="", LISTIK_HARNESSES="")
        self.install_plugins(env=env)
        self.assertEqual(self.installed_plugins(log), set(self.ALL_PLUGINS))

    def test_yes_installs_all_and_hides_nothing(self) -> None:
        env, log = self.fake_env(FAKE_CLAUDE_FAIL_PLUGIN="claude-codex@listik")
        result = self.install_plugins(env=env)
        self.assertEqual(self.installed_plugins(log), set(self.ALL_PLUGINS))
        self.assertIn("плагины: не удалось (claude-codex)", result.stdout)
        self.assertIn("/plugin install claude-codex@listik", result.stderr)
        self.assertTrue(all(self.route_visibility().values()))
        self.assertNotIn("скрыты маршруты", result.stdout)
        self.assertIn("grok: плагина в marketplace listik нет", result.stdout)

    def test_without_tty_installs_all(self) -> None:
        env, log = self.fake_env()
        result = self.run_install("--archive", str(self.make_archive(VERSION)),
                                  "--service", "no", "--mcp", "no", "--swarm", "no",
                                  "--plugins", "yes", "--codex-network", "no",
                                  env=env, stdin=subprocess.DEVNULL, session=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.installed_plugins(log), set(self.ALL_PLUGINS))
        self.assertTrue(all(self.route_visibility().values()))

    def test_marketplace_update_fails(self) -> None:
        env, log = self.fake_env(FAKE_CLAUDE_FAIL_MARKETPLACE="1")
        result = self.install_plugins("--models", "claude", "--harnesses", "claude", env=env)
        self.assertIn("плагины: не удалось (marketplace)", result.stdout)
        self.assertEqual(self.installed_plugins(log), {"listik", "feature-pipeline"})
        self.assertIn("/plugin install feature-pipeline@listik", result.stderr)
        self.assertNotIn("/plugin install dsh@listik", result.stderr)

    def test_no_claude_hint_lists_selected_only(self) -> None:
        env = self.env(PATH=path_without("claude"), LISTIK_PORT=str(free_port()))
        result = self.install_plugins("--models", "claude", "--harnesses", "claude", env=env)
        self.assertIn("плагины: не удалось", result.stdout)
        self.assertIn("/plugin install feature-pipeline@listik", result.stdout)
        self.assertNotIn("/plugin install dsh@listik", result.stdout)

    def test_plugins_no_keeps_routes(self) -> None:
        env, log = self.fake_env()
        self.install_plugins("--models", "openai", "--harnesses", "claude", env=env)
        conn = sqlite3.connect(self.installed_home / "listik.db")
        with conn:
            conn.execute("UPDATE routes SET visible = 1 WHERE key = 'low-pipeline'")
        conn.close()
        before = self.route_visibility()
        log.write_text("", encoding="utf-8")
        result = self.run_install("--archive", str(self.make_archive(VERSION)), "--yes",
                                  "--service", "no", "--mcp", "no", "--swarm", "no",
                                  "--plugins", "no", "--models", "none", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.route_visibility(), before)
        self.assertEqual(self.log_text(log), "")
        self.assertIn("плагины: пропущен", result.stdout)
        self.assertNotIn("нейронки:", result.stdout)
        self.assertNotIn("харнессы:", result.stdout)

    def test_plugins_no_still_validates_lists(self) -> None:
        result = self.run_install("--archive", str(self.make_archive(VERSION)), "--yes",
                                  "--plugins", "no", "--harnesses", "bogus")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--harnesses: неизвестный харнесс 'bogus'", result.stderr)

    def assert_list_rejected(self, args: tuple[str, ...], env_extra: dict, message: str) -> None:
        env, log = self.fake_env(**env_extra)
        result = self.run_install("--archive", str(self.make_archive(VERSION)), "--yes",
                                  "--plugins", "yes", *args, env=env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.app.exists(), "установка не остановилась до распаковки")
        self.assertEqual(self.log_text(log), "")

    def test_list_errors_stop_before_unpack(self) -> None:
        cases = [
            (("--models", "claude,Claude,bad"), {},
             "--models: неизвестная нейронка 'Claude' (можно: claude, openai, deepseek, glm, "
             "grok, devin, gemini, all, none)"),
            (("--harnesses", "foo"), {},
             "--harnesses: неизвестный харнесс 'foo' (можно: claude, codex, pi, devin, dsh, "
             "opencode, grok, all, none)"),
            ((), {"LISTIK_MODELS": "gpt"}, "--models: неизвестная нейронка 'gpt'"),
            ((), {"LISTIK_HARNESSES": "cursor"}, "--harnesses: неизвестный харнесс 'cursor'"),
            (("--models", "all,claude"), {}, "--models: all и none пишутся отдельно"),
            (("--harnesses", "none,claude"), {}, "--harnesses: all и none пишутся отдельно"),
            (("--models", " , "), {}, "--models: пустой список (ни одной — none)"),
            (("--models",), {}, "--models"),
            (("--harnesses",), {}, "--harnesses"),
        ]
        for args, env_extra, message in cases:
            with self.subTest(args=args, env=env_extra):
                self.assert_list_rejected(args, env_extra, message)

    def test_lists_in_help(self) -> None:
        result = self.run_install("--help")
        for text in ("--models", "--harnesses", "LISTIK_MODELS", "LISTIK_HARNESSES",
                     "gemini", "opencode", "all", "none", "по выбору"):
            self.assertIn(text, result.stdout)

    # --- listik-jdo8, порция b: построчный вопрос через pty ---------------

    def run_pty(self, answers: list[bytes], *, plain: bool = True,
                timeout: float = 240) -> tuple[int, str, pathlib.Path]:
        """install.sh под pty: на каждое новое приглашение — следующий ответ."""
        env, log = self.fake_env()
        if plain:
            env["LISTIK_PLAIN"] = "1"
        else:
            env.pop("NO_COLOR", None)
            env.pop("CI", None)
            env["TERM"] = "xterm-256color"
        prompt = self.PROMPT.encode("utf-8")
        sent = 0

        def step(buf: bytes) -> bytes | None:
            nonlocal sent
            if sent < len(answers) and buf.count(prompt) > sent:
                sent += 1
                return answers[sent - 1]
            return None

        code, out = self.pty_session(env, None if plain else (24, 100), step, timeout)
        self.assertEqual(sent, len(answers), out)
        return code, out, log

    def pty_session(self, env: dict, winsize: tuple[int, int] | None, step,
                    timeout: float) -> tuple[int, str]:
        """Общая pty-обвязка: `step(buf)` по накопленному выводу отдаёт байты для ввода или None."""
        argv = [SH, str(INSTALL_SH), "--archive", str(self.make_archive(VERSION)),
                "--service", "no", "--mcp", "no", "--swarm", "no", "--plugins", "yes",
                "--codex-network", "no"]
        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover — дочерний процесс
            try:
                if winsize:
                    fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", *winsize, 0, 0))
                os.execve(SH, argv, env)
            finally:
                os._exit(127)
        buf = b""
        reaped = False
        try:
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    self.fail(f"install.sh не закончил за {timeout} с: {buf.decode(errors='replace')}")
                ready, _, _ = select.select([fd], [], [], min(left, 1.0))
                if ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    buf += chunk
                data = step(buf)
                if data:
                    os.write(fd, data)
            _, status = os.waitpid(pid, 0)
            reaped = True
        finally:
            if not reaped:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
                os.waitpid(pid, 0)
            os.close(fd)
        return os.waitstatus_to_exitcode(status), buf.decode("utf-8", errors="replace")

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_pty_reasks_and_dedups(self) -> None:
        code, out, log = self.run_pty([b"2 x\r", b"1,3 3\r", b"\r"])
        self.assertEqual(code, 0, out)
        self.assertIn("Какие нейронки (подписки) у вас есть?", out)
        self.assertIn("  1) Claude (Anthropic)", out)
        self.assertIn("  7) Gemini (Google)", out)
        self.assertIn("Какие харнессы у вас установлены?", out)
        self.assertIn("  5) DeepSeek Harness (dsh)", out)
        self.assertIn("не понял: x", out)
        self.assertEqual(out.count(self.PROMPT), 3)
        self.assertIn("нейронки: claude, deepseek", out)
        self.assertIn("харнессы: claude, codex, pi, devin, dsh, opencode, grok", out)
        self.assertEqual(self.installed_plugins(log),
                         {"listik", "feature-pipeline", "dsh", "opencode", "pi",
                          "second-opinion"})

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_pty_zero_and_out_of_range(self) -> None:
        code, out, log = self.run_pty([b"0\r", b"0 1\r", b"9\r", b"01\r"])
        self.assertEqual(code, 0, out)
        self.assertIn("не понял: 0", out)
        self.assertIn("не понял: 9", out)
        self.assertIn("нейронки: —", out)
        self.assertIn("харнессы: claude", out)
        self.assertEqual(self.installed_plugins(log), {"listik"})
        self.assertFalse(any(self.route_visibility().values()))

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_pty_eof_means_all(self) -> None:
        code, out, log = self.run_pty([b"\r", b"\x04"])
        self.assertEqual(code, 0, out)
        self.assertEqual(self.installed_plugins(log), set(self.ALL_PLUGINS))

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_pty_decorated_mode_asks_plain(self) -> None:
        code, out, log = self.run_pty([b"1\r", b"1 2\r"], plain=False)
        self.assertEqual(code, 0, out)
        self.assertIn("\x1b[", out, "оформленный режим не включился")
        self.assertIn("Какие харнессы у вас установлены?", out)
        self.assertEqual(self.installed_plugins(log), {"listik", "feature-pipeline"})
        self.assertTrue(all(self.route_visibility().values()))


    # --- listik-jdo8, порция c: меню с галочками в оформленном режиме -----

    def run_menu(self, rows: int, cols: int, keys: list[tuple[tuple[str, ...], bytes]],
                 timeout: float = 240) -> tuple[int, str, pathlib.Path]:
        """Оформленный режим под pty; `keys` — (что дождаться после прошлой клавиши, клавиша).

        Каждая клавиша — отдельной записью и только после перерисовки: одиночный Esc иначе
        склеился бы со следующей клавишей в одну последовательность.
        """
        env, log = self.fake_env()
        env.pop("NO_COLOR", None)
        env.pop("CI", None)
        env["TERM"] = "xterm-256color"
        plain_prompt = "Номера через пробел или запятую".encode("utf-8")
        sent = 0
        pos = 0

        def step(buf: bytes) -> bytes | None:
            nonlocal sent, pos
            if sent < len(keys) and plain_prompt in buf:
                self.fail("ждали меню, а пришёл построчный вопрос: "
                          + buf.decode("utf-8", errors="replace"))
            if sent >= len(keys):
                return None
            wait, data = keys[sent]
            tail = buf[pos:]
            if all(w.encode("utf-8") in tail for w in wait):
                sent += 1
                pos = len(buf)
                return data
            return None

        code, out = self.pty_session(env, (rows, cols), step, timeout)
        self.assertEqual(sent, len(keys), out)
        return code, out, log

    @staticmethod
    def visible(out: str) -> str:
        """Вывод без escape-последовательностей."""
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out)

    def report(self, out: str, name: str) -> str:
        """Значение строки итоговой панели («нейронки», «харнессы»)."""
        found = re.findall(name + r":\s+([^\r\n]*)", self.visible(out))
        self.assertTrue(found, out)
        return found[-1].strip()

    def menu_lines(self, out: str) -> list[str]:
        """Видимый текст строк меню."""
        text = self.visible(out)
        return [ln for ln in re.split(r"\r\n|\r|\n", text)
                if any(m in ln for m in ("[✓]", "[ ]", "Пробел", "Какие", "Отметьте"))]

    KEYS_WIDE = "Пробел отметить"

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_menu_main(self) -> None:
        k = self.KEYS_WIDE
        code, out, log = self.run_menu(40, 100, [
            (("Какие нейронки", k), b"\r"),
            (("Какие харнессы", k), b"\x1b[B"),
            ((k,), b" "),
            ((k,), b"\r"),
        ])
        self.assertEqual(code, 0, out)
        self.assertIn("Отметьте, что есть", out)
        self.assertIn("[✓]", out)
        self.assertIn("[ ]", out)
        self.assertNotIn(self.PROMPT, out)
        self.assertEqual(self.report(out, "нейронки"), "claude, openai, deepseek, glm, grok, devin, gemini")
        self.assertEqual(self.report(out, "харнессы"), "claude, pi, devin, dsh, opencode, grok")
        self.assertNotIn("plugin install codex@listik", self.log_text(log))
        self.assertIn("plugin install pi@listik", self.log_text(log))

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_menu_edges_and_ignored_keys(self) -> None:
        k = self.KEYS_WIDE
        keys = [(("Какие нейронки", k), b"\x1b[A")]
        keys += [((k,), key) for key in (b"y", b"n", b"\x1b", b" ", b"\r")]
        keys.append((("Какие харнессы", k), b"\x1b[B"))
        keys += [((k,), b"\x1b[B")] * 9
        keys += [((k,), b" "), ((k,), b"\r")]
        code, out, log = self.run_menu(40, 100, keys)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.report(out, "нейронки"), "openai, deepseek, glm, grok, devin, gemini")
        self.assertEqual(self.report(out, "харнессы"), "claude, codex, pi, devin, dsh, opencode")
        self.assertNotIn("plugin install feature-pipeline@listik", self.log_text(log))

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_menu_narrow(self) -> None:
        k = "Пробел · Enter"
        keys = [(("Какие нейронки", k), b"\x1b[B")]
        keys += [((k,), key) for key in (b"\x1b[B", b"\x1b[A", b" ", b"\r")]
        keys.append((("Какие харнессы", k), b"\x1b[B"))
        keys += [((k,), key) for key in (b"\x1b[B", b"\x1b[B", b"\x1b[A", b" ", b"\r")]
        code, out, log = self.run_menu(40, 60, keys)
        self.assertEqual(code, 0, out)
        self.assertNotIn(self.KEYS_WIDE, out)
        self.assertEqual(self.report(out, "нейронки"), "claude, deepseek, glm, grok, devin, gemini")
        self.assertEqual(self.report(out, "харнессы"), "claude, codex, devin, dsh, opencode, grok")
        lines = self.menu_lines(out)
        self.assertTrue(lines)
        for line in lines:
            self.assertLessEqual(len(line), 60, line)

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_menu_ctrl_c_aborts(self) -> None:
        code, out, log = self.run_menu(40, 100, [(("Какие нейронки", self.KEYS_WIDE), b"\x03")])
        self.assertNotEqual(code, 0, out)
        self.assertIn("установка прервана", out)
        tail = out[out.rindex(self.KEYS_WIDE):]
        self.assertIn("\x1b[?25h", tail)
        self.assertNotIn("plugin install", self.log_text(log))

if __name__ == "__main__":
    unittest.main()
