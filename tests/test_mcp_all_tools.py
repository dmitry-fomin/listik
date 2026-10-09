"""Каждый инструмент MCP — по HTTP (`POST /mcp`), в локальном и серверном режиме (listik-r69k, порция c).

`CASES` — контракт: на каждый инструмент из `mcp.TOOLS` по кейсу `Case(setup, args, check)`.
Его же импортирует порция `d` и гоняет через stdio-прокси в другом процессе, поэтому:

* `setup(conn, ctx)` готовит фикстуры прямо в базе сервера (`store`/`routes_store` по
  переданному соединению), `ctx` — `{"tmp": Path, "owner": "ann", "project": "demo"}`;
  возвращает словарь фикстур, дополнив `ctx`. От транспорта и способа запуска сервера
  не зависит;
* `args(fx)` — аргументы `tools/call`; `project` у инструментов, у которых он есть в
  схеме, передаётся явно;
* `check(test, fx, payload)` — проверки разобранного JSON из `result.content[0].text`.

Плюс: stdio-путь `listik_launch`/`listik_revoke`/`listik_delete` (запрос к своему
серверу, без сервера — отказ или локальное удаление), владелец на управляющих
инструментах и ограждение по поколению.
"""
from __future__ import annotations

import json
import os
import pathlib
import queue
import socket
import sys
import time
import unittest
from typing import Callable, NamedTuple
from unittest import mock

from listik import client, db as db_mod, deps as deps_mod, harnesses_store, launcher as launcher_mod
from listik import mcp, paths, routes_store, stage_launch, store
from tests.test_mcp_http import AUTH, TOKEN, McpHttpCase
from tests.test_mcp_stdio_notify import StdioNotifyCase, rpc_reply, tool_call

OWNER = "ann"
PROJECT = "demo"
OWNER_HEADERS = {**AUTH, "X-Listik-Owner": OWNER}
SERVER_CONFIG = (f'[auth]\ntoken = "{TOKEN}"\n\n'
                 '[server]\nmode = "server"\nusers = ["ann", "bob"]\n')

FAKE_PY = """\
import pathlib, sys, time
pathlib.Path(sys.argv[1]).write_text("started", encoding="utf-8")
time.sleep(2)
"""


class Case(NamedTuple):
    setup: Callable[[object, dict], dict]
    args: Callable[[dict], dict]
    check: Callable[[unittest.TestCase, dict, object], None]


# ---------------------------------------------------------------- фикстуры

def _task(conn, ctx: dict, title: str = "Задача", **kwargs) -> str:
    return store.create_task(conn, title=title, project=ctx["project"],
                             owner=ctx["owner"], **kwargs)["id"]


def _with_task(conn, ctx: dict) -> dict:
    return {**ctx, "id": _task(conn, ctx)}


def _claimed(conn, ctx: dict) -> dict:
    fx = _with_task(conn, ctx)
    store.claim(conn, fx["id"], holder="agent:claude", actor="agent:claude",
                as_owner=ctx["owner"])
    return fx


def _pair(conn, ctx: dict) -> dict:
    return {**ctx, "id": _task(conn, ctx, "Ждёт"), "other": _task(conn, ctx, "Блокер")}


def _fake_route(conn, ctx: dict, key: str) -> pathlib.Path:
    """Маршрут-подделка: процесс пишет файл-след и спит ~2 с. Возвращает путь следа."""
    tmp = pathlib.Path(ctx["tmp"])
    script = tmp / "fake_worker.py"
    script.write_text(FAKE_PY, encoding="utf-8")
    trace = tmp / f"trace-{key}"
    routes_store.upsert_route(conn, {
        "key": key, "kind": "pipeline", "title": "Подделка", "visible": True,
        "command": [sys.executable, str(script), str(trace)]})
    return trace


def _launchable(conn, ctx: dict, key: str) -> dict:
    trace = _fake_route(conn, ctx, key)
    tid = _task(conn, ctx, route=key)
    store.update_task(conn, tid, worktree=str(ctx["tmp"]))
    return {**ctx, "id": tid, "trace": str(trace)}


def _setup_launch(conn, ctx: dict) -> dict:
    return _launchable(conn, ctx, "fake-launch")


def _setup_revoke(conn, ctx: dict) -> dict:
    fx = _launchable(conn, ctx, "fake-revoke")
    result = launcher_mod.start(conn, fx["id"], log_dir=pathlib.Path(ctx["tmp"]) / "logs")
    if result is not None:
        raise AssertionError(f"запуск для отзыва не удался: {result}")
    wait_file(fx["trace"])
    fx["generation"] = int(conn.execute("SELECT generation FROM tasks WHERE id = ?",
                                        (fx["id"],)).fetchone()["generation"])
    return fx


SWARM_ROLES = {role: {"harness": "probe"} for _, role in stage_launch.STAGE_ROLES}


def _setup_restart(conn, ctx: dict) -> dict:
    if not any(h["key"] == "probe" for h in harnesses_store.list_harnesses(conn)):
        harnesses_store.create(conn, {"key": "probe", "label": "probe",
                                      "argv": [sys.executable, "-c", "print('готово')"]})
    routes_store.upsert_route(conn, {"key": "roy", "kind": "swarm", "title": "Рой",
                                     "roles": SWARM_ROLES})
    tid = _task(conn, ctx, route="roy", stage="s2-review")
    conn.execute("UPDATE tasks SET launch_driver = 'swarm' WHERE id = ?", (tid,))
    conn.commit()
    return {**ctx, "id": tid}


def _setup_portions(conn, ctx: dict) -> dict:
    tid = _task(conn, ctx, "Шаг")
    steps = pathlib.Path(ctx["tmp"]) / f"steps-{tid}"
    steps.mkdir()
    for name in (f"{tid}.md", f"{tid}.a.md", f"{tid}.b.md"):
        (steps / name).write_text(f"# {name}\n", encoding="utf-8")
    store.update_task(conn, tid, spec_path=str(steps / f"{tid}.md"))
    return {**ctx, "id": tid}


def _setup_mentions(conn, ctx: dict) -> dict:
    other = _task(conn, ctx, "Упомянутая")
    tid = _task(conn, ctx, "Упоминает", description=f"смотри {other}")
    return {**ctx, "id": tid, "other": other}


def _setup_blocked(conn, ctx: dict) -> dict:
    fx = _pair(conn, ctx)
    store.add_dep(conn, fx["id"], fx["other"], "blocks", None, confirm=True)
    return fx


def _setup_suggested(conn, ctx: dict) -> dict:
    fx = _pair(conn, ctx)
    store.add_dep(conn, fx["id"], fx["other"], "blocks", "agent:claude", confirm=False)
    return fx


def _setup_document(conn, ctx: dict) -> dict:
    from listik import documents
    fx = _with_task(conn, ctx)
    documents.put_document(conn, fx["id"], "spec", "# ТЗ\nсуть\n", actor="agent:claude")
    return fx


def _setup_needs_owner(conn, ctx: dict) -> dict:
    fx = _with_task(conn, ctx)
    store.set_needs_owner(conn, fx["id"], value=True, text="как быть?", actor="agent:claude")
    return fx


def _setup_memory(conn, ctx: dict) -> dict:
    store.remember(conn, "грабли окружения", key="rake", project=ctx["project"])
    return dict(ctx)


def _setup_comment(conn, ctx: dict) -> dict:
    fx = _with_task(conn, ctx)
    store.add_comment(conn, fx["id"], "запись", author="agent:claude", kind="journal")
    return fx


def _ids(items) -> list[str]:
    return [item["id"] for item in items]


def wait_file(path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not pathlib.Path(path).exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"нет файла-следа {path} за {timeout} с")
        time.sleep(0.05)


def _check_launch(test, fx, payload) -> None:
    test.assertTrue(payload["launched"], payload)
    test.assertEqual(payload["id"], fx["id"])
    wait_file(fx["trace"])


# ---------------------------------------------------------------- контракт

CASES: dict[str, Case] = {
    "listik_search": Case(
        lambda conn, ctx: {**ctx, "id": _task(conn, ctx, "Редкоеслово зебра")},
        lambda fx: {"query": "Редкоеслово", "project": fx["project"], "mode": "text"},
        lambda t, fx, p: t.assertIn(fx["id"], _ids(p["items"] if "items" in p else p["results"]))),
    "listik_list": Case(
        _with_task,
        lambda fx: {"project": fx["project"]},
        lambda t, fx, p: t.assertIn(fx["id"], _ids(p["tasks"]))),
    "listik_show": Case(
        _with_task,
        lambda fx: {"id": fx["id"]},
        lambda t, fx, p: t.assertEqual(p["id"], fx["id"])),
    "listik_create": Case(
        lambda conn, ctx: dict(ctx),
        lambda fx: {"title": "Новая", "project": fx["project"]},
        lambda t, fx, p: (t.assertTrue(p["id"]), t.assertEqual(p["project"], fx["project"]))),
    "listik_update": Case(
        _with_task,
        lambda fx: {"id": fx["id"], "fields": {"priority": 1}},
        lambda t, fx, p: t.assertEqual(p["priority"], 1)),
    "listik_context": Case(
        _with_task,
        lambda fx: {"id": fx["id"], "stage": "s1-spec"},
        lambda t, fx, p: t.assertIn(fx["id"], json.dumps(p, ensure_ascii=False))),
    "listik_put_document": Case(
        _with_task,
        lambda fx: {"id": fx["id"], "kind": "spec", "content": "# ТЗ\nсуть\n"},
        lambda t, fx, p: t.assertEqual(p["kind"], "spec")),
    "listik_get_document": Case(
        _setup_document,
        lambda fx: {"id": fx["id"], "kind": "spec"},
        lambda t, fx, p: t.assertIn("суть", p["content"])),
    "listik_claim": Case(
        _with_task,
        lambda fx: {"id": fx["id"], "holder": "agent:claude", "actor": "agent:claude"},
        lambda t, fx, p: t.assertEqual(p["holder"], "agent:claude")),
    "listik_heartbeat": Case(
        _claimed,
        lambda fx: {"id": fx["id"], "holder": "agent:claude", "actor": "agent:claude",
                    "note": "работаю"},
        lambda t, fx, p: t.assertEqual(p["holder"], "agent:claude")),
    "listik_stage": Case(
        _with_task,
        lambda fx: {"id": fx["id"], "to": "s2-review"},
        lambda t, fx, p: t.assertEqual(p["stage"], "s2-review")),
    "listik_comment": Case(
        _with_task,
        lambda fx: {"id": fx["id"], "text": "привет", "kind": "journal"},
        lambda t, fx, p: t.assertEqual(p["text"], "привет")),
    "listik_needs_owner": Case(
        _with_task,
        lambda fx: {"id": fx["id"], "text": "как быть?"},
        lambda t, fx, p: t.assertTrue(p["needs_owner"])),
    "listik_done": Case(
        _with_task,
        lambda fx: {"id": fx["id"], "result": "сделано"},
        lambda t, fx, p: t.assertEqual(p["status"], "done")),
    "listik_ready": Case(
        _with_task,
        lambda fx: {"project": fx["project"]},
        lambda t, fx, p: t.assertIn(fx["id"], _ids(p["tasks"]))),
    "listik_blocked": Case(
        _setup_blocked,
        lambda fx: {"project": fx["project"]},
        lambda t, fx, p: t.assertIn(fx["id"], json.dumps(p["tasks"], ensure_ascii=False))),
    "listik_can_take": Case(
        _with_task,
        lambda fx: {"id": fx["id"]},
        lambda t, fx, p: t.assertEqual(p["task_id"], fx["id"])),
    "listik_dep_tree": Case(
        _setup_blocked,
        lambda fx: {"id": fx["id"]},
        lambda t, fx, p: t.assertIn(fx["other"], json.dumps(p, ensure_ascii=False))),
    "listik_board": Case(
        _with_task,
        lambda fx: {"project": fx["project"]},
        lambda t, fx, p: t.assertIn(fx["id"], json.dumps(p, ensure_ascii=False))),
    "listik_stats": Case(
        _with_task,
        lambda fx: {"project": fx["project"]},
        lambda t, fx, p: t.assertIn(fx["project"], json.dumps(p, ensure_ascii=False))),
    "listik_deps": Case(
        _pair,
        lambda fx: {"id": fx["id"], "depends_on": fx["other"], "dep_type": "relates-to"},
        lambda t, fx, p: t.assertEqual(p["dep_type"], "relates-to")),
    "listik_release": Case(
        _claimed,
        lambda fx: {"id": fx["id"], "actor": "agent:claude"},
        lambda t, fx, p: t.assertFalse(p["holder"])),
    "listik_inbox": Case(
        _setup_needs_owner,
        lambda fx: {},
        lambda t, fx, p: t.assertIn(fx["id"], _ids(p["questions"]))),
    "listik_memory": Case(
        _setup_memory,
        lambda fx: {"project": fx["project"]},
        lambda t, fx, p: t.assertIn("rake", [i["key"] for i in p["items"]])),
    "listik_remember": Case(
        lambda conn, ctx: dict(ctx),
        lambda fx: {"text": "договорённость", "key": "deal", "project": fx["project"]},
        lambda t, fx, p: t.assertEqual(p["key"], "deal")),
    "listik_projects": Case(
        lambda conn, ctx: {**ctx, "slug": store.upsert_project(
            conn, ctx["project"], title=ctx["project"])["slug"]},
        lambda fx: {},
        lambda t, fx, p: t.assertIn(fx["project"], [pr["slug"] for pr in p["projects"]])),
    "listik_actors": Case(
        _setup_comment,
        lambda fx: {},
        lambda t, fx, p: t.assertTrue(p["actors"])),
    "listik_timeline": Case(
        _setup_comment,
        lambda fx: {},
        lambda t, fx, p: t.assertIn(fx["id"], json.dumps(p["items"], ensure_ascii=False))),
    "listik_deps_suggested": Case(
        _setup_suggested,
        lambda fx: {"project": fx["project"]},
        lambda t, fx, p: t.assertIn(fx["id"], [i["issue_id"] for i in p["items"]])),
    "listik_cycles": Case(
        _pair,
        lambda fx: {},
        lambda t, fx, p: t.assertIsInstance(p["cycles"], list)),
    "listik_waves": Case(
        _with_task,
        lambda fx: {"project": fx["project"]},
        lambda t, fx, p: t.assertIsInstance(p["waves"], list)),
    "listik_delete": Case(
        _with_task,
        lambda fx: {"id": fx["id"]},
        lambda t, fx, p: t.assertEqual(p, {"deleted": fx["id"]})),
    "listik_mentions": Case(
        _setup_mentions,
        lambda fx: {"id": fx["id"], "link": True},
        lambda t, fx, p: (t.assertEqual(p["made"], 1),
                          t.assertEqual(_ids(p["candidates"]), [fx["other"]]))),
    "listik_portions": Case(
        _setup_portions,
        lambda fx: {"id": fx["id"], "action": "sync"},
        lambda t, fx, p: t.assertEqual(len(p["created"]), 2)),
    "listik_restart": Case(
        _setup_restart,
        lambda fx: {"id": fx["id"], "stage": "s1-spec"},
        lambda t, fx, p: t.assertEqual(p["stage"], "s1-spec")),
    "listik_revoke": Case(
        _setup_revoke,
        lambda fx: {"id": fx["id"], "note": "проверка"},
        lambda t, fx, p: t.assertEqual(p["generation"], fx["generation"] + 1)),
    "listik_launch": Case(
        _setup_launch,
        lambda fx: {"id": fx["id"]},
        _check_launch),
}


# ---------------------------------------------------------------- обвязка

class _LaunchCleanup:
    """Логи запусков — во временный каталог; процессы-подделки гасятся в tearDown."""

    def _patch_logs(self) -> None:
        launcher_mod._trackers.clear()
        patch = mock.patch.object(paths, "LOGS_DIR", pathlib.Path(self._tmp.name) / "logs")
        patch.start()
        self.addCleanup(patch.stop)

    def _kill_launched(self, conn) -> None:
        rows = conn.execute("SELECT id FROM tasks WHERE launched_by = 'listik' "
                            "AND launch_pid IS NOT NULL AND launch_finished_at IS NULL "
                            "AND dispatch_id IS NOT NULL").fetchall()
        for row in rows:
            try:
                launcher_mod.revoke(conn, row["id"], kill=True)
            except Exception:  # noqa: BLE001 — процесс уже завершился сам
                pass
        for thread in list(launcher_mod._trackers.values()):
            thread.join(timeout=15)


class AllToolsLocal(_LaunchCleanup, McpHttpCase):
    """Каждый инструмент — `tools/call` через `POST /mcp`, локальный конфиг."""

    def setUp(self) -> None:
        super().setUp()
        self._patch_logs()
        # схема — до первого запроса: соединение сервера создаётся лениво
        db_mod.init(self.db_path).close()
        self.conn = self.connect_db()
        self.ctx = {"tmp": pathlib.Path(self._tmp.name), "owner": OWNER, "project": PROJECT}

    def tearDown(self) -> None:
        self._kill_launched(self.conn)
        super().tearDown()

    def call_tool(self, name: str, arguments: dict) -> dict:
        status, _, raw = self.tool(name, arguments, headers=OWNER_HEADERS)
        self.assertEqual(status, 200, f"{name}: {raw!r}")
        return json.loads(raw)["result"]

    def test_every_tool_has_case(self) -> None:
        self.assertEqual(set(CASES), {t["name"] for t in mcp.TOOLS})

    def test_every_tool_over_http(self) -> None:
        for name, case in CASES.items():
            with self.subTest(tool=name):
                fx = case.setup(self.conn, dict(self.ctx))
                result = self.call_tool(name, case.args(fx))
                self.assertFalse(result.get("isError"), f"{name}: {result}")
                case.check(self, fx, json.loads(result["content"][0]["text"]))

    def test_portions_adopt_refusal_matches_http(self) -> None:
        """`adopt` на карточке не роя — штатный отказ, тот же, что у HTTP-ручки."""
        tid = _task(self.conn, self.ctx)
        result = self.call_tool("listik_portions", {"id": tid, "action": "adopt"})
        status, _, raw = self.call("POST", f"/api/tasks/{tid}/portions/adopt",
                                   {**OWNER_HEADERS, "Content-Type": "application/json"},
                                   b"{}")
        self.assertNotEqual(status, 200)
        error = json.loads(raw)["error"]
        self.assertTrue(result.get("isError"), result)
        self.assertIn(error, result["content"][0]["text"])

    def test_mentions_without_link_writes_nothing_and_is_silent(self) -> None:
        fx = _setup_mentions(self.conn, dict(self.ctx))
        q = self.subscribe()
        result = self.call_tool("listik_mentions", {"id": fx["id"]})
        self.assertFalse(result.get("isError"), result)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(_ids(payload["items"]), [fx["other"]])
        self.assertEqual(deps_mod.mentioned(self.conn, fx["id"])[0]["id"], fx["other"])
        with self.assertRaises(queue.Empty):
            q.get(timeout=0.3)

    def test_delete_publishes_deleted(self) -> None:
        tid = _task(self.conn, self.ctx)
        q = self.subscribe()
        result = self.call_tool("listik_delete", {"id": tid})
        self.assertFalse(result.get("isError"), result)
        frame = json.loads(q.get(timeout=2))
        self.assertEqual(frame["payload"], {"id": tid, "action": "deleted"})

    def test_launch_refusal_has_code(self) -> None:
        fx = _setup_launch(self.conn, dict(self.ctx))
        self.assertFalse(self.call_tool("listik_launch", {"id": fx["id"]}).get("isError"))
        again = self.call_tool("listik_launch", {"id": fx["id"]})
        self.assertTrue(again.get("isError"), again)
        self.assertIn("already_launched", again["content"][0]["text"])

    def test_stale_fence_refuses_like_http(self) -> None:
        """Устаревший токен поколения: задача не удаляется, не перезапускается, не режется."""
        cases = {
            "listik_delete": (_with_task, lambda fx: {"id": fx["id"]}),
            "listik_restart": (_setup_restart, lambda fx: {"id": fx["id"], "stage": "s1-spec"}),
            "listik_portions": (_setup_portions, lambda fx: {"id": fx["id"], "action": "sync"}),
            "listik_launch": (_setup_launch, lambda fx: {"id": fx["id"]}),
        }
        for name, (setup, args) in cases.items():
            with self.subTest(tool=name):
                fx = setup(self.conn, dict(self.ctx))
                self.conn.execute("UPDATE tasks SET generation = 3 WHERE id = ?", (fx["id"],))
                self.conn.commit()
                before = dict(store.get_task(self.conn, fx["id"]))
                headers = {**OWNER_HEADERS, "X-Listik-Task": fx["id"],
                           "X-Listik-Generation": "2"}
                status, _, raw = self.tool(name, args(fx), headers=headers)
                result = json.loads(raw)["result"]
                self.assertTrue(result.get("isError"), result)
                self.assertIn("полномочия отозваны", result["content"][0]["text"])
                after = store.get_task(self.conn, fx["id"])
                for field in ("stage", "status", "generation", "launched_by", "holder"):
                    self.assertEqual(after[field], before[field], field)
                self.assertFalse(store.child_cards(self.conn, fx["id"]))
                if "trace" in fx:
                    self.assertFalse(pathlib.Path(fx["trace"]).exists())


class AllToolsServer(AllToolsLocal):
    """Те же кейсы в серверном режиме (`mode = "server"`, `users = ["ann", "bob"]`)."""

    config_text = SERVER_CONFIG

    def test_foreign_owner_cannot_manage(self) -> None:
        """bob не удаляет, не запускает, не отзывает, не перезапускает и не режет задачу ann."""
        bob = {**AUTH, "X-Listik-Owner": "bob"}
        mcp_cases = {
            "listik_delete": (_with_task, lambda fx: {"id": fx["id"]}),
            "listik_restart": (_setup_restart, lambda fx: {"id": fx["id"], "stage": "s1-spec"}),
            "listik_portions": (_setup_portions, lambda fx: {"id": fx["id"], "action": "sync"}),
            "listik_launch": (_setup_launch, lambda fx: {"id": fx["id"]}),
            "listik_revoke": (_setup_revoke, lambda fx: {"id": fx["id"]}),
        }
        for name, (setup, args) in mcp_cases.items():
            with self.subTest(tool=name):
                fx = setup(self.conn, dict(self.ctx))
                before = store.get_task(self.conn, fx["id"])
                _, _, raw = self.tool(name, args(fx), headers=bob)
                result = json.loads(raw)["result"]
                self.assertTrue(result.get("isError"), result)
                self.assertIn("нельзя", result["content"][0]["text"])
                self.assertIn("принадлежит ann", result["content"][0]["text"])
                after = store.get_task(self.conn, fx["id"])
                for field in ("stage", "status", "generation", "launched_by", "updated_at"):
                    self.assertEqual(after[field], before[field], f"{name}: {field}")
                self.assertFalse(store.child_cards(self.conn, fx["id"]))
        http_cases = [
            ("DELETE", "", _with_task),
            ("POST", "/restart", _setup_restart),
            ("POST", "/portions/sync", _setup_portions),
            ("POST", "/portions/adopt", _setup_portions),
            ("POST", "/launch", _setup_launch),
            ("POST", "/revoke", _setup_revoke),
        ]
        for method, suffix, setup in http_cases:
            with self.subTest(http=f"{method} {suffix}"):
                fx = setup(self.conn, dict(self.ctx))
                before = store.get_task(self.conn, fx["id"])
                status, _, raw = self.call(method, f"/api/tasks/{fx['id']}{suffix}",
                                           {**bob, "Content-Type": "application/json"}, b"{}")
                self.assertEqual(status, 403, raw)
                self.assertEqual(json.loads(raw)["code"], "forbidden")
                after = store.get_task(self.conn, fx["id"])
                self.assertEqual(after["updated_at"], before["updated_at"])
                self.assertEqual(after["generation"], before["generation"])

    def test_foreign_owner_local_fallback(self) -> None:
        """`listik --local`: restart и portions sync/adopt отказывают чужому так же."""
        for op, setup in (("restart", _setup_restart), ("portions_sync", _setup_portions),
                          ("portions_adopt", _setup_portions)):
            with self.subTest(op=op):
                fx = setup(self.conn, dict(self.ctx))
                before = store.get_task(self.conn, fx["id"])
                with self.assertRaises(PermissionError):
                    client.local_call(op, task_id=fx["id"], as_owner="bob")
                after = store.get_task(self.conn, fx["id"])
                self.assertEqual(after["updated_at"], before["updated_at"])
                self.assertFalse(store.child_cards(self.conn, fx["id"]))


# ---------------------------------------------------------------- stdio

def _dead_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StdioServerOnlyTools(_LaunchCleanup, StdioNotifyCase):
    """stdio: launch/revoke/delete уходят к своему серверу; без сервера — отказ/локально."""

    def setUp(self) -> None:
        super().setUp()
        self._patch_logs()
        self.ctx = {"tmp": self.tmp, "owner": OWNER, "project": PROJECT}
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in ("LISTIK_TASK_ID", "LISTIK_GENERATION", "LISTIK_DISPATCH_ID", "LISTIK_OWNER"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._kill_launched(self.conn)
        super().tearDown()

    def stdio(self, name: str, arguments: dict) -> dict:
        return rpc_reply(self.run_stdio([tool_call(name, arguments)]))["result"]

    def server_down(self) -> None:
        self.write_config(port=_dead_port())

    def test_launch_goes_to_own_server(self) -> None:
        fx = _setup_launch(self.conn, dict(self.ctx))
        q = self.subscribe()
        result = self.stdio("listik_launch", {"id": fx["id"]})
        self.assertFalse(result.get("isError"), result)
        _check_launch(self, fx, json.loads(result["content"][0]["text"]))
        # Кадр шлёт launcher сервера (`notify=publish`): stdio сам процесс не порождал.
        frame = json.loads(q.get(timeout=2))
        self.assertEqual(frame["payload"]["id"], fx["id"])

    def test_launch_without_server_is_unsupported(self) -> None:
        fx = _setup_launch(self.conn, dict(self.ctx))
        self.server_down()
        with mock.patch.object(launcher_mod, "start") as start:
            result = self.stdio("listik_launch", {"id": fx["id"]})
        start.assert_not_called()
        self.assertTrue(result.get("isError"), result)
        text = result["content"][0]["text"]
        self.assertIn("unsupported", text)
        self.assertIn(client.server_only_error("launch").message, text)
        self.assertIn("подними сервер: listik serve", text)
        self.assertEqual(store.get_task(self.conn, fx["id"])["generation"], 0)
        time.sleep(0.3)
        self.assertFalse(pathlib.Path(fx["trace"]).exists())

    def test_revoke_goes_to_own_server(self) -> None:
        fx = _setup_revoke(self.conn, dict(self.ctx))
        result = self.stdio("listik_revoke", {"id": fx["id"]})
        self.assertFalse(result.get("isError"), result)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["generation"], fx["generation"] + 1)

    def test_revoke_without_server_is_unsupported(self) -> None:
        fx = _setup_revoke(self.conn, dict(self.ctx))
        self.server_down()
        result = self.stdio("listik_revoke", {"id": fx["id"]})
        self.assertTrue(result.get("isError"), result)
        self.assertIn("revoke выполняет только сервер", result["content"][0]["text"])
        self.assertEqual(store.get_task(self.conn, fx["id"])["generation"], fx["generation"])

    def test_delete_goes_to_own_server(self) -> None:
        tid = _task(self.conn, self.ctx)
        q = self.subscribe()
        result = self.stdio("listik_delete", {"id": tid})
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(json.loads(result["content"][0]["text"]), {"deleted": tid})
        frame = json.loads(q.get(timeout=2))
        self.assertEqual(frame["payload"], {"id": tid, "action": "deleted"})
        self.assertIsNone(self.conn.execute("SELECT 1 FROM tasks WHERE id = ?",
                                            (tid,)).fetchone())

    def test_delete_without_server_is_local(self) -> None:
        tid = _task(self.conn, self.ctx)
        self.server_down()
        result = self.stdio("listik_delete", {"id": tid})
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(json.loads(result["content"][0]["text"]), {"deleted": tid})
        self.assertIsNone(self.conn.execute("SELECT 1 FROM tasks WHERE id = ?",
                                            (tid,)).fetchone())


if __name__ == "__main__":
    unittest.main()
