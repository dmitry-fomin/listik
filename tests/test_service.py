"""Шаг 12, порция d: `listik service install|uninstall|status` (launchd/systemd --user).

`bin/listik` грузится как модуль (у файла нет расширения .py, как в `test_port_conflicts.py`)
и вызывается в процессе теста — иначе `listik.service.run` было бы нечем подменить. Настоящие
`launchctl`/`systemctl` тесты не зовут никогда: `listik.service.run` подменён во всех сценариях,
а `sys.platform` — временный (`mock.patch("sys.platform", ...)`), поэтому linux-сценарии
выполняются и на macOS-хосте, а не пропускаются. `Path.home()` следует за `HOME` из окружения:
каждый тест работает во временном `HOME` и временном `LISTIK_HOME`
(`listik.paths.DATA_DIR`/`LOGS_DIR` подменены атрибутами модуля — их читают `paths.DATA_DIR`
на каждый вызов, а не один раз при импорте), поэтому настоящие `~/Library/LaunchAgents`,
`~/.config/systemd` и каталог данных репозитория не трогаются.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import plistlib
import tempfile
import unittest
from unittest import mock

from listik import paths, server, service

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"


def _load_cli():
    """Загрузить `bin/listik` как модуль — у файла нет расширения `.py`."""
    loader = importlib.machinery.SourceFileLoader("listik_cli_service_test", str(LISTIK_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


CLI = _load_cli()


class FakeRunner:
    """Подмена `service.run`: пишет argv в лог, отдаёт заготовленный (код, stdout)."""

    def __init__(self, default: tuple[int, str] = (0, "")):
        self.calls: list[list[str]] = []
        self.default = default
        #: argv[0]+argv[1] (первые два токена команды) -> (код, stdout) для отдельных шагов.
        self.overrides: dict[tuple[str, ...], tuple[int, str]] = {}

    def set(self, *prefix: str, code: int, out: str = "") -> None:
        self.overrides[tuple(prefix)] = (code, out)

    def __call__(self, argv: list[str]) -> tuple[int | None, str]:
        self.calls.append(list(argv))
        for prefix, result in self.overrides.items():
            if tuple(argv[:len(prefix)]) == prefix:
                return result
        return self.default


class ServiceCliTestCase(unittest.TestCase):
    """Каждый тест — свой временный `HOME`/`LISTIK_HOME`, свой поддельный `run`."""

    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory(prefix="listik-service-test-")
        self.addCleanup(tmpdir.cleanup)
        # resolve(): macOS mktemp lives under /tmp, a symlink to /private/tmp — resolve()
        # de-symlinks it just like `resolve_bin`, so comparisons below don't need to guess.
        self.tmp = pathlib.Path(tmpdir.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.data_dir = self.tmp / "data"

        self.runner = FakeRunner()
        self._enter(mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False))
        os.environ.pop("LISTIK_WRAPPER", None)
        self._enter(mock.patch.object(paths, "DATA_DIR", self.data_dir))
        self._enter(mock.patch.object(paths, "LOGS_DIR", self.data_dir / "logs"))
        self._enter(mock.patch.object(service, "run", self.runner))
        self._enter(mock.patch("sys.platform", "darwin"))
        # По умолчанию — ни pid-файла, ни занятого порта: реальный (возможно, работающий
        # на машине) сервер Listik тесты трогать не должны. Сценарии конфликта подменяют
        # эти же функции своим `return_value` через `mock.patch.object` в самом тесте.
        self._enter(mock.patch.object(server, "read_pid", return_value=None))
        self._enter(mock.patch.object(server, "port_holder", return_value=None))

    def _enter(self, patcher):
        obj = patcher.start()
        self.addCleanup(patcher.stop)
        return obj

    # --- вспомогательное ---------------------------------------------------

    def run_cli(self, *args: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = CLI.main(["service", *args])
        return code, buf.getvalue()

    def run_json(self, *args: str) -> tuple[int, dict]:
        code, out = self.run_cli(*args, "--json")
        return code, json.loads(out)

    def make_bin(self, name: str = "fake-listik") -> pathlib.Path:
        p = self.tmp / name
        p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        p.chmod(0o755)
        return p

    def launchd_unit(self) -> pathlib.Path:
        return self.home / "Library" / "LaunchAgents" / "listik.server.plist"

    def legacy_launchd_unit(self) -> pathlib.Path:
        return self.home / "Library" / "LaunchAgents" / "dev.listik.server.plist"

    def systemd_unit(self) -> pathlib.Path:
        return self.home / ".config" / "systemd" / "user" / "listik.service"

    # --- darwin: install --no-load ------------------------------------------

    def test_darwin_install_no_load_writes_plist(self) -> None:
        fake_bin = self.make_bin()
        code, info = self.run_json("install", "--no-load", "--bin", str(fake_bin))
        self.assertEqual(code, 0, info)
        self.assertTrue(info["ok"])

        plist_path = self.launchd_unit()
        self.assertTrue(plist_path.is_file())
        with plist_path.open("rb") as fh:
            data = plistlib.load(fh)
        self.assertEqual(data["Label"], "listik.server")
        self.assertEqual(data["ProgramArguments"], [str(fake_bin), "serve", "--quiet"])
        self.assertEqual(data["EnvironmentVariables"]["LISTIK_HOME"], str(self.data_dir))
        self.assertIs(data["RunAtLoad"], True)
        self.assertIs(data["KeepAlive"], True)
        log = str(self.data_dir / "logs" / "service.log")
        self.assertEqual(data["StandardOutPath"], log)
        self.assertEqual(data["StandardErrorPath"], log)

        self.assertTrue((self.data_dir / "logs").is_dir(), "LOGS_DIR не создан")
        self.assertEqual(self.runner.calls, [], "раннер не должен вызываться с --no-load")

    def test_darwin_install_without_no_load_bootstraps(self) -> None:
        fake_bin = self.make_bin()
        code, out = self.run_cli("install", "--bin", str(fake_bin))
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.runner.calls), 3, self.runner.calls)
        self.assertEqual(self.runner.calls[0][:2], ["launchctl", "print"])
        self.assertEqual(self.runner.calls[1][:2], ["launchctl", "bootout"])
        self.assertEqual(self.runner.calls[2][0], "launchctl")
        self.assertEqual(self.runner.calls[2][1], "bootstrap")
        self.assertEqual(self.runner.calls[2][2], f"gui/{os.getuid()}")
        self.assertEqual(self.runner.calls[2][3], str(self.launchd_unit()))

    def test_darwin_install_migrates_legacy_plist(self) -> None:
        legacy = self.legacy_launchd_unit()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(plistlib.dumps({"Label": "dev.listik.server"}))

        code, out = self.run_cli("install", "--bin", str(self.make_bin()))
        self.assertEqual(code, 0, out)
        self.assertFalse(legacy.exists())
        self.assertTrue(self.launchd_unit().exists())
        self.assertIn(
            ["launchctl", "bootout", f"gui/{os.getuid()}/dev.listik.server"],
            self.runner.calls,
        )

    def test_darwin_install_no_load_removes_legacy_without_runner(self) -> None:
        legacy = self.legacy_launchd_unit()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(plistlib.dumps({"Label": "dev.listik.server"}))

        code, out = self.run_cli("install", "--no-load", "--bin", str(self.make_bin()))
        self.assertEqual(code, 0, out)
        self.assertFalse(legacy.exists())
        self.assertEqual(self.runner.calls, [])

    # --- linux ---------------------------------------------------------------

    def test_linux_install_writes_unit_and_reloads(self) -> None:
        with mock.patch("sys.platform", "linux"):
            fake_bin = self.make_bin()
            code, out = self.run_cli("install", "--bin", str(fake_bin))
            self.assertEqual(code, 0, out)

        unit = self.systemd_unit()
        self.assertTrue(unit.is_file())
        text = unit.read_text(encoding="utf-8")
        self.assertIn(f"ExecStart={fake_bin} serve --quiet", text)
        self.assertIn(f"Environment=LISTIK_HOME={self.data_dir}", text)
        log = self.data_dir / "logs" / "service.log"
        self.assertIn(f"StandardOutput=append:{log}", text)
        self.assertIn(f"StandardError=append:{log}", text)
        self.assertIn("WantedBy=default.target", text)

        argvs = [tuple(c[:3]) for c in self.runner.calls]
        self.assertIn(("systemctl", "--user", "daemon-reload"), argvs)
        self.assertIn(("systemctl", "--user", "enable"), argvs)
        self.assertIn(("systemctl", "--user", "restart"), argvs)

    # --- --bin: относительный, отсутствующий, LISTIK_WRAPPER, дефолт --------

    def test_bin_relative_resolves_to_absolute(self) -> None:
        fake_bin = self.make_bin()
        rel = os.path.relpath(fake_bin, os.getcwd())
        code, info = self.run_json("install", "--no-load", "--bin", rel)
        self.assertEqual(code, 0, info)
        self.assertEqual(info["bin"], str(fake_bin))

    def test_bin_missing_is_bad_argument(self) -> None:
        code, out = self.run_json("install", "--no-load", "--bin", str(self.tmp / "nope"))
        self.assertNotEqual(code, 0)
        self.assertEqual(out["error"]["code"], "bad_argument")

    def test_bin_default_uses_wrapper_env(self) -> None:
        wrapper = self.make_bin("wrapper-listik")
        with mock.patch.dict(os.environ, {"LISTIK_WRAPPER": str(wrapper)}):
            code, info = self.run_json("install", "--no-load")
        self.assertEqual(code, 0, info)
        self.assertEqual(info["bin"], str(wrapper))

    def test_bin_default_without_wrapper_uses_root_bin(self) -> None:
        os.environ.pop("LISTIK_WRAPPER", None)
        code, info = self.run_json("install", "--no-load")
        self.assertEqual(code, 0, info)
        self.assertEqual(info["bin"], str(paths.ROOT_DIR / "bin" / "listik"))

    # --- конфликт с посторонним сервером -------------------------------------

    def test_foreign_server_blocks_install(self) -> None:
        self.runner.default = (1, "")  # юнит не загружен
        with mock.patch.object(server, "read_pid", return_value=4242):
            code, out = self.run_json("install", "--bin", str(self.make_bin()))
        self.assertNotEqual(code, 0)
        self.assertEqual(out["error"]["code"], "conflict")
        self.assertIn("listik stop", out["error"]["hint"])
        # раннер вызван только для проверки «загружен ли юнит», процесс не тронут.
        self.assertEqual(len(self.runner.calls), 1, self.runner.calls)
        self.assertEqual(self.runner.calls[0][:2], ["launchctl", "print"])
        self.assertFalse(self.launchd_unit().exists(), "юнит не должен быть записан")

    def test_server_under_service_reloads_without_conflict(self) -> None:
        self.runner.default = (0, "")  # юнит уже загружен
        with mock.patch.object(server, "read_pid", return_value=4242):
            code, out = self.run_cli("install", "--bin", str(self.make_bin()))
        self.assertEqual(code, 0, out)
        argvs = [tuple(c[:2]) for c in self.runner.calls]
        self.assertIn(("launchctl", "print"), argvs)
        self.assertIn(("launchctl", "bootout"), argvs)
        self.assertIn(("launchctl", "bootstrap"), argvs)

    def test_foreign_server_via_port_holder(self) -> None:
        self.runner.default = (1, "")
        with mock.patch.object(server, "read_pid", return_value=None), \
             mock.patch.object(server, "port_holder",
                               return_value=(555, "/opt/listik/bin/listik serve")):
            code, out = self.run_json("install", "--bin", str(self.make_bin()))
        self.assertNotEqual(code, 0)
        self.assertEqual(out["error"]["code"], "conflict")

    # --- uninstall -------------------------------------------------------------

    def test_uninstall_removes_unit_and_unloads(self) -> None:
        self.run_cli("install", "--no-load", "--bin", str(self.make_bin()))
        self.assertTrue(self.launchd_unit().exists())
        self.runner.calls.clear()

        code, info = self.run_json("uninstall")
        self.assertEqual(code, 0, info)
        self.assertTrue(info["existed"])
        self.assertFalse(self.launchd_unit().exists())
        self.assertEqual(self.runner.calls[0][:2], ["launchctl", "bootout"])

        code, info = self.run_json("uninstall")
        self.assertEqual(code, 0, info)
        self.assertFalse(info["existed"])

    def test_uninstall_no_load_leaves_runner_untouched(self) -> None:
        self.run_cli("install", "--no-load", "--bin", str(self.make_bin()))
        self.runner.calls.clear()

        code, out = self.run_cli("uninstall", "--no-load")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(self.launchd_unit().exists())
        self.assertIn("launchctl bootout", out)

    def test_uninstall_migrates_legacy_plist(self) -> None:
        legacy = self.legacy_launchd_unit()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(plistlib.dumps({"Label": "dev.listik.server"}))

        code, info = self.run_json("uninstall")
        self.assertEqual(code, 0, info)
        self.assertTrue(info["existed"])
        self.assertFalse(legacy.exists())
        self.assertIn(
            ["launchctl", "bootout", f"gui/{os.getuid()}/dev.listik.server"],
            self.runner.calls,
        )

    # --- status ------------------------------------------------------------

    def test_status_json_reflects_install_and_uninstall(self) -> None:
        fake_bin = self.make_bin()
        self.run_cli("install", "--no-load", "--bin", str(fake_bin))
        code, raw = self.run_cli("status", "--json")
        self.assertEqual(code, 0, raw)
        st = json.loads(raw.strip())
        self.assertEqual(raw.strip().count("\n"), 0, "stdout должен быть одним JSON-объектом")
        self.assertTrue(st["installed"])
        self.assertEqual(st["bin"], str(fake_bin))
        self.assertEqual(st["platform"], "launchd")
        self.assertEqual(st["data_dir"], str(self.data_dir))

        self.run_cli("uninstall")
        code, out = self.run_json("status")
        self.assertEqual(code, 0, out)
        self.assertFalse(out["installed"])

    def test_status_migrates_legacy_plist(self) -> None:
        legacy = self.legacy_launchd_unit()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(plistlib.dumps({"Label": "dev.listik.server"}))

        code, out = self.run_json("status")
        self.assertEqual(code, 0, out)
        self.assertFalse(out["installed"])
        self.assertFalse(legacy.exists())
        self.assertIn(
            ["launchctl", "bootout", f"gui/{os.getuid()}/dev.listik.server"],
            self.runner.calls,
        )

    # --- платформа не поддерживается -----------------------------------------

    def test_unsupported_platform(self) -> None:
        with mock.patch("sys.platform", "win32"):
            code, out = self.run_json("status")
        self.assertNotEqual(code, 0)
        self.assertEqual(out["error"]["code"], "unsupported")


if __name__ == "__main__":
    unittest.main()
