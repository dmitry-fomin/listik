"""Стенд судей `judge_bench.py`: сборка деревьев кейсов и сводка отчётов на поддельном репозитории."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFS = ROOT / "plugins/feature-pipeline/references"
SCRIPT = REFS / "bench/judge_bench.py"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
}
DEFECT = "pkg.py: double складывает вместо умножения"
CLASS = "граница/сложение-вместо-умножения"

BASE_PKG = "def one():\n    return 1\n"
IMPL_PKG = BASE_PKG + "\n\ndef double(x):\n    return x * 2\n"
BASE_TEST = ("import unittest\n\nfrom pkg import one\n\n\nclass T(unittest.TestCase):\n"
             "    def test_one(self):\n        self.assertEqual(one(), 1)\n")
IMPL_TEST = ("import unittest\n\nfrom pkg import double, one\n\n\nclass T(unittest.TestCase):\n"
             "    def test_one(self):\n        self.assertEqual(one(), 1)\n\n"
             "    def test_double(self):\n        self.assertEqual(double(2), 4)\n")

PROBE_OK = """import sys
try:
    from pkg import double
    got = double(3)
    print(f"double(3) = {got}")
    sys.exit(0 if got == 6 else 1)
except Exception as e:
    print(f"ошибка пробы: {e}")
    sys.exit(2)
"""
PROBE_ALWAYS_RED = 'print("всегда дефект")\nraise SystemExit(1)\n'
PROBE_ALWAYS_GREEN = 'print("всё верно")\n'
PROBE_CRASH = 'print("до падения")\nraise RuntimeError("непредвиденное")\n'


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env={**os.environ, **GIT_ENV}).stdout


def write(root, rel, text):
    p = Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def commit(repo, msg, *extra):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", *extra, "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


def make_patch(before, after):
    """Патч pkg.py `before` → `after` в виде `git diff`."""
    with tempfile.TemporaryDirectory() as d:
        git(d, "init", "-q")
        write(d, "pkg.py", before)
        commit(d, "b")
        write(d, "pkg.py", after)
        return git(d, "diff")


def snapshot(tree):
    """Содержимое и mtime всех файлов дерева."""
    out = {}
    for dirpath, _, files in os.walk(tree):
        for f in files:
            p = Path(dirpath, f)
            if not p.is_symlink():
                out[str(p)] = (p.read_bytes(), p.stat().st_mtime_ns)
    return out


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "нужны git и bash")
class JudgeBench(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._repo_tmp = tempfile.TemporaryDirectory()
        repo = cls.repo = Path(cls._repo_tmp.name)
        git(repo, "init", "-q", "-b", "main")
        write(repo, ".gitignore", "docs/specs/\n")
        write(repo, "tests/__init__.py", "")
        write(repo, "pkg.py", BASE_PKG)
        write(repo, "tests/test_pkg.py", BASE_TEST)
        commit(repo, "база")
        write(repo, "pkg.py", IMPL_PKG)
        write(repo, "tests/test_pkg.py", IMPL_TEST)
        cls.impl = commit(repo, "реализация")
        cls.empty = commit(repo, "пусто", "--allow-empty")
        write(repo, ".gitattributes", "ghost.txt export-ignore\n")
        write(repo, "ghost.txt", "1\n")
        commit(repo, "призрак")
        write(repo, "ghost.txt", "2\n")
        cls.ghost = commit(repo, "правка призрака")
        write(repo, "docs/specs/only.md", "бумага\n")
        git(repo, "add", "-f", "docs/specs/only.md")
        git(repo, "commit", "-q", "-m", "только бумаги")
        cls.papers_only = git(repo, "rev-parse", "HEAD").strip()
        cls.good_patch = make_patch(IMPL_PKG, IMPL_PKG.replace("x * 2", "x + 2"))
        cls.red_patch = make_patch(IMPL_PKG, IMPL_PKG.replace("x * 2", "x * 3"))
        cls.stale_patch = make_patch("нет такого\n", "совсем\n")

    @classmethod
    def tearDownClass(cls):
        cls._repo_tmp.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.bench = self.tmp / "bench"
        self.out = self.tmp / "out"
        self.runs = self.tmp / "runs"
        self.cases = []

    def tearDown(self):
        self._tmp.cleanup()

    def case(self, name="case-01", commit=None, patch="good", probe=PROBE_OK, papers=True,
             defect=DEFECT, drop_paper=None):
        d = self.bench / "cases" / name
        if papers:
            for f in ("s.md", "s.a.md", "s.check-a.md"):
                if f != drop_paper:
                    write(d, f"papers/{f}", f"# {f}\nДерево: {{{{TREE}}}}\n")
        if patch is not None:
            write(d, "mutation.patch", {"good": self.good_patch, "red": self.red_patch,
                                        "stale": self.stale_patch}[patch])
        if probe is not None:
            write(d, "probe.py", probe)
        self.cases.append({
            "name": name, "class": CLASS if defect else "чистый", "step": "s", "portion": "a",
            "commit": commit or self.impl, "tests": ["tests.test_pkg"],
            "defect": defect, "defect_path": "pkg.py" if defect else None,
        })

    def run_bench(self, *args, script=SCRIPT, env=None, cmd="prepare"):
        write(self.bench, "cases.json", json.dumps(self.cases, ensure_ascii=False))
        argv = [sys.executable, str(script), cmd, "--cases", str(self.bench / "cases.json"),
                "--runs", str(self.runs)]
        if cmd == "prepare":
            argv += ["--repo", str(self.repo), "--out", str(self.out)]
            if "--judge" not in args:
                argv += ["--judge", "j1"]
        return subprocess.run(argv + list(args), capture_output=True, text=True,
                              env=env or os.environ, timeout=600)

    def tree(self, name="case-01"):
        return self.out / "j1" / name

    def assert_fails(self, reason, name="case-01", *args):
        r = self.run_bench(*args)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), f"{name}: {reason}")

    # --- годные сборки

    def test_good_case(self):
        self.case()
        before = [git(self.repo, *a) for a in (["status", "--porcelain"], ["branch", "--list"],
                                               ["worktree", "list"], ["rev-parse", "HEAD"])]
        r = self.run_bench()
        after = [git(self.repo, *a) for a in (["status", "--porcelain"], ["branch", "--list"],
                                              ["worktree", "list"], ["rev-parse", "HEAD"])]
        self.assertEqual(before, after, "исходный репозиторий изменился")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tree = self.tree()
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "case-01: готов")
        self.assertEqual(lines[1], f"дерево: {tree}")
        task_file = self.out / "j1/case-01.task.md"
        self.assertEqual(lines[2], f"задание: {task_file}")
        self.assertEqual(lines[3], f"отчёт: {self.runs / 'j1/case-01.md'}")
        self.assertTrue((tree / ".git").is_dir())
        self.assertEqual(git(tree, "rev-list", "--count", "HEAD").strip(), "1")
        self.assertEqual(git(tree, "branch", "--show-current").strip(), "main")
        self.assertEqual(git(tree, "remote").strip(), "")
        self.assertEqual(git(tree, "status", "--porcelain").splitlines(),
                         [" M pkg.py", " M tests/test_pkg.py"])
        self.assertIn("x + 2", (tree / "pkg.py").read_text(encoding="utf-8"))
        exclude = (tree / ".git/info/exclude").read_text(encoding="utf-8").splitlines()
        self.assertIn("docs/specs/", exclude)
        self.assertIn("web/node_modules", exclude)
        for f in ("s.md", "s.a.md", "s.check-a.md"):
            text = (tree / "docs/specs/steps" / f).read_text(encoding="utf-8")
            self.assertNotIn("{{TREE}}", text)
            self.assertIn(str(tree), text)
        dump = tree / ".git/feature-pipeline/s.diff-a.r1.txt"
        self.assertIn("return x + 2", dump.read_text(encoding="utf-8"))
        task = task_file.read_text(encoding="utf-8")
        self.assertIn(task, r.stdout)
        self.assertIn("Ты — приёмка одной порции ТЗ", task)
        for p in (f"{tree}/docs/specs/steps/s.check-a.md", f"{tree}/docs/specs/steps/s.a.md",
                  f"{tree}/.git/feature-pipeline/s.diff-a.r1.txt"):
            self.assertIn(p, task)
        for bad in ("Listik, карточка", "listik claim", DEFECT, CLASS, self.impl):
            self.assertNotIn(bad, task)
        self.assertFalse((tree / "web").exists(), "без node_modules в репо симлинка нет")

    def test_clean_case(self):
        self.case(patch=None, probe=None, defect=None)
        r = self.run_bench()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "case-01: готов")

    def test_no_mutation(self):
        self.case()
        r = self.run_bench("--no-mutation")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "case-01: готов (без мутации)")
        self.assertNotIn("x + 2", (self.tree() / "pkg.py").read_text(encoding="utf-8"))

    def test_node_modules_symlink(self):
        modules = self.repo / "web/node_modules"
        modules.mkdir(parents=True)
        try:
            self.case()
            r = self.run_bench()
        finally:
            shutil.rmtree(self.repo / "web")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        link = self.tree() / "web/node_modules"
        self.assertTrue(link.is_symlink())
        self.assertEqual(Path(os.readlink(link)).resolve(), modules.resolve())
        self.assertNotIn("web", git(self.tree(), "status", "--porcelain", "-uall"))

    # --- негативные

    def test_name_not_neutral(self):
        self.case(name="clean-01")
        self.assert_fails("имя кейса не нейтральное", "clean-01")

    def test_missing_paper(self):
        self.case(drop_paper="s.check-a.md")
        self.assert_fails("нет бумаг s.check-a.md")

    def test_mutation_without_probe(self):
        self.case(probe=None)
        self.assert_fails("мутация без пробы")

    def test_tree_exists(self):
        self.case()
        self.assertEqual(self.run_bench().returncode, 0)
        snap = snapshot(self.tree())
        self.assert_fails(f"дерево уже есть {self.tree()}")
        self.assertEqual(snapshot(self.tree()), snap)

    def test_empty_implementation(self):
        self.case(commit=self.empty)
        self.assert_fails("реализация пуста")

    def test_implementation_does_not_apply(self):
        self.case(commit=self.ghost, patch=None, probe=None, defect=None)
        self.assert_fails("реализация не накладывается")

    def test_mutation_does_not_apply(self):
        self.case(patch="stale")
        self.assert_fails("мутация не накладывается")

    def test_probe_red_on_implementation(self):
        self.case(probe=PROBE_ALWAYS_RED)
        self.assert_fails("проба красная на реализации")

    def test_probe_blind(self):
        self.case(probe=PROBE_ALWAYS_GREEN)
        self.assert_fails("проба не видит дефекта")

    def test_probe_crash(self):
        self.case(probe=PROBE_CRASH)
        self.assert_fails("ошибка пробы")

    def test_portion_tests_red(self):
        self.case(patch="red")
        self.assert_fails("тесты порции красные")

    def test_diff_package_empty(self):
        self.case(commit=self.papers_only, patch=None, probe=None, defect=None)
        self.assert_fails("пакет диффа пуст")

    def test_unknown_case(self):
        self.case()
        self.assert_fails("нет такого кейса", "case-99", "case-99")

    def test_first_fails_second_ready(self):
        self.case(name="case-01", probe=None)
        self.case(name="case-02")
        r = self.run_bench()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "case-01: мутация без пробы")
        self.assertEqual(lines[1], "case-02: готов")

    def test_bad_judge_label(self):
        self.case()
        r = self.run_bench("--judge", "Bad Label")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.out.exists())

    def copy_script(self, skill_text, core_text):
        root = self.tmp / "plugin"
        write(root, "skills/low-pipeline/SKILL.md", skill_text)
        write(root, "references/pipeline-core.md", core_text)
        (root / "references/bench").mkdir()
        shutil.copy(SCRIPT, root / "references/bench/judge_bench.py")
        return root

    def test_no_judge_task(self):
        root = self.copy_script("# без якоря\n", (REFS / "pipeline-core.md").read_text("utf-8"))
        self.case()
        r = self.run_bench(script=root / "references/bench/judge_bench.py")
        self.assertEqual(r.returncode, 2)
        self.assertIn(f"нет задания судьи в {root / 'skills/low-pipeline/SKILL.md'}", r.stderr)
        self.assertFalse(self.out.exists())

    def test_no_diff_block(self):
        skill = (ROOT / "plugins/feature-pipeline/skills/low-pipeline/SKILL.md").read_text("utf-8")
        root = self.copy_script(skill, "# без раздела\n")
        self.case()
        r = self.run_bench(script=root / "references/bench/judge_bench.py")
        self.assertEqual(r.returncode, 2)
        self.assertIn("нет блока пакета диффа в", r.stderr)
        self.assertFalse(self.out.exists())

    def test_probe_environment(self):
        given = str(self.tmp / "given-home")
        probe = f"""import os, sys
bad = [k for k in ("LISTIK_DB", "LISTIK_CONFIG") if k in os.environ]
home = os.environ.get("LISTIK_HOME")
if bad or not home or home == {given!r} or not os.path.isdir(home):
    print(f"окружение: {{bad}} {{home}}")
    sys.exit(2)
from pkg import double
print(f"double(3) = {{double(3)}}")
sys.exit(0 if double(3) == 6 else 1)
"""
        self.case(probe=probe)
        env = {**os.environ, "LISTIK_HOME": given, "LISTIK_DB": str(self.tmp / "real.db"),
               "LISTIK_CONFIG": str(self.tmp / "real.toml")}
        r = self.run_bench(env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # --- summarize

    def report(self, judge, name, answer, seconds="12"):
        write(self.runs, f"{judge}/{name}.md",
              f"судья: {judge}\nмодель: m\nначало: 2026-09-29T10:00:00\n"
              f"конец: 2026-09-29T10:01:00\nсекунд: {seconds}\n---\n{answer}\n")

    def test_summarize(self):
        for n in ("case-01", "case-02", "case-03"):
            self.case(name=n)
        self.case(name="case-04", patch=None, probe=None, defect=None)
        self.report("alpha", "case-01", "**Красный**\n1. pkg.py:5 — сломано", "10")
        self.report("alpha", "case-02", "Вердикт: красный\nничего про файл", "20")
        self.report("alpha", "case-03", "не знаю что сказать", "30")
        self.report("alpha", "case-04", "зелёный abc1234", "много")
        self.report("beta", "case-01", "\n\nзелёный abc1234", "5")
        self.report("beta", "case-04", "## Красный\nложная тревога", "7")
        self.report("beta", "case-77", "зелёный")
        r = self.run_bench(cmd="summarize")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [l for l in r.stdout.splitlines() if l.startswith("| ")]
        self.assertEqual(rows[0], "| судья | кейс | класс | ожидается | вердикт | секунд | "
                                  "файл дефекта назван |")
        self.assertEqual(rows[2:], [
            f"| alpha | case-01 | {CLASS} | красный | красный | 10 | да |",
            f"| alpha | case-02 | {CLASS} | красный | красный | 20 | нет |",
            f"| alpha | case-03 | {CLASS} | красный | нет вердикта | 30 | нет |",
            "| alpha | case-04 | чистый | зелёный | зелёный | ? | — |",
            f"| beta | case-01 | {CLASS} | красный | зелёный | 5 | нет |",
            f"| beta | case-02 | {CLASS} | красный | нет отчёта | ? | нет |",
            f"| beta | case-03 | {CLASS} | красный | нет отчёта | ? | нет |",
            "| beta | case-04 | чистый | зелёный | красный | 7 | — |",
        ])
        self.assertIn("alpha: красный на дефектах 2 из 3, ложный красный на чистых 0 из 1, "
                      "медиана 20 с", r.stdout)
        self.assertIn("beta: красный на дефектах 0 из 3, ложный красный на чистых 1 из 1, "
                      "медиана 6 с", r.stdout)
        self.assertIn(f"лишний отчёт: {self.runs / 'beta/case-77.md'}", r.stdout)
        self.assertNotIn("case-77 |", r.stdout)

    def test_summarize_empty(self):
        self.case()
        self.runs.mkdir()
        r = self.run_bench(cmd="summarize")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("отчётов нет", r.stdout)
        self.assertIn("| судья | кейс |", r.stdout)


if __name__ == "__main__":
    unittest.main()
