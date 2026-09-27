"""`done`/`release`: владелец и заметка одинаково во всех путях (listik-jaid, порция a).

Закрытие и освобождение идут через `store.close_task`/`store.release_task`, а те —
через `update_task` с `as_owner`: чужому в серверном режиме отказ по правилу PATCH и
по HTTP, и по MCP (`/mcp` и stdio), и в CLI (`--local` и через живой сервер), в том
числе через `stage` в `done`. Локальный режим не отказывает никому.

Базы и конфиги временные — фикстуры `OwnerStoreCase`/`OwnerHttpCase`/`OwnerCliCase`
(в них нет `test_*`-методов, поэтому импорт ничего не запускает повторно).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from listik import client, errors, mcp, paths, store
from listik import db as db_mod
from tests.test_claim import LISTIK_BIN
from tests.test_owner_cli import LOCAL_CONFIG as CLI_LOCAL_CONFIG
from tests.test_owner_cli import OwnerCliCase
from tests.test_owner_http import OwnerHttpCase
from tests.test_owner_store import OwnerStoreCase

#: «Задача не изменилась» — эти колонки плюс число её событий.
STATE_COLUMNS = ("status", "stage", "holder", "closed_at", "close_reason", "result",
                 "updated_at")


def state(conn, task_id: str) -> tuple:
    row = conn.execute(f"SELECT {', '.join(STATE_COLUMNS)} FROM tasks WHERE id = ?",
                       (task_id,)).fetchone()
    events = conn.execute("SELECT count(*) FROM events WHERE task_id = ?",
                          (task_id,)).fetchone()[0]
    return tuple(row), events


def events_of(conn, task_id: str, kind: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE task_id = ? AND kind = ? ORDER BY id", (task_id, kind))]


# ------------------------------------------------------------------ store

class StoreServerModeTests(OwnerStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_mode()
        self.tid = self.make(as_owner="ann")["id"]

    def claimed(self, **kwargs) -> str:
        tid = self.make(as_owner="ann", **kwargs)["id"]
        store.claim(self.conn, tid, holder="agent:dsh", as_owner="ann")
        return tid

    def test_foreign_close_forbidden(self) -> None:
        tid = self.claimed()
        before = state(self.conn, tid)
        with self.assertRaises(errors.Forbidden) as ctx:
            store.close_task(self.conn, tid, as_owner="bob", result="x", reason="r")
        self.assertIn("ann", str(ctx.exception))
        self.assertEqual(state(self.conn, tid), before)

    def test_foreign_release_forbidden(self) -> None:
        tid = self.claimed()
        before = state(self.conn, tid)
        with self.assertRaises(errors.Forbidden):
            store.release_task(self.conn, tid, as_owner="bob")
        self.assertEqual(store.get_task(self.conn, tid)["holder"], "agent:dsh")
        self.assertEqual(state(self.conn, tid), before)

    def test_foreign_noop_is_forbidden_too(self) -> None:
        before = state(self.conn, self.tid)
        with self.assertRaises(errors.Forbidden):
            store.release_task(self.conn, self.tid, as_owner="bob")
        self.assertEqual(state(self.conn, self.tid), before)
        store.close_task(self.conn, self.tid, as_owner="ann")
        before = state(self.conn, self.tid)
        with self.assertRaises(errors.Forbidden):
            store.close_task(self.conn, self.tid, as_owner="bob")
        self.assertEqual(state(self.conn, self.tid), before)

    def test_owner_closes_and_releases(self) -> None:
        tid = self.claimed()
        out = store.close_task(self.conn, tid, as_owner="ann")
        self.assertEqual((out["status"], out["stage"]), ("done", "done"))
        self.assertTrue(out["closed_at"])
        self.assertFalse(out["holder"])
        tid = self.claimed()
        self.assertFalse(store.release_task(self.conn, tid, as_owner="ann")["holder"])

    def test_anonymous_passes(self) -> None:
        tid = self.claimed()
        self.assertFalse(store.release_task(self.conn, tid)["holder"])
        self.assertEqual(store.close_task(self.conn, tid)["status"], "done")

    def test_unknown_owner_bad_argument(self) -> None:
        tid = self.claimed()
        before = state(self.conn, tid)
        with self.assertRaises(errors.BadArgument):
            store.close_task(self.conn, tid, as_owner="carol")
        with self.assertRaises(errors.BadArgument):
            store.release_task(self.conn, tid, as_owner="carol")
        self.assertEqual(state(self.conn, tid), before)

    def test_ownerless_task_closed_by_anyone(self) -> None:
        self.conn.execute("UPDATE tasks SET owner = NULL WHERE id = ?", (self.tid,))
        self.conn.commit()
        self.assertEqual(store.close_task(self.conn, self.tid, as_owner="bob")["status"],
                         "done")

    def test_release_note(self) -> None:
        tid = self.claimed()
        store.release_task(self.conn, tid, as_owner="ann")
        self.assertEqual(events_of(self.conn, tid, "release")[-1]["note"], "освободил")
        store.claim(self.conn, tid, holder="agent:dsh", as_owner="ann")
        store.release_task(self.conn, tid, as_owner="ann", note="ухожу", harness="grok")
        last = events_of(self.conn, tid, "release")[-1]
        self.assertEqual((last["note"], last["harness"]), ("ухожу", "grok"))

    def test_close_reason(self) -> None:
        cases = (({"reason": "R", "result": "X"}, "R"), ({"result": "X"}, "X"), ({}, None))
        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                task = self.make(as_owner="ann")
                out = store.close_task(self.conn, task["id"], as_owner="ann", **kwargs)
                self.assertEqual(out["close_reason"], expected)
                # `result` пишется, только если передан.
                self.assertEqual(out["result"], kwargs.get("result", task["result"]))

    def test_stage_done_forbidden(self) -> None:
        explicit = self.claimed()
        implicit = self.claimed(stage="s4-judge")
        for tid, kwargs in ((explicit, {"to_stage": "done"}), (implicit, {})):
            with self.subTest(kwargs=kwargs):
                before = state(self.conn, tid)
                with self.assertRaises(errors.Forbidden):
                    store.next_stage(self.conn, tid, as_owner="bob", **kwargs)
                self.assertEqual(state(self.conn, tid), before)

    def test_stage_done_by_owner_and_anonymous(self) -> None:
        for as_owner in ("ann", None):
            with self.subTest(as_owner=as_owner):
                tid = self.claimed(stage="s3-impl")
                out = store.next_stage(self.conn, tid, to_stage="done", as_owner=as_owner)
                self.assertEqual(out["status"], "done")
                self.assertFalse(out["holder"])
                self.assertEqual(events_of(self.conn, tid, "stage")[-1]["note"],
                                 "этап -> done (закрыта из s3-impl)")

    def test_stage_not_done_ignores_owner(self) -> None:
        tid = self.make(as_owner="ann", stage="s1-spec")["id"]
        self.assertEqual(store.next_stage(self.conn, tid, as_owner="bob")["stage"], "s2-review")


class StoreLocalModeTests(OwnerStoreCase):
    def owned_by_ann(self) -> str:
        tid = self.make()["id"]
        self.conn.execute("UPDATE tasks SET owner = 'ann' WHERE id = ?", (tid,))
        self.conn.commit()
        store.claim(self.conn, tid, holder="agent:dsh")
        return tid

    def test_local_mode_never_refuses(self) -> None:
        self.assertEqual(
            store.close_task(self.conn, self.owned_by_ann(), as_owner="bob")["status"], "done")
        self.assertFalse(
            store.release_task(self.conn, self.owned_by_ann(), as_owner="bob")["holder"])
        self.assertEqual(store.next_stage(self.conn, self.owned_by_ann(), to_stage="done",
                                          as_owner="bob")["status"], "done")


# ------------------------------------------------------------------ MCP stdio

class McpStdioTests(OwnerStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_mode()
        self.tid = self.make(as_owner="ann")["id"]
        store.claim(self.conn, self.tid, holder="agent:dsh", as_owner="ann")

    def call(self, name: str, owner: str) -> dict:
        with mock.patch.dict(os.environ, {"LISTIK_OWNER": owner}):
            return mcp.call_tool(name, {"id": self.tid}, conn=self.conn)

    def test_release(self) -> None:
        before = state(self.conn, self.tid)
        with self.assertRaises(errors.Forbidden):
            self.call("listik_release", "bob")
        self.assertEqual(state(self.conn, self.tid), before)
        self.assertFalse(self.call("listik_release", "ann")["holder"])

    def test_done(self) -> None:
        before = state(self.conn, self.tid)
        with self.assertRaises(errors.Forbidden):
            self.call("listik_done", "bob")
        self.assertEqual(state(self.conn, self.tid), before)
        self.assertEqual(self.call("listik_done", "ann")["status"], "done")


# ------------------------------------------------------------------ local_call и ограждение

class LocalCallFenceTests(OwnerStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.tid = self.make()["id"]
        store.claim(self.conn, self.tid, holder="agent:dsh")
        self.conn.execute("UPDATE tasks SET generation = 2, dispatch_id = 'cur' WHERE id = ?",
                          (self.tid,))
        self.conn.commit()
        for patch in (mock.patch.object(paths, "DB_PATH", self.db_path),
                      mock.patch.object(db_mod, "init", return_value=self.conn)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_stale_token_rejected(self) -> None:
        for op in ("done", "release"):
            with self.subTest(op=op):
                before = len(events_of(self.conn, self.tid, "rejected"))
                with self.assertRaises(errors.Revoked):
                    client.local_call(op, fence={"task_id": self.tid, "generation": 1,
                                                 "dispatch_id": "old"},
                                      task_id=self.tid, actor="agent:x")
                rejected = events_of(self.conn, self.tid, "rejected")
                self.assertEqual(len(rejected), before + 1)
                self.assertEqual(json.loads(rejected[-1]["note"])["op"], op)
                task = store.get_task(self.conn, self.tid)
                self.assertNotEqual(task["status"], "done")
                self.assertEqual(task["holder"], "agent:dsh")


# ------------------------------------------------------------------ HTTP и /mcp

class HttpCase(OwnerHttpCase):
    def setUp(self) -> None:
        super().setUp()
        self.db = db_mod.connect(self.db_path)
        self.tid = self.claimed()

    def claimed(self, **body) -> str:
        tid = self.make_task("ann", **body)["id"]
        self.data(*self.api("POST", f"/api/tasks/{tid}/claim", owner="ann",
                            body={"holder": "agent:dsh"}))
        return tid

    def state(self, tid: str) -> tuple:
        return state(self.db, tid)


class HttpTests(HttpCase):
    def assertForbidden(self, tid: str, action: str, body: dict | None = None,
                        owner: str = "bob") -> None:
        before = self.state(tid)
        status, payload = self.api("POST", f"/api/tasks/{tid}/{action}", owner=owner,
                                   body=body or {})
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["code"], "forbidden")
        self.assertIn("ann", payload["error"])
        self.assertEqual(self.state(tid), before)

    def test_foreign_done_release_stage(self) -> None:
        self.assertForbidden(self.tid, "done", {"result": "x"})
        self.assertForbidden(self.tid, "release")
        self.assertForbidden(self.tid, "stage", {"to": "done"})
        self.assertForbidden(self.claimed(stage="s4-judge"), "stage")

    def test_foreign_noop_forbidden(self) -> None:
        free = self.make_task("ann")["id"]
        self.assertForbidden(free, "release")
        self.data(*self.api("POST", f"/api/tasks/{free}/done", owner="ann", body={}))
        self.assertForbidden(free, "done")

    def test_identity_not_from_body(self) -> None:
        self.assertForbidden(self.tid, "done", {"owner": "ann"})
        self.assertForbidden(self.tid, "done", {"as_owner": "ann"})

    def test_owner_and_anonymous(self) -> None:
        task = self.data(*self.api("POST", f"/api/tasks/{self.tid}/done", owner="ann", body={}))
        self.assertEqual(task["status"], "done")
        other = self.claimed()
        task = self.data(*self.api("POST", f"/api/tasks/{other}/release", owner="ann",
                                   body={}))
        self.assertFalse(task["holder"])
        task = self.data(*self.api("POST", f"/api/tasks/{other}/done", body={}))
        self.assertEqual(task["status"], "done")

    def test_unknown_owner(self) -> None:
        before = self.state(self.tid)
        status, payload = self.api("POST", f"/api/tasks/{self.tid}/done", owner="carol",
                                   body={})
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], "bad_argument")
        self.assertEqual(self.state(self.tid), before)

    def test_note_and_harness(self) -> None:
        self.data(*self.api("POST", f"/api/tasks/{self.tid}/release", owner="ann",
                            body={"harness": "grok"}))
        release = events_of(self.db, self.tid, "release")[-1]
        self.assertEqual((release["harness"], release["note"]), ("grok", "освободил"))
        self.data(*self.api("POST", f"/api/tasks/{self.tid}/done", owner="ann",
                            body={"note": "N", "harness": "grok"}))
        closed = events_of(self.db, self.tid, "status")[-1]
        self.assertEqual((closed["note"], closed["harness"]), ("N", "grok"))

    def test_stage_not_done_ignores_owner(self) -> None:
        tid = self.make_task("ann", stage="s1-spec")["id"]
        task = self.data(*self.api("POST", f"/api/tasks/{tid}/stage", owner="bob", body={}))
        self.assertEqual(task["stage"], "s2-review")


class McpHttpTests(HttpCase):
    def tool(self, name: str, arguments: dict, owner: str) -> dict:
        status, _, body = self.call(
            "POST", "/mcp", {"Authorization": "Bearer test-token", "X-Listik-Owner": owner},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": name, "arguments": arguments}})
        self.assertEqual(status, 200, body)
        return body["result"]

    def assertRefused(self, name: str, arguments: dict) -> None:
        before = self.state(self.tid)
        result = self.tool(name, arguments, "bob")
        self.assertTrue(result.get("isError"), result)
        self.assertIn("принадлежит ann", result["content"][0]["text"])
        self.assertEqual(self.state(self.tid), before)

    def test_foreign_done_and_release(self) -> None:
        self.assertRefused("listik_done", {"id": self.tid})
        self.assertRefused("listik_release", {"id": self.tid})
        self.assertRefused("listik_done", {"id": self.tid, "owner": "ann"})

    def test_owner_closes(self) -> None:
        result = self.tool("listik_done", {"id": self.tid}, "ann")
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(json.loads(result["content"][0]["text"])["status"], "done")


# ------------------------------------------------------------------ CLI --local

class CliLocalServerModeTests(OwnerCliCase):
    def setUp(self) -> None:
        super().setUp()
        self.tid = self.new_task("--owner", "ann")["id"]
        self.json_out(self.run_cli("claim", self.tid, "--owner", "ann",
                                   "--holder", "agent:dsh", "--json"))

    def test_foreign_refused(self) -> None:
        for args in (("done", self.tid), ("release", self.tid),
                     ("stage", self.tid, "--to", "done")):
            with self.subTest(cmd=args[0]):
                before = state(self.conn, self.tid)
                err = self.error_json(self.run_cli("--owner", "bob", *args, "--json"))
                self.assertEqual(err["code"], "forbidden")
                self.assertIn("owner=", err["hint"])
                self.assertEqual(state(self.conn, self.tid), before)

    def test_owner_closes(self) -> None:
        out = self.json_out(self.run_cli("--owner", "ann", "done", self.tid, "--json"))
        self.assertEqual(out["status"], "done")


class CliLocalModeTests(OwnerCliCase):
    def setUp(self) -> None:
        super().setUp()
        self.config_path.write_text(CLI_LOCAL_CONFIG, encoding="utf-8")
        self.tid = self.new_task()["id"]
        self.json_out(self.run_cli("claim", self.tid, "--holder", "agent:dsh", "--json"))

    def test_done_keeps_note_and_harness(self) -> None:
        self.json_out(self.run_cli("done", self.tid, "--note", "заметка X",
                                   "--harness", "grok", "--json"))
        task = self.json_out(self.run_cli("show", self.tid, "--json"))
        [event] = [e for e in task["events"] if e["kind"] == "status" and e["to_value"] == "done"]
        self.assertEqual((event["note"], event["harness"]), ("заметка X", "grok"))

    def test_release_note(self) -> None:
        self.json_out(self.run_cli("release", self.tid, "--json"))
        self.assertEqual(events_of(self.conn, self.tid, "release")[-1]["note"], "освободил")
        self.json_out(self.run_cli("claim", self.tid, "--holder", "agent:dsh", "--json"))
        self.json_out(self.run_cli("release", self.tid, "--note", "ухожу",
                                   "--harness", "grok", "--json"))
        event = events_of(self.conn, self.tid, "release")[-1]
        self.assertEqual((event["note"], event["harness"]), ("ухожу", "grok"))


# ------------------------------------------------------------------ CLI через живой сервер

class CliServerTests(HttpCase):
    def run_cli(self, *args):
        env = {**os.environ, "LISTIK_CONFIG": str(paths.CONFIG_PATH),
               "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": os.path.join(self._tmp.name, "listik.log")}
        env.pop("LISTIK_OWNER", None)
        proc = subprocess.run([sys.executable, str(LISTIK_BIN), "--port", str(self.port), *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))
        self.assertNotIn("не отвечает", proc.stderr)
        return proc

    def refused(self, *args) -> None:
        before = self.state(self.tid)
        proc = self.run_cli("--owner", "bob", *args, "--json")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["error"]["code"], "forbidden")
        self.assertEqual(self.state(self.tid), before)

    def ok(self, *args) -> dict:
        proc = self.run_cli("--owner", "ann", *args, "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def test_foreign_refused(self) -> None:
        self.refused("done", self.tid)
        self.refused("release", self.tid)
        self.assertEqual(self.get_task(self.tid)["holder"], "agent:dsh")

    def test_owner_passes(self) -> None:
        self.assertEqual(self.ok("done", self.tid)["status"], "done")
        self.assertFalse(self.ok("release", self.claimed())["holder"])


if __name__ == "__main__":
    unittest.main()
