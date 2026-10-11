"""Рецепты «Правил судьи: дерево и живые пункты» и «Отпечатка дерева вокруг судьи» из
pipeline-core-accept.md исполняются на временном репозитории (listik-ixej, порция a)."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_pipeline_diff_package import CORE, DUMP_REL, GIT_ENV, core_block, git, write

RULES_HEAD = "## Правила судьи: дерево и живые пункты"
FP_HEAD = "### Отпечаток дерева вокруг судьи"
MISMATCH = "дерево не совпало с пакетом диффа"
CHECK_PLACEHOLDER = "… проверка в \"$T\" …"


def _section(head):
    text = CORE.read_text(encoding="utf-8")
    if head not in text:
        raise AssertionError(f"в {CORE} нет раздела {head!r}")
    level = head.split(" ", 1)[0]
    lines = text.split(head, 1)[1].splitlines()
    end = next((i for i, line in enumerate(lines)
                if re.match(r"#{1,%d} " % len(level), line)), len(lines))
    return lines[:end]


def _fenced(lines, needle):
    block, inside = None, None
    for line in lines:
        if line.startswith("```"):
            if inside is None:
                inside = []
            else:
                if any(needle in s for s in inside):
                    return inside
                inside = None
        elif inside is not None:
            inside.append(line)
    raise AssertionError(f"нет fenced-блока с {needle!r}")


def _subst(script):
    for k, v in {"<дерево>": '"$W"', "<дамп>": '"$DUMP"', "<пути пакета>": '"${PKG[@]}"',
                 "<steps>": "docs/specs/steps"}.items():
        script = script.replace(k, v)
    return script


def _indented(block, first, last):
    """Строки блока правил с отступом 4 от строки, начинающейся с `first`, до строки с `last`."""
    out, on = [], False
    for line in block:
        body = line[4:] if line.startswith("    ") else None
        if body is not None and body.startswith(first):
            on = True
        if on:
            if body is None:
                raise AssertionError(f"рецепт {first!r} оборвался до {last!r}")
            out.append(body)
            if last in body:
                return out
    raise AssertionError(f"в блоке правил нет рецепта {first!r} … {last!r}")


def rules_block():
    return _fenced(_section(RULES_HEAD), "CGSSessionScreenIsLocked")


def pkg_command():
    m = re.search(r"по команде (.+?); <имя процесса>", rules_block()[0])
    if not m:
        raise AssertionError("в первой строке блока правил нет команды <пути пакета>")
    return _subst(m.group(1))


def verify_script():
    lines = _indented(rules_block(), 'I="$(mktemp -u)"', 'rm -f "$I"')
    return _subst("\n".join(lines))


def copy_script(check):
    lines = _indented(rules_block(), 'T="$(mktemp -d)"', "worktree remove --force")
    script = "\n".join(lines)
    if CHECK_PLACEHOLDER not in script:
        raise AssertionError(f"в рецепте копии нет строки {CHECK_PLACEHOLDER!r}")
    return _subst(script.replace(CHECK_PLACEHOLDER, check))


def fingerprint_script():
    return _subst("\n".join(_fenced(_section(FP_HEAD), "shasum -a 256")))


@unittest.skipUnless(shutil.which("git") and shutil.which("bash") and shutil.which("shasum"),
                     "нужны git, bash и shasum")
class JudgeRules(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name, "repo")
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        write(self.repo, "tracked.py", "a = 1\n")
        write(self.repo, "gone.py", "g = 1\n")
        write(self.repo, ".gitignore", "docs/specs/\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        write(self.repo, "tracked.py", "a = 2\n")
        write(self.repo, "pkg/new_mod.py", "n = 1\n")
        git(self.repo, "rm", "-q", "gone.py")
        write(self.repo, "docs/specs/steps/x.a.md")
        r = self.bash(core_block())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.dump = Path(git(self.repo, "rev-parse", "--absolute-git-dir").strip(), DUMP_REL)
        self.assertTrue(self.dump.is_file())

    def tearDown(self):
        self._tmp.cleanup()

    def bash(self, script):
        env = {**os.environ, **GIT_ENV, "W": str(self.repo), "DUMP": str(getattr(self, "dump", ""))}
        return subprocess.run(["bash", "-c", script], cwd=self.repo, capture_output=True, text=True, env=env)

    def with_pkg(self, script):
        return f"PKG=()\nwhile IFS= read -r p; do PKG+=(\"$p\"); done < <({pkg_command()})\n{script}"

    def status(self):
        return git(self.repo, "status", "--porcelain", "-uall")

    def verify(self):
        r = self.bash(self.with_pkg(verify_script()))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def fingerprint(self):
        r = self.bash(fingerprint_script())
        self.assertEqual(r.returncode, 0, r.stderr)
        sha = r.stdout.strip()
        self.assertRegex(sha, r"^[0-9a-f]{64}$")
        return sha

    def test_pkg_paths(self):
        r = self.bash(self.with_pkg('printf "%s\\n" "${PKG[@]}"'))
        self.assertEqual(sorted(r.stdout.split()), ["gone.py", "pkg/new_mod.py", "tracked.py"])

    def test_verify_silent_and_keeps_status(self):
        before = self.status()
        self.assertEqual(self.verify(), "")
        self.assertEqual(self.status(), before)

    def test_verify_catches_return(self):
        write(self.repo, "tracked.py", "a = 2\nreturn\n")
        self.assertIn(MISMATCH, self.verify())

    def test_fingerprint_ignores_index(self):
        before = self.fingerprint()
        git(self.repo, "add", "--", "pkg/new_mod.py", "tracked.py")
        self.assertEqual(self.fingerprint(), before)

    def test_fingerprint_ignores_untracked_outside_package(self):
        before = self.fingerprint()
        write(self.repo, "snap/cache.bin", "junk\n")
        self.assertEqual(self.fingerprint(), before)

    def test_fingerprint_sees_return_in_tracked(self):
        before = self.fingerprint()
        write(self.repo, "tracked.py", "a = 2\nreturn\n")
        self.assertNotEqual(self.fingerprint(), before)

    def test_fingerprint_sees_new_package_file(self):
        before = self.fingerprint()
        write(self.repo, "pkg/new_mod.py", "n = 2\n")
        self.assertNotEqual(self.fingerprint(), before)

    def test_copy_recipe(self):
        before = (self.status(), git(self.repo, "diff", "HEAD"))
        out = Path(self._tmp.name, "copy-status.txt")
        r = self.bash(copy_script(f'git -C "$T" status --porcelain -uall > "{out}"'))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertIn(" M tracked.py", lines)
        self.assertIn(" D gone.py", lines)
        self.assertIn("?? pkg/new_mod.py", lines)
        self.assertEqual((self.status(), git(self.repo, "diff", "HEAD")), before)
        self.assertEqual(len(git(self.repo, "worktree", "list").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
