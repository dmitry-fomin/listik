import json
import os
import queue
import sqlite3
import subprocess
import sys
from contextlib import closing

from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN
from tests.test_local_bypass_warning import LocalBypassWarningCase
from listik import fence, server, store


class DepLinkCliTests(TempDbTestCase):
    def _run(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def test_link_with_second_arg_refuses_with_hint(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        p = self._run("dep", "link", a, b, "--dep-type", "parent-child")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(f"dep add {a} {b} --dep-type parent-child", p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        n = self.conn.execute("SELECT COUNT(*) FROM deps WHERE issue_id=?", (a,)).fetchone()[0]
        self.assertEqual(n, 0)

    def test_suggest_with_second_arg_refuses(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        p = self._run("dep", "suggest", a, "x-1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("dep add", p.stderr)


# --- listik-2y9s: dep link пишет связи через call("dep_add"), --json — один объект.

A, B, C, D, X = "demo-aaaa", "demo-bbbb", "demo-cccc", "demo-dddd", "demo-xxxx"
FENCE_ENV = (fence.ENV_TASK, fence.ENV_GENERATION, fence.ENV_DISPATCH)


def _env(**extra) -> dict:
    """Окружение подпроцесса без чужого токена поколения (его мог оставить лаунчер)."""
    env = {k: v for k, v in os.environ.items() if k not in FENCE_ENV}
    env.update(extra)
    return env


def _fixture(conn) -> None:
    """В тексте A упомянуты B и C, в этом порядке; D и X — вне текста."""
    for tid in (B, C, D):
        store.create_task(conn, title=f"задача {tid[-1]}", project="demo", task_id=tid)
    store.create_task(conn, title="задача a", project="demo", task_id=A,
                      description=f"упирается в {B}, потом {C}")
    store.create_task(conn, title="без упоминаний", project="demo", task_id=X)


def _cycle(conn) -> None:
    """B → D → A жёстко: связь A → B замкнула бы цикл, A → C — нет."""
    store.add_dep(conn, B, D, "blocks", created_by="me")
    store.add_dep(conn, D, A, "blocks", created_by="me")


def _deps(conn, issue_id: str) -> set:
    return {(r["depends_on"], r["dep_type"]) for r in conn.execute(
        "SELECT depends_on, dep_type FROM deps WHERE issue_id = ?", (issue_id,))}


class DepLinkLocalTests(TempDbTestCase):
    """`--local`: связи через client.local_call (fence), stdout с --json — один объект."""

    def setUp(self) -> None:
        super().setUp()
        _fixture(self.conn)
        # Порт, где никто не слушает: --local не ходит за health к живому серверу.
        self.port = LocalBypassWarningCase.free_port()

    def _run(self, *args, **env):
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", "--port", str(self.port), *args],
            capture_output=True, text=True, env=_env(LISTIK_DB=str(self.db_path), **env),
            cwd=str(LISTIK_BIN.parent.parent))

    def test_json_is_one_object(self) -> None:
        p = self._run("dep", "link", A, "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(set(out), {"candidates", "linked", "made", "skipped"})
        self.assertEqual([x["id"] for x in out["candidates"]], [B, C])
        self.assertEqual([x["depends_on"] for x in out["linked"]], [B, C])
        self.assertEqual(out["made"], 2)
        self.assertEqual(out["skipped"], [])
        self.assertEqual(_deps(self.conn, A), {(B, "relates-to"), (C, "relates-to")})

    def test_json_empty(self) -> None:
        p = self._run("dep", "link", X, "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout),
                         {"candidates": [], "linked": [], "made": 0, "skipped": []})

    def test_json_only(self) -> None:
        p = self._run("dep", "link", A, "--only", C, "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual([x["id"] for x in out["candidates"]], [B, C])
        self.assertEqual([x["depends_on"] for x in out["linked"]], [C])
        self.assertEqual(out["made"], 1)

    def test_text_output(self) -> None:  # регрессия
        p = self._run("dep", "link", A)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("создано связей: 2 (тип relates-to)", p.stdout)

    def test_cycle_skips_candidate_and_goes_on(self) -> None:
        _cycle(self.conn)
        p = self._run("dep", "link", A, "--dep-type", "blocks", "--actor", "me", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual([x["depends_on"] for x in out["linked"]], [C])
        self.assertEqual([(s["id"], s["code"]) for s in out["skipped"]], [(B, "conflict")])
        deps = _deps(self.conn, A)
        self.assertIn((C, "blocks"), deps)
        self.assertFalse([d for d in deps if d[0] == B], deps)

    def test_general_error_aborts(self) -> None:
        p = self._run("dep", "link", A, "--dep-type", "resource-blocks", "--json")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["error"]["code"], "bad_argument")
        self.assertEqual(_deps(self.conn, A), set())

    def test_zombie_is_revoked(self) -> None:
        self.conn.execute("UPDATE tasks SET generation = 2, dispatch_id = 'cur' WHERE id = ?",
                          (A,))
        self.conn.commit()
        p = self._run("dep", "link", A, "--json", **{fence.ENV_TASK: A,
                                                      fence.ENV_GENERATION: "1",
                                                      fence.ENV_DISPATCH: "old"})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)["error"]["code"], "revoked")
        self.assertEqual(_deps(self.conn, A), set())
        rejected = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id = ? AND kind = 'rejected'",
            (A,)).fetchone()[0]
        self.assertGreater(rejected, 0)


class DepLinkServerTests(LocalBypassWarningCase):
    """Живой сервер: связи идут по HTTP, доска получает кадр на каждую связь."""

    def setUp(self) -> None:
        super().setUp()
        _fixture(self.conn)
        # Отдельная база CLI: запись мимо сервера оказалась бы здесь.
        self.cli_db = self.tmp_path / "cli.db"
        self.events: queue.Queue = queue.Queue()
        with server._subs_lock:
            server._subs.append(self.events)

    def tearDown(self) -> None:
        with server._subs_lock:
            if self.events in server._subs:
                server._subs.remove(self.events)
        super().tearDown()

    def _run(self, *args):
        env = _env(LISTIK_CONFIG=str(self.config_path), LISTIK_DB=str(self.cli_db),
                   LISTIK_LOG=str(self.tmp_path / "listik.log"))
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--port", str(self.port), *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def _deps_frames(self) -> list:
        frames = []
        while True:
            try:
                frames.append(json.loads(self.events.get_nowait()))
            except queue.Empty:
                break
        return [f for f in frames
                if f["kind"] == "task" and f["payload"] == {"id": A, "action": "deps"}]

    def test_links_over_http(self) -> None:
        p = self._run("dep", "link", A, "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("не отвечает", p.stderr)
        self.assertEqual(json.loads(p.stdout)["made"], 2)
        self.assertEqual(_deps(self.conn, A), {(B, "relates-to"), (C, "relates-to")})
        self.assertEqual(len(self._deps_frames()), 2)
        if self.cli_db.exists():
            with closing(sqlite3.connect(self.cli_db)) as cli:
                self.assertEqual(cli.execute("SELECT COUNT(*) FROM deps").fetchone()[0], 0)

    def test_cycle_over_http(self) -> None:
        _cycle(self.conn)
        p = self._run("dep", "link", A, "--dep-type", "blocks", "--actor", "me", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual([(s["id"], s["code"]) for s in out["skipped"]], [(B, "conflict")])
        self.assertIn((C, "blocks"), _deps(self.conn, A))
