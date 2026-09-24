"""Порции шага — дочерние карточки (решение listik-9gsh).

Одна карточка хранит ровно один `spec_path`/`checklist_path`/`review_path`, поэтому
несколько порций шага заводятся дочерними карточками со связью `parent-child`:
у каждой порции свои документы, а родитель-шаг видит их все. Здесь проверяется
весь путь: создание с `parent`/`--parent`, холодный старт родителя
(`show`/`context` → `children[]` с документами) и разрешение `context --portion`
в дочернюю карточку.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from listik import documents, mcp, server, store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


def _walk(value, path=""):
    """Все (path, key, value) вложенной структуры — как в test_context."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield path, k, v
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


class PortionCardsTests(TempDbTestCase):
    """`parent` у create_task и холодный старт родителя через `show`."""

    def setUp(self) -> None:
        super().setUp()
        self.parent = store.create_task(self.conn, title="Шаг 09: автостарт", project="listik",
                                        stage="s1-spec")
        self.pid = self.parent["id"]
        self.spec = self.tmp_path / "step-09.md"
        self.spec.write_text("# Шаг 09\n\n## Цель\n\nобщая цель шага\n", encoding="utf-8")

    def _doc(self, name: str, text: str) -> str:
        path = self.tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _portion(self, title: str, *, letter: str, spec: bool = False) -> dict:
        return store.create_task(
            self.conn, title=title, project="listik", parent=self.pid,
            spec_path=str(self.spec) if spec else None,
            checklist_path=self._doc(f"step-09.check-{letter}.md",
                                     f"# Чек-лист {letter}\n\n- [ ] {letter}\n"),
            review_path=self._doc(f"step-09.review-{letter}.md",
                                  f"# Ревью {letter}\n\nзамечание {letter}\n"),
        )

    # ---------------------------------------------------------------- создание

    def test_parent_creates_soft_parent_child_link(self) -> None:
        from listik import deps as deps_mod
        child = self._portion("Шаг 09, порция a", letter="a")
        rows = self.conn.execute(
            "SELECT depends_on, dep_type FROM deps WHERE issue_id = ?", (child["id"],)).fetchall()
        self.assertIn((self.pid, "parent-child"), [(r["depends_on"], r["dep_type"]) for r in rows])
        self.assertEqual(deps_mod.parent(self.conn, child["id"])["id"], self.pid)
        self.assertEqual([c["id"] for c in deps_mod.children(self.conn, self.pid)], [child["id"]])

    def test_parent_child_is_soft_and_does_not_block_the_child(self) -> None:
        from listik import deps as deps_mod
        child = self._portion("Шаг 09, порция a", letter="a")
        state_child = deps_mod.ready(self.conn, child["id"])
        self.assertTrue(state_child["ready"], state_child["reasons"])
        self.assertEqual(state_child["blocked_by"], [])
        state_parent = deps_mod.ready(self.conn, self.pid)
        self.assertFalse(state_parent["can_finish"])
        self.assertIn(child["id"], [c["id"] for c in state_parent["children_open"]])

    def test_child_inherits_parent_project_when_not_given(self) -> None:
        child = store.create_task(self.conn, title="Шаг 09, порция a", parent=self.pid)
        self.assertEqual(child["project"], "listik")
        explicit = store.create_task(self.conn, title="Шаг 09, порция x", project="demo",
                                     parent=self.pid)
        self.assertEqual(explicit["project"], "demo")

    def test_unknown_parent_raises_and_creates_nothing(self) -> None:
        before = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        with self.assertRaises(KeyError):
            store.create_task(self.conn, title="сирота", project="listik", parent="listik-nope")
        after = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        self.assertEqual(before, after)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM tasks WHERE title = 'сирота'").fetchone())

    def test_self_parent_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            store.create_task(self.conn, title="сам себе", project="listik", task_id=self.pid,
                              parent=self.pid)

    # ---------------------------------------------------------------- show

    def test_show_parent_lists_every_children_with_documents(self) -> None:
        a = self._portion("Шаг 09, порция a", letter="a", spec=True)
        b = self._portion("Шаг 09, порция b", letter="b")
        out = store.get_task(self.conn, self.pid)
        self.assertEqual([c["id"] for c in out["children"]], [a["id"], b["id"]])
        by_id = {c["id"]: c for c in out["children"]}
        kinds_a = {d["kind"]: d["path"] for d in by_id[a["id"]]["documents"]}
        self.assertEqual(kinds_a["spec"], str(self.spec))
        self.assertEqual(kinds_a["checklist"], a["checklist_path"])
        self.assertEqual(kinds_a["review"], a["review_path"])
        self.assertEqual([d["kind"] for d in by_id[b["id"]]["documents"]],
                         ["checklist", "review"])
        # Возрастных полей в справке о порции нет: context обязан быть стабильным.
        for _path, key, _value in _walk(out["children"]):
            self.assertFalse(key.endswith("_age"), key)
            self.assertFalse(key.endswith("_hours"), key)

    def test_show_parent_includes_closed_portions(self) -> None:
        a = self._portion("Шаг 09, порция a", letter="a")
        b = self._portion("Шаг 09, порция b", letter="b")
        store.update_task(self.conn, a["id"], status="done", stage="done", result="готово")
        out = store.get_task(self.conn, self.pid)
        self.assertEqual([c["id"] for c in out["children"]], [a["id"], b["id"]])
        self.assertEqual(out["children"][0]["status"], "done")
        self.assertEqual([c["id"] for c in out["deps_state"]["children_open"]], [b["id"]])


class PortionContextTests(TempDbTestCase):
    """`context`: children[] и разрешение `portion` в дочернюю карточку."""

    def setUp(self) -> None:
        super().setUp()
        self.spec = self.tmp_path / "step-09.md"
        self.spec.write_text(
            "# Шаг 09\n\n## Цель\n\nобщая цель\n\n## Раздел особый\n\nтекст раздела\n",
            encoding="utf-8")
        self.parent = store.create_task(
            self.conn, title="Шаг 09: автостарт", project="listik", stage="s1-spec",
            spec_path=str(self.spec), acceptance="приёмка шага")
        self.pid = self.parent["id"]
        self.a = self._portion("Шаг 09, порция a", "a")
        self.b = self._portion("Шаг 09, порция b", "b")

    def _doc(self, name: str, text: str) -> str:
        path = self.tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _portion(self, title: str, letter: str) -> dict:
        return store.create_task(
            self.conn, title=title, project="listik", parent=self.pid,
            checklist_path=self._doc(f"step-09.check-{letter}.md",
                                     f"# Чек-лист {letter}\n\nделай {letter}\n"),
            review_path=self._doc(f"step-09.review-{letter}.md",
                                  f"# Ревью {letter}\n\nзамечание {letter}\n"))

    def ctx(self, stage: str, task_id: str | None = None, **kwargs) -> dict:
        return documents.context(self.conn, task_id or self.pid, stage, **kwargs)

    # ------------------------------------------------------------- children

    def test_children_with_documents_at_every_stage(self) -> None:
        for stage in ("s1-spec", "s2-review", "s3-impl", "s4-judge"):
            out = self.ctx(stage)
            self.assertEqual([c["id"] for c in out["children"]], [self.a["id"], self.b["id"]], stage)
            self.assertIsNone(out["portion_card"], stage)
            self.assertIsNone(out["parent"], stage)
            for child in out["children"]:
                paths = {d["kind"]: d["path"] for d in child["documents"]}
                self.assertEqual(paths["checklist"], child["checklist_path"])
                self.assertEqual(paths["review"], child["review_path"])

    def test_closed_portion_stays_in_children(self) -> None:
        store.update_task(self.conn, self.a["id"], status="done", stage="done", result="готово")
        out = self.ctx("s2-review")
        self.assertEqual([c["id"] for c in out["children"]], [self.a["id"], self.b["id"]])
        self.assertEqual(out["children"][0]["status"], "done")
        open_ids = [c["id"] for c in out["dependencies"]["children_open"]]
        self.assertEqual(open_ids, [self.b["id"]])

    def test_context_repeated_calls_with_children_are_byte_stable(self) -> None:
        for stage in ("s1-spec", "s3-impl", "s4-judge"):
            first = json.dumps(self.ctx(stage, portion="b"), ensure_ascii=False, sort_keys=True)
            second = json.dumps(self.ctx(stage, portion="b"), ensure_ascii=False, sort_keys=True)
            self.assertEqual(first, second, stage)

    def test_children_do_not_leak_age_fields(self) -> None:
        for _path, key, _value in _walk(self.ctx("s3-impl")):
            self.assertFalse(key.endswith("_age"), key)
            self.assertFalse(key.endswith("_hours"), key)

    # ------------------------------------------------------- portion resolve

    def test_portion_by_title_resolves_to_child_card(self) -> None:
        out = self.ctx("s3-impl", portion="b")
        self.assertEqual(out["task"]["id"], self.b["id"])
        self.assertEqual(out["card"]["checklist_path"], self.b["checklist_path"])
        self.assertEqual(out["acceptance"], "")
        self.assertEqual(out["portion_card"]["id"], self.b["id"])
        self.assertEqual(out["parent"]["id"], self.pid)
        # Контекст собран по документам порции, а не по документам шага: чанки — только
        # её чек-лист, метаданные документов — чек-лист и ревью порции.
        chunk_paths = {c["path"] for c in out["chunks"]}
        self.assertEqual(chunk_paths, {self.b["checklist_path"]})
        self.assertEqual({d["path"] for d in out["documents"]},
                         {self.b["checklist_path"], self.b["review_path"]})
        self.assertIsNone(out["task"]["spec_path"])
        self.assertIsNone(out["card"]["spec_path"])
        blocks = [r["block"] for r in out["reasons"]]
        self.assertIn("children", blocks)
        self.assertIn("portion_card", blocks)

    def test_portion_by_child_id(self) -> None:
        out = self.ctx("s3-impl", task_id=self.pid, portion=self.a["id"])
        self.assertEqual(out["task"]["id"], self.a["id"])
        self.assertEqual(out["portion_card"]["id"], self.a["id"])

    def test_portion_by_child_document_path_token(self) -> None:
        # Заголовок карточки буквы не содержит: порция узнаётся по имени файла.
        d = store.create_task(
            self.conn, title="Маршруты JSON", project="listik", parent=self.pid,
            checklist_path=self._doc("step-09.check-d.md", "# Чек-лист d\n\nделай d\n"))
        out = self.ctx("s3-impl", portion="d")
        self.assertEqual(out["task"]["id"], d["id"])
        self.assertEqual(out["card"]["checklist_path"], d["checklist_path"])

    def test_unknown_portion_falls_back_to_spec_heading(self) -> None:
        out = self.ctx("s3-impl", portion="Раздел особый")
        self.assertEqual(out["task"]["id"], self.pid)
        self.assertIsNone(out["portion_card"])
        self.assertEqual([c["id"] for c in out["children"]], [self.a["id"], self.b["id"]])
        spec_chunks = [c for c in out["chunks"] if c["kind"] == "spec"]
        self.assertTrue(spec_chunks)
        self.assertTrue(all("совпадение с порцией" in c["reason"] for c in spec_chunks))

    def test_ambiguous_portion_is_not_resolved(self) -> None:
        store.create_task(self.conn, title="Маршруты чтение", project="listik", parent=self.pid)
        store.create_task(self.conn, title="Маршруты запись", project="listik", parent=self.pid)
        out = self.ctx("s3-impl", portion="маршруты")
        self.assertEqual(out["task"]["id"], self.pid)
        self.assertIsNone(out["portion_card"])
        self.assertEqual(len(out["children"]), 4)

    def test_s1_portion_does_not_swap_the_card(self) -> None:
        out = self.ctx("s1-spec", portion="b")
        self.assertEqual(out["task"]["id"], self.pid)
        self.assertIsNone(out["portion_card"])
        # Текст порции всё равно доступен: он в children[] метаданными, а не чанками.
        self.assertEqual(len(out["children"]), 2)

    def test_child_context_knows_its_parent(self) -> None:
        out = self.ctx("s3-impl", task_id=self.a["id"])
        self.assertEqual(out["task"]["id"], self.a["id"])
        self.assertEqual(out["parent"]["id"], self.pid)
        self.assertEqual(out["children"], [])

    def test_resolved_context_takes_reviews_from_the_child(self) -> None:
        store.add_comment(self.conn, self.pid, "ревью шага, не порции", author="human",
                          kind="review")
        store.add_comment(self.conn, self.b["id"], "ревью порции b", author="human",
                          kind="review")
        out = self.ctx("s3-impl", portion="b")
        self.assertEqual([r["text"] for r in out["reviews"]], ["ревью порции b"])
        self.assertNotIn("ревью шага, не порции", json.dumps(out, ensure_ascii=False))


class PortionCliTests(TempDbTestCase):
    """CLI: `new --parent`, `show` родителя и `context --portion`."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", *args],
            capture_output=True, text=True,
            env={**os.environ, "LISTIK_DB": str(self.db_path)},
            cwd=str(LISTIK_BIN.parent.parent),
        )

    def setUp(self) -> None:
        super().setUp()
        self.spec = self.tmp_path / "step-09.md"
        self.spec.write_text("# Шаг 09\n\n## Цель\n\nобщая цель\n", encoding="utf-8")
        self.check = self.tmp_path / "step-09.check-b.md"
        self.check.write_text("# Чек-лист b\n\n- [ ] b\n", encoding="utf-8")
        self.parent = store.create_task(self.conn, title="Шаг 09", project="listik",
                                        stage="s1-spec", spec_path=str(self.spec))
        self.pid = self.parent["id"]

    def _new_portion(self) -> str:
        p = self._run("new", "Шаг 09, порция b", "-p", "listik", "--parent", self.pid,
                      "--checklist", str(self.check), "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)["id"]

    def test_new_with_parent_creates_portion(self) -> None:
        child_id = self._new_portion()
        rows = self.conn.execute("SELECT depends_on, dep_type FROM deps WHERE issue_id = ?",
                                 (child_id,)).fetchall()
        self.assertEqual([(r["depends_on"], r["dep_type"]) for r in rows],
                         [(self.pid, "parent-child")])

    def test_new_with_unknown_parent_fails_cleanly(self) -> None:
        p = self._run("new", "сирота", "-p", "listik", "--parent", "listik-nope")
        self.assertEqual(p.returncode, 1)
        self.assertIn("задача не найдена", p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        self.assertNotIn("сирота", self._run("list", "--json").stdout)

    def test_dep_add_parent_child_links_existing_card(self) -> None:
        child = store.create_task(self.conn, title="Порция c", project="listik")
        p = self._run("dep", "add", child["id"], self.pid, "--dep-type", "parent-child",
                      "--actor", "agent:dsh")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(self._run("context", self.pid, "--stage", "s2-review",
                                    "--format", "json").stdout)
        self.assertEqual([c["id"] for c in data["children"]], [child["id"]])

    def test_show_parent_prints_portions_with_documents(self) -> None:
        child_id = self._new_portion()
        p = self._run("show", self.pid)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("порции (дочерние карточки", p.stdout)
        self.assertIn(child_id, p.stdout)
        self.assertIn(str(self.check), p.stdout)
        data = json.loads(self._run("show", self.pid, "--json").stdout)
        self.assertEqual([c["id"] for c in data["children"]], [child_id])

    def test_context_portion_prints_resolved_child(self) -> None:
        child_id = self._new_portion()
        p = self._run("context", self.pid, "--stage", "s3-impl", "--portion", "b")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(f"«b» → карточка {child_id}", p.stdout)
        self.assertIn(f"родитель:    {self.pid}", p.stdout)
        data = json.loads(self._run("context", self.pid, "--stage", "s3-impl",
                                    "--portion", "b", "--format", "json").stdout)
        self.assertEqual(data["task"]["id"], child_id)
        self.assertEqual(data["portion_card"]["id"], child_id)


class PortionMcpTests(TempDbTestCase):
    """MCP: `parent` у listik_create и children в listik_show (тот же store)."""

    def test_create_schema_has_parent_and_tool_links_the_card(self) -> None:
        tools = {t["name"]: t for t in mcp.TOOLS}
        self.assertIn("parent", tools["listik_create"]["inputSchema"]["properties"])
        parent = store.create_task(self.conn, title="Шаг", project="listik")
        child = mcp.call_tool("listik_create", {"title": "Порция a", "project": "listik",
                                                "parent": parent["id"]}, conn=self.conn)
        deps_row = self.conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id = ? AND depends_on = ?",
            (child["id"], parent["id"])).fetchone()
        self.assertEqual(deps_row["dep_type"], "parent-child")
        shown = mcp.call_tool("listik_show", {"id": parent["id"]}, conn=self.conn)
        self.assertEqual([c["id"] for c in shown["children"]], [child["id"]])

    def test_unknown_parent_is_a_tool_error(self) -> None:
        parent = store.create_task(self.conn, title="Шаг", project="listik")
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "listik_create",
                                      "arguments": {"title": "сирота", "parent": "listik-nope"}}},
                          conn=self.conn)
        self.assertTrue(resp["result"]["isError"])
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM tasks WHERE title='сирота'").fetchone()[0], 0)
        self.assertEqual([c["id"] for c in store.child_cards(self.conn, parent["id"])], [])


class PortionHttpTests(TempDbTestCase):
    """HTTP: POST /api/tasks с `parent` и children в GET /api/tasks/{id}."""

    def setUp(self) -> None:
        super().setUp()
        self._conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        self._conn_patch.start()
        self.addCleanup(self._conn_patch.stop)
        self.parent = store.create_task(self.conn, title="Шаг", project="listik")

    def post(self, **body):
        return server.handle("POST", "/api/tasks", {}, body, authed=True)

    def test_post_with_parent_links_and_returns_card(self) -> None:
        status, child = self.post(title="Порция a", project="listik", parent=self.parent["id"])
        self.assertEqual(status, 201)
        self.assertEqual(child["title"], "Порция a")
        self.assertEqual(self.conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=?",
            (child["id"], self.parent["id"])).fetchone()["dep_type"], "parent-child")

    def test_post_with_unknown_parent_is_404_and_creates_nothing(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post(title="сирота", parent="listik-nope")
        self.assertEqual(ctx.exception.status, 404)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM tasks WHERE title='сирота'").fetchone())

    def test_post_with_non_string_parent_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post(title="t", parent=123)
        self.assertEqual(ctx.exception.status, 400)

    def test_get_parent_returns_children(self) -> None:
        _, child = self.post(title="Порция a", parent=self.parent["id"])
        status, task = server.handle("GET", f"/api/tasks/{self.parent['id']}", {}, {},
                                     authed=True)
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in task["children"]], [child["id"]])


class PortionJournalTests(TempDbTestCase):
    """Порция без своего `journal_path` наследует журнал родителя, и только его."""

    def setUp(self) -> None:
        super().setUp()
        self.journal = self.tmp_path / "step.journal.md"
        self.journal.write_text("# Журнал шага\n\nрешение\n", encoding="utf-8")
        self.P = str(self.journal)
        self.parent = store.create_task(self.conn, title="Шаг", project="listik",
                                        stage="s1-spec", journal_path=self.P)
        self.pid = self.parent["id"]

    def test_inherits_parent_journal(self) -> None:
        child = store.create_task(self.conn, title="порция", parent=self.pid)
        task = store.get_task(self.conn, child["id"])
        self.assertEqual(task["journal_path"], self.P)
        self.assertIn(("decision", self.P),
                      [(d["kind"], d["path"]) for d in task["documents"]])

    def test_explicit_journal_wins(self) -> None:
        q = self.tmp_path / "own.journal.md"
        q.write_text("# свой\n", encoding="utf-8")
        child = store.create_task(self.conn, title="порция", parent=self.pid,
                                  journal_path=str(q))
        self.assertEqual(store.get_task(self.conn, child["id"])["journal_path"], str(q))

    def test_parent_without_journal_leaves_child_empty(self) -> None:
        bare = store.create_task(self.conn, title="Шаг без журнала", project="listik",
                                 stage="s1-spec")
        child = store.create_task(self.conn, title="порция", parent=bare["id"])
        self.assertFalse(store.get_task(self.conn, child["id"])["journal_path"])

    def test_empty_journal_inherits(self) -> None:
        for empty in ("", "   "):
            child = store.create_task(self.conn, title="порция", parent=self.pid,
                                      journal_path=empty)
            self.assertEqual(store.get_task(self.conn, child["id"])["journal_path"], self.P)

    def test_missing_parent_journal_file_still_inherited(self) -> None:
        ghost = str(self.tmp_path / "nope.journal.md")
        parent = store.create_task(self.conn, title="Шаг", project="listik",
                                   stage="s1-spec", journal_path=ghost)
        child = store.create_task(self.conn, title="порция", parent=parent["id"])
        self.assertEqual(store.get_task(self.conn, child["id"])["journal_path"], ghost)

    def test_other_fields_not_inherited(self) -> None:
        spec = self.tmp_path / "s.md"
        spec.write_text("# spec\n", encoding="utf-8")
        check = self.tmp_path / "c.md"
        check.write_text("# check\n- [ ] x\n", encoding="utf-8")
        parent = store.create_task(self.conn, title="Шаг", project="listik", stage="s1-spec",
                                   labels=["x"], priority=0, spec_path=str(spec),
                                   checklist_path=str(check), journal_path=self.P)
        task = store.get_task(self.conn, store.create_task(
            self.conn, title="порция", parent=parent["id"])["id"])
        self.assertEqual(task["labels"], [])
        self.assertEqual(task["priority"], 2)
        self.assertFalse(task["spec_path"])
        self.assertFalse(task["checklist_path"])

    def test_cli_new_parent_inherits_journal(self) -> None:
        p = subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", "new", "порция",
             "--parent", self.pid, "--json"],
            capture_output=True, text=True,
            env={**os.environ, "LISTIK_DB": str(self.db_path)},
            cwd=str(LISTIK_BIN.parent.parent),
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["journal_path"], self.P)


if __name__ == "__main__":
    unittest.main()
