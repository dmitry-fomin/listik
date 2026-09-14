"""MCP по stdio сообщает серверу о записи — доска двигается без перезагрузки (listik-hkdp).

`publish` живёт в процессе сервера, а stdio-MCP пишет в ту же sqlite мимо него.
Проверяется весь путь:

* эндпоинт `POST /api/notify` — кадр подписчику, токен, отказы 400/404;
* stdio-цикл `mcp.run()` — событие уходит после ответа, в фоне, и молчит на чтениях,
  ошибках инструмента и без сервера;
* сквозной сценарий — настоящий `bin/listik mcp` пишет в базу, а подписчик SSE
  (`GET /api/stream`, та же подписка, что у доски) получает кадр с id задачи.

Живой сервер на свободном порту, временная база и config.toml с этим портом — из
`McpHttpCase` (см. tests/test_mcp_http.py): другого способа проверить HTTP-часть
stdio-MCP нет, а настоящий сервер на 8787 тесты не трогают.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from unittest import mock

from listik import db as db_mod
from listik import mcp, paths, server, store
from tests.test_mcp_http import TOKEN, McpHttpCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


def tool_call(name: str, arguments: dict, rid: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def rpc_reply(text: str, rid: int = 1) -> dict:
    """Ответ stdio-цикла на запрос: строк может быть больше одной.

    В stdout процесса попадают и логи сервера (наблюдатель за подменой файла базы
    печатает в stdout из своего потока), поэтому ответ ищется по `id`, а не по
    номеру строки.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("jsonrpc") and payload.get("id") == rid:
            return payload
    raise AssertionError(f"в stdout нет ответа на запрос {rid}: {text!r}")


class _SignalStream(io.StringIO):
    """stdout stdio-цикла, который отдаёт строки в очередь по мере записи."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: queue.Queue = queue.Queue()

    def write(self, text: str) -> int:
        written = super().write(text)
        if text.strip():
            self.lines.put(text)
        return written


class StdioNotifyCase(McpHttpCase):
    """Живой сервер, временная база и config.toml, указывающий stdio-MCP на этот сервер."""

    def setUp(self) -> None:
        super().setUp()
        self.tmp = pathlib.Path(self._tmp.name)
        self.write_config()
        # init, а не connect: схема нужна до первого HTTP-запроса, а соединение
        # закроет tearDown McpHttpCase (он патчит db.init и помнит открытые).
        self.conn = db_mod.init(self.db_path)

    def write_config(self, port: int | None = None) -> None:
        paths.CONFIG_PATH.write_text(
            f'[server]\nhost = "127.0.0.1"\nport = {port or self.port}\n\n'
            f'[auth]\ntoken = "{TOKEN}"\n',
            encoding="utf-8",
        )

    # --- подготовка и запросы
    def new_task(self, title: str = "Задача") -> str:
        return store.create_task(self.conn, title=title)["id"]

    def notify(self, body: dict, headers: dict | None = None):
        return self.post(body, headers=headers if headers is not None else {"Authorization": f"Bearer {TOKEN}"},
                         path="/api/notify")

    def assert_silent(self, q: queue.Queue) -> None:
        with self.assertRaises(queue.Empty):
            q.get(timeout=0.3)

    def run_stdio(self, requests: list[dict]) -> str:
        """Прогнать запросы через stdio-цикл так, как это делает `bin/listik mcp`."""
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(text)), \
                mock.patch.object(sys, "stdout", out):
            mcp.run()
        return out.getvalue()

    def run_binary(self, requests: list[dict]) -> subprocess.CompletedProcess:
        """Настоящий `bin/listik mcp`: только он проверяет и запуск, и выход процесса."""
        payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
        env = {**os.environ,
               "LISTIK_DB": str(self.db_path),
               "LISTIK_CONFIG": str(paths.CONFIG_PATH),
               "LISTIK_LOG": str(self.tmp / "listik.log")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "mcp"], input=payload,
                              capture_output=True, text=True, env=env, timeout=30,
                              cwd=str(LISTIK_BIN.parent.parent))


class NotifyEndpoint(StdioNotifyCase):
    """`POST /api/notify`: под токеном, ничего не меняет, будит доску кадром задачи."""

    def test_notify_publishes_task_frame(self) -> None:
        task_id = self.new_task()
        q = self.subscribe()
        status, _, raw = self.notify({"task_id": task_id, "action": "listik_comment"})
        self.assertEqual(status, 200, raw)
        data = json.loads(raw)["data"]
        self.assertTrue(data["published"])
        self.assertEqual(data["kind"], "task")
        event = json.loads(q.get(timeout=2))
        self.assertEqual(event["kind"], "task")
        self.assertEqual(event["payload"], {"id": task_id, "action": "listik_comment"})
        self.assertTrue(event["at"])

    def test_notify_does_not_touch_the_card(self) -> None:
        """Событие — только «перечитай»: доска обновляется, история задачи — нет."""
        task_id = self.new_task()
        before = self.conn.execute("SELECT updated_at FROM tasks WHERE id = ?",
                                   (task_id,)).fetchone()["updated_at"]
        status, _, _ = self.notify({"task_id": task_id, "action": "listik_comment"})
        self.assertEqual(status, 200)
        after = self.conn.execute("SELECT updated_at FROM tasks WHERE id = ?",
                                  (task_id,)).fetchone()["updated_at"]
        self.assertEqual(before, after)
        events = self.conn.execute("SELECT count(*) n FROM events WHERE task_id = ?",
                                   (task_id,)).fetchone()["n"]
        self.assertEqual(events, 1)  # только created

    def test_notify_needs_token(self) -> None:
        task_id = self.new_task()
        q = self.subscribe()
        status, _, _ = self.notify({"task_id": task_id}, headers={})
        self.assertEqual(status, 401)
        self.assert_silent(q)

    def test_notify_unknown_task_is_404_and_silent(self) -> None:
        q = self.subscribe()
        status, _, raw = self.notify({"task_id": "нет-такой"})
        self.assertEqual(status, 404, raw)
        self.assertIn("не найдена", json.loads(raw)["error"])
        self.assert_silent(q)

    def test_notify_without_task_id_is_400_and_silent(self) -> None:
        q = self.subscribe()
        for body in ({}, {"task_id": "  "}):
            with self.subTest(body=body):
                status, _, raw = self.notify(body)
                self.assertEqual(status, 400, raw)
                self.assertIn("task_id", json.loads(raw)["error"])
        self.assert_silent(q)

    def test_notify_unknown_kind_is_400_and_silent(self) -> None:
        task_id = self.new_task()
        q = self.subscribe()
        status, _, raw = self.notify({"task_id": task_id, "kind": "project"})
        self.assertEqual(status, 400, raw)
        self.assertIn("kind", json.loads(raw)["error"])
        self.assert_silent(q)


class StdioWritesNotify(StdioNotifyCase):
    """stdio-цикл: пишущий инструмент сообщает серверу, чтение и ошибка — нет."""

    def test_write_tool_notifies_live_server(self) -> None:
        task_id = self.new_task()
        q = self.subscribe()
        out = self.run_stdio([tool_call("listik_comment",
                                        {"id": task_id, "text": "из stdio", "kind": "journal"})])
        result = rpc_reply(out)["result"]
        self.assertFalse(result.get("isError"), out)
        event = json.loads(q.get(timeout=5))
        self.assertEqual(event["kind"], "task")
        self.assertEqual(event["payload"], {"id": task_id, "action": "listik_comment"})

    def test_create_notifies_new_id(self) -> None:
        q = self.subscribe()
        out = self.run_stdio([tool_call("listik_create", {"title": "Новая", "project": "demo"})])
        task_id = json.loads(rpc_reply(out)["result"]["content"][0]["text"])["id"]
        event = json.loads(q.get(timeout=5))
        self.assertEqual(event["payload"], {"id": task_id, "action": "listik_create"})

    def test_read_tool_is_silent(self) -> None:
        task_id = self.new_task()
        q = self.subscribe()
        out = self.run_stdio([tool_call("listik_show", {"id": task_id})])
        self.assertEqual(rpc_reply(out)["result"]["content"][0]["type"], "text")
        self.assert_silent(q)

    def test_failed_write_is_silent(self) -> None:
        q = self.subscribe()
        out = self.run_stdio([tool_call("listik_comment", {"id": "нет-такой", "text": "x"})])
        self.assertTrue(rpc_reply(out)["result"]["isError"], out)
        self.assert_silent(q)

    def test_tool_answer_does_not_wait_for_notify(self) -> None:
        """Ответ уходит клиенту до того, как уведомление дошло до сервера."""
        task_id = self.new_task()
        started = threading.Event()
        release = threading.Event()

        def slow_notify(*_args) -> None:
            started.set()
            release.wait(10)

        out = _SignalStream()
        errors: list[BaseException] = []

        def loop() -> None:
            try:
                mcp.run()
            except BaseException as exc:  # noqa: BLE001 — падение потока видно в ошибках
                errors.append(exc)

        text = json.dumps(tool_call("listik_comment", {"id": task_id, "text": "x"})) + "\n"
        with mock.patch.object(mcp, "_post_notify", slow_notify), \
                mock.patch.object(sys, "stdin", io.StringIO(text)), \
                mock.patch.object(sys, "stdout", out):
            thread = threading.Thread(target=loop, daemon=True)
            started_at = time.monotonic()
            thread.start()
            try:
                line = out.lines.get(timeout=5)
                answered_after = time.monotonic() - started_at
                self.assertTrue(started.wait(2), "уведомление не отправлено")
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(errors, errors)
        self.assertFalse(json.loads(line)["result"].get("isError"), line)
        self.assertLess(answered_after, 1.0)

    def test_write_works_without_server(self) -> None:
        """Сервера нет: инструмент пишет в базу и отвечает, уведомление молча пропадает."""
        task_id = self.new_task()
        self._stop_server()
        out = self.run_stdio([tool_call("listik_comment", {"id": task_id, "text": "без сервера"})])
        self.assertFalse(rpc_reply(out)["result"].get("isError"), out)
        rows = self.conn.execute("SELECT text FROM comments WHERE task_id = ?",
                                 (task_id,)).fetchall()
        self.assertEqual([r["text"] for r in rows], ["без сервера"])

    def test_broken_server_does_not_break_tool(self) -> None:
        """Порт занят тем, кто не отвечает: POST висит, но инструмент уже ответил."""
        task_id = self.new_task()
        with _black_hole() as port:
            self.write_config(port=port)
            started_at = time.monotonic()
            out = self.run_stdio([tool_call("listik_comment", {"id": task_id, "text": "в тишину"})])
            elapsed = time.monotonic() - started_at
        self.assertFalse(rpc_reply(out)["result"].get("isError"), out)
        self.assertLess(elapsed, mcp.NOTIFY_TIMEOUT + 1.0)

    def test_broken_config_does_not_break_tool(self) -> None:
        """Конфиг не читается — инструмент всё равно отвечает, поток молчит."""
        task_id = self.new_task()
        paths.CONFIG_PATH.write_text("[server\nпорт = ", encoding="utf-8")
        out = self.run_stdio([tool_call("listik_comment", {"id": task_id, "text": "битый конфиг"})])
        self.assertFalse(rpc_reply(out)["result"].get("isError"), out)


class _black_hole:
    """Сокет, который принимает соединение и никогда не отвечает.

    Нужен, чтобы отличить «фоново» от «синхронно с таймаутом»: сервер, который
    молча висит, задерживает POST на весь `NOTIFY_TIMEOUT`.
    """

    def __init__(self) -> None:
        import socket
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]

    def __enter__(self) -> int:
        return self.port

    def __exit__(self, *_exc) -> None:
        self._sock.close()


class StdioProcessEndToEnd(StdioNotifyCase):
    """Сквозной путь: `bin/listik mcp` → `POST /api/notify` → кадр подписчику SSE."""

    def stream_frames(self, frames: queue.Queue, errors: list) -> None:
        """Подписка доски: читает `GET /api/stream` и складывает `data:`-кадры."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/stream?token={TOKEN}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: "):
                        frames.put(line[len("data: "):])
        except Exception as exc:  # noqa: BLE001 — соединение закрыл сервер при остановке
            errors.append(exc)

    def wait_subscriber(self, at_least: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with server._subs_lock:
                if len(server._subs) >= at_least:
                    return True
            time.sleep(0.02)
        return False

    def test_stdio_process_write_reaches_board_stream(self) -> None:
        task_id = self.new_task()
        with server._subs_lock:
            before = len(server._subs)
        frames: queue.Queue = queue.Queue()
        errors: list = []
        threading.Thread(target=self.stream_frames, args=(frames, errors), daemon=True).start()
        self.assertTrue(self.wait_subscriber(before + 1), "доска не подписалась на поток SSE")

        proc = self.run_binary([tool_call("listik_comment",
                                          {"id": task_id, "text": "через процесс"})])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = rpc_reply(proc.stdout)["result"]
        self.assertFalse(result.get("isError"), proc.stdout)

        frame = json.loads(frames.get(timeout=5))
        self.assertEqual(frame["kind"], "task")
        self.assertEqual(frame["payload"], {"id": task_id, "action": "listik_comment"})
        rows = self.conn.execute("SELECT text FROM comments WHERE task_id = ?",
                                 (task_id,)).fetchall()
        self.assertEqual([r["text"] for r in rows], ["через процесс"])


if __name__ == "__main__":
    unittest.main()
