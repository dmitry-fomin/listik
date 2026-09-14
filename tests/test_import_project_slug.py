"""Импортёры переиспользуют проект, отличающийся только регистром slug (listik-ovjr).

`POST /api/projects` сверяет slug без учёта регистра (`store.existing_slug`, listik-7vhk),
и импортёры обязаны делать так же: иначе рядом с `writerllm` появляется проект `WriterLLM`,
а задачи повисают на другом написании `project`.
"""
from __future__ import annotations

import json
from pathlib import Path

from listik import import_beads
from listik import import_writerllm
from listik import store
from tests.helpers import FIXTURES_DIR, TempDbTestCase

WL_EXPORT = FIXTURES_DIR / "writerllm" / "export.jsonl"


class ProjectSlugCaseAssertions(TempDbTestCase):
    def project_slugs(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT slug FROM projects ORDER BY slug")]

    def task_projects(self) -> list[str]:
        return [r[0] for r in
                self.conn.execute("SELECT DISTINCT project FROM tasks ORDER BY project")]


class WriterllmSlugCaseTests(ProjectSlugCaseAssertions):
    def test_project_with_other_case_is_reused(self) -> None:
        store.add_project(self.conn, slug="WriterLLM")
        report = import_writerllm.import_file(self.conn, WL_EXPORT, project="writerllm")
        # В фикстуре есть ссылка на несуществующий WL-missing — ошибка не про slug.
        self.assertEqual([e["target_ref"] for e in report["errors"]], ["WL-missing"])
        self.assertEqual(report["project"], "WriterLLM")
        self.assertEqual(self.project_slugs(), ["WriterLLM"])
        self.assertEqual(self.task_projects(), ["WriterLLM"])

    def test_repeat_import_with_other_case_does_not_duplicate(self) -> None:
        first = import_writerllm.import_file(self.conn, WL_EXPORT)
        second = import_writerllm.import_file(self.conn, WL_EXPORT, project="WRITERLLM")
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["skipped"], first["created"])
        self.assertEqual(self.project_slugs(), ["writerllm"])
        self.assertEqual(self.task_projects(), ["writerllm"])


class BeadsSlugCaseTests(ProjectSlugCaseAssertions):
    def beads_root(self) -> Path:
        root = self.tmp_path / "projects"
        beads = root / "Sub" / "Repo" / ".beads"
        beads.mkdir(parents=True)
        issue = {"id": "sr-1", "title": "Задача из beads", "status": "open", "priority": 2,
                 "issue_type": "task", "created_at": "2024-01-01T00:00:00Z",
                 "updated_at": "2024-01-02T00:00:00Z"}
        (beads / "issues.jsonl").write_text(
            json.dumps(issue, ensure_ascii=False) + "\n", encoding="utf-8")
        return root

    def test_project_with_other_case_is_reused(self) -> None:
        root = self.beads_root()
        store.add_project(self.conn, slug="sub/repo")
        report = import_beads.import_all(self.conn, root=root, verbose=False)
        self.assertEqual(report["tasks"], 1)
        self.assertEqual(report["per_project"][0]["slug"], "sub/repo")
        self.assertEqual(self.project_slugs(), ["sub/repo"])
        self.assertEqual(self.task_projects(), ["sub/repo"])

    def test_empty_tracker_with_other_case_is_not_a_duplicate(self) -> None:
        root = self.tmp_path / "projects"
        (root / "Sub" / "Repo" / ".beads").mkdir(parents=True)
        store.add_project(self.conn, slug="sub/repo")
        report = import_beads.import_all(self.conn, root=root, verbose=False)
        self.assertEqual(report["projects"], 0)
        self.assertEqual(self.project_slugs(), ["sub/repo"])
