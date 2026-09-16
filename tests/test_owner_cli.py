"""Владелец-человек в CLI (listik-xt69, порция b).

`bin/listik --local` запускается подпроцессом с `LISTIK_DB`/`LISTIK_CONFIG` во
временном каталоге (образец — `tests/test_cli_errors.py`): реальные `listik.db` и
`config.toml` не трогаются, сети нет.

Конфигов три: локальный (`[auth] token`), серверный без `[auth] owner` (проверки
«не задан») и серверный с `[auth] owner = "ann"` (приоритет источников).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN

TOKEN = "test-token"
# Порт заведомо мёртвый: `--local` при живом сервере печатает в stderr предупреждение
# об обходе доски, и тесты не должны зависеть от того, поднят ли Listik на машине.
DEAD_SERVER = 'host = "127.0.0.1"\nport = 1\n'
LOCAL_CONFIG = f'[auth]\ntoken = "{TOKEN}"\n\n[server]\n{DEAD_SERVER}'
SERVER_CONFIG = (f'[auth]\ntoken = "{TOKEN}"\n'
                 f'\n[server]\n{DEAD_SERVER}mode = "server"\nusers = ["ann", "bob"]\n')
SERVER_CONFIG_WITH_OWNER = (f'[auth]\ntoken = "{TOKEN}"\nowner = "ann"\n'
                            f'\n[server]\n{DEAD_SERVER}mode = "server"\n'
                            'users = ["ann", "bob"]\n')


class OwnerCliCase(TempDbTestCase):
    """CLI в локальном режиме: временная база, временный конфиг, никакой сети."""

    config_text = SERVER_CONFIG

    def setUp(self) -> None:
        super().setUp()
        self.config_path = self.tmp_path / "config.toml"
        self.config_path.write_text(self.config_text, encoding="utf-8")

    def run_cli(self, *args, config=None, **env_extra):
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_CONFIG": str(config or self.config_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        env.pop("LISTIK_OWNER", None)
        env.update(env_extra)
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def json_out(self, proc) -> dict:
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def error_json(self, proc) -> dict:
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)["error"]

    def new_task(self, *args, **kwargs) -> dict:
        return self.json_out(self.run_cli("new", "T", "--json", *args, **kwargs))


class TestCreate(OwnerCliCase):
    """Пункты 25–27, 33: источники владельца у создания задачи."""

    def test_no_owner_refused(self):  # 25
        err = self.error_json(self.run_cli("new", "T", "--json"))
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn("владельца", err["message"])

    def test_flag(self):  # 25
        self.assertEqual(self.new_task("--owner", "ann")["owner"], "ann")

    def test_env_and_flag_priority(self):  # 26
        self.assertEqual(self.new_task(LISTIK_OWNER="bob")["owner"], "bob")
        self.assertEqual(self.new_task("--owner", "ann", LISTIK_OWNER="bob")["owner"], "ann")

    def test_config_owner(self):  # 27
        cfg = self.tmp_path / "config-owner.toml"
        cfg.write_text(SERVER_CONFIG_WITH_OWNER, encoding="utf-8")
        self.assertEqual(self.new_task(config=cfg)["owner"], "ann")
        self.assertEqual(self.new_task(config=cfg, LISTIK_OWNER="bob")["owner"], "bob")

    def test_local_mode_ignores_owner(self):  # 33
        cfg = self.tmp_path / "config-local.toml"
        cfg.write_text(LOCAL_CONFIG, encoding="utf-8")
        self.assertIsNone(self.new_task("--owner", "carol", config=cfg)["owner"])


class TestTaskCommands(OwnerCliCase):
    """Пункты 28–31: claim, list, show, set."""

    def setUp(self) -> None:
        super().setUp()
        self.ann = self.new_task("--owner", "ann")["id"]

    def test_foreign_claim(self):  # 28
        proc = self.run_cli("--owner", "bob", "claim", self.ann, "--holder", "agent:dsh",
                            "--json")
        err = self.error_json(proc)
        self.assertEqual(err["code"], "forbidden")
        self.assertIn("ann", err["message"])
        self.assertIn("owner=", err["hint"])

    def test_foreign_claim_text_has_no_traceback(self):  # 28
        proc = self.run_cli("--owner", "bob", "claim", self.ann, "--holder", "agent:dsh")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)
        lines = [line for line in proc.stderr.splitlines() if line.strip()]
        self.assertTrue(1 <= len(lines) <= 2, proc.stderr)
        self.assertTrue(lines[0].startswith("ошибка:"), proc.stderr)

    def test_own_claim(self):  # 28
        out = self.json_out(self.run_cli("claim", self.ann, "--owner", "ann",
                                         "--holder", "agent:dsh", "--json"))
        self.assertEqual(out["holder"], "agent:dsh")

    def test_list_filtered(self):  # 29
        free = self.new_task("--owner", "bob", "--json")["id"]
        self.json_out(self.run_cli("--owner", "bob", "set", free, "owner=", "--json"))
        res = self.json_out(self.run_cli("list", "--owner", "bob", "--json"))
        ids = {t["id"] for t in res["tasks"]}
        self.assertNotIn(self.ann, ids)
        self.assertIn(free, ids)

    def test_show_prints_owner(self):  # 30
        proc = self.run_cli("show", self.ann)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("владелец: ann", proc.stdout)
        cfg = self.tmp_path / "config-local.toml"
        cfg.write_text(LOCAL_CONFIG, encoding="utf-8")
        proc = self.run_cli("show", self.ann, config=cfg)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("владелец", proc.stdout)

    def test_set_owner(self):  # 31
        out = self.json_out(self.run_cli("set", self.ann, "owner=bob", "--json"))
        self.assertEqual(out["owner"], "bob")
        out = self.json_out(self.run_cli("set", self.ann, "owner=", "--json"))
        self.assertIsNone(out["owner"])

    def test_set_foreign_task(self):  # 31
        err = self.error_json(self.run_cli("--owner", "bob", "set", self.ann, "title=x",
                                           "--json"))
        self.assertEqual(err["code"], "forbidden")


class TestStatus(OwnerCliCase):
    """Пункт 32: режим и владелец в `listik status`."""

    def test_server_mode_without_owner(self):
        proc = self.run_cli("status")
        self.assertIn("режим: серверный", proc.stdout)
        self.assertIn("владелец: не задан", proc.stdout)

    def test_server_mode_with_owner(self):
        proc = self.run_cli("--owner", "ann", "status")
        self.assertIn("владелец: ann", proc.stdout)

    def test_json(self):
        proc = self.run_cli("--owner", "ann", "status", "--json")
        out = json.loads(proc.stdout)
        self.assertEqual(out["mode"], "server")
        self.assertEqual(out["owner"], "ann")

    def test_local_mode(self):
        cfg = self.tmp_path / "config-local.toml"
        cfg.write_text(LOCAL_CONFIG, encoding="utf-8")
        proc = self.run_cli("status", config=cfg)
        self.assertIn("режим: локальный", proc.stdout)
        out = json.loads(self.run_cli("status", "--json", config=cfg).stdout)
        self.assertEqual(out["mode"], "local")
        self.assertIsNone(out["owner"])


if __name__ == "__main__":
    unittest.main()
