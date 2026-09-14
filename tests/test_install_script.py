"""Шаг 12, порция c: `install.sh` (пункты 1–10 и 15).

Сети наружу нет: архивы собираются во временном каталоге вручную (`tarfile`) в формате
порции b, а «сеть» изображает `http.server` из stdlib на `127.0.0.1:0`. Установка идёт
только во временные `HOME`, `LISTIK_BIN_DIR` и `LISTIK_ROUTES`; `LISTIK_HOME` не задан,
поэтому работает значение по умолчанию `$HOME/.listik`.

Скрипт ставит обёртку, которая запускает установленный код тем `python3`, который нашёлся
в `PATH`, — поэтому в сценариях с поддельным старым `python3` вокруг ничего не создаётся.
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import io
import json
import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
README = REPO_ROOT / "README.md"

#: Версия дерева и вторая версия — прибавлением единицы к патчу (номера не хардкодим).
VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
_major, _minor, _patch = VERSION.split(".")
NEXT_VERSION = f"{_major}.{_minor}.{int(_patch) + 1}"

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
        self.routes_copy = self.tmp / "runtime-routes.json"
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
        env["LISTIK_BIN_DIR"] = str(self.bin_dir)
        env["LISTIK_ROUTES"] = str(self.routes_copy)
        env.update(overrides)
        return env

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
        result = self.run_install("--archive", str(archive), "--yes", *extra, env=env)
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

    # --- пункт 15: routes.json -------------------------------------------

    def _differing_routes(self) -> str:
        self.routes_copy.write_text('{"version": 1, "routes": []}\n', encoding="utf-8")
        return self.routes_copy.read_text(encoding="utf-8")

    def test_routes_keep(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        old = self._differing_routes()
        result = self.install(archive, "--routes", "keep")
        self.assertEqual(self.routes_copy.read_text(encoding="utf-8"), old)
        self.assertIn(str(self.app / "current" / "routes.json"), result.stdout)
        self.assertEqual(list(self.routes_copy.parent.glob("runtime-routes.json.bak-*")), [])

    def test_routes_replace_keeps_backup(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        old = self._differing_routes()
        self.install(archive, "--routes", "replace")
        self.assertNotEqual(self.routes_copy.read_text(encoding="utf-8"), old)
        backups = list(self.routes_copy.parent.glob("runtime-routes.json.bak-*"))
        self.assertEqual(len(backups), 1, f"нет .bak рядом с копией: {backups}")
        self.assertEqual(backups[0].read_text(encoding="utf-8"), old)

    def test_routes_ask_without_tty_keeps(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        old = self._differing_routes()
        # stdin=/dev/null и своя сессия — управляющего терминала нет вовсе.
        result = self.run_install("--archive", str(archive), "--routes", "ask",
                                  stdin=subprocess.DEVNULL, session=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.routes_copy.read_text(encoding="utf-8"), old)
        self.assertIn("оставлена", result.stdout)
        self.assertEqual(list(self.routes_copy.parent.glob("runtime-routes.json.bak-*")), [])

    def test_routes_ask_with_yes_keeps(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        old = self._differing_routes()
        result = self.install(archive, "--routes", "ask")
        self.assertEqual(self.routes_copy.read_text(encoding="utf-8"), old)
        self.assertIn("оставлена", result.stdout)

    def test_routes_copy_is_not_created(self) -> None:
        archive = self.make_archive(VERSION)
        self.install(archive)
        self.assertFalse(self.routes_copy.exists(), "установщик создал рабочую копию")
        self.install(archive, "--routes", "replace")
        self.assertFalse(self.routes_copy.exists(),
                         "замена не должна создавать копию с нуля")

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
        result = self.run_install("--archive", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.installed_home.exists(), "каталог данных создан установкой")
        self.assertFalse(self.wrapper.exists(), "обёртка записана при плохой сумме")

    def test_bad_checksum_keeps_previous_install(self) -> None:
        self.install(self.make_archive(VERSION))
        wrapper_before = sha256_file(self.wrapper)
        archive = self.make_archive(NEXT_VERSION)
        (archive.parent / (archive.name + ".sha256")).write_text(
            f"{'1' * 64}  {archive.name}\n", encoding="utf-8")
        result = self.run_install("--archive", str(archive), "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assert_installed(VERSION)
        self.assertEqual(sha256_file(self.wrapper), wrapper_before)
        self.assertFalse((self.app / NEXT_VERSION).exists())

    def test_broken_init_reports(self) -> None:
        archive = self.make_archive(NEXT_VERSION, broken_init=True)
        result = self.run_install("--archive", str(archive), "--yes")
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
        result = self.run_install("--archive", str(archive), "--yes", env=env)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.installed_home.exists(), "$HOME/.listik создан")

    # --- пункт 15: сетевой путь ------------------------------------------

    def test_network_install_without_version(self) -> None:
        base = self.start_stub(VERSION)
        env = self.env(LISTIK_RELEASES_API=f"{base}/latest", LISTIK_DOWNLOAD_BASE=base)
        result = self.run_install("--yes", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_installed(VERSION)

    def test_network_flag_wins_over_env(self) -> None:
        base = self.start_stub(VERSION)
        env = self.env(LISTIK_RELEASES_API=f"{base}/latest", LISTIK_DOWNLOAD_BASE=base,
                       LISTIK_VERSION="9.9.9")
        result = self.run_install("--version", VERSION, "--yes", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_installed(VERSION)
        self.assertFalse((self.app / "9.9.9").exists())

    def test_network_without_sha256_fails(self) -> None:
        base = self.start_stub(VERSION, sha=False)
        env = self.env(LISTIK_RELEASES_API=f"{base}/latest", LISTIK_DOWNLOAD_BASE=base)
        result = self.run_install("--yes", env=env)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.installed_home.exists(), "что-то поставлено без суммы")
        self.assertFalse(self.wrapper.exists())

    # --- пункт 1 и 2: stdin-режим и справка ------------------------------

    def test_piped_to_sh(self) -> None:
        archive = self.make_archive(VERSION)
        result = subprocess.run(
            [SH, "-s", "--", "--archive", str(archive), "--yes"],
            input=INSTALL_SH.read_text(encoding="utf-8"), env=self.env(),
            capture_output=True, text=True, timeout=240)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_installed(VERSION)

    def test_help(self) -> None:
        result = self.run_install("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("--version", "--archive", "--routes", "LISTIK_DOWNLOAD_BASE",
                     "LISTIK_HOME"):
            self.assertIn(text, result.stdout, f"в --help нет {text}")


if __name__ == "__main__":
    unittest.main()
