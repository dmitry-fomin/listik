"""Рой живёт в процессе сервера и слушает [swarm] enabled."""
from __future__ import annotations

import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

from listik import paths
from listik import swarm_proc


class _Proc:
    def __init__(self, pid: int, *, dead: bool = False):
        self.pid = pid
        self.returncode = 1 if dead else None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminated = True
        self.returncode = -9

    def wait(self, timeout: float | None = None):
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode


class BuildArgvTests(unittest.TestCase):
    def test_argv_is_all_projects_every_30s(self) -> None:
        argv = swarm_proc.build_argv("127.0.0.1", 8787)
        if argv is None:
            self.skipTest("нет node в PATH")
        self.assertNotIn("--project", argv)
        self.assertIn("--interval", argv)
        self.assertEqual(argv[argv.index("--interval") + 1], "30")
        self.assertEqual(argv[argv.index("--listik-host") + 1], "127.0.0.1")
        self.assertEqual(argv[argv.index("--listik-port") + 1], "8787")
        self.assertTrue(argv[1].endswith("bin/listik-swarm"))


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = pathlib.Path(self.tmp.name) / "config.toml"
        self.config_path.write_text("[auth]\ntoken = \"t\"\n", encoding="utf-8")
        patch = mock.patch.object(paths, "CONFIG_PATH", self.config_path)
        patch.start()
        self.addCleanup(patch.stop)
        self.spawned: list[_Proc] = []
        self.argv: list[list[str]] = []

    def _popen(self, argv, **_kwargs):
        self.argv.append(list(argv))
        proc = _Proc(pid=41000 + len(self.spawned))
        self.spawned.append(proc)
        return proc

    def _supervisor(self, **kwargs) -> swarm_proc.Supervisor:
        return swarm_proc.Supervisor(
            "127.0.0.1", 9, popen=self._popen, argv=lambda: ["node", "swarm"],
            poll_seconds=0.05, restart_seconds=0.05, busy_seconds=0.05,
            log=lambda _text: None, **kwargs,
        )

    def test_off_does_not_spawn(self) -> None:
        sup = self._supervisor()
        sup.start()
        self.addCleanup(sup.stop)
        time.sleep(0.15)
        self.assertEqual(self.spawned, [])
        self.assertFalse(swarm_proc.runtime()["running"])

    def test_on_spawns_and_off_stops(self) -> None:
        self.config_path.write_text("[swarm]\nenabled = true\n", encoding="utf-8")
        sup = self._supervisor()
        sup.start()
        self.addCleanup(sup.stop)
        deadline = time.monotonic() + 2
        while not self.spawned and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(self.argv[0][:2], ["node", "swarm"])
        self.assertTrue(sup.running())
        self.config_path.write_text("[swarm]\nenabled = false\n", encoding="utf-8")
        deadline = time.monotonic() + 2
        while not self.spawned[0].terminated and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.spawned[0].terminated)

    def test_exit_restarts(self) -> None:
        self.config_path.write_text("[swarm]\nenabled = true\n", encoding="utf-8")

        def popen(argv, **_kwargs):
            self.argv.append(list(argv))
            proc = _Proc(pid=42000 + len(self.spawned), dead=True)
            self.spawned.append(proc)
            return proc

        sup = swarm_proc.Supervisor(
            "127.0.0.1", 9, popen=popen, argv=lambda: ["node", "swarm"],
            poll_seconds=0.05, restart_seconds=0.05, busy_seconds=0.05,
            log=lambda _text: None,
        )
        sup.start()
        self.addCleanup(sup.stop)
        deadline = time.monotonic() + 2
        while len(self.spawned) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertGreaterEqual(len(self.spawned), 2)

    def test_busy_lock_is_logged_once(self) -> None:
        self.config_path.write_text("[swarm]\nenabled = true\n", encoding="utf-8")
        logs: list[str] = []

        def popen(argv, **_kwargs):
            self.argv.append(list(argv))
            proc = _Proc(pid=43000 + len(self.spawned), dead=True)
            proc.returncode = 2
            self.spawned.append(proc)
            return proc

        sup = swarm_proc.Supervisor(
            "127.0.0.1", 9, popen=popen, argv=lambda: ["node", "swarm"],
            poll_seconds=0.05, restart_seconds=0.05, busy_seconds=0.05,
            log=logs.append,
        )
        sup.start()
        self.addCleanup(sup.stop)
        deadline = time.monotonic() + 2
        while len(self.spawned) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertGreaterEqual(len(self.spawned), 3)
        self.assertEqual(logs.count("уже запущен другим процессом, подожду"), 1)


class RuntimePidTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = pathlib.Path(self.tmp.name)
        self.config_path = self.tmp_path / "config.toml"
        self.config_path.write_text("[swarm]\nenabled = true\n", encoding="utf-8")
        self.addCleanup(setattr, swarm_proc, "_current", swarm_proc._current)
        for patch in (
            mock.patch.object(paths, "CONFIG_PATH", self.config_path),
            mock.patch.object(paths, "LOGS_DIR", self.tmp_path),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_supervisor_does_not_claim_a_foreign_pid(self) -> None:
        (self.tmp_path / "swarm.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

        class Idle:
            def running(self) -> bool:
                return False

            def pid(self) -> None:
                return None

        swarm_proc._current = Idle()
        info = swarm_proc.runtime()
        self.assertFalse(info["running"])
        self.assertIsNone(info["pid"])

        swarm_proc._current = None
        info = swarm_proc.runtime()
        self.assertTrue(info["running"])
        self.assertEqual(info["pid"], os.getpid())


if __name__ == "__main__":
    unittest.main()
