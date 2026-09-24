"""`Server.handle_error`: обрыв соединения клиентом — молча, остальное — трейсбек (listik-dyho)."""
from __future__ import annotations

import contextlib
import io
import unittest

from listik import server


class ServerHandleErrorTests(unittest.TestCase):
    def setUp(self):
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.addCleanup(self.srv.server_close)

    def _stderr_of(self, exc: BaseException) -> str:
        buf = io.StringIO()
        try:
            raise exc
        except BaseException:  # noqa: BLE001
            with contextlib.redirect_stderr(buf):
                self.srv.handle_error(None, ("127.0.0.1", 1))
        return buf.getvalue()

    def test_client_disconnects_are_silent(self):
        for cls in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
            with self.subTest(cls=cls.__name__):
                self.assertEqual(self._stderr_of(cls()), "")

    def test_other_errors_keep_traceback(self):
        for cls in (ValueError, PermissionError):
            with self.subTest(cls=cls.__name__):
                out = self._stderr_of(cls("boom"))
                self.assertIn("Traceback", out)
                self.assertIn(cls.__name__, out)


if __name__ == "__main__":
    unittest.main()
