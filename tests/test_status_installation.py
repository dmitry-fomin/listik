"""Status: старый сервер и HTTP-отказ не превращаются в успешную сверку."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
import urllib.error
from unittest import mock

from listik import client, paths
from tests.test_health_db_error import _load_cli


class StatusInstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _load_cli()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        for key, value in (("DATA_DIR", self.root),
                           ("CONFIG_PATH", self.root / "config.toml"),
                           ("ROOT_DIR", self.root / "code")):
            patch = mock.patch.object(paths, key, value)
            patch.start()
            self.addCleanup(patch.stop)

    def status(self, health: dict) -> dict:
        out = io.StringIO()
        args = argparse.Namespace(host=None, port=None, json=True)
        with mock.patch.object(client, "health", return_value=health), \
             mock.patch.object(client, "local_call", side_effect=AssertionError("local fallback")), \
             contextlib.redirect_stdout(out):
            code = self.cli.cmd_status(args)
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def test_old_server_without_paths_reports_unknown(self) -> None:
        for authed in (True, False):
            with self.subTest(authed=authed):
                data = self.status({"status": "ok", "authed": authed})
                self.assertEqual(data["diagnostics"]["installation"], "unknown")
                self.assertIn("обновите сервер", " ".join(data["diagnostics"]["warnings"]))

    def test_other_code_with_same_config_is_reported(self) -> None:
        data = self.status({"status": "ok", "authed": True, "installation": {
            "data_dir": str(paths.DATA_DIR), "config_path": str(paths.CONFIG_PATH),
            "code_dir": str(self.root / "other-code"),
        }})
        self.assertEqual(data["diagnostics"]["installation"], "mismatch")
        self.assertIn("code_dir", " ".join(data["diagnostics"]["warnings"]))

    def test_http_auth_refusal_is_not_down_or_local(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status):
                error = urllib.error.HTTPError("http://localhost/api/health", status,
                                               "refused", {}, None)
                with mock.patch("urllib.request.urlopen", side_effect=error):
                    health = client.health()
                self.assertIs(health["authed"], False)
                data = self.status(health)
                self.assertEqual(data["server"], "unauthorized")
                self.assertEqual(data["diagnostics"]["token"], "rejected")
                self.assertTrue(data["diagnostics"]["warnings"])
