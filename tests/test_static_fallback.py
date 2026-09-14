"""Статика доски: SPA-фолбэк только для маршрутов, у ассетов — честный 404 (listik-5cb0).

Safari, попросивший `/favicon.ico` и получивший HTML-страницу с кодом 200, считает
иконку битой, запоминает отказ и оставляет вкладку без фавиконки; Chrome такую
подмену молча игнорирует. Поэтому отсутствующий файл с расширением получает 404,
а на index.html по-прежнему падают только пути без расширения (маршруты приложения).

Сервер поднимается в процессе, по одному на тест; `paths.WEB_DIR` подменяется на
временный каталог с поддельным `dist`, поэтому сборка фронта для теста не нужна.
"""
from __future__ import annotations

import pathlib
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from listik import paths, server

INDEX = "<!doctype html><html><head><title>доска</title></head><body></body></html>"


class StaticCase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (paths.DB_PATH, paths.CONFIG_PATH, paths.WEB_DIR)
        self._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self._tmp.name)
        paths.DB_PATH = tmp / "listik.db"
        paths.CONFIG_PATH = tmp / "config.toml"
        paths.CONFIG_PATH.write_text('[auth]\ntoken = "test-token"\n', encoding="utf-8")
        # Настоящий web/public (фавиконки) + временный dist: файлы dist — артефакт сборки.
        self.public = paths.WEB_DIR / "public"
        paths.WEB_DIR = tmp / "web"
        self.dist = paths.WEB_DIR / "dist"
        self.dist.mkdir(parents=True)
        (self.dist / "index.html").write_text(INDEX, encoding="utf-8")
        (self.dist / "app.js").write_text("export const x = 1\n", encoding="utf-8")
        self._saved_conn = (server._conn_made, server._conn_local)
        server._conn_made = False
        server._conn_local = threading.local()
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        server._conn_made, server._conn_local = self._saved_conn
        paths.DB_PATH, paths.CONFIG_PATH, paths.WEB_DIR = self._saved
        self._tmp.cleanup()

    def get(self, path: str):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()


class StaticFallbackTest(StaticCase):
    def test_root_serves_board(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(body.decode("utf-8"), INDEX)

    def test_existing_asset_served_as_file(self):
        status, headers, body = self.get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn(b"export const x", body)

    def test_missing_asset_is_404_not_board(self):
        """Регрессия listik-5cb0: /favicon.ico не должен отдавать index.html."""
        status, headers, body = self.get("/favicon.ico")
        self.assertEqual(status, 404)
        self.assertNotIn("text/html", headers["Content-Type"])
        self.assertNotIn(b"<html", body)

    def test_missing_asset_in_subdirectory_is_404(self):
        status, headers, _ = self.get("/assets/index-deadbeef.js")
        self.assertEqual(status, 404)
        self.assertNotIn("text/html", headers["Content-Type"])

    def test_route_without_extension_falls_back_to_board(self):
        for path in ("/board", "/task/listik-5cb0", "/board/listik-5cb0"):
            with self.subTest(path=path):
                status, headers, body = self.get(path)
                self.assertEqual(status, 200)
                self.assertIn("text/html", headers["Content-Type"])
                self.assertEqual(body.decode("utf-8"), INDEX)


class FaviconAssetsTest(unittest.TestCase):
    """Фавиконки лежат в web/public (Vite копирует их в dist) и объявлены в index.html."""

    def test_favicon_ico_is_real_ico_with_16_and_32(self):
        raw = (paths.WEB_DIR / "public" / "favicon.ico").read_bytes()
        reserved, kind, count = struct.unpack("<HHH", raw[:6])
        self.assertEqual((reserved, kind), (0, 1), "не ICONDIR")
        sizes = set()
        for i in range(count):
            width, height = struct.unpack("<BB", raw[6 + i * 16:8 + i * 16])
            sizes.add((width or 256, height or 256))
        self.assertIn((16, 16), sizes)
        self.assertIn((32, 32), sizes)

    def test_index_html_declares_favicon_ico(self):
        html = (paths.WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/favicon.ico"', html)


if __name__ == "__main__":
    unittest.main()
