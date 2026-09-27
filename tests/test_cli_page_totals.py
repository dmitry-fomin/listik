"""Итоговые строки `list`/`ready`/`blocked` считают полное число, а не размер страницы;
`list --offset` и `list -n 0` дают одну и ту же страницу через сервер и с `--local`
(listik-n1aw).
"""
from __future__ import annotations

import json
import unittest

from listik import store
from tests.test_local_bypass_warning import LocalBypassWarningCase

FALLBACK = "сервер Listik не отвечает"


def last_line(out: str) -> str:
    """Последняя непустая строка вывода."""
    return [line.strip() for line in out.splitlines() if line.strip()][-1]


def ids_of(stdout: str) -> list[str]:
    return [t["id"] for t in json.loads(stdout)["tasks"]]


class ListPagingTests(LocalBypassWarningCase):
    def setUp(self) -> None:
        super().setUp()
        self.p1, self.p2, self.p3 = (
            store.create_task(self.conn, title=f"P{i}", project="demo", priority=i)["id"]
            for i in (1, 2, 3))

    def test_offset_json_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("list", "-p", "demo", "--order", "priority", "-n", "1",
                                 "--offset", "1", "--json", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                res = json.loads(p.stdout)
                self.assertEqual(res["offset"], 1)
                self.assertEqual(res["total"], 3)
                self.assertEqual(ids_of(p.stdout), [self.p2])

    def test_offset_text_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("list", "-p", "demo", "--order", "priority", "-n", "1",
                                 "--offset", "1", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn(self.p2, p.stdout)
                self.assertNotIn(self.p1, p.stdout)
                self.assertNotIn(self.p3, p.stdout)
                self.assertEqual(last_line(p.stdout), "показано 1 из 3")

    def test_offset_past_end_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("list", "-p", "demo", "--offset", "10", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotIn("пусто", p.stdout)
                self.assertEqual(last_line(p.stdout), "показано 0 из 3")

    def test_negative_offset_rejected(self) -> None:
        p = self.run_cli("list", "-p", "demo", "--offset", "-1", "--json", local=True)
        self.assertEqual(p.returncode, 2, p.stderr)
        err = json.loads(p.stdout)["error"]
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn("--offset", err["message"])
        self.assertNotIn("неизвестный аргумент", err["message"])
        self.assertTrue(err.get("hint"))

    def test_limit_zero_json_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("list", "-p", "demo", "-n", "0", "--json", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                res = json.loads(p.stdout)
                self.assertEqual(res["limit"], 200)
                self.assertEqual(res["total"], 3)
                self.assertEqual(set(ids_of(p.stdout)), {self.p1, self.p2, self.p3})

    def test_limit_zero_text_local(self) -> None:
        p = self.run_cli("list", "-p", "demo", "-n", "0", local=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        for tid in (self.p1, self.p2, self.p3):
            self.assertIn(tid, p.stdout)
        self.assertNotIn("пусто", p.stdout)
        self.assertEqual(last_line(p.stdout), "всего: 3")


class ReadyTotalTests(LocalBypassWarningCase):
    def setUp(self) -> None:
        super().setUp()
        for i in range(3):
            store.create_task(self.conn, title=f"T{i}", project="demo")

    def test_truncated_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("ready", "-p", "demo", "-n", "2", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(last_line(p.stdout), "показано 2 из 3 готовых")
                self.assertEqual(sum(line.startswith("○ ") for line in p.stdout.splitlines()), 2)
                self.assertNotIn("всего готовых", p.stdout)

    def test_full_page_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("ready", "-p", "demo", "-n", "3", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(last_line(p.stdout), "всего готовых: 3")
                self.assertNotIn("показано", p.stdout)

    def test_unlimited_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("ready", "-p", "demo", "-n", "0", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(last_line(p.stdout), "всего готовых: 3")

    def test_json_unchanged_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("ready", "-p", "demo", "-n", "2", "--json", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                res = json.loads(p.stdout)
                self.assertEqual(len(res["tasks"]), 2)
                self.assertNotIn("total", res)

    def test_fallback_warns_once(self) -> None:
        p = self.run_cli("ready", "-p", "demo", "-n", "2", local=False, port=self.free_port())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stderr.count(FALLBACK), 1, p.stderr)
        self.assertEqual(last_line(p.stdout), "показано 2 из 3 готовых")


class BlockedTotalTests(LocalBypassWarningCase):
    def setUp(self) -> None:
        super().setUp()
        x = store.create_task(self.conn, title="X", project="demo")["id"]
        for i in range(3):
            w = store.create_task(self.conn, title=f"W{i}", project="demo")["id"]
            store.add_dep(self.conn, w, x, "blocks", created_by="me")

    def test_truncated_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("blocked", "-p", "demo", "-n", "2", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(last_line(p.stdout), "показано 2 из 3 ждущих")
                self.assertEqual(sum(line.startswith("■ ") for line in p.stdout.splitlines()), 2)
                self.assertNotIn("всего ждущих", p.stdout)

    def test_full_page_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("blocked", "-p", "demo", "-n", "3", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(last_line(p.stdout), "всего ждущих: 3")
                self.assertNotIn("показано", p.stdout)

    def test_unlimited_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("blocked", "-p", "demo", "-n", "0", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(last_line(p.stdout), "всего ждущих: 3")

    def test_json_unchanged_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("blocked", "-p", "demo", "-n", "2", "--json", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                res = json.loads(p.stdout)
                self.assertEqual(len(res["tasks"]), 2)
                self.assertNotIn("total", res)

    def test_fallback_warns_once(self) -> None:
        p = self.run_cli("blocked", "-p", "demo", "-n", "2", local=False, port=self.free_port())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stderr.count(FALLBACK), 1, p.stderr)
        self.assertEqual(last_line(p.stdout), "показано 2 из 3 ждущих")


if __name__ == "__main__":
    unittest.main()


class MemoryTotalTests(LocalBypassWarningCase):
    """`listik memory` без запроса: итог по полному числу, `-n 0` — страница 20 (listik-5qay)."""

    def setUp(self) -> None:
        super().setUp()
        for i in range(3):
            store.remember(self.conn, f"заметка {i}", key=f"m{i}", project="demo")

    def test_truncated_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("memory", "--project", "demo", "-n", "2", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(last_line(p.stdout), "показано 2 из 3")

    def test_full_page_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("memory", "--project", "demo", "-n", "5", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(last_line(p.stdout), "всего: 3")

    def test_limit_zero_text_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("memory", "--project", "demo", "-n", "0", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotIn("памяти пока нет", p.stdout)
                self.assertIn("m1", p.stdout)
                self.assertEqual(last_line(p.stdout), "всего: 3")

    def test_limit_zero_json_default_page_both_paths(self) -> None:
        for i in range(3, 25):
            store.remember(self.conn, f"заметка {i}", key=f"m{i}", project="demo")
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("memory", "--project", "demo", "-n", "0", "--json", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(len(json.loads(p.stdout)), 20)

    def test_json_unchanged_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("memory", "--project", "demo", "-n", "2", "--json", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                res = json.loads(p.stdout)
                self.assertIsInstance(res, list)
                self.assertEqual(len(res), 2)

    def test_fallback_warns_once(self) -> None:
        p = self.run_cli("memory", "--project", "demo", "-n", "2", local=False,
                         port=self.free_port())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stderr.count(FALLBACK), 1, p.stderr)
        self.assertEqual(last_line(p.stdout), "показано 2 из 3")


class DepSuggestedTotalTests(LocalBypassWarningCase):
    """`dep suggested`: `total` в ответе, итог по нему, `-n 0` — страница 100 (listik-5qay)."""

    def setUp(self) -> None:
        super().setUp()
        x = store.create_task(self.conn, title="X", project="demo")["id"]
        for i in range(3):
            t = store.create_task(self.conn, title=f"S{i}", project="demo")["id"]
            store.add_dep(self.conn, t, x, "blocks", "agent:codex", confirm=False)

    def test_truncated_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("dep", "suggested", "--project", "demo", "-n", "2", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(last_line(p.stdout), "показано 2 из 3 предложений")

    def test_full_page_both_paths(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("dep", "suggested", "--project", "demo", "-n", "5", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(last_line(p.stdout), "всего предложений: 3")

    def test_limit_zero_json_both_paths(self) -> None:
        counts = []
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("dep", "suggested", "--project", "demo", "-n", "0", "--json",
                                 local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                res = json.loads(p.stdout)
                self.assertIsInstance(res, list)
                counts.append(len(res))
        self.assertEqual(counts, [3, 3])

    def test_total_in_response_http_and_local(self) -> None:
        import urllib.request
        from listik import client
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/deps/suggested?project=demo&limit=2",
            headers={"Authorization": "Bearer test-token"})
        with urllib.request.urlopen(req, timeout=10) as r:
            http = json.loads(r.read())["data"]
        local = client.local_call("dep_suggested", project="demo", limit=2)
        for res in (http, local):
            self.assertEqual(res["total"], 3)
            self.assertEqual(len(res["items"]), 2)
            self.assertIn("generated_at", res)


class McpPageDefaultsTests(LocalBypassWarningCase):
    """MCP: `limit: 0` — страница по умолчанию, как у HTTP (listik-5qay)."""

    def test_list_limit_zero(self) -> None:
        from listik import mcp
        for i in range(3):
            store.create_task(self.conn, title=f"T{i}", project="demo")
        out = mcp.call_tool("listik_list", {"project": "demo", "limit": 0}, conn=self.conn)
        self.assertEqual(len(out["tasks"]), 3)
        self.assertEqual(out["total"], 3)

    def test_memory_limit_zero(self) -> None:
        from listik import mcp
        store.remember(self.conn, "заметка про кота", key="k1", project="demo")
        out = mcp.call_tool("listik_memory", {"limit": 0}, conn=self.conn)
        self.assertEqual(len(out["items"]), 1)
        out = mcp.call_tool("listik_memory", {"query": "кота", "limit": 0}, conn=self.conn)
        self.assertEqual(len(out["items"]), 1)

    def test_deps_suggested_limit(self) -> None:
        from listik import mcp
        x = store.create_task(self.conn, title="X", project="demo")["id"]
        for i in range(3):
            t = store.create_task(self.conn, title=f"S{i}", project="demo")["id"]
            store.add_dep(self.conn, t, x, "blocks", "agent:codex", confirm=False)
        out = mcp.call_tool("listik_deps_suggested", {"limit": 0}, conn=self.conn)
        self.assertEqual(out["total"], 3)
        self.assertEqual(len(out["items"]), out["total"])
        out = mcp.call_tool("listik_deps_suggested", {"limit": 1}, conn=self.conn)
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["total"], 3)
