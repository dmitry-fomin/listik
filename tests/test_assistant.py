"""Тесты помощника DeepSeek (`listik/assistant.py`, `POST /api/assistant/suggest`).

Реальная сеть не трогается: HTTP к DeepSeek подменяется моком `urlopen` — тест
проверяет и сам запрос (URL, заголовок Authorization, модель, промпт), и разбор
ответа, и все ветки ошибок. Конфиг — временный файл, `config.toml` репозитория
не читается; ключ в тестах — фиктивный.
"""
from __future__ import annotations

import io
import json
import logging
import pathlib
import tempfile
import threading
import unittest
import urllib.error
from unittest import mock

from listik import assistant as assistant_mod
from listik import errors as errors_mod
from listik import paths
from listik import routes_store
from listik import server
from tests.helpers import TempDbTestCase

API_KEY = "test-deepseek-key"
TOKEN = "assistant-test-token"


def route_records() -> list[dict]:
    return [
        {
            "key": "low-pipeline",
            "kind": "pipeline",
            "title": "low",
            "hint": "на 20 мин, самый дешёвый",
            "visible": True,
            "roles": {"impl": {"provider": "deepseek", "label": "dsh", "title": "dsh"}},
            "command": None,
        },
        {
            "key": "high-pipeline",
            "kind": "pipeline",
            "title": "high",
            "hint": "от 40 мин",
            "visible": True,
            "roles": {"impl": {"provider": "claude", "label": "medium", "title": "Opus"}},
            "command": None,
        },
        {
            "key": "hidden-pipeline",
            "kind": "pipeline",
            "title": "скрытый",
            "hint": "",
            "visible": False,
            "roles": {"impl": {"provider": "claude", "label": "low", "title": "Opus"}},
            "command": None,
        },
        {
            "key": "dsh",
            "kind": "swarm",
            "title": "dsh",
            "hint": "",
            "visible": True,
            "roles": {"impl": {"harness": "dsh"}},
            "command": None,
        },
    ]


def reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def suggestion_json(**overrides) -> str:
    data = {
        "text": "Переписанное описание",
        "acceptance": ["Тест падает без правки", "Кнопка видна на hover"],
        "complexity": {"level": "medium", "reason": "три шага и одна развилка"},
        "route": {"key": "low-pipeline", "reason": "работа понятная и короткая"},
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class _FakeResponse:
    def __init__(self, payload: dict | str):
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _Recorder:
    """Мок urlopen: запоминает Request-ы и отдаёт заранее заданный ответ/ошибку."""

    def __init__(self, payload=None, error: BaseException | None = None):
        self.payload = payload if payload is not None else reply(suggestion_json())
        self.error = error
        self.requests: list = []
        self.timeouts: list = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.payload)


class AssistantConfigTestCase(unittest.TestCase):
    """Общая настройка: временный config.toml вместо настоящего."""

    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)
        self.config_path = self.tmp_path / "config.toml"
        self.write_config(api_key=API_KEY)
        patch = mock.patch.object(paths, "CONFIG_PATH", self.config_path)
        patch.start()
        self.addCleanup(patch.stop)
        # Тело ошибок DeepSeek пишется в лог сервера; в тестах он не нужен.
        logger = logging.getLogger("listik.assistant")
        null_handler = logging.NullHandler()
        logger.addHandler(null_handler)
        self.addCleanup(logger.removeHandler, null_handler)

    def write_config(self, api_key: str | None = None, **assistant) -> None:
        lines = ['[auth]', f'token = "{TOKEN}"']
        if api_key is not None or assistant:
            lines.append("[assistant]")
            if api_key is not None:
                lines.append(f'api_key = {json.dumps(api_key)}')
            for key, value in assistant.items():
                lines.append(f"{key} = {json.dumps(value) if isinstance(value, str) else value}")
        self.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class AssistantStatusTests(AssistantConfigTestCase):
    def test_without_key_disabled_and_key_never_returned(self) -> None:
        self.write_config()
        state = assistant_mod.status()
        self.assertFalse(state["enabled"])
        self.assertEqual(state["model"], assistant_mod.DEFAULT_MODEL)
        self.assertEqual(state["base_url"], assistant_mod.DEFAULT_BASE_URL)
        self.assertNotIn("api_key", state)
        self.assertNotIn(API_KEY, json.dumps(state))

    def test_with_key_enabled_but_plain(self) -> None:
        state = assistant_mod.status()
        self.assertTrue(state["enabled"])
        self.assertEqual(set(state), {"enabled", "model", "base_url"})
        self.assertNotIn(API_KEY, json.dumps(state))

    def test_config_overrides_base_url_and_model(self) -> None:
        self.write_config(api_key=API_KEY, base_url="https://api.deepseek.com/v1/",
                          model="deepseek-chat")
        state = assistant_mod.status()
        self.assertEqual(state["base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(state["model"], "deepseek-chat")


class AssistantSuggestTests(AssistantConfigTestCase):
    def test_without_key_raises_clear_error(self) -> None:
        self.write_config()
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            assistant_mod.suggest("title", "сделать кнопку", {})
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, errors_mod.SERVER_ERROR)
        self.assertIn("api_key", ctx.exception.message)
        self.assertIn("[assistant]", ctx.exception.message)

    def test_unknown_field_is_bad_argument(self) -> None:
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            assistant_mod.suggest("project", "listik", {})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors_mod.BAD_ARGUMENT)

    def test_empty_field_and_context_is_bad_argument(self) -> None:
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            assistant_mod.suggest("description", "   ", {})
        self.assertEqual(ctx.exception.status, 400)

    def test_request_goes_to_deepseek_with_key_and_routes(self) -> None:
        opener = _Recorder()
        result = assistant_mod.suggest(
            "description", "добавить кнопку помощника", {"type": "task", "priority": 2,
                                                         "title": "кнопки"},
            routes=route_records(), opener=opener)
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {API_KEY}")
        self.assertEqual(request.get_method(), "POST")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], assistant_mod.DEFAULT_MODEL)
        self.assertFalse(body["stream"])
        prompt = body["messages"][1]["content"]
        self.assertIn("добавить кнопку помощника", prompt)
        self.assertIn("low-pipeline", prompt)
        self.assertIn("dsh", prompt)
        # Скрытый маршрут модели не предлагается.
        self.assertNotIn("hidden-pipeline", prompt)

        suggestion = result["suggestion"]
        self.assertEqual(result["field"], "description")
        self.assertEqual(result["model"], assistant_mod.DEFAULT_MODEL)
        self.assertEqual(suggestion["text"], "Переписанное описание")
        self.assertEqual(suggestion["acceptance"],
                         ["Тест падает без правки", "Кнопка видна на hover"])
        self.assertEqual(suggestion["complexity"], {"level": "medium",
                                                    "reason": "три шага и одна развилка"})
        self.assertEqual(suggestion["route"]["key"], "low-pipeline")
        self.assertEqual(suggestion["route"]["title"], "low")
        self.assertEqual(suggestion["route"]["reason"], "работа понятная и короткая")

    def test_unknown_route_key_is_dropped(self) -> None:
        opener = _Recorder(payload=reply(suggestion_json(
            route={"key": "нет-такого", "reason": "потому что"})))
        result = assistant_mod.suggest("title", "заголовок", {}, routes=route_records(),
                                       opener=opener)
        self.assertIsNone(result["suggestion"]["route"])

    def test_unknown_complexity_level_is_dropped(self) -> None:
        opener = _Recorder(payload=reply(suggestion_json(
            complexity={"level": "extreme", "reason": "очень сложно"})))
        result = assistant_mod.suggest("title", "заголовок", {}, routes=route_records(),
                                       opener=opener)
        self.assertIsNone(result["suggestion"]["complexity"])

    def test_acceptance_is_cleaned_and_deduplicated(self) -> None:
        opener = _Recorder(payload=reply(suggestion_json(
            acceptance=["- первый", "первый", "", "  ", "второй", 42])))
        result = assistant_mod.suggest("acceptance", "старое", {}, routes=route_records(),
                                       opener=opener)
        self.assertEqual(result["suggestion"]["acceptance"], ["первый", "второй"])

    def test_fenced_json_is_parsed(self) -> None:
        fenced = "```json\n" + suggestion_json() + "\n```"
        opener = _Recorder(payload=reply(fenced))
        result = assistant_mod.suggest("title", "заголовок", {}, routes=route_records(),
                                       opener=opener)
        self.assertEqual(result["suggestion"]["text"], "Переписанное описание")

    def test_broken_json_is_502(self) -> None:
        opener = _Recorder(payload=reply("извините, не могу"))
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            assistant_mod.suggest("title", "заголовок", {}, routes=route_records(),
                                  opener=opener)
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("JSON", ctx.exception.message)

    def test_missing_choices_is_502(self) -> None:
        opener = _Recorder(payload={"error": "нет доступа"})
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            assistant_mod.suggest("title", "заголовок", {}, routes=route_records(),
                                  opener=opener)
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("choices", ctx.exception.message)

    def test_upstream_401_mentions_config(self) -> None:
        # Провайдер может эхоить присланный ключ в теле ошибки: в сообщение
        # для клиента тело не попадает — только в лог сервера и без ключа.
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions", 401, "Unauthorized", {},
            io.BytesIO(json.dumps({"error": f"invalid api key {API_KEY}"}).encode("utf-8")))
        opener = _Recorder(error=error)
        with self.assertLogs("listik.assistant", level="WARNING") as logs:
            with self.assertRaises(assistant_mod.AssistantError) as ctx:
                assistant_mod.suggest("title", "заголовок", {}, routes=route_records(),
                                      opener=opener)
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("api_key", ctx.exception.message)
        self.assertIn("[assistant]", ctx.exception.message)
        self.assertNotIn(API_KEY, ctx.exception.message)
        self.assertNotIn("invalid api key", ctx.exception.message)
        # Тело уходит только в лог сервера, с замаскированным ключом.
        logged = "\n".join(logs.output)
        self.assertIn("HTTP 401", logged)
        self.assertIn("***", logged)
        self.assertNotIn(API_KEY, logged)

    def test_upstream_500_hides_body(self) -> None:
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions", 500, "Server Error", {},
            io.BytesIO(json.dumps({"error": f"boom {API_KEY}"}).encode("utf-8")))
        opener = _Recorder(error=error)
        with self.assertLogs("listik.assistant", level="WARNING") as logs:
            with self.assertRaises(assistant_mod.AssistantError) as ctx:
                assistant_mod.suggest("title", "заголовок", {}, routes=route_records(),
                                      opener=opener)
        self.assertEqual(ctx.exception.status, 502)
        self.assertEqual(ctx.exception.message, "DeepSeek ответил ошибкой HTTP 500")
        self.assertNotIn(API_KEY, ctx.exception.message)
        self.assertNotIn(API_KEY, "\n".join(logs.output))

    def test_broken_upstream_json_body_is_not_proxied(self) -> None:
        opener = _Recorder(payload=f'{{"error": "broken {API_KEY}"')
        with self.assertLogs("listik.assistant", level="WARNING") as logs:
            with self.assertRaises(assistant_mod.AssistantError) as ctx:
                assistant_mod.suggest("title", "заголовок", {}, routes=route_records(),
                                      opener=opener)
        self.assertEqual(ctx.exception.status, 502)
        self.assertNotIn(API_KEY, ctx.exception.message)
        self.assertNotIn(API_KEY, "\n".join(logs.output))

    def test_network_error_is_504(self) -> None:
        opener = _Recorder(error=urllib.error.URLError("connection refused"))
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            assistant_mod.suggest("title", "заголовок", {}, routes=route_records(),
                                  opener=opener)
        self.assertEqual(ctx.exception.status, 504)
        self.assertIn("недоступен", ctx.exception.message)

    def test_endpoint_appends_chat_completions(self) -> None:
        self.assertEqual(assistant_mod.endpoint("https://api.deepseek.com"),
                         "https://api.deepseek.com/chat/completions")
        self.assertEqual(assistant_mod.endpoint("https://api.deepseek.com/v1/"),
                         "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(assistant_mod.endpoint("https://host/chat/completions"),
                         "https://host/chat/completions")


class AssistantApiTests(TempDbTestCase):
    """Эндпоинты сервера: /api/assistant/status и /api/assistant/suggest."""

    def setUp(self) -> None:
        super().setUp()
        self.config_path = self.tmp_path / "config.toml"
        self.config_path.write_text(f'[auth]\ntoken = "{TOKEN}"\n[assistant]\n'
                                    f'api_key = "{API_KEY}"\n', encoding="utf-8")
        config_patch = mock.patch.object(paths, "CONFIG_PATH", self.config_path)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        conn_patch.start()
        self.addCleanup(conn_patch.stop)

        for record in route_records():
            routes_store.upsert_route(self.conn, record)

    def _no_key_config(self) -> None:
        self.config_path.write_text(f'[auth]\ntoken = "{TOKEN}"\n', encoding="utf-8")

    def test_status_endpoint_reports_disabled_without_key(self) -> None:
        self._no_key_config()
        status, data = server.handle("GET", "/api/assistant/status", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertFalse(data["enabled"])
        self.assertNotIn(API_KEY, json.dumps(data))

    def test_status_endpoint_enabled_with_key(self) -> None:
        status, data = server.handle("GET", "/api/assistant/status", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertTrue(data["enabled"])
        self.assertEqual(data["model"], assistant_mod.DEFAULT_MODEL)
        self.assertNotIn("api_key", data)

    def test_suggest_without_key_is_503_with_code(self) -> None:
        self._no_key_config()
        with self.assertRaises(server.ApiError) as ctx:
            server.handle("POST", "/api/assistant/suggest", {},
                          {"field": "title", "text": "сделать"}, authed=True)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, errors_mod.SERVER_ERROR)
        self.assertIn("api_key", ctx.exception.message)

    def test_suggest_with_mocked_http_returns_suggestion(self) -> None:
        opener = _Recorder()
        with mock.patch.object(assistant_mod.urllib.request, "urlopen", opener):
            status, data = server.handle(
                "POST", "/api/assistant/suggest", {},
                {"field": "acceptance", "text": "старые критерии",
                 "context": {"type": "task", "project": "listik"}}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(data["field"], "acceptance")
        self.assertEqual(data["suggestion"]["route"]["key"], "low-pipeline")
        self.assertEqual(opener.requests[0].full_url,
                         "https://api.deepseek.com/chat/completions")

    def test_suggest_with_upstream_error_is_mapped(self) -> None:
        opener = _Recorder(error=urllib.error.URLError("no route to host"))
        with mock.patch.object(assistant_mod.urllib.request, "urlopen", opener):
            with self.assertRaises(server.ApiError) as ctx:
                server.handle("POST", "/api/assistant/suggest", {},
                              {"field": "title", "text": "сделать"}, authed=True)
        self.assertEqual(ctx.exception.status, 504)
        self.assertEqual(ctx.exception.code, errors_mod.SERVER_ERROR)

    def test_upstream_401_key_not_in_api_error(self) -> None:
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions", 401, "Unauthorized", {},
            io.BytesIO(json.dumps({"error": f"invalid api key {API_KEY}"}).encode("utf-8")))
        opener = _Recorder(error=error)
        with self.assertLogs("listik.assistant", level="WARNING"):
            with mock.patch.object(assistant_mod.urllib.request, "urlopen", opener):
                with self.assertRaises(server.ApiError) as ctx:
                    server.handle("POST", "/api/assistant/suggest", {},
                                  {"field": "title", "text": "сделать"}, authed=True)
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("[assistant]", ctx.exception.message)
        self.assertNotIn(API_KEY, ctx.exception.message)


class AssistantHttpTests(AssistantApiTests):
    """Живой HTTP-сервер: конверт ответа, токен и отсутствие ключа в ответе."""

    def setUp(self) -> None:
        super().setUp()
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

    def _request(self, method: str, path: str, body: dict | None = None,
                 token: str | None = TOKEN) -> tuple[int, dict]:
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_status_requires_token(self) -> None:
        status, payload = self._request("GET", "/api/assistant/status", token=None)
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_suggest_over_http_with_mocked_deepseek(self) -> None:
        opener = _Recorder()
        with mock.patch.object(assistant_mod.urllib.request, "urlopen", opener):
            status, payload = self._request(
                "POST", "/api/assistant/suggest",
                {"field": "description", "text": "добавить помощника",
                 "context": {"type": "task"}})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertEqual(data["suggestion"]["text"], "Переписанное описание")
        self.assertEqual(data["suggestion"]["complexity"]["level"], "medium")
        self.assertNotIn(API_KEY, json.dumps(payload))

    def test_suggest_without_key_over_http_is_503(self) -> None:
        self._no_key_config()
        status, payload = self._request(
            "POST", "/api/assistant/suggest", {"field": "title", "text": "сделать"})
        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], errors_mod.SERVER_ERROR)
        self.assertIn("api_key", payload["error"])

    def test_upstream_401_over_http_hides_key_and_body(self) -> None:
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions", 401, "Unauthorized", {},
            io.BytesIO(json.dumps({"error": f"invalid api key {API_KEY}"}).encode("utf-8")))
        opener = _Recorder(error=error)
        with self.assertLogs("listik.assistant", level="WARNING"):
            with mock.patch.object(assistant_mod.urllib.request, "urlopen", opener):
                status, payload = self._request(
                    "POST", "/api/assistant/suggest", {"field": "title", "text": "сделать"})
        self.assertEqual(status, 502)
        self.assertFalse(payload["ok"])
        self.assertIn("[assistant]", payload["error"])
        self.assertNotIn(API_KEY, json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
