"""Тесты голосового ввода (`listik/voice.py`, эндпоинты `/api/assistant/*`).

Реальная сеть не трогается: HTTP и к Deepgram, и к DeepSeek подменяется моком
`urlopen` — тест проверяет и сам запрос (URL, заголовки, тело), и разбор ответа,
и все ветки ошибок. Конфиг — временный файл, `config.toml` репозитория не
читается; ключи в тестах фиктивные.
"""
from __future__ import annotations

import base64
import http.client
import io
import json
import logging
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from listik import assistant as assistant_mod
from listik import errors as errors_mod
from listik import paths
from listik import routes_store
from listik import server
from listik import store
from listik import voice as voice_mod
from tests.helpers import TempDbTestCase

DEEPGRAM_KEY = "test-deepgram-key"
API_KEY = "test-deepseek-key"
TOKEN = "voice-test-token"
AUDIO = b"\x1aE\xdf\xa3-fake-webm-bytes"
SECRET_BODY = "secret-body"


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
    ]


def dg_reply(transcript: str = " записать задачу ") -> dict:
    return {"results": {"channels": [{"alternatives": [{"transcript": transcript}]}]}}


def ds_reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def draft_json(**overrides) -> str:
    data = {
        "project": "listik",
        "type": "task",
        "title": "Задачу через голос",
        "description": "Записать задачу голосом",
        "acceptance": ["Черновик показан в форме"],
        "route": {"key": "low-pipeline", "reason": "работа короткая"},
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def http_error(url: str, code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "upstream error", {},
                                  io.BytesIO(body.encode("utf-8")))


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
        self.payload = payload if payload is not None else dg_reply()
        self.error = error
        self.requests: list = []
        self.timeouts: list = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.payload)


class ConfigMixin:
    """Временный config.toml вместо настоящего; ключи в тестах фиктивные."""

    tmp_path: pathlib.Path

    def install_config(self, api_key: str | None = API_KEY,
                       deepgram_key: str | None = DEEPGRAM_KEY, *,
                       assistant: dict | None = None, deepgram: dict | None = None) -> None:
        self.config_path = self.tmp_path / "config.toml"
        self.write_config(api_key=api_key, deepgram_key=deepgram_key,
                          assistant=assistant, deepgram=deepgram)
        patch = mock.patch.object(paths, "CONFIG_PATH", self.config_path)
        patch.start()
        self.addCleanup(patch.stop)
        # Тело ошибок провайдеров пишется в лог сервера; в тестах он не нужен.
        for name in ("listik.voice", "listik.assistant"):
            logger = logging.getLogger(name)
            handler = logging.NullHandler()
            logger.addHandler(handler)
            self.addCleanup(logger.removeHandler, handler)

    def write_config(self, api_key: str | None = API_KEY,
                     deepgram_key: str | None = DEEPGRAM_KEY, *,
                     assistant: dict | None = None, deepgram: dict | None = None) -> None:
        lines = ["[auth]", f'token = "{TOKEN}"']
        if api_key is not None or assistant:
            lines.append("[assistant]")
            if api_key is not None:
                lines.append(f"api_key = {json.dumps(api_key)}")
            for key, value in (assistant or {}).items():
                lines.append(f"{key} = {json.dumps(value) if isinstance(value, str) else value}")
        if deepgram_key is not None or deepgram:
            lines.append("[deepgram]")
            if deepgram_key is not None:
                lines.append(f"api_key = {json.dumps(deepgram_key)}")
            for key, value in (deepgram or {}).items():
                lines.append(f"{key} = {json.dumps(value) if isinstance(value, str) else value}")
        self.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class VoiceConfigTestCase(ConfigMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = pathlib.Path(self._tmpdir.name)
        self.install_config()


class TranscribeTests(VoiceConfigTestCase):
    """`voice.transcribe`: запрос к Deepgram, разбор ответа и все ветки ошибок."""

    def test_without_key_is_503_and_provider_not_called(self) -> None:
        self.write_config(deepgram_key=None)
        opener = _Recorder()
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            voice_mod.transcribe(AUDIO, "audio/webm", opener=opener)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, errors_mod.SERVER_ERROR)
        self.assertIn("api_key", ctx.exception.message)
        self.assertIn("[deepgram]", ctx.exception.message)
        self.assertEqual(opener.requests, [])

    def test_empty_audio_is_400(self) -> None:
        opener = _Recorder()
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            voice_mod.transcribe(b"", "audio/webm", opener=opener)
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors_mod.BAD_ARGUMENT)
        self.assertEqual(ctx.exception.message, "пустая запись")
        self.assertEqual(opener.requests, [])

    def test_audio_larger_than_10mb_is_400(self) -> None:
        opener = _Recorder()
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            voice_mod.transcribe(b"x" * (voice_mod.MAX_AUDIO_BYTES + 1), "audio/webm",
                                 opener=opener)
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors_mod.BAD_ARGUMENT)
        self.assertIn("10 МБ", ctx.exception.message)
        self.assertEqual(opener.requests, [])

    def test_mime_not_audio_is_400(self) -> None:
        for mime in ("video/webm", "audio", "", None, "application/octet-stream"):
            with self.subTest(mime=mime):
                opener = _Recorder()
                with self.assertRaises(assistant_mod.AssistantError) as ctx:
                    voice_mod.transcribe(AUDIO, mime, opener=opener)
                self.assertEqual(ctx.exception.status, 400)
                self.assertEqual(ctx.exception.code, errors_mod.BAD_ARGUMENT)
                self.assertEqual(opener.requests, [])

    def test_request_goes_to_deepgram(self) -> None:
        opener = _Recorder(payload=dg_reply("  записать задачу  "))
        result = voice_mod.transcribe(AUDIO, "audio/webm", opener=opener)
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        parsed = urllib.parse.urlparse(request.full_url)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                         "https://api.deepgram.com/v1/listen")
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query["model"], [voice_mod.DEFAULT_MODEL])
        self.assertEqual(query["language"], ["ru"])
        self.assertEqual(query["smart_format"], ["true"])
        self.assertEqual(query["punctuate"], ["true"])
        self.assertEqual(request.get_header("Authorization"), f"Token {DEEPGRAM_KEY}")
        self.assertEqual(request.get_header("Content-type"), "audio/webm")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, AUDIO)
        self.assertEqual(result, {"transcript": "записать задачу", "model": "nova-2"})

    def test_mime_parameters_pass_through(self) -> None:
        opener = _Recorder()
        voice_mod.transcribe(AUDIO, "audio/webm;codecs=opus", opener=opener)
        self.assertEqual(opener.requests[0].get_header("Content-type"),
                         "audio/webm;codecs=opus")

    def test_custom_base_url_model_and_language(self) -> None:
        self.write_config(deepgram={"base_url": "https://dg.example/", "model": "nova-3",
                                    "language": "en"})
        opener = _Recorder()
        result = voice_mod.transcribe(AUDIO, "audio/ogg", opener=opener)
        parsed = urllib.parse.urlparse(opener.requests[0].full_url)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                         "https://dg.example/v1/listen")
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query["model"], ["nova-3"])
        self.assertEqual(query["language"], ["en"])
        self.assertEqual(result["model"], "nova-3")

    def test_empty_transcript_is_not_an_error(self) -> None:
        opener = _Recorder(payload=dg_reply("   "))
        self.assertEqual(voice_mod.transcribe(AUDIO, "audio/webm", opener=opener),
                         {"transcript": "", "model": voice_mod.DEFAULT_MODEL})

    def test_response_without_transcript_is_502(self) -> None:
        payloads = [
            {},
            {"metadata": {}},
            {"results": {}},
            {"results": {"channels": []}},
            {"results": {"channels": [{"alternatives": []}]}},
            {"results": {"channels": [{"alternatives": [{"transcript": 42}]}]}},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                opener = _Recorder(payload=payload)
                with self.assertLogs("listik.voice", level="WARNING"):
                    with self.assertRaises(assistant_mod.AssistantError) as ctx:
                        voice_mod.transcribe(AUDIO, "audio/webm", opener=opener)
                self.assertEqual(ctx.exception.status, 502)
                self.assertIn("transcript", ctx.exception.message)

    def test_non_json_body_is_502(self) -> None:
        opener = _Recorder(payload="<html>" + SECRET_BODY + "</html>")
        with self.assertLogs("listik.voice", level="WARNING"):
            with self.assertRaises(assistant_mod.AssistantError) as ctx:
                voice_mod.transcribe(AUDIO, "audio/webm", opener=opener)
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("не JSON", ctx.exception.message)
        self.assertNotIn(SECRET_BODY, ctx.exception.message)

    def test_401_mentions_deepgram_key_and_hides_body(self) -> None:
        body = json.dumps({"err_msg": f"invalid key {DEEPGRAM_KEY}", "body": SECRET_BODY})
        opener = _Recorder(error=http_error("https://api.deepgram.com/v1/listen", 401, body))
        with self.assertLogs("listik.voice", level="WARNING") as logs:
            with self.assertRaises(assistant_mod.AssistantError) as ctx:
                voice_mod.transcribe(AUDIO, "audio/webm", opener=opener)
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("[deepgram].api_key", ctx.exception.message)
        self.assertNotIn(DEEPGRAM_KEY, ctx.exception.message)
        self.assertNotIn(SECRET_BODY, ctx.exception.message)
        logged = "\n".join(logs.output)
        self.assertIn("HTTP 401", logged)
        self.assertIn("***", logged)
        self.assertNotIn(DEEPGRAM_KEY, logged)

    def test_403_has_the_same_text_as_401(self) -> None:
        body = json.dumps({"err_msg": f"invalid key {DEEPGRAM_KEY}", "body": SECRET_BODY})
        messages = {}
        for code in (401, 403):
            opener = _Recorder(error=http_error("https://api.deepgram.com/v1/listen", code,
                                                body))
            with self.assertLogs("listik.voice", level="WARNING"):
                with self.assertRaises(assistant_mod.AssistantError) as ctx:
                    voice_mod.transcribe(AUDIO, "audio/webm", opener=opener)
            self.assertEqual(ctx.exception.status, 502)
            self.assertIn("[deepgram].api_key", ctx.exception.message)
            messages[code] = ctx.exception.message
        self.assertEqual(messages[401].replace("401", "403"), messages[403])

    def test_500_is_502_without_body(self) -> None:
        body = json.dumps({"err_msg": f"boom {DEEPGRAM_KEY}", "body": SECRET_BODY})
        opener = _Recorder(error=http_error("https://api.deepgram.com/v1/listen", 500, body))
        with self.assertLogs("listik.voice", level="WARNING") as logs:
            with self.assertRaises(assistant_mod.AssistantError) as ctx:
                voice_mod.transcribe(AUDIO, "audio/webm", opener=opener)
        self.assertEqual(ctx.exception.status, 502)
        self.assertEqual(ctx.exception.message, "Deepgram ответил ошибкой HTTP 500")
        self.assertNotIn(SECRET_BODY, ctx.exception.message)
        # Тело уходит только в лог сервера, с замаскированным ключом.
        logged = "\n".join(logs.output)
        self.assertIn(SECRET_BODY, logged)
        self.assertNotIn(DEEPGRAM_KEY, logged)

    def test_network_error_is_504(self) -> None:
        opener = _Recorder(error=urllib.error.URLError("connection refused"))
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            voice_mod.transcribe(AUDIO, "audio/webm", opener=opener)
        self.assertEqual(ctx.exception.status, 504)
        self.assertIn("недоступен", ctx.exception.message)

    def test_timeout_is_504(self) -> None:
        opener = _Recorder(error=TimeoutError("timed out"))
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            voice_mod.transcribe(AUDIO, "audio/webm", opener=opener, timeout=5)
        self.assertEqual(ctx.exception.status, 504)
        self.assertIn("не ответил", ctx.exception.message)

    def test_settings_defaults_and_overrides(self) -> None:
        self.write_config(deepgram_key=None)
        self.assertEqual(voice_mod.settings(),
                         {"api_key": "", "base_url": voice_mod.DEFAULT_BASE_URL,
                          "model": voice_mod.DEFAULT_MODEL,
                          "language": voice_mod.DEFAULT_LANGUAGE})
        self.write_config(deepgram={"base_url": "https://dg.example/", "model": "nova-3",
                                    "language": "en"})
        cfg_settings = voice_mod.settings()
        self.assertEqual(cfg_settings["base_url"], "https://dg.example")
        self.assertEqual(cfg_settings["model"], "nova-3")
        self.assertEqual(cfg_settings["language"], "en")


class DraftTests(VoiceConfigTestCase):
    """`voice.draft`: промпт, нормализация ответа и ошибки."""

    def setUp(self) -> None:
        super().setUp()
        self.projects = [{"slug": "listik", "title": "Listik"},
                         {"slug": "orders", "title": "Заказы"}]

    def _draft(self, opener, text: str = "запиши задачу про голосовой ввод",
               projects=None) -> dict:
        return voice_mod.draft(
            text, projects=self.projects if projects is None else projects,
            routes=route_records(), opener=opener)

    def test_empty_text_is_400(self) -> None:
        for text in ("", "   ", None):
            with self.subTest(text=text):
                opener = _Recorder(payload=ds_reply(draft_json()))
                with self.assertRaises(assistant_mod.AssistantError) as ctx:
                    self._draft(opener, text=text)
                self.assertEqual(ctx.exception.status, 400)
                self.assertEqual(ctx.exception.code, errors_mod.BAD_ARGUMENT)
                self.assertIn("нечего разбирать", ctx.exception.message)
                self.assertEqual(opener.requests, [])

    def test_too_long_text_is_400(self) -> None:
        opener = _Recorder(payload=ds_reply(draft_json()))
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            self._draft(opener, text="а" * (voice_mod.MAX_TEXT_CHARS + 1))
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors_mod.BAD_ARGUMENT)
        self.assertEqual(opener.requests, [])

    def test_without_assistant_key_is_503_with_the_same_text_as_suggest(self) -> None:
        self.write_config(api_key=None)
        opener = _Recorder(payload=ds_reply(draft_json()))
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            self._draft(opener)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, errors_mod.SERVER_ERROR)
        with self.assertRaises(assistant_mod.AssistantError) as suggest_ctx:
            assistant_mod.suggest("title", "текст", {})
        self.assertEqual(ctx.exception.message, suggest_ctx.exception.message)
        self.assertEqual(opener.requests, [])

    def test_request_goes_to_deepseek_with_projects_and_routes(self) -> None:
        opener = _Recorder(payload=ds_reply(draft_json()))
        result = self._draft(opener, text="надо записать задачу голосом")
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {API_KEY}")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], assistant_mod.DEFAULT_MODEL)
        self.assertFalse(body["stream"])
        prompt = json.loads(body["messages"][1]["content"])
        self.assertEqual(prompt["text"], "надо записать задачу голосом")
        self.assertEqual([item["slug"] for item in prompt["projects"]], ["listik", "orders"])
        self.assertIn("low-pipeline", json.dumps(prompt, ensure_ascii=False))
        self.assertNotIn("hidden-pipeline", json.dumps(prompt, ensure_ascii=False))

        self.assertEqual(result["model"], assistant_mod.DEFAULT_MODEL)
        self.assertEqual(result["draft"], {
            "project": "listik",
            "type": "task",
            "title": "Задачу через голос",
            "description": "Записать задачу голосом",
            "acceptance": ["Черновик показан в форме"],
            "route": {"key": "low-pipeline", "kind": "pipeline", "title": "low",
                      "hint": "на 20 мин, самый дешёвый", "reason": "работа короткая"},
        })

    def test_missing_fields_become_null_and_all_keys_present(self) -> None:
        opener = _Recorder(payload=ds_reply("{}"))
        draft = self._draft(opener)["draft"]
        self.assertEqual(set(draft), {"project", "type", "title", "description",
                                      "acceptance", "route"})
        self.assertIsNone(draft["project"])
        self.assertIsNone(draft["type"])
        self.assertIsNone(draft["title"])
        self.assertIsNone(draft["description"])
        self.assertIsNone(draft["acceptance"])
        self.assertIsNone(draft["route"])

    def test_blank_title_and_description_become_null(self) -> None:
        opener = _Recorder(payload=ds_reply(draft_json(title="   ", description="\t\n")))
        draft = self._draft(opener)["draft"]
        self.assertIsNone(draft["title"])
        self.assertIsNone(draft["description"])

    def test_project_outside_the_list_becomes_null(self) -> None:
        for project in ("evil", "listik ", "LISTIK", "", None, 42):
            with self.subTest(project=project):
                opener = _Recorder(payload=ds_reply(draft_json(project=project)))
                self.assertIsNone(self._draft(opener)["draft"]["project"])

    def test_unknown_type_becomes_null(self) -> None:
        opener = _Recorder(payload=ds_reply(draft_json(type="feature")))
        self.assertIsNone(self._draft(opener)["draft"]["type"])

    def test_acceptance_is_cleaned_and_truncated(self) -> None:
        opener = _Recorder(payload=ds_reply(draft_json(
            acceptance=["- первый", "первый", "", "  ", "второй", 42, "• третий", "* третий"])))
        draft = self._draft(opener)["draft"]
        self.assertEqual(draft["acceptance"], ["первый", "второй", "третий"])

        opener = _Recorder(payload=ds_reply(draft_json(
            acceptance=[f"критерий {i}" for i in range(12)])))
        self.assertEqual(self._draft(opener)["draft"]["acceptance"],
                         [f"критерий {i}" for i in range(10)])

    def test_empty_acceptance_becomes_null(self) -> None:
        for acceptance in ([], ["   ", 42]):
            with self.subTest(acceptance=acceptance):
                opener = _Recorder(payload=ds_reply(draft_json(acceptance=acceptance)))
                self.assertIsNone(self._draft(opener)["draft"]["acceptance"])

    def test_route_only_from_visible_routes(self) -> None:
        opener = _Recorder(payload=ds_reply(draft_json(
            route={"key": "low-pipeline", "reason": "короткая работа"})))
        route = self._draft(opener)["draft"]["route"]
        self.assertEqual(route["key"], "low-pipeline")
        self.assertEqual(route["kind"], "pipeline")
        self.assertEqual(route["title"], "low")
        self.assertEqual(route["reason"], "короткая работа")

        for key in ("нет-такого", "hidden-pipeline", "", None):
            with self.subTest(key=key):
                opener = _Recorder(payload=ds_reply(draft_json(route={"key": key})))
                self.assertIsNone(self._draft(opener)["draft"]["route"])

    def test_more_than_100_projects_are_truncated(self) -> None:
        projects = [{"slug": f"p{i:03d}", "title": f"проект {i}"} for i in range(105)]
        opener = _Recorder(payload=ds_reply(draft_json(project="p100")))
        result = voice_mod.draft("запиши задачу", projects=projects[::-1],
                                 routes=route_records(), opener=opener)
        self.assertIsNone(result["draft"]["project"])
        payload = json.loads(opener.requests[0].data.decode("utf-8"))
        prompt = json.loads(payload["messages"][1]["content"])
        self.assertEqual([item["slug"] for item in prompt["projects"]],
                         [f"p{i:03d}" for i in range(100)])
        self.assertNotIn("p100", opener.requests[0].data.decode("utf-8"))

    def test_project_outside_the_truncated_list_becomes_null(self) -> None:
        projects = [{"slug": f"p{i:03d}", "title": ""} for i in range(105)]
        opener = _Recorder(payload=ds_reply(draft_json(project="p099")))
        result = voice_mod.draft("запиши задачу", projects=projects,
                                 routes=route_records(), opener=opener)
        self.assertEqual(result["draft"]["project"], "p099")

    def test_network_error_is_504(self) -> None:
        opener = _Recorder(error=urllib.error.URLError("no route to host"))
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            self._draft(opener)
        self.assertEqual(ctx.exception.status, 504)

    def test_broken_model_answer_is_502(self) -> None:
        opener = _Recorder(payload=ds_reply("извините, не могу"))
        with self.assertRaises(assistant_mod.AssistantError) as ctx:
            self._draft(opener)
        self.assertEqual(ctx.exception.status, 502)


class VoiceApiTests(ConfigMixin, TempDbTestCase):
    """Эндпоинты `/api/assistant/*` через `server.handle`."""

    def setUp(self) -> None:
        super().setUp()
        self.install_config()
        conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        conn_patch.start()
        self.addCleanup(conn_patch.stop)
        for record in route_records():
            routes_store.upsert_route(self.conn, record)

    def _transcribe(self, body: dict):
        return server.handle("POST", "/api/assistant/transcribe", {}, body, authed=True)

    def _draft(self, text: str = "записать задачу голосом"):
        return server.handle("POST", "/api/assistant/draft", {}, {"text": text}, authed=True)

    def test_status_without_deepgram_has_voice_false(self) -> None:
        self.write_config(deepgram_key=None)
        status, data = server.handle("GET", "/api/assistant/status", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(set(data), {"enabled", "model", "base_url", "voice"})
        self.assertTrue(data["enabled"])
        self.assertFalse(data["voice"])
        self.assertNotIn(API_KEY, json.dumps(data))

    def test_status_without_assistant_key_has_voice_false(self) -> None:
        self.write_config(api_key=None)
        status, data = server.handle("GET", "/api/assistant/status", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertFalse(data["enabled"])
        self.assertFalse(data["voice"])

    def test_status_with_both_keys_has_voice_true_without_keys(self) -> None:
        status, data = server.handle("GET", "/api/assistant/status", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertTrue(data["enabled"])
        self.assertTrue(data["voice"])
        payload = json.dumps(data, ensure_ascii=False)
        self.assertNotIn(API_KEY, payload)
        self.assertNotIn(DEEPGRAM_KEY, payload)

    def test_transcribe_endpoint_returns_transcript(self) -> None:
        opener = _Recorder(payload=dg_reply("записать задачу голосом"))
        body = {"audio_base64": base64.b64encode(AUDIO).decode("ascii"), "mime": "audio/webm"}
        with mock.patch.object(voice_mod.urllib.request, "urlopen", opener):
            status, data = self._transcribe(body)
        self.assertEqual(status, 200)
        self.assertEqual(data, {"transcript": "записать задачу голосом",
                                "model": voice_mod.DEFAULT_MODEL})
        self.assertEqual(len(opener.requests), 1)
        self.assertNotIn(DEEPGRAM_KEY, json.dumps(data))

    def test_transcribe_invalid_base64_is_400(self) -> None:
        opener = _Recorder()
        with mock.patch.object(voice_mod.urllib.request, "urlopen", opener):
            with self.assertRaises(server.ApiError) as ctx:
                self._transcribe({"audio_base64": "!!!не base64!!!", "mime": "audio/webm"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors_mod.BAD_ARGUMENT)
        self.assertEqual(opener.requests, [])

    def test_transcribe_without_audio_base64_is_400(self) -> None:
        opener = _Recorder()
        with mock.patch.object(voice_mod.urllib.request, "urlopen", opener):
            with self.assertRaises(server.ApiError) as ctx:
                self._transcribe({"mime": "audio/webm"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors_mod.BAD_ARGUMENT)
        self.assertEqual(opener.requests, [])

    def test_transcribe_oversized_audio_is_400_and_deepgram_not_called(self) -> None:
        audio = b"x" * (voice_mod.MAX_AUDIO_BYTES + 1)
        opener = _Recorder()
        body = {"audio_base64": base64.b64encode(audio).decode("ascii"), "mime": "audio/webm"}
        with mock.patch.object(voice_mod.urllib.request, "urlopen", opener):
            with self.assertRaises(server.ApiError) as ctx:
                self._transcribe(body)
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors_mod.BAD_ARGUMENT)
        self.assertIn("10 МБ", ctx.exception.message)
        self.assertEqual(opener.requests, [])

    def test_transcribe_without_deepgram_key_is_503(self) -> None:
        self.write_config(deepgram_key=None)
        opener = _Recorder()
        with mock.patch.object(voice_mod.urllib.request, "urlopen", opener):
            with self.assertRaises(server.ApiError) as ctx:
                self._transcribe({"audio_base64": base64.b64encode(AUDIO).decode("ascii"),
                                  "mime": "audio/webm"})
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, errors_mod.SERVER_ERROR)
        self.assertIn("[deepgram]", ctx.exception.message)
        self.assertEqual(opener.requests, [])

    def test_draft_takes_project_from_db_and_creates_nothing(self) -> None:
        store.add_project(self.conn, slug="listik", title="Listik")
        store.add_project(self.conn, slug="orders", title="Заказы")
        before = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        opener = _Recorder(payload=ds_reply(draft_json(project="listik")))
        with mock.patch.object(assistant_mod.urllib.request, "urlopen", opener):
            status, data = self._draft()
        self.assertEqual(status, 200)
        self.assertEqual(data["model"], assistant_mod.DEFAULT_MODEL)
        self.assertEqual(set(data["draft"]), {"project", "type", "title", "description",
                                              "acceptance", "route"})
        self.assertEqual(data["draft"]["project"], "listik")
        after = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        self.assertEqual(before, after)
        prompt = json.loads(opener.requests[0].data.decode("utf-8"))["messages"][1]["content"]
        self.assertIn("listik", prompt)
        self.assertIn("orders", prompt)

    def test_archived_project_does_not_pass_into_draft(self) -> None:
        store.add_project(self.conn, slug="old-project", title="Старый")
        store.update_project(self.conn, "old-project", archived=True)
        opener = _Recorder(payload=ds_reply(draft_json(project="old-project")))
        with mock.patch.object(assistant_mod.urllib.request, "urlopen", opener):
            status, data = self._draft()
        self.assertEqual(status, 200)
        self.assertIsNone(data["draft"]["project"])
        self.assertNotIn("old-project", opener.requests[0].data.decode("utf-8"))

    def test_draft_empty_text_is_400(self) -> None:
        opener = _Recorder(payload=ds_reply(draft_json()))
        with mock.patch.object(assistant_mod.urllib.request, "urlopen", opener):
            with self.assertRaises(server.ApiError) as ctx:
                self._draft(text="   ")
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors_mod.BAD_ARGUMENT)
        self.assertEqual(opener.requests, [])


class VoiceHttpTests(VoiceApiTests):
    """Живой HTTP-сервер: конверт ответа, токен и отсутствие ключей в ответе."""

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
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        payload = (json.dumps(body, ensure_ascii=False).encode("utf-8")
                   if body is not None else None)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_status_without_token_is_401(self) -> None:
        status, payload = self._request("GET", "/api/assistant/status", token=None)
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_draft_without_token_is_401(self) -> None:
        status, payload = self._request("POST", "/api/assistant/draft",
                                        {"text": "записать задачу"}, token=None)
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_status_reports_voice_flag_over_http(self) -> None:
        status, payload = self._request("GET", "/api/assistant/status")
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["voice"])
        self.assertNotIn(DEEPGRAM_KEY, json.dumps(payload, ensure_ascii=False))
        self.write_config(deepgram_key=None)
        status, payload = self._request("GET", "/api/assistant/status")
        self.assertEqual(status, 200)
        self.assertFalse(payload["data"]["voice"])

    def test_transcribe_over_http_with_mocked_deepgram(self) -> None:
        opener = _Recorder(payload=dg_reply("записать задачу голосом"))
        body = {"audio_base64": base64.b64encode(AUDIO).decode("ascii"), "mime": "audio/webm"}
        with mock.patch.object(voice_mod.urllib.request, "urlopen", opener):
            status, payload = self._request("POST", "/api/assistant/transcribe", body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["transcript"], "записать задачу голосом")
        self.assertNotIn(DEEPGRAM_KEY, json.dumps(payload, ensure_ascii=False))

    def test_invalid_base64_over_http_is_400(self) -> None:
        opener = _Recorder()
        with mock.patch.object(voice_mod.urllib.request, "urlopen", opener):
            status, payload = self._request("POST", "/api/assistant/transcribe",
                                            {"audio_base64": "не-base64", "mime": "audio/webm"})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], errors_mod.BAD_ARGUMENT)
        self.assertEqual(opener.requests, [])

    def test_oversized_audio_over_http_is_400_and_deepgram_not_called(self) -> None:
        audio = b"x" * (voice_mod.MAX_AUDIO_BYTES + 1)
        body = {"audio_base64": base64.b64encode(audio).decode("ascii"), "mime": "audio/webm"}
        opener = _Recorder()
        with mock.patch.object(voice_mod.urllib.request, "urlopen", opener):
            status, payload = self._request("POST", "/api/assistant/transcribe", body)
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], errors_mod.BAD_ARGUMENT)
        self.assertEqual(opener.requests, [])

    def test_upstream_401_over_http_hides_key_and_body(self) -> None:
        body = json.dumps({"err_msg": f"invalid key {DEEPGRAM_KEY}", "body": SECRET_BODY})
        opener = _Recorder(error=http_error("https://api.deepgram.com/v1/listen", 401, body))
        with self.assertLogs("listik.voice", level="WARNING"):
            with mock.patch.object(voice_mod.urllib.request, "urlopen", opener):
                status, payload = self._request(
                    "POST", "/api/assistant/transcribe",
                    {"audio_base64": base64.b64encode(AUDIO).decode("ascii"),
                     "mime": "audio/webm"})
        self.assertEqual(status, 502)
        self.assertFalse(payload["ok"])
        self.assertIn("[deepgram].api_key", payload["error"])
        wire = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(DEEPGRAM_KEY, wire)
        self.assertNotIn(SECRET_BODY, wire)

    def test_upstream_500_over_http_hides_body(self) -> None:
        opener = _Recorder(error=http_error("https://api.deepgram.com/v1/listen", 500,
                                            SECRET_BODY))
        with self.assertLogs("listik.voice", level="WARNING"):
            with mock.patch.object(voice_mod.urllib.request, "urlopen", opener):
                status, payload = self._request(
                    "POST", "/api/assistant/transcribe",
                    {"audio_base64": base64.b64encode(AUDIO).decode("ascii"),
                     "mime": "audio/webm"})
        self.assertEqual(status, 502)
        self.assertNotIn(SECRET_BODY, json.dumps(payload, ensure_ascii=False))

    def test_draft_over_http_returns_draft(self) -> None:
        store.add_project(self.conn, slug="listik", title="Listik")
        opener = _Recorder(payload=ds_reply(draft_json(project="listik")))
        with mock.patch.object(assistant_mod.urllib.request, "urlopen", opener):
            status, payload = self._request("POST", "/api/assistant/draft",
                                            {"text": "записать задачу голосом"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["draft"]["project"], "listik")
        self.assertEqual(payload["data"]["draft"]["route"]["key"], "low-pipeline")
        self.assertNotIn(API_KEY, json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
