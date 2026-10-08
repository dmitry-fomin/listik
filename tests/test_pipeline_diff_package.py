"""Блок «Пакет диффа для приёмки» из pipeline-core.md исполняется на временном репозитории."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "plugins/pipeline-core/references/pipeline-core.md"
DUMP_REL = "pipeline-core/t.diff-a.r1.txt"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def core_block():
    text = CORE.read_text(encoding="utf-8")
    head = "## Пакет диффа для приёмки"
    if head not in text:
        raise AssertionError(f"в {CORE} нет раздела {head!r}")
    lines, block, inside = text.split(head, 1)[1].splitlines(), None, None
    for line in lines:
        if line.startswith("```"):
            if inside is None:
                inside = []
            else:
                if any("add -A -N" in s for s in inside):
                    block = "\n".join(inside)
                    break
                inside = None
        elif inside is not None:
            inside.append(line)
    if block is None:
        raise AssertionError(f"в разделе {head!r} нет fenced-блока с `add -A -N`")
    for k, v in {"<steps>": "docs/specs/steps", "<трек>": "t", "<X>": "a", "<R>": "1"}.items():
        block = block.replace(k, v)
    return block


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env={**os.environ, **GIT_ENV}).stdout


def write(repo, rel, text="x\n"):
    p = Path(repo, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "нужны git и bash")
class DiffPackageBlock(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        git(self.repo, "init", "-q")
        write(self.repo, "tracked.py", "a = 1\n")

    def tearDown(self):
        self._tmp.cleanup()

    def commit(self, gitignore=None):
        if gitignore is not None:
            write(self.repo, ".gitignore", gitignore)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")

    def papers(self):
        write(self.repo, "docs/specs/steps/x.a.md")
        write(self.repo, "docs/specs/steps/x.journal.md")

    def dump_path(self):
        return Path(git(self.repo, "rev-parse", "--absolute-git-dir").strip(), DUMP_REL)

    def run_block(self, block=None, errexit=True):
        flags = ["-e"] if errexit else []
        return subprocess.run(["bash", *flags, "-c", block or core_block()], cwd=self.repo, capture_output=True, text=True,
                              env={**os.environ, **GIT_ENV})

    def ok_dump(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("are ignored", r.stderr)
        path = Path(r.stdout.strip().splitlines()[-1])
        self.assertTrue(path.is_file(), path)
        dump = path.read_text(encoding="utf-8")
        self.assertIn("pkg/new_mod.py", dump)
        self.assertIn("tracked.py", dump)
        self.assertNotIn("docs/specs/steps", dump)
        return dump

    def test_steps_ignored(self):
        self.commit("docs/specs/\n")
        write(self.repo, "tracked.py", "a = 2\n")
        write(self.repo, "pkg/new_mod.py")
        self.papers()
        self.ok_dump(self.run_block())
        self.assertIn("?? pkg/new_mod.py", git(self.repo, "status", "--short", "-uall"))

    def test_steps_not_ignored(self):
        self.commit()
        write(self.repo, "tracked.py", "a = 2\n")
        write(self.repo, "pkg/new_mod.py")
        write(self.repo, "notes.journal.md")
        self.papers()
        self.assertNotIn("journal.md", self.ok_dump(self.run_block()))

    def test_empty_package(self):
        self.commit("docs/specs/\n")
        self.papers()
        r = self.run_block()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("пакет диффа пуст", r.stdout)

    def test_add_failure_stops(self):
        block = core_block()
        if "PATHS=(.)" not in block:
            self.fail("в блоке ядра нет строки `PATHS=(.)` — сценарий отказа add не собрать")
        self.commit("docs/specs/\nignored.log\n")
        write(self.repo, "ignored.log")
        write(self.repo, "pkg/new_mod.py")
        r = self.run_block(block.replace("PATHS=(.)", "PATHS=(. ignored.log)", 1), errexit=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("git add -N упал", r.stdout)
        self.assertFalse(self.dump_path().exists())


if __name__ == "__main__":
    unittest.main()
