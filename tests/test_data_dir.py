"""Шаг 12, порция a: каталог данных (`LISTIK_HOME`) отдельно от каталога кода и файл VERSION.

`listik/paths.py` читает переменные окружения при импорте, поэтому пути проверяются
в подпроцессе `python3 -c` с очищенным от `LISTIK_*` окружением. Настоящие `listik.db`,
`config.toml` и `listik.log` репозитория тесты не трогают: все данные — во временных
каталогах, каталог кода — сам репозиторий.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"
PYTHON = sys.executable

#: Пути, которые обязаны считаться от каталога данных, и пути, которые остаются от кода.
DATA_KEYS = ("DB_PATH", "CONFIG_PATH", "LOG_PATH", "PID_PATH", "LOGS_DIR")
CODE_KEYS = ("WEB_DIR", "SOURCE_PATH", "ICONS_PATH")

PATHS_SCRIPT = """
import json
from listik import launcher, paths, routes, server

info = server.runtime_info()
print(json.dumps({
    "ROOT_DIR": str(paths.ROOT_DIR),
    "DATA_DIR": str(paths.DATA_DIR),
    "DB_PATH": str(paths.DB_PATH),
    "CONFIG_PATH": str(paths.CONFIG_PATH),
    "LOG_PATH": str(paths.LOG_PATH),
    "PID_PATH": str(paths.PID_PATH),
    "LOGS_DIR": str(paths.LOGS_DIR),
    "WEB_DIR": str(paths.WEB_DIR),
    "SOURCE_PATH": str(routes.SOURCE_PATH),
    "ICONS_PATH": str(routes.ICONS_PATH),
    "RUNTIME_PATH": str(routes.RUNTIME_PATH),
    "pid_file": str(server.pid_file()),
    "code_dir": info["code_dir"],
    "data_dir": info["data_dir"],
    "runtime_keys": sorted(info),
}))
"""

#: Минимальный импорт: проверяем, что один импорт `paths` ничего не создаёт на диске.
IMPORT_SCRIPT = "import listik.paths\n"

LAUNCHER_SCRIPT = """
import json
import sys
from pathlib import Path
from listik import db, launcher, paths, routes, store

tmp = Path(sys.argv[1])
captured = {}


def fake_popen(argv, **kwargs):
    stdout = kwargs.get("stdout")
    captured["log"] = getattr(stdout, "name", None)
    raise OSError("тест: процесс не запускаем")


launcher.subprocess.Popen = fake_popen
source = tmp / "routes-source.json"
source.write_text(json.dumps({"version": 1, "routes": [
    {"key": "probe", "kind": "direct", "harness": "dsh", "title": "проба",
     "hint": "", "visible": True, "command": ["/bin/true"]}]}, ensure_ascii=False),
    encoding="utf-8")
routes.init_at_startup(source=source, target=tmp / "runtime" / "routes.json")
work = tmp / "work"
work.mkdir(exist_ok=True)
conn = db.init(paths.DB_PATH)
store.upsert_project(conn, "probe", title="probe", path=str(work))
task = store.create_task(conn, title="проба", project="probe", route="probe")
launcher.start(conn, task["id"])
print(json.dumps({"log": captured.get("log"), "logs_dir": str(paths.LOGS_DIR)}))
"""

CONFIG_SCRIPT = """
import json
from listik import config, paths

print(json.dumps({"saved": str(config.save(config.load())),
                  "config_path": str(paths.CONFIG_PATH)}))
"""

#: `log_traceback` из `bin/listik`: пишет трейсбек в LOG_PATH и глотает OSError.
LOG_SCRIPT = """
import importlib.machinery
import importlib.util
import json
import sys
from listik import paths

loader = importlib.machinery.SourceFileLoader("listik_cli_data_dir", sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
module.log_traceback(ValueError("тест: непойманное исключение"))
print(json.dumps({"log_path": str(paths.LOG_PATH)}))
"""

#: Пункт 4: живой pid в `<LISTIK_HOME>/listik.pid` относится только к базе установки.
RESTORE_SCRIPT = """
import json
import os
import sys
from pathlib import Path
from listik import backup, db, errors, paths

paths.PID_PATH.parent.mkdir(parents=True, exist_ok=True)
paths.PID_PATH.write_text(str(os.getpid()))
db.init(paths.DB_PATH)
copy = backup.backup()["backup"]


def outcome(target):
    try:
        backup.restore(copy, target, force=True)
        return "восстановил"
    except errors.ListikError as exc:
        return exc.code


foreign = Path(sys.argv[1]) / "other" / "listik.db"
foreign.parent.mkdir(parents=True, exist_ok=True)
print(json.dumps({"install": outcome(paths.DB_PATH), "foreign": outcome(foreign),
                  "db": str(paths.DB_PATH), "pid_path": str(paths.PID_PATH)}))
"""


def clean_env(**overrides: str) -> dict:
    """Окружение без `LISTIK_*`: значения задаются только явно, каждым тестом."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("LISTIK_")}
    env["PYTHONPATH"] = str(REPO_DIR)
    env.update(overrides)
    return env


def run_python(code: str, *, env: dict, cwd: pathlib.Path, args: tuple = ()) -> subprocess.CompletedProcess:
    return subprocess.run([PYTHON, "-c", code, *args], env=env, cwd=str(cwd),
                          capture_output=True, text=True, timeout=90)


def run_cli(args: list[str], *, env: dict, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run([PYTHON, str(LISTIK_BIN), *args], env=env, cwd=str(cwd),
                          capture_output=True, text=True, timeout=90)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def paths_of(env: dict, cwd: pathlib.Path) -> dict:
    result = run_python(PATHS_SCRIPT, env=env, cwd=cwd)
    if result.returncode != 0:
        raise AssertionError(f"подпроцесс упал: {result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


class DataDirPathsTests(unittest.TestCase):
    """Пункты 1–3: каталог данных, pid-файл, каталог логов и неприкосновенный код."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)
        self.repo = str(REPO_DIR)

    def test_without_home_all_paths_are_in_repo_root(self) -> None:
        for value in (None, ""):
            with self.subTest(home=value):
                env = clean_env(**({} if value is None else {"LISTIK_HOME": value}))
                data = paths_of(env, self.tmp_path)
                self.assertEqual(data["DATA_DIR"], self.repo)
                for key in DATA_KEYS:
                    self.assertEqual(data[key], str(REPO_DIR / {"DB_PATH": "listik.db",
                                                                "CONFIG_PATH": "config.toml",
                                                                "LOG_PATH": "listik.log",
                                                                "PID_PATH": "listik.pid",
                                                                "LOGS_DIR": "logs"}[key]), key)

    def test_home_moves_only_data_paths(self) -> None:
        home = self.tmp_path / "data"
        data = paths_of(clean_env(LISTIK_HOME=str(home)), self.tmp_path)
        self.assertEqual(data["DATA_DIR"], str(home))
        self.assertEqual(data["DB_PATH"], str(home / "listik.db"))
        self.assertEqual(data["CONFIG_PATH"], str(home / "config.toml"))
        self.assertEqual(data["LOG_PATH"], str(home / "listik.log"))
        self.assertEqual(data["PID_PATH"], str(home / "listik.pid"))
        self.assertEqual(data["LOGS_DIR"], str(home / "logs"))
        self.assertEqual(data["pid_file"], str(home / "listik.pid"))
        # Код остаётся в репозитории: доска, routes.json и протокол migrate.
        self.assertEqual(data["ROOT_DIR"], self.repo)
        self.assertEqual(data["WEB_DIR"], str(REPO_DIR / "web"))
        self.assertEqual(data["SOURCE_PATH"], str(REPO_DIR / "routes.json"))
        self.assertEqual(data["ICONS_PATH"], str(REPO_DIR / "web" / "src" / "lib" / "icons.ts"))
        self.assertEqual(data["code_dir"], self.repo)
        self.assertEqual(data["data_dir"], str(home))
        self.assertEqual(data["runtime_keys"],
                         ["code_dir", "cwd", "data_dir", "main_repo", "warning", "worktree"])

    def test_tilde_home_is_expanded(self) -> None:
        home = self.tmp_path / "home"
        home.mkdir()
        data = paths_of(clean_env(LISTIK_HOME="~/x", HOME=str(home)), self.tmp_path)
        self.assertEqual(data["DATA_DIR"], str(home / "x"))

    def test_explicit_paths_win_over_home(self) -> None:
        home = self.tmp_path / "data"
        env = clean_env(LISTIK_HOME=str(home),
                        LISTIK_DB=str(self.tmp_path / "своя.db"),
                        LISTIK_CONFIG=str(self.tmp_path / "свой.toml"),
                        LISTIK_LOG=str(self.tmp_path / "свой.log"))
        data = paths_of(env, self.tmp_path)
        self.assertEqual(data["DB_PATH"], str(self.tmp_path / "своя.db"))
        self.assertEqual(data["CONFIG_PATH"], str(self.tmp_path / "свой.toml"))
        self.assertEqual(data["LOG_PATH"], str(self.tmp_path / "свой.log"))
        # pid-файл и logs/ своих переменных не имеют — они всегда в каталоге данных.
        self.assertEqual(data["PID_PATH"], str(home / "listik.pid"))
        self.assertEqual(data["LOGS_DIR"], str(home / "logs"))

    def test_import_does_not_create_data_dir(self) -> None:
        home = self.tmp_path / "nope"
        result = run_python(IMPORT_SCRIPT, env=clean_env(LISTIK_HOME=str(home)),
                            cwd=self.tmp_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(home.exists(), "импорт listik.paths создал каталог данных")

    def test_launcher_default_log_dir_is_data_dir(self) -> None:
        home = self.tmp_path / "data"
        result = run_python(LAUNCHER_SCRIPT, env=clean_env(LISTIK_HOME=str(home)),
                            cwd=self.tmp_path, args=(str(self.tmp_path),))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(data["logs_dir"], str(home / "logs"))
        self.assertIsNotNone(data["log"], "launcher не открыл лог")
        self.assertEqual(pathlib.Path(data["log"]).parent, home / "logs")


class DataDirWritesTests(unittest.TestCase):
    """Пункт 5: каталог данных создаётся при записи, а не при импорте."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)

    def _git_status(self) -> str:
        result = subprocess.run(["git", "-C", str(REPO_DIR), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=30)
        return result.stdout

    def test_local_init_creates_db_in_new_home(self) -> None:
        home = self.tmp_path / "new"
        before = self._git_status()
        result = run_cli(["--local", "init"], env=clean_env(LISTIK_HOME=str(home)),
                         cwd=self.tmp_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((home / "listik.db").is_file(), result.stdout)
        self.assertEqual(before, self._git_status(),
                         "init создал файлы вне каталога данных")

    def test_config_save_creates_home(self) -> None:
        home = self.tmp_path / "new2"
        result = run_python(CONFIG_SCRIPT, env=clean_env(LISTIK_HOME=str(home)),
                            cwd=self.tmp_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(data["config_path"], str(home / "config.toml"))
        self.assertEqual(data["saved"], str(home / "config.toml"))
        self.assertTrue((home / "config.toml").is_file())

    def test_log_traceback_creates_home(self) -> None:
        home = self.tmp_path / "new3"
        result = run_python(LOG_SCRIPT, env=clean_env(LISTIK_HOME=str(home)),
                            cwd=self.tmp_path, args=(str(LISTIK_BIN),))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        log = pathlib.Path(data["log_path"])
        self.assertEqual(log, home / "listik.log")
        self.assertTrue(log.is_file(), "log_traceback не создал listik.log")
        self.assertIn("тест: непойманное исключение", log.read_text(encoding="utf-8"))

    def test_status_prints_data_dir_without_server(self) -> None:
        home = self.tmp_path / "data"
        home.mkdir()
        port = free_port()
        env = clean_env(LISTIK_HOME=str(home),
                        LISTIK_CONFIG=str(home / "config.toml"))
        (home / "config.toml").write_text(
            f'[server]\nhost = "127.0.0.1"\nport = {port}\n', encoding="utf-8")
        # --port — глобальный флаг CLI, поэтому он идёт до подкоманды.
        result = run_cli(["--port", str(port), "status"], env=env, cwd=self.tmp_path)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"каталог данных: {home}", result.stdout)
        self.assertIn("сервер: не отвечает", result.stdout)

    def test_restore_anchor_is_install_db(self) -> None:
        home = self.tmp_path / "home"
        home.mkdir()
        (home / "config.toml").write_text(
            f'[server]\nhost = "127.0.0.1"\nport = {free_port()}\n', encoding="utf-8")
        result = run_python(RESTORE_SCRIPT, env=clean_env(LISTIK_HOME=str(home)),
                            cwd=self.tmp_path, args=(str(self.tmp_path),))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(data["db"], str(home / "listik.db"))
        self.assertEqual(data["pid_path"], str(home / "listik.pid"))
        self.assertEqual(data["install"], "conflict", "restore в базу установки не отказал")
        self.assertEqual(data["foreign"], "восстановил",
                         "restore во временную базу отказал из-за чужого pid-файла")


class VersionTests(unittest.TestCase):
    """Пункты 6 и 9: версия из файла VERSION, в том числе без файла."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)

    def test_version_file_and_cli_output(self) -> None:
        self.assertEqual((REPO_DIR / "VERSION").read_text(encoding="utf-8").strip(), "0.5.0")
        result = run_cli(["--version"], env=clean_env(), cwd=self.tmp_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "listik 0.5.0")

    def test_copy_without_version_file(self) -> None:
        copy = self.tmp_path / "без-версии"
        copy.mkdir()
        self._copy_code(copy)
        result = run_python("import listik; print(listik.__version__)", env=clean_env(
            PYTHONPATH=str(copy)), cwd=copy)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0+unknown")

    def test_copy_with_other_version_file(self) -> None:
        copy = self.tmp_path / "с-версией"
        copy.mkdir()
        self._copy_code(copy)
        (copy / "VERSION").write_text("  9.9.9  \n", encoding="utf-8")
        result = subprocess.run([PYTHON, str(copy / "bin" / "listik"), "--version"],
                                env=clean_env(PYTHONPATH=str(copy)), cwd=str(copy),
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "listik 9.9.9")

    def _copy_code(self, target: pathlib.Path) -> None:
        shutil.copytree(REPO_DIR / "listik", target / "listik",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (target / "bin").mkdir()
        shutil.copy2(LISTIK_BIN, target / "bin" / "listik")


class DocumentationTests(unittest.TestCase):
    """Пункт 8: `data_dir` в docs/API.md и пункт про LISTIK_HOME в README."""

    def test_api_documents_data_dir(self) -> None:
        api = (REPO_DIR / "docs/API.md").read_text(encoding="utf-8")
        row = next(line for line in api.splitlines() if line.startswith("| GET | `/api/health`"))
        runtime = row.split("runtime{", 1)[1].split("}", 1)[0]
        self.assertIn("data_dir", runtime)
        self.assertIn("LISTIK_HOME", row)

    def test_readme_documents_listik_home(self) -> None:
        text = (REPO_DIR / "README.md").read_text(encoding="utf-8")
        section = text.split("## Установка и запуск", 1)[1].split("\n## ", 1)[0]
        self.assertIn("LISTIK_HOME", section)
        for name in ("LISTIK_DB", "LISTIK_CONFIG", "LISTIK_LOG"):
            self.assertIn(name, section)


if __name__ == "__main__":
    unittest.main()
