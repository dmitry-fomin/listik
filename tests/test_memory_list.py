"""Список памяти без запроса: `store.list_memories` для CLI, HTTP, MCP и фолбэка (listik-x0g6)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from listik import client as client_mod
from listik import db as db_mod
from listik import mcp, paths, store
from tests.helpers import TempDbTestCase
from tests.test_owner_http import AUTH, OwnerHttpCase

LISTIK_BIN = Path(__file__).resolve().parent.parent / "bin" / "listik"
KEYS = {"key", "project", "body", "updated_at"}


def _seed(conn) -> None:
    for i, (key, project) in enumerate((("a", "demo"), ("b", "demo-2"), ("c", "demo"),
                                        ("d", "other"))):
        conn.execute("INSERT INTO memories(key, project, body, source, updated_at) "
                     "VALUES(?,?,?, 'native', ?)",
                     (key, project, f"тело {key}", f"2026-01-0{i + 1}T00:00:00Z"))
    conn.commit()


class ListMemoriesStoreTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        _seed(self.conn)

    def test_order_and_keys(self) -> None:
        items = store.list_memories(self.conn)
        self.assertEqual([m["key"] for m in items], ["d", "c", "b", "a"])
        for m in items:
            self.assertEqual(set(m), KEYS)

    def test_exact_project_filter(self) -> None:
        self.assertEqual([m["key"] for m in store.list_memories(self.conn, project="demo")],
                         ["c", "a"])

    def test_empty_project_no_filter(self) -> None:
        self.assertEqual(len(store.list_memories(self.conn, project=None)), 4)
        self.assertEqual(len(store.list_memories(self.conn, project="")), 4)

    def test_limit(self) -> None:
        self.assertEqual([m["key"] for m in store.list_memories(self.conn, limit=2)],
                         ["d", "c"])

    def test_local_call_matches_store(self) -> None:
        expected = store.list_memories(self.conn, project="demo", limit=5)
        with mock.patch.object(paths, "DB_PATH", self.db_path):
            got = client_mod.local_call("memory", query=None, project="demo", limit=5)
        self.assertEqual(got, expected)


class ListMemoriesHttpCliTests(OwnerHttpCase):
    def _conn(self):
        conn = db_mod.init(self.db_path)
        self._opened.append(conn)
        return conn

    def test_http_project_filter(self) -> None:
        _seed(self._conn())
        status, _, payload = self.call("GET", "/api/memory?project=demo", AUTH)
        self.assertEqual(status, 200)
        self.assertEqual([m["key"] for m in payload["data"]], ["c", "a"])

    def test_http_default_limit_20(self) -> None:
        conn = self._conn()
        for i in range(25):
            store.remember(conn, f"заметка {i}", key=f"k{i}", project="demo")
        status, _, payload = self.call("GET", "/api/memory", AUTH)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["data"]), 20)

    def _cli(self, *global_flags):
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_CONFIG": str(paths.CONFIG_PATH)}
        p = subprocess.run(
            [sys.executable, str(LISTIK_BIN), *global_flags, "--host", "127.0.0.1",
             "--port", str(self.port), "memory", "--project", "demo", "--json"],
            capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        if not global_flags:  # живой сервер, а не молчаливый фолбэк в базу
            self.assertNotIn("не отвечает", p.stderr)
        return json.loads(p.stdout)

    def test_cli_server_local_mcp_agree(self) -> None:
        conn = self._conn()
        _seed(conn)
        via_server = self._cli()
        via_local = self._cli("--local")
        via_mcp = mcp.call_tool("listik_memory", {"project": "demo"}, conn=conn)["items"]
        self.assertEqual([m["key"] for m in via_server], ["c", "a"])
        self.assertEqual(via_server, via_local)
        self.assertEqual(via_server, via_mcp)
