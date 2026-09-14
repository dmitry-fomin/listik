"""listik-a7pw: `serve` без --daemon после SIGTERM удаляет pid-файл.

Сервер поднимается в подпроцессе со своим ROOT_DIR (pid-файл), базой, конфигом и логом во
временном каталоге и на свободном порту; настоящий сервер и listik.pid репозитория не трогаются.
"""
from __future__ import annotations

import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent

SCRIPT = """
import pathlib, sys
from listik import paths, server
paths.ROOT_DIR = pathlib.Path(sys.argv[1])
server.serve(host="127.0.0.1", port=int(sys.argv[2]), quiet=True, no_embed=True)
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServeSigtermTest(unittest.TestCase):
    def test_sigterm_removes_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            pid_path = tmp_path / "listik.pid"
            env = dict(os.environ,
                       LISTIK_DB=str(tmp_path / "listik.db"),
                       LISTIK_CONFIG=str(tmp_path / "config.toml"),
                       LISTIK_LOG=str(tmp_path / "listik.log"),
                       PYTHONPATH=str(REPO_DIR))
            proc = subprocess.Popen([sys.executable, "-c", SCRIPT, tmp, str(_free_port())],
                                    cwd=tmp, env=env, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 20
                while not pid_path.exists():
                    if proc.poll() is not None:
                        self.fail("serve завершился до старта: " + proc.stderr.read().decode())
                    if time.monotonic() > deadline:
                        self.fail("pid-файл не появился")
                    time.sleep(0.1)
                self.assertEqual(pid_path.read_text().strip(), str(proc.pid))
                proc.send_signal(signal.SIGTERM)
                code = proc.wait(timeout=15)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            self.assertEqual(code, 0, proc.stderr.read().decode())
            self.assertFalse(pid_path.exists(), "listik.pid остался после SIGTERM")


if __name__ == "__main__":
    unittest.main()
