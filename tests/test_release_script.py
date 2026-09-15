"""`scripts/release.sh` — сборка архива релиза (шаг 12, порция b).

Тест работает только во временных каталогах: в `tmp` создаётся маленький
git-репозиторий с тем же раскладом файлов, что и в настоящем дереве, туда
копируется настоящий скрипт, и все сценарии гоняются в нём. Настоящий
репозиторий не меняется — из него только читается сам скрипт.

`gh` подменяется sh-скриптом в начале `PATH`: он пишет argv в лог, поэтому
настоящий релиз не создаётся ни при каких условиях. Так же подменяется `git`,
чтобы убедиться, что тег и push скрипт не создаёт сам.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import textwrap
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release.sh"

VERSION = "1.2.3"
TOP = "listik-" + VERSION
ARCHIVE = TOP + ".tar.gz"

# Отслеживаемые файлы тестового репозитория — тот же белый список, что в настоящем.
TRACKED_FILES = {
    "VERSION": VERSION + "\n",
    "bin/listik": "#!/bin/sh\necho listik\n",
    "listik/__init__.py": '"""listik."""\n',
    "listik/paths.py": "HOME = 1\n",
    "alembic/env.py": "# env\n",
    "alembic.ini": "[alembic]\n",
    "routes.json": "{}\n",
    "config.example.toml": "[server]\n",
    "README.md": "# Listik\n",
    "docs/API.md": "# API\n",
    "AGENTS.md": "# AGENTS\n",
    "docs/harness-protocol.md": "# protocol\n",
    "docs/specs/x.md": "# spec\n",
    "tests/test_x.py": "# test\n",
    "plugins/p/a.md": "# plugin\n",
    ".claude-plugin/marketplace.json": "{}\n",
    "web/src/main.ts": "// main\n",
    ".gitignore": (
        "junk.txt\n"
        "listik/junk.py\n"
        ".env\n"
        "listik.db\n"
        "listik.pid\n"
        "logs/\n"
        "config.toml\n"
        "web/node_modules/\n"
        "web/dist/\n"
        # install.sh здесь затем, чтобы «неотслеживаемый install.sh» не делал
        # дерево грязным и проверялся именно гард про ls-files.
        "install.sh\n"
    ),
}

# Неотслеживаемые файлы: в архив не попадают ни в корне, ни во вложенных каталогах.
UNTRACKED_FILES = {
    "junk.txt": "junk\n",
    "listik/junk.py": "# не отслеживается\n",
    ".env": "TOKEN=junk\n",
    "listik.db": "",
    "listik.pid": "123\n",
    "logs/a.log": "log\n",
    "config.toml": "[server]\n",
    "web/node_modules/x/i.js": "//\n",
}

INSTALL_SH = "#!/bin/sh\necho install\n"

NEEDED_TOOLS = ("sh", "tar", "git")


@unittest.skipUnless(
    all(shutil.which(tool) for tool in NEEDED_TOOLS),
    "нужны sh, tar и git",
)
class ReleaseScriptTests(unittest.TestCase):
    """Сценарии `scripts/release.sh` во временном git-репозитории."""

    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory(prefix="listik-release-test-")
        self.addCleanup(tmpdir.cleanup)
        self.tmp = pathlib.Path(tmpdir.name)

    # --- вспомогательное -------------------------------------------------

    @staticmethod
    def write(root: pathlib.Path, rel: str, text: str) -> pathlib.Path:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def git(self, root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.fail(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result

    def make_repo(self, *, install_tracked: bool = False, with_web_dist: bool = True) -> pathlib.Path:
        """Временный git-репозиторий с настоящим scripts/release.sh внутри."""
        root = self.tmp / "repo"
        for rel, text in TRACKED_FILES.items():
            self.write(root, rel, text)
        for rel, text in UNTRACKED_FILES.items():
            self.write(root, rel, text)
        if with_web_dist:
            self.write(root, "web/dist/index.html", "<!doctype html>\n<title>listik</title>\n")
        if install_tracked:
            self.write(root, "install.sh", INSTALL_SH)
        # Нет scripts/release.sh — тест падает, а не пропускается.
        script = root / "scripts" / "release.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(RELEASE_SCRIPT, script)
        script.chmod(0o755)
        self.git(root, "init", "-q")
        self.git(root, "config", "user.name", "Listik Test")
        self.git(root, "config", "user.email", "listik@example.invalid")
        self.git(root, "add", "-A")
        if install_tracked:
            self.git(root, "add", "-f", "install.sh")
        self.git(root, "commit", "-q", "-m", "init")
        return root

    def run_release(self, root: pathlib.Path, *args: str, path_prefix=(), cwd=None):
        env = dict(os.environ)
        if path_prefix:
            env["PATH"] = os.pathsep.join([str(p) for p in path_prefix] + [env.get("PATH", "")])
        return subprocess.run(
            [str(root / "scripts" / "release.sh"), *args],
            cwd=str(cwd if cwd is not None else root),
            capture_output=True, text=True, env=env,
        )

    def fake_bin(self, name: str, body: str):
        """Каталог с фальшивой командой `name`; возвращает (каталог, файл-тело)."""
        bindir = self.tmp / f"fake-{name}"
        bindir.mkdir(exist_ok=True)
        script = bindir / name
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
        return bindir, script

    def fake_gh(self):
        """Фальшивый gh: пишет argv в <tmp>/gh.log и копирует файл заметок."""
        log = self.tmp / "gh.log"
        body = textwrap.dedent(f"""\
            #!/bin/sh
            log={shlex.quote(str(log))}
            for arg in "$@"; do
                printf '%s\\n' "$arg" >> "$log"
            done
            prev=""
            for arg in "$@"; do
                if [ "$prev" = "--notes-file" ]; then
                    cp "$arg" "$log.notes" 2>/dev/null || true
                fi
                prev=$arg
            done
            exit 0
            """)
        bindir, _ = self.fake_bin("gh", body)
        return bindir, log

    def fake_git(self):
        """Фальшивый git: пишет argv в <tmp>/git.log и передаёт настоящему git."""
        log = self.tmp / "git.log"
        real = shutil.which("git")
        body = textwrap.dedent(f"""\
            #!/bin/sh
            printf '%s\\n' "$*" >> {shlex.quote(str(log))}
            exec {shlex.quote(real)} "$@"
            """)
        bindir, _ = self.fake_bin("git", body)
        return bindir, log

    def git_invocations(self, log: pathlib.Path):
        """Аргументы вызовов git без `-C <каталог>`."""
        invocations = []
        for line in log.read_text(encoding="utf-8").splitlines():
            tokens = line.split()
            if tokens[:1] == ["-C"]:
                tokens = tokens[2:]
            invocations.append(tokens)
        return invocations

    def tar_names(self, archive: pathlib.Path):
        with tarfile.open(archive, "r:gz") as tar:
            return tar.getnames()

    def assert_in_archive(self, names, rel: str) -> None:
        self.assertIn(f"{TOP}/{rel}", names)

    def assert_not_in_archive(self, names, rel: str) -> None:
        prefix = f"{TOP}/{rel}"
        hits = [name for name in names if name == prefix or name.startswith(prefix + "/")]
        self.assertEqual([], hits, f"в архиве не должно быть {rel}: {hits}")

    def assert_gh_silent(self, log: pathlib.Path) -> None:
        self.assertFalse(log.exists() and log.read_text(encoding="utf-8"), "gh не должен вызываться")

    # --- сборка архива ---------------------------------------------------

    def test_skip_web_out_new_dir_and_archive_contents(self):
        root = self.make_repo()
        out = self.tmp / "new" / "out"
        result = self.run_release(root, "--skip-web", "--out", str(out))
        self.assertEqual(0, result.returncode, result.stderr)
        archive = out / ARCHIVE
        self.assertTrue(archive.is_file(), result.stdout + result.stderr)
        self.assertTrue((out / (ARCHIVE + ".sha256")).is_file())

        names = self.tar_names(archive)
        self.assertEqual({TOP}, {name.split("/", 1)[0] for name in names})
        self.assertEqual([], [name for name in names if "._" in name],
                         "в архиве не должно быть AppleDouble-мусора macOS")
        for rel in ("bin/listik", "listik/paths.py", "alembic/env.py", "alembic.ini", "VERSION",
                    "routes.json", "config.example.toml", "README.md", "docs/API.md", "AGENTS.md",
                    "docs/harness-protocol.md", "web/dist/index.html"):
            self.assert_in_archive(names, rel)
        for rel in ("tests", "docs/specs", "plugins", ".claude-plugin", "web/src",
                    "node_modules", "web/node_modules", "junk.txt", "listik/junk.py", ".env",
                    "listik.db", "listik.pid", "logs", "config.toml", "install.sh"):
            self.assert_not_in_archive(names, rel)

    def test_sha256_file_matches_hashlib(self):
        root = self.make_repo()
        out = self.tmp / "out"
        result = self.run_release(root, "--skip-web", "--out", str(out))
        self.assertEqual(0, result.returncode, result.stderr)
        archive = out / ARCHIVE
        line = (out / (ARCHIVE + ".sha256")).read_text(encoding="utf-8")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(f"{digest}  {ARCHIVE}\n", line)

    def test_default_out_is_repo_dist(self):
        root = self.make_repo()
        result = self.run_release(root, "--skip-web")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((root / "dist" / ARCHIVE).is_file())
        self.assertTrue((root / "dist" / (ARCHIVE + ".sha256")).is_file())

    def test_bad_version_fails(self):
        root = self.make_repo()
        self.write(root, "VERSION", "1.2\n")
        result = self.run_release(root, "--skip-web", "--out", str(self.tmp / "out"))
        self.assertNotEqual(0, result.returncode)
        self.assertTrue(result.stderr.strip())

    def test_skip_web_without_web_dist_fails(self):
        root = self.make_repo(with_web_dist=False)
        result = self.run_release(root, "--skip-web", "--out", str(self.tmp / "out"))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("npm run build", result.stderr)

    def test_out_pointing_to_file_fails(self):
        root = self.make_repo()
        occupied = self.write(self.tmp, "occupied", "не каталог\n")
        result = self.run_release(root, "--skip-web", "--out", str(occupied))
        self.assertNotEqual(0, result.returncode)
        self.assertTrue(result.stderr.strip())

    def test_help_lists_flags(self):
        root = self.make_repo()
        result = self.run_release(root, "--help")
        self.assertEqual(0, result.returncode, result.stderr)
        for flag in ("--skip-web", "--out", "--publish", "--notes-file"):
            self.assertIn(flag, result.stdout)

    # --- публикация ------------------------------------------------------

    def test_without_publish_gh_is_not_called(self):
        root = self.make_repo()
        gh_bin, gh_log = self.fake_gh()
        out = self.tmp / "out"
        result = self.run_release(root, "--skip-web", "--out", str(out), path_prefix=[gh_bin])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((out / ARCHIVE).is_file())
        self.assert_gh_silent(gh_log)

    def test_publish_with_untracked_install_sh_fails(self):
        root = self.make_repo()
        self.write(root, "install.sh", INSTALL_SH)  # не в git (и в .gitignore)
        gh_bin, gh_log = self.fake_gh()
        result = self.run_release(root, "--publish", "--skip-web",
                                  "--out", str(self.tmp / "out"), path_prefix=[gh_bin])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("install.sh нет в дереве", result.stderr)
        self.assert_gh_silent(gh_log)

    def test_publish_with_dirty_tree_fails(self):
        root = self.make_repo(install_tracked=True)
        readme = root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\nгрязь\n", encoding="utf-8")
        gh_bin, gh_log = self.fake_gh()
        out = self.tmp / "out"
        result = self.run_release(root, "--publish", "--skip-web", "--out", str(out),
                                  path_prefix=[gh_bin])
        self.assertNotEqual(0, result.returncode)
        self.assert_gh_silent(gh_log)
        self.assertFalse((out / ARCHIVE).exists(), "до публикации архив не создаётся")

    def test_publish_with_existing_tag_fails(self):
        root = self.make_repo(install_tracked=True)
        self.git(root, "tag", f"v{VERSION}")
        gh_bin, gh_log = self.fake_gh()
        result = self.run_release(root, "--publish", "--skip-web",
                                  "--out", str(self.tmp / "out"), path_prefix=[gh_bin])
        self.assertNotEqual(0, result.returncode)
        self.assert_gh_silent(gh_log)

    def test_publish_success_invokes_gh_once(self):
        root = self.make_repo(install_tracked=True)
        out = self.tmp / "out"
        gh_bin, gh_log = self.fake_gh()
        git_bin, git_log = self.fake_git()
        result = self.run_release(root, "--publish", "--skip-web", "--out", str(out),
                                  path_prefix=[gh_bin, git_bin])
        self.assertEqual(0, result.returncode, result.stderr)

        archive = out / ARCHIVE
        sum_file = out / (ARCHIVE + ".sha256")
        self.assertTrue(archive.is_file())
        self.assertTrue(sum_file.is_file())

        lines = gh_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual([
            "release", "create", f"v{VERSION}",
            str(archive), str(sum_file), str(root / "install.sh"),
            "--title", f"v{VERSION}",
            "--notes-file",
        ], lines[:9])
        self.assertEqual(10, len(lines), f"ровно один вызов gh: {lines}")
        self.assertEqual(f"Listik v{VERSION}\n",
                         pathlib.Path(str(gh_log) + ".notes").read_text(encoding="utf-8"))

        for tokens in self.git_invocations(git_log):
            self.assertNotIn("push", tokens)
            if tokens[:1] == ["tag"]:
                self.assertIn("-l", tokens, f"скрипт может только смотреть теги: {tokens}")

    def test_script_is_posix_sh(self):
        result = subprocess.run(["/bin/sh", "-n", str(RELEASE_SCRIPT)],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(RELEASE_SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n"))


if __name__ == "__main__":
    unittest.main()
