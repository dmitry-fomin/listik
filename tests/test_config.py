"""Тесты записи config.toml: файл, созданный CLI, обязан быть валидным TOML.

Регрессия listik-17mh: `listik status`/`show` в worktree без своего config.toml
создавали файл с голыми ключами вида `s1-spec:s2-review`; tomllib такой файл не
читает, и после этого падал весь CLI и `python3 -m unittest discover tests`.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

from listik import assistant as assistant_mod
from listik import config as config_mod
from listik import paths
from listik import store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


def free_port() -> int:
    """Свободный порт, чтобы CLI в тесте не постучался в живой сервер Listik."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class DumpTests(unittest.TestCase):
    def test_dump_of_defaults_is_valid_toml(self) -> None:
        text = config_mod._dump(config_mod.DEFAULTS)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed, config_mod.DEFAULTS)

    def test_transition_keys_are_quoted(self) -> None:
        text = config_mod._dump(config_mod.DEFAULTS)
        self.assertIn('"s1-spec:s2-review" = "sticky"', text)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["routing"]["transitions"],
                         config_mod.DEFAULTS["routing"]["transitions"])

    def test_missing_config_dump_roundtrips(self) -> None:
        """load() без файла отдаёт DEFAULTS — их дамп тоже должен читаться."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config_mod.load(pathlib.Path(tmp) / "missing.toml")
            self.assertEqual(tomllib.loads(config_mod._dump(cfg)), cfg)

    def test_awkward_keys_roundtrip(self) -> None:
        cfg = copy.deepcopy(config_mod.DEFAULTS)
        cfg["routing"]["projects"] = {
            "demo": {"harnesses": {"s3-impl": ["dsh"]}},
            "Zoloto585/repo": {"transitions": {"s1-spec:s2-review": "handoff"}},
            "my.project": {"return_window_hours": 1},
            "проект с пробелом": {"default_process": ["s1-spec"]},
        }
        parsed = tomllib.loads(config_mod._dump(cfg))
        self.assertEqual(parsed, cfg)
        # Ни один slug не должен «распасться» на вложенные таблицы.
        self.assertEqual(set(parsed["routing"]["projects"]),
                         {"demo", "Zoloto585/repo", "my.project", "проект с пробелом"})

    def test_root_scalars_stay_at_root(self) -> None:
        cfg = {"routing": {"harnesses": {"s3-impl": ["dsh"]}}, "version": 3}
        parsed = tomllib.loads(config_mod._dump(cfg))
        self.assertEqual(parsed, cfg)

    def test_scalar_values_keep_types(self) -> None:
        cfg = {"s": "текст", "i": 7, "f": 1.5, "b": True, "n": False,
               "lst": ["a", 1, True], "quoted": 'he said "hi"'}
        self.assertEqual(tomllib.loads(config_mod._dump(cfg)), cfg)


class SaveLoadTests(unittest.TestCase):
    def test_save_load_roundtrip_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            cfg = copy.deepcopy(config_mod.DEFAULTS)
            cfg["auth"]["token"] = "secret"
            cfg["routing"]["projects"] = {"Zoloto585/repo": {"harnesses": {"s3-impl": ["dsh"]}}}
            config_mod.save(cfg, path)
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            self.assertEqual(config_mod.load(path), cfg)
            # Временных файлов от атомарной записи не остаётся.
            self.assertEqual([p.name for p in pathlib.Path(tmp).iterdir()], ["config.toml"])

    def test_save_through_symlink_keeps_the_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = pathlib.Path(tmp) / "real.toml"
            real.write_text('[auth]\ntoken = "old"\n', encoding="utf-8")
            link = pathlib.Path(tmp) / "config.toml"
            link.symlink_to(real)
            config_mod.save({"auth": {"token": "new"}}, link)
            self.assertTrue(link.is_symlink())
            self.assertEqual(tomllib.loads(real.read_text(encoding="utf-8")),
                             {"auth": {"token": "new"}})

    def test_ensure_token_creates_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            with mock.patch.object(paths, "CONFIG_PATH", path):
                cfg, token = config_mod.ensure_token()
                self.assertTrue(token)
                self.assertEqual(cfg["auth"]["token"], token)
                parsed = tomllib.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(parsed["auth"]["token"], token)
                self.assertEqual(parsed["routing"], config_mod.DEFAULTS["routing"])

                # Повторный вызов не перегенерирует токен и не переписывает файл.
                before = path.read_text(encoding="utf-8")
                _, token2 = config_mod.ensure_token()
                self.assertEqual(token2, token)
                self.assertEqual(path.read_text(encoding="utf-8"), before)


class WorktreeCliRegressionTests(TempDbTestCase):
    """CLI в каталоге без config.toml создаёт валидный конфиг, а не ломает чтение."""

    def _cli(self, *args: str, config_path: pathlib.Path) -> subprocess.CompletedProcess:
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_CONFIG": str(config_path)}
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), *args],
            capture_output=True, text=True, env=env, cwd=str(LISTIK_BIN.parent.parent),
        )

    def test_status_creates_parsable_config_and_show_still_works(self) -> None:
        config_path = self.tmp_path / "config.toml"
        self.assertFalse(config_path.exists())

        p = self._cli("--port", str(free_port()), "status", config_path=config_path)
        # Сервера нет — команда честно говорит об этом, но конфиг обязан быть валидным.
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
        self.assertTrue(parsed["auth"]["token"])
        self.assertEqual(set(parsed["routing"]["transitions"]),
                         set(config_mod.DEFAULTS["routing"]["transitions"]))

        # Раньше следующая же команда падала на tomllib при чтении этого файла.
        task = store.create_task(self.conn, title="проба", project="listik")
        p2 = self._cli("--local", "show", task["id"], "--json", config_path=config_path)
        self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)
        self.assertEqual(json.loads(p2.stdout)["id"], task["id"])

    def test_ensure_token_preserves_existing_routing(self) -> None:
        config_path = self.tmp_path / "config.toml"
        config_path.write_text(
            '[routing.transitions]\n"s1-spec:s2-review" = "handoff"\n',
            encoding="utf-8",
        )
        with mock.patch.object(paths, "CONFIG_PATH", config_path):
            _, token = config_mod.ensure_token()
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["auth"]["token"], token)
        self.assertEqual(parsed["routing"]["transitions"]["s1-spec:s2-review"], "handoff")


class AssistantSectionTests(unittest.TestCase):
    """[assistant] — пользовательская секция: запись конфига её не выдумывает.

    Регрессия listik-odxq: `assistant` лежал в DEFAULTS, и `ensure_token` дописывал
    в чужой config.toml `[assistant]` с пустым api_key. Дефолты помощника живут
    в `assistant.settings()`.
    """

    def test_ensure_token_does_not_create_assistant_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            with mock.patch.object(paths, "CONFIG_PATH", path):
                config_mod.ensure_token()
            text = path.read_text(encoding="utf-8")
            parsed = tomllib.loads(text)
            self.assertNotIn("assistant", parsed)
            self.assertNotIn("api_key", text)

            # Помощник при этом остаётся настроенным по умолчанию.
            settings = assistant_mod.settings(parsed)
            self.assertEqual(settings["api_key"], "")
            self.assertEqual(settings["base_url"], assistant_mod.DEFAULT_BASE_URL)
            self.assertEqual(settings["model"], assistant_mod.DEFAULT_MODEL)
            self.assertFalse(assistant_mod.status(parsed)["enabled"])

    def test_ensure_token_preserves_existing_assistant_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            path.write_text('[assistant]\napi_key = "user-secret-key"\n'
                            'model = "deepseek-chat"\n', encoding="utf-8")
            with mock.patch.object(paths, "CONFIG_PATH", path):
                _, token = config_mod.ensure_token()
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["assistant"],
                             {"api_key": "user-secret-key", "model": "deepseek-chat"})
            self.assertEqual(parsed["auth"]["token"], token)
            settings = assistant_mod.settings(parsed)
            self.assertEqual(settings["api_key"], "user-secret-key")
            self.assertEqual(settings["model"], "deepseek-chat")
            self.assertTrue(assistant_mod.status(parsed)["enabled"])


class DefaultsIsolationTests(unittest.TestCase):
    """load() не должен отдавать вложенные словари DEFAULTS по ссылке."""

    def test_load_result_is_detached_from_defaults(self) -> None:
        before = copy.deepcopy(config_mod.DEFAULTS)
        cfg = config_mod.load(pathlib.Path("/nonexistent-listik-config.toml"))
        self.assertIsNot(cfg["auth"], config_mod.DEFAULTS["auth"])
        cfg["auth"]["token"] = "leaked"
        cfg["routing"]["harnesses"]["s3-impl"].append("leaked")
        self.assertEqual(config_mod.DEFAULTS, before)
        self.assertEqual(config_mod.DEFAULTS["auth"]["token"], "")

    def test_ensure_token_does_not_pollute_defaults(self) -> None:
        before = copy.deepcopy(config_mod.DEFAULTS)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(paths, "CONFIG_PATH", pathlib.Path(tmp) / "config.toml"):
                config_mod.ensure_token()
        self.assertEqual(config_mod.DEFAULTS, before)


if __name__ == "__main__":
    unittest.main()
