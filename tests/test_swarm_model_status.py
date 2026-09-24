"""Порция a: имя модели роя в health и status.

Тесты используют только временные конфиги и базу. CLI запускается подпроцессом,
чтобы проверить тот же путь, которым пользуется пользователь.
"""
from __future__ import annotations

import json
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from listik import embed as embed_mod
from listik import paths, server
from tests.helpers import TempDbTestCase


REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"


class HealthModelTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = self.tmp_path / "config.toml"
        self.config.write_text("[auth]\ntoken = \"test\"\n", encoding="utf-8")
        self._config_patch = mock.patch.object(paths, "CONFIG_PATH", self.config)
        self._conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        self._embed_patch = mock.patch.object(
            embed_mod, "health", return_value={"ok": False, "model": "test"})
        self._config_patch.start()
        self._conn_patch.start()
        self._embed_patch.start()
        self.addCleanup(self._embed_patch.stop)
        self.addCleanup(self._conn_patch.stop)
        self.addCleanup(self._config_patch.stop)

    def health(self, authed: bool = True) -> dict:
        status, body = server.handle("GET", "/api/health", {}, {}, authed=authed)
        self.assertEqual(status, 200)
        return body

    def test_health_model_comes_from_file_environment_and_default(self) -> None:
        self.config.write_text('[auth]\ntoken = "test"\n[swarm]\nmodel = "cfg-model"\n',
                               encoding="utf-8")
        self.assertEqual(self.health()["swarm"]["model"], "cfg-model")
        with mock.patch.dict(os.environ, {"LISTIK_SWARM_MODEL": "env-model"}):
            self.assertEqual(self.health()["swarm"]["model"], "env-model")
        with mock.patch.dict(os.environ, {"LISTIK_SWARM_MODEL": ""}):
            self.config.write_text('[auth]\ntoken = "test"\n', encoding="utf-8")
            self.assertEqual(self.health()["swarm"]["model"], "z-ai/glm-5.3-flash")

    def test_unauthorized_health_has_only_public_swarm_fields(self) -> None:
        self.config.write_text(
            '[auth]\ntoken = "test"\n[swarm]\napi_key = "secret-swarm"\nmodel = "m"\n',
            encoding="utf-8")
        body = self.health(authed=False)
        self.assertEqual(set(body["swarm"]), {"enabled", "running"})
        self.assertNotIn("secret-swarm", json.dumps(body))


class StatusModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="listik-swarm-model-")
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.config = self.home / "config.toml"

    def env(self, **extra: str) -> dict:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("LISTIK_")}
        env.update({"LISTIK_HOME": str(self.home), "PYTHONPATH": str(REPO_DIR), "HOME": str(self.home)})
        env.update(extra)
        return env

    def run_cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(LISTIK_BIN), *args], env=env or self.env(),
                              capture_output=True, text=True, timeout=30)

    def write_config(self, swarm: str = "") -> None:
        self.config.write_text('[server]\nport = 9\n' + swarm, encoding="utf-8")

    @staticmethod
    def cli_module():
        loader = importlib.machinery.SourceFileLoader(
            "listik_cli_swarm_model_under_test", str(LISTIK_BIN))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def test_server_health_payload_is_used_verbatim(self) -> None:
        cli = self.cli_module()
        health = {"swarm": {"enabled": True, "running": True, "pid": 42,
                             "model": "server-model"}}
        with mock.patch("listik.swarm_llm.model_name",
                        side_effect=AssertionError("status recalculated server model")):
            self.assertIs(cli._swarm_view(health), health["swarm"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli._print_swarm(health)
        self.assertIn("рой:    работает (pid 42) · модель server-model", output.getvalue())

    def test_unauthorized_health_payload_has_no_model_suffix(self) -> None:
        cli = self.cli_module()
        health = {"swarm": {"enabled": False, "running": False}}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli._print_swarm(health)
        self.assertIn("рой:    выключен (listik swarm on)", output.getvalue())
        self.assertNotIn("модель", output.getvalue())

    def test_local_json_uses_default_environment_and_file_model(self) -> None:
        self.write_config()
        result = self.run_cli("status", "--json", "--local")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["swarm"]["model"], "z-ai/glm-5.3-flash")

        self.write_config('[swarm]\napi_key = "secret-swarm"\nmodel = "cfg-model"\n')
        result = self.run_cli("status", "--json", "--local")
        self.assertEqual(json.loads(result.stdout)["swarm"]["model"], "cfg-model")
        result = self.run_cli("status", "--json", "--local",
                          env=self.env(LISTIK_SWARM_MODEL="env-model"))
        data = json.loads(result.stdout)
        self.assertEqual(data["swarm"]["model"], "env-model")
        self.assertNotIn("secret-swarm", result.stdout)

    def test_text_status_and_swarm_status_show_model(self) -> None:
        self.write_config()
        result = self.run_cli("status", "--local")
        self.assertIn("рой:", result.stdout)
        self.assertIn("модель z-ai/glm-5.3-flash", result.stdout)
        result = self.run_cli("swarm", "status", "--json")
        self.assertEqual(json.loads(result.stdout)["model"], "z-ai/glm-5.3-flash")

    def test_invalid_swarm_config_has_no_model_suffix(self) -> None:
        self.write_config('[swarm]\nenabled = "да"\napi_key = "secret-swarm"\n')
        result = self.run_cli("status", "--local")
        self.assertEqual(result.returncode, 0, result.stderr)
        swarm_line = next(line for line in result.stdout.splitlines() if line.startswith("рой:"))
        self.assertIn("конфиг:", swarm_line)
        self.assertNotIn("модель", swarm_line)
        self.assertNotIn("secret-swarm", result.stdout)


if __name__ == "__main__":
    unittest.main()
