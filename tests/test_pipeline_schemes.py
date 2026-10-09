"""SVG-схемы пресетов `scripts/pipeline_schemes.py` и раздел «Схемы» README плагинов (listik-d9rj, порция g)."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_DIR / "scripts" / "pipeline_schemes.py"
SVG = "{http://www.w3.org/2000/svg}"
#: Заголовки узлов и подпись петли: по ним считаются узлы, шапка схемы в подсчёт не входит.
HEADS = ("ТЗ", "Критик", "Код", "Линза scope", "Линза holes", "Линза intent", "Судья по находке",
         "Приёмка", "Коммит порции", "красный вердикт")


def _load_module():
    spec = importlib.util.spec_from_file_location("pipeline_schemes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _routes(root: pathlib.Path = REPO_DIR) -> list[dict]:
    data = json.loads((root / "routes.json").read_text(encoding="utf-8"))
    return [r for r in data["routes"] if r.get("kind") == "pipeline" and r.get("plugin")]


def _skill(route: dict) -> str:
    return route["key"].removeprefix(route["plugin"].removeprefix("pipeline-") + "-")


def _svg_path(route: dict, root: pathlib.Path = REPO_DIR) -> pathlib.Path:
    return root / "plugins" / route["plugin"] / "docs" / f"{_skill(route)}.svg"


def _texts(path: pathlib.Path) -> list[str]:
    root = ET.parse(path).getroot()
    return ["".join(el.itertext()) for el in root.iter(f"{SVG}text")]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def _route(key: str) -> dict:
    return next(r for r in _routes() if r["key"] == key)


class PipelineSchemesTests(unittest.TestCase):
    def test_check_on_repo(self) -> None:
        result = _run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_role_caption(self) -> None:
        caption = _load_module().role_caption
        cases = {
            ("xhigh", "Opus 5.5"): "Opus 5.5 · xhigh",
            ("GLM", "GLM 5.3"): "GLM 5.3",
            ("Opus", "Opus 5.5"): "Opus 5.5",
            ("max", "SWE-2"): "SWE-2 · max",
            ("SWE", "SWE-2"): "SWE-2",
            ("high", "Grok 4.7"): "Grok 4.7 · high",
        }
        for (label, title), want in cases.items():
            self.assertEqual(caption(label, title), want, (label, title))

    def test_every_route_has_valid_svg(self) -> None:
        routes = _routes()
        self.assertEqual(len(routes), 17)
        for route in routes:
            name = f"{route['plugin']}:{_skill(route)}"
            path = _svg_path(route)
            with self.subTest(name):
                self.assertTrue(path.is_file(), path)
                root = ET.parse(path).getroot()
                self.assertEqual(root.tag, f"{SVG}svg")
                self.assertIn(name, ["".join(el.itertext()) for el in root.iter(f"{SVG}title")])
                first = next(el for el in root if el.tag not in {f"{SVG}{t}" for t in ("title", "desc", "defs", "style")})
                self.assertEqual(first.tag, f"{SVG}rect")
                self.assertEqual((first.get("fill"), first.get("width"), first.get("height")), ("#fff", "100%", "100%"))
                texts = _texts(path)
                self.assertIn(name, texts)
                self.assertTrue(any(route["title"] in t and route["hint"] in t for t in texts), texts)
                raw = path.read_text(encoding="utf-8")
                self.assertNotIn("<image", raw)
                self.assertNotIn("@font-face", raw)
                self.assertNotIn('href="http', raw)

    def test_full_high_nodes(self) -> None:
        texts = _texts(_svg_path(_route("full-high")))
        self.assertEqual(texts.count("ТЗ"), 1)
        self.assertEqual(texts.count("Критик"), 3)
        for head in ("Код", "Линза scope", "Линза holes", "Линза intent", "Судья по находке", "Коммит порции",
                     "красный вердикт", "Opus 5.5 · high", "GLM 5.3 Flash", "Grok 4.7 · xhigh"):
            self.assertIn(head, texts)
        for text in texts:
            self.assertNotIn("S+DS+SWE", text)
            self.assertNotIn(" · линзы", text)

    def test_short_presets(self) -> None:
        xlow = _texts(_svg_path(_route("full-xlow")))
        self.assertNotIn("ТЗ", xlow)
        self.assertNotIn("Критик", xlow)
        opus = _texts(_svg_path(_route("claude-opus")))
        self.assertEqual(sorted(t for t in opus if t in HEADS), ["Код", "Коммит порции"])
        self.assertEqual(_texts(_svg_path(_route("cc-low"))).count("Критик"), 1)

    def test_readme_schemes_section(self) -> None:
        for plugin in ("pipeline-full", "pipeline-cc", "pipeline-claude"):
            lines = (REPO_DIR / "plugins" / plugin / "README.md").read_text(encoding="utf-8").splitlines()
            with self.subTest(plugin):
                start = lines.index("## Схемы")
                self.assertGreater(start, max(i for i, line in enumerate(lines) if line.startswith("| ")))
                section = lines[start + 1:]
                skills = [_skill(r) for r in _routes() if r["plugin"] == plugin]
                found = [line.removeprefix(f"### {plugin}:") for line in section if line.startswith("### ")]
                self.assertEqual(found, skills)
                for route in (r for r in _routes() if r["plugin"] == plugin):
                    skill = _skill(route)
                    begin = section.index(f"### {plugin}:{skill}")
                    end = next((i for i in range(begin + 1, len(section)) if section[i].startswith("#")), len(section))
                    body = section[begin:end]
                    self.assertTrue(any(f"](docs/{skill}.svg)" in line for line in body), skill)
                    self.assertIn(f"Когда брать: {route['hint']}", body)


class PipelineSchemesCheckNegativeTests(unittest.TestCase):
    """`--check` во временном корне ловит изменённый, удалённый и лишний SVG."""

    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        shutil.copy(REPO_DIR / "routes.json", self.root / "routes.json")
        for docs in (REPO_DIR / "plugins").glob("*/docs"):
            shutil.copytree(docs, self.root / docs.relative_to(REPO_DIR))
        self.assertEqual(_run("--check", "--root", str(self.root)).returncode, 0)

    def _assert_fails_on(self, rel: str) -> None:
        result = _run("--check", "--root", str(self.root))
        self.assertEqual(result.returncode, 1)
        self.assertIn(rel, result.stderr)

    def test_changed_byte(self) -> None:
        rel = "plugins/pipeline-full/docs/high.svg"
        path = self.root / rel
        data = bytearray(path.read_bytes())
        data[-2] = ord("X")
        path.write_bytes(bytes(data))
        self._assert_fails_on(rel)

    def test_missing_file(self) -> None:
        rel = "plugins/pipeline-cc/docs/sol.svg"
        (self.root / rel).unlink()
        self._assert_fails_on(rel)

    def test_extra_file(self) -> None:
        rel = "plugins/pipeline-full/docs/extra.svg"
        (self.root / rel).write_text("<svg/>", encoding="utf-8")
        self._assert_fails_on(rel)


class PipelineSchemesDeterminismTests(unittest.TestCase):
    def test_two_runs_identical(self) -> None:
        outputs = []
        for _ in range(2):
            root = pathlib.Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, root)
            shutil.copy(REPO_DIR / "routes.json", root / "routes.json")
            self.assertEqual(_run("--root", str(root)).returncode, 0)
            outputs.append({p.relative_to(root).as_posix(): p.read_bytes() for p in root.glob("plugins/*/docs/*.svg")})
        self.assertEqual(len(outputs[0]), 17)
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
