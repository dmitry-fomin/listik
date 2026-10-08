"""Поле «оркестратор» (`tasks.orchestrator`, listik-6g0q, порция a).

Кто ведёт карточку по маршруту: `listik` пишет захват запуска (`launcher.start`),
`claude` — успешный `claim` Claude по карточке с пустым полем. Ручной записи нет:
ни через API, ни через CLI, ни через MCP, ни импортом bd. Старые значения бывшей
колонки `assignee` переносятся миграцией как есть.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from listik import db as db_mod
from listik import errors
from listik import import_writerllm
from listik import mcp
from listik import paths
from listik import search
from listik import server
from listik import store
from tests.helpers import FIXTURES_DIR, TempDbTestCase
from tests.test_autostart import _load_cli

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent

#: Старые значения колонки `assignee`: человек, агент, имя из импорта bd, харнесс, NULL.
OLD_VALUES = {"t-me": "me", "t-claude": "claude", "t-ivan": "ivan petrov",
              "t-grok": "grok", "t-null": None}


def _old_schema() -> str:
    """`db.SCHEMA` схемы 12: в строках колонки и индекса `tasks` — `assignee`."""
    lines = []
    for line in db_mod.SCHEMA.splitlines():
        if line.startswith("    orchestrator TEXT,") or "idx_tasks_orchestrator" in line:
            line = line.replace("orchestrator", "assignee")
        lines.append(line)
    return "\n".join(lines)


def _columns(conn) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}


def _indexes(conn) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA index_list(tasks)")}


class MigrationTests(unittest.TestCase):
    """M1, M2: перевод старой базы и откат сорванной миграции."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "old.db"
        raw = sqlite3.connect(self.path)
        raw.executescript(_old_schema())
        raw.execute("INSERT INTO meta(key, value) VALUES('schema_version', '12')")
        for tid, value in OLD_VALUES.items():
            raw.execute("INSERT INTO tasks(id, title, assignee) VALUES(?, ?, ?)",
                        (tid, f"Старая {tid}", value))
        raw.commit()
        self.assertIn("assignee", _columns(raw))
        self.assertNotIn("orchestrator", _columns(raw))
        self.assertIn("idx_tasks_assignee", _indexes(raw))
        raw.close()

    def open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        self.addCleanup(conn.close)
        return conn

    def test_m1_old_database_is_renamed_values_kept(self) -> None:
        conn = db_mod.init(self.path)
        self.addCleanup(conn.close)
        self.assertIn("orchestrator", _columns(conn))
        self.assertNotIn("assignee", _columns(conn))
        values = {r[0]: r[1] for r in conn.execute("SELECT id, orchestrator FROM tasks")}
        self.assertEqual(values, OLD_VALUES)
        self.assertIn("idx_tasks_orchestrator", _indexes(conn))
        self.assertNotIn("idx_tasks_assignee", _indexes(conn))
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        self.assertEqual(version, "16")

        self.assertFalse(db_mod.rename_task_orchestrator(conn))
        again = db_mod.init(self.path)
        self.addCleanup(again.close)
        self.assertEqual({r[0]: r[1] for r in again.execute("SELECT id, orchestrator FROM tasks")},
                         OLD_VALUES)

    def test_m2_failure_rolls_back_everything(self) -> None:
        broken = (*db_mod.RENAME_ORCHESTRATOR_SQL[:-1], "CREATE INDEX idx_x ON нет_такой(x)")
        with mock.patch.object(db_mod, "RENAME_ORCHESTRATOR_SQL", broken):
            with self.assertRaises(sqlite3.OperationalError):
                db_mod.init(self.path)
        conn = self.open()
        self.assertIn("assignee", _columns(conn))
        self.assertNotIn("orchestrator", _columns(conn))
        self.assertEqual({r[0]: r[1] for r in conn.execute("SELECT id, assignee FROM tasks")},
                         OLD_VALUES)
        self.assertIn("idx_tasks_assignee", _indexes(conn))
        self.assertNotIn("idx_tasks_orchestrator", _indexes(conn))


class AlembicTests(unittest.TestCase):
    """M3: ревизия 0011 и её offline-SQL."""

    REVISION = REPO_DIR / "alembic" / "versions" / "0011_task_orchestrator.py"

    def test_m3_revision_links_to_drop_direct_routes(self) -> None:
        text = self.REVISION.read_text(encoding="utf-8")
        self.assertIn('revision = "0011_task_orchestrator"', text)
        self.assertIn('down_revision = "0010_drop_direct_routes"', text)

    def test_m3_offline_sql_renames_without_update(self) -> None:
        if shutil.which("alembic") is None:
            self.skipTest("alembic не установлен")
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "LISTIK_DB": str(pathlib.Path(tmp) / "sql.db")}
            done = subprocess.run(
                ["alembic", "upgrade", "0010_drop_direct_routes:0011_task_orchestrator", "--sql"],
                cwd=REPO_DIR, capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ALTER TABLE tasks RENAME COLUMN assignee TO orchestrator", done.stdout)
        # Единственный UPDATE в выводе — служебная отметка версии самого alembic.
        updates = [line for line in done.stdout.splitlines()
                   if line.lstrip().upper().startswith("UPDATE")
                   and not line.lstrip().startswith("UPDATE alembic_version ")]
        self.assertEqual(updates, [], done.stdout)


class OrchestratorCase(TempDbTestCase):
    def new(self, title="Задача", project="demo") -> str:
        return store.create_task(self.conn, title=title, project=project)["id"]

    def set_orchestrator(self, tid: str, value: str | None) -> None:
        self.conn.execute("UPDATE tasks SET orchestrator = ? WHERE id = ?", (value, tid))
        self.conn.commit()

    def orchestrator(self, tid: str):
        return self.conn.execute("SELECT orchestrator FROM tasks WHERE id = ?",
                                 (tid,)).fetchone()["orchestrator"]


class ClaimTests(OrchestratorCase):
    """C1–C7: `claim` пишет `claude` только в пустое поле и только от Claude."""

    def test_c1_claude_claims_fresh_card(self) -> None:
        tid = self.new()
        self.assertIsNone(self.orchestrator(tid))
        out = store.claim(self.conn, tid, holder="claude")
        self.assertEqual(out["orchestrator"], "claude")
        self.assertEqual(self.orchestrator(tid), "claude")

    def test_c2_agent_claude_stores_plain_claude(self) -> None:
        tid = self.new()
        store.claim(self.conn, tid, holder="agent:claude")
        self.assertEqual(self.orchestrator(tid), "claude")

    def test_c3_other_harnesses_do_not_write(self) -> None:
        for holder in ("dsh", "grok"):
            # Свой проект у каждой: иначе вторая упрётся в занятое дерево.
            tid = self.new(title=f"Задача {holder}", project=f"p-{holder}")
            store.claim(self.conn, tid, holder=holder)
            self.assertIsNone(self.orchestrator(tid), holder)

    def test_c4_listik_stays_listik(self) -> None:
        tid = self.new()
        self.set_orchestrator(tid, "listik")
        store.claim(self.conn, tid, holder="claude")
        self.assertEqual(self.orchestrator(tid), "listik")

    def test_c5_assigned_then_taken(self) -> None:
        tid = self.new()
        store.next_stage(self.conn, tid, to_stage="s3-impl", holder="claude")
        self.assertIsNone(self.orchestrator(tid))
        out = store.claim(self.conn, tid, holder="claude")
        self.assertEqual(out["orchestrator"], "claude")
        self.assertEqual(self.orchestrator(tid), "claude")
        notes = [r["note"] for r in self.conn.execute(
            "SELECT note FROM events WHERE task_id = ? AND kind = 'claim'", (tid,))]
        self.assertIn("взял задачу, которую выдали", notes)

    def test_c6_refused_claim_does_not_write(self) -> None:
        blocker = self.new(title="Блокер")
        tid = self.new()
        store.add_dep(self.conn, tid, blocker, dep_type="blocks", created_by="автор")
        with self.assertRaises(ValueError):
            store.claim(self.conn, tid, holder="claude")
        self.assertIsNone(self.orchestrator(tid))

    def test_c7_repeated_claim_by_same_holder(self) -> None:
        tid = self.new()
        store.claim(self.conn, tid, holder="claude")
        self.assertEqual(self.orchestrator(tid), "claude")
        self.set_orchestrator(tid, None)
        out = store.claim(self.conn, tid, holder="claude")
        self.assertEqual(out["orchestrator"], "claude")

        old = self.new(title="Старая")
        store.claim(self.conn, old, holder="claude")
        self.set_orchestrator(old, "me")
        store.claim(self.conn, old, holder="claude")
        self.assertEqual(self.orchestrator(old), "me")

    def test_claim_writes_no_orchestrator_event(self) -> None:
        tid = self.new()
        store.claim(self.conn, tid, holder="claude")
        notes = [r["note"] or "" for r in self.conn.execute(
            "SELECT note FROM events WHERE task_id = ?", (tid,))]
        self.assertFalse([n for n in notes if "оркестратор" in n], notes)


class NoManualWriteTests(OrchestratorCase):
    """W1–W3: поле не пишется ни правкой, ни созданием."""

    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        patch.start()
        self.addCleanup(patch.stop)

    def test_w1_update_task_rejects_field(self) -> None:
        tid = self.new()
        with self.assertRaises(errors.BadArgument):
            store.update_task(self.conn, tid, orchestrator="claude")
        with self.assertRaises(errors.BadArgument):
            store.update_task(self.conn, tid, assignee="claude")
        self.assertIsNone(self.orchestrator(tid))
        self.assertNotIn("orchestrator", store.UPDATABLE)
        self.assertNotIn("assignee", store.UPDATABLE)

    def test_w3_patch_rejects_field(self) -> None:
        tid = self.new()
        for body in ({"orchestrator": "claude"}, {"assignee": "claude"}):
            with self.assertRaises(server.ApiError) as cm:
                server.handle("PATCH", f"/api/tasks/{tid}", {}, body, authed=True)
            self.assertEqual(cm.exception.status, 400, body)
            self.assertEqual(cm.exception.code, "bad_argument", body)
            self.assertIsNone(self.orchestrator(tid), body)

    def test_w2_post_ignores_old_field(self) -> None:
        status, task = server.handle("POST", "/api/tasks", {},
                                     {"title": "t", "assignee": "me"}, authed=True)
        self.assertEqual(status, 201)
        self.assertIsNone(task["orchestrator"])
        self.assertIsNone(self.orchestrator(task["id"]))


class ReadTests(OrchestratorCase):
    """F1, K1: фильтр, статистика, фасеты, акторы, карточка."""

    def setUp(self) -> None:
        super().setUp()
        self.by_listik = self.new(title="От Listik")
        self.by_claude = self.new(title="От Claude")
        self.set_orchestrator(self.by_listik, "listik")
        self.set_orchestrator(self.by_claude, "claude")

    def test_f1_filter(self) -> None:
        ids = [t["id"] for t in store.list_tasks(self.conn, orchestrator="listik")["tasks"]]
        self.assertEqual(ids, [self.by_listik])
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            status, res = server.handle("GET", "/api/tasks", {"orchestrator": "listik"}, {},
                                        authed=True)
        self.assertEqual(status, 200)
        self.assertEqual([t["id"] for t in res["tasks"]], [self.by_listik])

    def test_f1_facets_and_stats(self) -> None:
        facets = store.facet_values(self.conn)
        self.assertIn("orchestrators", facets)
        self.assertNotIn("assignees", facets)
        self.assertIn("listik", facets["orchestrators"])
        stats = store.stats(self.conn)
        self.assertNotIn("by_actor", stats)
        items = {i["orchestrator"]: i for i in stats["by_orchestrator"]}
        self.assertEqual(items["listik"], {"orchestrator": "listik", "title": "listik",
                                           "count": 1})
        self.assertEqual(set(items["claude"]), {"orchestrator", "title", "count"})

    def test_f1_actor_counts_by_orchestrator(self) -> None:
        mine = self.new(title="Моя")
        self.set_orchestrator(mine, "me")
        actors = {a["key"]: a for a in store.list_actors(self.conn)}
        self.assertEqual(actors["me"]["n_tasks"], 1)

    def test_k1_card_fields(self) -> None:
        task = store.get_task(self.conn, self.by_listik)
        self.assertEqual(task["orchestrator"], "listik")
        self.assertEqual(task["orchestrator_title"], "listik")
        self.assertNotIn("assignee", task)
        self.assertNotIn("assignee_title", task)
        empty = store.get_task(self.conn, self.new(title="Пустая"))
        self.assertIsNone(empty["orchestrator"])
        self.assertEqual(empty["orchestrator_title"], "—")


class SearchTests(OrchestratorCase):
    """S1: поиск отдаёт и фильтрует по оркестратору."""

    WORD = "квазиоркестрон"

    def test_s1_search(self) -> None:
        tid = self.new(title=f"Карточка {self.WORD}")
        self.set_orchestrator(tid, "listik")
        res = search.search(self.conn, self.WORD, mode="text")
        hit = next(r for r in res["results"] if r["id"] == tid)
        self.assertEqual(hit["orchestrator"], "listik")
        self.assertIn("orchestrator_title", hit)
        self.assertNotIn("assignee", hit)

        found = search.search(self.conn, self.WORD, mode="text", actor="listik")
        self.assertIn(tid, [r["id"] for r in found["results"]])
        missed = search.search(self.conn, self.WORD, mode="text", actor="nobody")
        self.assertNotIn(tid, [r["id"] for r in missed["results"]])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            search.print_results(res)
        self.assertIn("оркестратор:", out.getvalue())
        self.assertNotIn("исполнитель:", out.getvalue())


class CliTests(OrchestratorCase):
    """CLI1–CLI4: `show`, `new --assignee`, `list --orchestrator`, строка списка."""

    def setUp(self) -> None:
        super().setUp()
        for patch in (mock.patch.object(paths, "DB_PATH", self.db_path),
                      mock.patch.object(db_mod, "init", return_value=self.conn)):
            patch.start()
            self.addCleanup(patch.stop)
        self.cli = _load_cli()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_cli1_show(self) -> None:
        tid = self.new()
        self.set_orchestrator(tid, "listik")
        code, out, _ = self.run_cli(["--local", "show", tid])
        self.assertEqual(code, 0)
        self.assertIn("оркестратор: listik", out)
        self.assertNotIn("исполнитель:", out)

    def test_cli2_new_rejects_assignee(self) -> None:
        code, _, err = self.run_cli(["--local", "new", "t", "--assignee", "x"])
        self.assertEqual(code, 2)
        self.assertIn("неизвестный аргумент: --assignee", err)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    def test_cli3_list_filter(self) -> None:
        listik_id, claude_id = self.new(title="Первая"), self.new(title="Вторая")
        self.set_orchestrator(listik_id, "listik")
        self.set_orchestrator(claude_id, "claude")
        code, out, _ = self.run_cli(["--local", "list", "--orchestrator", "listik"])
        self.assertEqual(code, 0)
        self.assertIn(listik_id, out)
        self.assertNotIn(claude_id, out)

    def test_cli4_list_line(self) -> None:
        tid = self.new()
        self.set_orchestrator(tid, "listik")
        code, out, _ = self.run_cli(["--local", "list"])
        self.assertEqual(code, 0)
        line = next(x for x in out.splitlines() if tid in x)
        self.assertIn("оркестратор listik", line)
        self.assertNotIn("назначен", out)


class ImportTests(TempDbTestCase):
    """I1: смена `assignee` в выгрузке bd не попадает ни в поле, ни в diff."""

    def test_i1_update_ignores_assignee(self) -> None:
        source = FIXTURES_DIR / "writerllm" / "export.jsonl"
        lines = source.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() and json.loads(line).get("id") == "WL-a1":
                record = json.loads(line)
                self.assertEqual(record["assignee"], "dfomin")
                record["assignee"] = "ivan petrov"
                lines[i] = json.dumps(record, ensure_ascii=False)
                break
        else:
            self.fail("в выгрузке нет WL-a1")
        changed = self.tmp_path / "export-assignee.jsonl"
        changed.write_text("\n".join(lines) + "\n", encoding="utf-8")

        import_writerllm.import_file(self.conn, source)
        report = import_writerllm.import_file(self.conn, changed, update=True)
        self.assertEqual(report["updated"], 0)
        self.assertNotIn("WL-a1", [e["external_ref"] for e in report["examples"]["update"]])
        task = store.get_task(self.conn, "WL-a1")
        self.assertIsNone(task["orchestrator"])
        self.assertFalse([c for c in task["comments"] if "assignee" in c["text"]])


class McpTests(OrchestratorCase):
    """MCP1: схемы инструментов и фильтр `listik_list`."""

    def schema(self, name: str) -> dict:
        tool = next(t for t in mcp.TOOLS if t["name"] == name)
        return tool["inputSchema"]["properties"]

    def test_mcp1_schemas(self) -> None:
        self.assertNotIn("assignee", self.schema("listik_create"))
        self.assertIn("orchestrator", self.schema("listik_list"))
        self.assertNotIn("assignee", self.schema("listik_list"))

    def test_mcp1_list_filter(self) -> None:
        listik_id, claude_id = self.new(title="Первая"), self.new(title="Вторая")
        self.set_orchestrator(listik_id, "listik")
        self.set_orchestrator(claude_id, "claude")
        res = mcp.call_tool("listik_list", {"orchestrator": "listik"}, conn=self.conn)
        self.assertEqual([t["id"] for t in res["tasks"]], [listik_id])


if __name__ == "__main__":
    unittest.main()
