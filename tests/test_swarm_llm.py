"""`listik/swarm_llm.py` — единственная точка вызова модели в рое (`complete_json`), и
`deps.find_cycles`/`deps.apply_planned_blocks` — запись машинных `blocks` от модели
(listik-kbh5, шаг swarm-6, порция a).

Реальная сеть и подпроцессы в тестах не участвуют, кроме одного теста «настоящий
подпроцесс» — он зовёт `sys.executable`, не сеть.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
import urllib.error
from unittest import mock

from listik import deps, errors, store, swarm_llm
from tests.helpers import TempDbTestCase

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}}


class _FakeResponse:
    def __init__(self, payload):
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


class _Opener:
    """Мок `urlopen`: запоминает `Request`-ы, отдаёт заранее заданный ответ/ошибку."""

    def __init__(self, payload=None, error: BaseException | None = None):
        self.payload = payload if payload is not None else _reply('{"a": 1}')
        self.error = error
        self.requests: list = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.payload)


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Runner:
    """Мок команды: запоминает вызовы, отдаёт заданный результат/ошибку."""

    def __init__(self, result: _Result | None = None, error: BaseException | None = None):
        self.result = result if result is not None else _Result(stdout='{"b": 2}')
        self.error = error
        self.calls: list = []

    def __call__(self, argv, input_text, timeout):
        self.calls.append((argv, input_text, timeout))
        if self.error is not None:
            raise self.error
        return self.result


# --------------------------------------------------------------------------- settings


class SettingsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        out = swarm_llm.settings({})
        self.assertEqual(out["api_key"], "")
        self.assertEqual(out["base_url"], swarm_llm.DEFAULT_BASE_URL)
        self.assertEqual(out["model"], swarm_llm.DEFAULT_MODEL)
        self.assertEqual(out["command"], [])

    def test_file_and_env(self) -> None:
        cfg = {"swarm": {"api_key": "k", "base_url": "https://x/v1/", "model": "m",
                         "command": ["python3", "f.py"]}}
        out = swarm_llm.settings(cfg)
        self.assertEqual(out["api_key"], "k")
        self.assertEqual(out["base_url"], "https://x/v1")
        self.assertEqual(out["model"], "m")
        self.assertEqual(out["command"], ["python3", "f.py"])

        with mock.patch.dict("os.environ", {
            swarm_llm.ENV_MODEL: "m2",
            swarm_llm.ENV_BASE_URL: "https://y",
            swarm_llm.ENV_API_KEY: "k2",
        }):
            out2 = swarm_llm.settings(cfg)
        self.assertEqual(out2["model"], "m2")
        self.assertEqual(out2["base_url"], "https://y")
        self.assertEqual(out2["api_key"], "k2")

    def test_command_must_be_list_of_strings(self) -> None:
        with self.assertRaises(errors.BadArgument):
            swarm_llm.settings({"swarm": {"command": "pi"}})

    def test_model_name_resolution(self) -> None:
        self.assertEqual(swarm_llm.model_name({}), swarm_llm.DEFAULT_MODEL)
        cfg = {"swarm": {"model": " m "}}
        self.assertEqual(swarm_llm.model_name(cfg), "m")
        with mock.patch.dict("os.environ", {swarm_llm.ENV_MODEL: "m2"}, clear=False):
            self.assertEqual(swarm_llm.model_name(cfg), "m2")
        with mock.patch.dict("os.environ", {swarm_llm.ENV_MODEL: ""}, clear=False):
            self.assertEqual(swarm_llm.model_name(cfg), "m")
        command_cfg = {"swarm": {"command": "pi", "model": "m"}}
        self.assertEqual(swarm_llm.model_name(command_cfg), "m")
        with self.assertRaises(errors.BadArgument):
            swarm_llm.settings(command_cfg)
        self.assertEqual(swarm_llm.settings(cfg)["model"], swarm_llm.model_name(cfg))

    def test_jev_defaults(self) -> None:
        with mock.patch.dict("os.environ", {
            swarm_llm.ENV_JEV_API_KEY: "", swarm_llm.ENV_JEV_URL: "",
            swarm_llm.ENV_JEV_MODEL: "",
        }):
            out = swarm_llm.settings({})
        self.assertEqual(out["jev_api_key"], "")
        self.assertEqual(out["jev_url"], "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(out["jev_url"], swarm_llm.JEV_DEFAULT_URL)
        self.assertEqual(out["jev_model"], "typesafe/jev-1.13")
        self.assertFalse(swarm_llm.jev_enabled(out))

    def test_jev_file_and_env_settings(self) -> None:
        cfg = {"swarm": {"jev_api_key": " k ", "jev_url": "https://x/d/",
                         "jev_model": "m"}}
        blank = {swarm_llm.ENV_JEV_API_KEY: "", swarm_llm.ENV_JEV_URL: "",
                 swarm_llm.ENV_JEV_MODEL: ""}
        for value in ("", "   "):
            with self.subTest(value=value), mock.patch.dict(
                    "os.environ", dict.fromkeys(blank, value)):
                out = swarm_llm.settings(cfg)
            self.assertEqual((out["jev_api_key"], out["jev_url"], out["jev_model"]),
                             ("k", "https://x/d", "m"))
            self.assertTrue(swarm_llm.jev_enabled(out))
        with mock.patch.dict("os.environ", {
            swarm_llm.ENV_JEV_API_KEY: " k2 ", swarm_llm.ENV_JEV_URL: " https://y/d/ ",
            swarm_llm.ENV_JEV_MODEL: " m2 ",
        }):
            out = swarm_llm.settings(cfg)
        self.assertEqual((out["jev_api_key"], out["jev_url"], out["jev_model"]),
                         ("k2", "https://y/d", "m2"))

    def test_jev_enabled_uses_only_jev_key(self) -> None:
        self.assertFalse(swarm_llm.jev_enabled({"api_key": "glm", "jev_api_key": ""}))
        self.assertTrue(swarm_llm.jev_enabled({"jev_api_key": "k"}))


class DecideTests(unittest.TestCase):
    question = {"needed": {"type": "noul", "instructions": "Нужна зависимость?",
                            "criteria": {"true": "да", "false": "нет"}}}
    state = {"a": {"id": "a"}, "b": {"id": "b"}}

    def _settings(self, **kwargs):
        return {"jev_api_key": "secret-key", "jev_url": "https://x/d",
                "jev_model": "typesafe/jev-1.13", **kwargs}

    def test_request_shape_and_tutorial_response(self) -> None:
        questions = {**self.question,
                     "route": {"type": "choice", "instructions": "Выбери маршрут",
                               "criteria": {"fast": "быстрый", "slow": "медленный"}}}
        payload = {
            "id": "gen-dec-example", "model": "typesafe/jev-1.13-20260917",
            "provider": "TypeSafe",
            "answers": {"needed": {"type": "noul", "noul": 0.96},
                        "route": {"type": "choice", "choice": "fast", "confidence": 0.67,
                                  "probabilities": {"fast": 0.78, "slow": 0.22}}},
            "usage": {"input_tokens": 476, "output_tokens": 70, "cost": 0.00002},
        }
        opener = mock.Mock(wraps=_Opener(payload=payload))
        out = swarm_llm.decide(self.state, questions, cfg_settings=self._settings(jev_api_key="k"),
                               opener=opener)
        req = opener.call_args.args[0]
        self.assertEqual(req.full_url, "https://x/d")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer k")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("Accept"), "application/json")
        self.assertEqual(opener.call_args.kwargs, {"timeout": swarm_llm.JEV_TIMEOUT})
        self.assertEqual(json.loads(req.data), {"model": "typesafe/jev-1.13",
                                                "state": self.state, "questions": questions})
        self.assertEqual(out, {k: payload[k] for k in ("answers", "usage", "model")})

    def test_optional_metadata_and_probability_endpoints(self) -> None:
        for value in (0, 1, 0.0, 1.0):
            answers = {"needed": {"type": "noul", "noul": value}}
            with self.subTest(value=value):
                out = swarm_llm.decide(None, self.question, cfg_settings=self._settings(),
                                       opener=_Opener(payload={"answers": answers}))
            self.assertEqual(out, {"answers": answers, "usage": None, "model": None})

    def test_http_errors_mask_key_and_log_body(self) -> None:
        for code in (401, 403, 429, 500):
            error = urllib.error.HTTPError(
                "https://x/d", code, "err", {}, io.BytesIO(b'{"error":"invalid secret-key"}'))
            with self.subTest(code=code), self.assertLogs("listik.swarm_llm", "WARNING") as logs:
                with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
                    swarm_llm.decide(self.state, self.question, cfg_settings=self._settings(),
                                     opener=_Opener(error=error))
            self.assertEqual(ctx.exception.status, 502)
            self.assertIn(f"HTTP {code}", ctx.exception.message)
            if code in (401, 403):
                self.assertIn("[swarm].jev_api_key", ctx.exception.message)
            self.assertNotIn("secret-key", ctx.exception.message)
            self.assertIn('"error":"invalid ***"', "\n".join(logs.output))
            self.assertNotIn("secret-key", "\n".join(logs.output))

    def test_transport_errors_are_504(self) -> None:
        cases = [(urllib.error.URLError("boom"), "jev недоступен (https://x/d): boom"),
                 (TimeoutError(), "jev не ответил за 7 с")]
        for error, message in cases:
            with self.subTest(error=error), self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
                swarm_llm.decide(self.state, self.question, cfg_settings=self._settings(),
                                 opener=_Opener(error=error), timeout=7)
            self.assertEqual(ctx.exception.status, 504)
            self.assertEqual(ctx.exception.message, message)

    def test_invalid_json_and_answers_container_log_body(self) -> None:
        for payload in ("secret-key не JSON", ["secret-key"],
                        {"error": "secret-key"}, {"answers": ["secret-key"]}):
            with self.subTest(payload=payload), self.assertLogs("listik.swarm_llm", "WARNING") as logs:
                with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
                    swarm_llm.decide(self.state, self.question, cfg_settings=self._settings(),
                                     opener=_Opener(payload=payload))
            self.assertEqual(ctx.exception.status, 502)
            self.assertNotIn("secret-key", ctx.exception.message)
            self.assertNotIn("secret-key", "\n".join(logs.output))
            self.assertIn("***", "\n".join(logs.output))

    def test_invalid_question_answers_are_502_and_logged(self) -> None:
        choice = {"needed": {"type": "choice", "instructions": "Выбор",
                             "criteria": {"yes": "да"}}}
        cases = [(self.question, {}),
                 (self.question, {"needed": None}),
                 (self.question, {"needed": []}),
                 (self.question, {"needed": {"noul": 0.9}}),
                 (self.question, {"needed": {"type": "choice", "choice": "yes"}}),
                 (self.question, {"needed": {"type": "noul"}}),
                 *[(self.question, {"needed": {"type": "noul", "noul": value}})
                   for value in (-0.1, 1.5, "да", True, False, None, float("nan"), float("inf"))],
                 *[(choice, {"needed": {"type": "choice", "choice": value}})
                   for value in ("other", 1, None)],
                 (choice, {"needed": {"type": "noul", "noul": 0.9}})]
        for question, answers in cases:
            payload = {"answers": answers, "extra": "secret-key"}
            with self.subTest(answers=answers), self.assertLogs("listik.swarm_llm", "WARNING") as logs:
                with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
                    swarm_llm.decide(self.state, question, cfg_settings=self._settings(),
                                     opener=_Opener(payload=payload))
            self.assertEqual(ctx.exception.status, 502)
            self.assertTrue(ctx.exception.message.startswith("jev: негодный ответ на needed:"))
            self.assertLessEqual(len(ctx.exception.message), 200)
            self.assertIn('"answers"', "\n".join(logs.output))
            self.assertIn("***", "\n".join(logs.output))
            self.assertNotIn("secret-key", "\n".join(logs.output))

    def test_bad_answer_message_is_bounded_for_long_question_name(self) -> None:
        with self.assertLogs("listik.swarm_llm", "WARNING"):
            with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
                swarm_llm.decide({}, {"x" * 300: self.question["needed"]},
                                 cfg_settings=self._settings(), opener=_Opener(payload={"answers": {}}))
        self.assertLessEqual(len(ctx.exception.message), 200)

    def test_empty_key_is_503_without_network(self) -> None:
        opener = _Opener()
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.decide(self.state, self.question,
                             cfg_settings=self._settings(jev_api_key=""), opener=opener)
        self.assertEqual(ctx.exception.status, 503)
        self.assertIn("LISTIK_SWARM_JEV_API_KEY", ctx.exception.message)
        self.assertEqual(opener.requests, [])


# --------------------------------------------------------------------------- HTTP


class HttpRequestTests(unittest.TestCase):
    def test_request_shape(self) -> None:
        opener = _Opener()
        cfg_settings = swarm_llm.settings({"swarm": {"api_key": "k", "base_url": "https://x/v1"}})
        messages = [{"role": "user", "content": "hi"}]
        swarm_llm.complete_json(messages, SCHEMA, name="swarm_plan", cfg_settings=cfg_settings,
                                opener=opener)
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://x/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer k")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], cfg_settings["model"])
        self.assertEqual(body["messages"], messages)
        self.assertEqual(body["temperature"], 0)
        self.assertIs(body["stream"], False)
        self.assertEqual(body["response_format"]["type"], "json_schema")
        js = body["response_format"]["json_schema"]
        self.assertEqual(js["name"], "swarm_plan")
        self.assertIs(js["strict"], True)
        self.assertEqual(js["schema"], SCHEMA)


class HttpParseTests(unittest.TestCase):
    def test_plain(self) -> None:
        self.assertEqual(swarm_llm.parse_json('{"a": 1}'), {"a": 1})

    def test_fenced(self) -> None:
        self.assertEqual(swarm_llm.parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_surrounded_by_text(self) -> None:
        self.assertEqual(swarm_llm.parse_json('text {"a": 1} text'), {"a": 1})


class HttpErrorTests(unittest.TestCase):
    def _cfg(self):
        return swarm_llm.settings({"swarm": {"api_key": "k", "base_url": "https://x/v1"}})

    def test_401_masks_key_and_no_key_in_message(self) -> None:
        error = urllib.error.HTTPError(
            "https://x/v1/chat/completions", 401, "Unauthorized", {},
            io.BytesIO(json.dumps({"error": "invalid api key k"}).encode("utf-8")))
        opener = _Opener(error=error)
        with self.assertLogs("listik.swarm_llm", level="WARNING") as logs:
            with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
                swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(),
                                        opener=opener)
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("[swarm].api_key", ctx.exception.message)
        self.assertNotIn("k", ctx.exception.message.replace("[swarm].api_key", ""))
        joined = "\n".join(logs.output)
        self.assertIn("***", joined)
        self.assertNotIn("invalid api key k", joined)

    def test_500_is_502(self) -> None:
        error = urllib.error.HTTPError(
            "https://x/v1/chat/completions", 500, "err", {}, io.BytesIO(b""))
        opener = _Opener(error=error)
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(), opener=opener)
        self.assertEqual(ctx.exception.status, 502)

    def test_url_error_is_504(self) -> None:
        opener = _Opener(error=urllib.error.URLError("boom"))
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(), opener=opener)
        self.assertEqual(ctx.exception.status, 504)

    def test_timeout_is_504(self) -> None:
        opener = _Opener(error=TimeoutError("timed out"))
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(), opener=opener)
        self.assertEqual(ctx.exception.status, 504)

    def test_body_not_json_is_502(self) -> None:
        opener = _Opener(payload="не json")
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(), opener=opener)
        self.assertEqual(ctx.exception.status, 502)

    def test_missing_choices_is_502(self) -> None:
        opener = _Opener(payload={"error": "нет"})
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(), opener=opener)
        self.assertEqual(ctx.exception.status, 502)

    def test_content_not_object_list(self) -> None:
        opener = _Opener(payload=_reply("[1]"))
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(), opener=opener)
        self.assertEqual(ctx.exception.status, 502)

    def test_content_not_object_text(self) -> None:
        opener = _Opener(payload=_reply("нет"))
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(), opener=opener)
        self.assertEqual(ctx.exception.status, 502)


class HttpNoKeyTests(unittest.TestCase):
    def test_no_key_no_command_is_503_and_no_network(self) -> None:
        cfg_settings = swarm_llm.settings({})
        opener = _Opener()
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=cfg_settings,
                                    opener=opener)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(opener.requests, [])


# --------------------------------------------------------------------------- command


class CommandRequestTests(unittest.TestCase):
    def test_request_shape(self) -> None:
        cfg_settings = swarm_llm.settings({"swarm": {"command": ["python3", "fake.py"],
                                                       "model": "m"}})
        opener = _Opener()
        runner = _Runner()
        messages = [{"role": "user", "content": "hi"}]
        out = swarm_llm.complete_json(messages, SCHEMA, name="swarm_plan",
                                      cfg_settings=cfg_settings, opener=opener, runner=runner)
        self.assertEqual(out, {"b": 2})
        self.assertEqual(len(runner.calls), 1)
        argv, stdin_text, _timeout = runner.calls[0]
        self.assertEqual(argv, ["python3", "fake.py"])
        stdin = json.loads(stdin_text)
        self.assertEqual(stdin["name"], "swarm_plan")
        self.assertEqual(stdin["model"], "m")
        self.assertEqual(stdin["messages"], messages)
        self.assertEqual(stdin["schema"], SCHEMA)
        self.assertEqual(opener.requests, [])


class CommandErrorTests(unittest.TestCase):
    def _cfg(self):
        return swarm_llm.settings({"swarm": {"command": ["cmd"], "model": "m"}})

    def test_nonzero_exit_is_502_stderr_only_in_log(self) -> None:
        runner = _Runner(result=_Result(returncode=3, stdout="", stderr="secret trace"))
        with self.assertLogs("listik.swarm_llm", level="WARNING") as logs:
            with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
                swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(),
                                        runner=runner)
        self.assertEqual(ctx.exception.status, 502)
        self.assertNotIn("secret trace", ctx.exception.message)
        self.assertIn("secret trace", "\n".join(logs.output))

    def test_timeout_expired_is_504(self) -> None:
        runner = _Runner(error=subprocess.TimeoutExpired(cmd="cmd", timeout=1))
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(), runner=runner)
        self.assertEqual(ctx.exception.status, 504)

    def test_os_error_is_503(self) -> None:
        runner = _Runner(error=OSError("no such file"))
        with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
            swarm_llm.complete_json([], SCHEMA, name="n", cfg_settings=self._cfg(), runner=runner)
        self.assertEqual(ctx.exception.status, 503)


class CommandRealSubprocessTests(unittest.TestCase):
    def test_real_subprocess_roundtrip(self) -> None:
        script = ("import sys, json\n"
                  "d = json.load(sys.stdin)\n"
                  "print(json.dumps({'echo': d['name']}))\n")
        cfg_settings = {"command": [sys.executable, "-c", script], "model": "m"}
        out = swarm_llm.complete_json([], SCHEMA, name="swarm_plan", cfg_settings=cfg_settings)
        self.assertEqual(out, {"echo": "swarm_plan"})


# --------------------------------------------------------------------------- find_cycles


class FindCyclesTests(unittest.TestCase):
    def test_chain_no_cycle(self) -> None:
        # a <- b <- c: c ждёт b, b ждёт a.
        incoming = {"a": set(), "b": {"a"}, "c": {"b"}}
        self.assertEqual(deps.find_cycles(["a", "b", "c"], incoming), [])

    def test_two_cycle(self) -> None:
        incoming = {"a": {"b"}, "b": {"a"}}
        self.assertEqual(deps.find_cycles(["a", "b"], incoming), [["a", "b"]])

    def test_three_cycle_plus_isolated(self) -> None:
        incoming = {"a": {"c"}, "b": {"a"}, "c": {"b"}, "d": set()}
        self.assertEqual(deps.find_cycles(["a", "b", "c", "d"], incoming), [["a", "b", "c"]])

    def test_edge_to_unknown_id_ignored(self) -> None:
        incoming = {"a": {"ghost"}}
        self.assertEqual(deps.find_cycles(["a"], incoming), [])


# --------------------------------------------------------------------------- apply_planned_blocks


def _task(conn, title: str) -> str:
    return store.create_task(conn, title=title, project="demo", priority=2)["id"]


def _direct_edge(conn, issue_id: str, depends_on: str, dep_type: str, created_by: str) -> None:
    conn.execute(
        "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?)",
        (issue_id, depends_on, dep_type, created_by),
    )
    conn.commit()


def _deps_snapshot(conn):
    return conn.execute(
        "SELECT issue_id, depends_on, dep_type, created_by FROM deps ORDER BY 1,2,3").fetchall()


def _tasks_blocked(conn):
    return conn.execute("SELECT id, blocked_by FROM tasks ORDER BY id").fetchall()


def _tasks_shape(conn):
    return conn.execute(
        "SELECT id, status, holder, stage, launch_route, write_scope FROM tasks "
        "ORDER BY id").fetchall()


class ApplySetsTests(TempDbTestCase):
    def test_sets_edges(self) -> None:
        a, b, c = _task(self.conn, "A"), _task(self.conn, "B"), _task(self.conn, "C")
        out = deps.apply_planned_blocks(self.conn, working=[a, b, c], edges=[[a, b], [a, c]])
        self.assertEqual(out["added"], [[a, b], [a, c]])
        self.assertEqual(out["removed"], [])
        self.assertEqual(out["kept"], 0)
        self.assertEqual(out["covered"], [])
        rows = {(r["issue_id"], r["depends_on"], r["dep_type"], r["created_by"])
                for r in _deps_snapshot(self.conn)}
        self.assertIn((b, a, "blocks", deps.PLANNED_BLOCK_AUTHOR), rows)
        self.assertIn((c, a, "blocks", deps.PLANNED_BLOCK_AUTHOR), rows)

        b_row = self.conn.execute("SELECT blocked_by FROM tasks WHERE id=?", (b,)).fetchone()
        self.assertIn(a, json.loads(b_row["blocked_by"]))
        self.assertFalse(deps.ready(self.conn, b)["claimable"])
        with self.assertRaises(ValueError):
            store.claim(self.conn, b, holder="x")


class ApplyStaleRemovedTests(TempDbTestCase):
    def test_own_stale_edge_removed(self) -> None:
        a, b, c = _task(self.conn, "A"), _task(self.conn, "B"), _task(self.conn, "C")
        _direct_edge(self.conn, c, b, "blocks", deps.PLANNED_BLOCK_AUTHOR)
        out = deps.apply_planned_blocks(self.conn, working=[a, b, c], edges=[[a, b]])
        self.assertEqual(out["removed"], [[b, c]])
        c_row = self.conn.execute("SELECT blocked_by FROM tasks WHERE id=?", (c,)).fetchone()
        self.assertEqual(json.loads(c_row["blocked_by"]), [])


class ApplyForeignUntouchedTests(TempDbTestCase):
    def test_foreign_edges_survive(self) -> None:
        a, b, c, d = (_task(self.conn, x) for x in "ABCD")
        store.add_dep(self.conn, b, a, "blocks", created_by="ann")
        store.add_dep(self.conn, c, a, "waits-for", created_by="ann")
        _direct_edge(self.conn, c, b, "resource-blocks", deps.RESOURCE_BLOCK_AUTHOR)
        store.add_dep(self.conn, d, a, "blocks", created_by="agent:x")  # suggested-blocks

        before = _deps_snapshot(self.conn)
        out = deps.apply_planned_blocks(self.conn, working=[a, b, c, d], edges=[[a, b]])
        after = _deps_snapshot(self.conn)

        self.assertEqual(before, after)
        self.assertEqual(out["covered"], [[a, b]])
        self.assertEqual(out["added"], [])


class ApplyPromotionTests(TempDbTestCase):
    def test_suggested_is_promoted(self) -> None:
        a, b = _task(self.conn, "A"), _task(self.conn, "B")
        store.add_dep(self.conn, b, a, "blocks", created_by="agent:x")  # suggested-blocks
        out = deps.apply_planned_blocks(self.conn, working=[a, b], edges=[[a, b]])
        self.assertEqual(out["promoted"], 1)
        rows = {(r["issue_id"], r["depends_on"], r["dep_type"])
                for r in _deps_snapshot(self.conn)}
        self.assertNotIn((b, a, "suggested-blocks"), rows)
        self.assertIn((b, a, "blocks"), rows)


class ApplyIdempotenceTests(TempDbTestCase):
    def test_repeat_no_changes(self) -> None:
        a, b, c = _task(self.conn, "A"), _task(self.conn, "B"), _task(self.conn, "C")
        deps.apply_planned_blocks(self.conn, working=[a, b, c], edges=[[a, b], [a, c]])
        before = _deps_snapshot(self.conn)
        out2 = deps.apply_planned_blocks(self.conn, working=[a, b, c], edges=[[a, b], [a, c]])
        self.assertEqual(out2["added"], [])
        self.assertEqual(out2["removed"], [])
        self.assertEqual(out2["kept"], 2)
        self.assertEqual(_deps_snapshot(self.conn), before)


class ApplyCycleTests(TempDbTestCase):
    def test_cycle_rejected_nothing_written(self) -> None:
        a, b = _task(self.conn, "A"), _task(self.conn, "B")
        store.add_dep(self.conn, a, b, "blocks", created_by="ann")  # a ждёт b
        before_deps = _deps_snapshot(self.conn)
        before_tasks = _tasks_blocked(self.conn)
        with self.assertRaises(errors.ListikError) as ctx:
            deps.apply_planned_blocks(self.conn, working=[a, b], edges=[[a, b]])  # b ждёт a
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        self.assertIn(a, ctx.exception.message)
        self.assertIn(b, ctx.exception.message)
        self.assertEqual(_deps_snapshot(self.conn), before_deps)
        self.assertEqual(_tasks_blocked(self.conn), before_tasks)


class ApplyBadInputTests(TempDbTestCase):
    def test_self_loop_is_bad_argument(self) -> None:
        a = _task(self.conn, "A")
        with self.assertRaises(errors.BadArgument):
            deps.apply_planned_blocks(self.conn, working=[a], edges=[[a, a]])

    def test_edge_outside_working_is_bad_argument(self) -> None:
        a, b = _task(self.conn, "A"), _task(self.conn, "B")
        with self.assertRaises(errors.BadArgument):
            deps.apply_planned_blocks(self.conn, working=[a, b], edges=[[a, "x"]])

    def test_unknown_task_is_not_found(self) -> None:
        b = _task(self.conn, "B")
        with self.assertRaises(errors.NotFound):
            deps.apply_planned_blocks(self.conn, working=["nope", b], edges=[["nope", b]])

    def test_empty_is_zero_response_without_sql(self) -> None:
        out = deps.apply_planned_blocks(self.conn, working=[], edges=[])
        self.assertEqual(out, {"added": [], "removed": [], "kept": 0, "covered": [],
                               "promoted": 0})


class ApplyRollbackTests(TempDbTestCase):
    def test_rollback_on_error_leaves_deps_untouched(self) -> None:
        a, b, c = _task(self.conn, "A"), _task(self.conn, "B"), _task(self.conn, "C")
        before = _deps_snapshot(self.conn)

        # `refresh_task` кладут после INSERT'ов, до `commit` — сбой здесь должен
        # откатить уже выполненные (но не закоммиченные) INSERT'ы.
        with mock.patch.object(deps, "refresh_task", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                deps.apply_planned_blocks(self.conn, working=[a, b, c],
                                          edges=[[a, b], [a, c]])
        self.assertEqual(_deps_snapshot(self.conn), before)


class ApplyBoundaryTests(TempDbTestCase):
    def test_own_edge_to_task_outside_working_untouched(self) -> None:
        a, b, z = _task(self.conn, "A"), _task(self.conn, "B"), _task(self.conn, "Z")
        # своя строка у задачи z, которая не входит в working
        y = _task(self.conn, "Y")
        _direct_edge(self.conn, z, y, "blocks", deps.PLANNED_BLOCK_AUTHOR)
        # своя строка (b, z): z вне working, b в working
        _direct_edge(self.conn, b, z, "blocks", deps.PLANNED_BLOCK_AUTHOR)

        out = deps.apply_planned_blocks(self.conn, working=[a, b], edges=[[a, b]])

        rows = {(r["issue_id"], r["depends_on"], r["dep_type"], r["created_by"])
                for r in _deps_snapshot(self.conn)}
        self.assertIn((z, y, "blocks", deps.PLANNED_BLOCK_AUTHOR), rows)
        self.assertIn((b, z, "blocks", deps.PLANNED_BLOCK_AUTHOR), rows)
        self.assertNotIn([b, z], out["removed"])


class ApplyDoesNotTouchCardsTests(TempDbTestCase):
    def test_task_shape_unchanged(self) -> None:
        a, b, c = _task(self.conn, "A"), _task(self.conn, "B"), _task(self.conn, "C")
        before = _tasks_shape(self.conn)
        deps.apply_planned_blocks(self.conn, working=[a, b, c], edges=[[a, b], [a, c]])
        after = _tasks_shape(self.conn)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
