"""Проверка слияния jev: снимки diff3, CLI и HTTP без внешней сети."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import runpy
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from listik import client, db as db_mod, errors, store, swarm_llm, util
from tests.helpers import TempDbTestCase
from tests.test_owner_http import AUTH, LOCAL_CONFIG, OwnerHttpCase

LISTIK_BIN = Path(__file__).resolve().parent.parent / "bin" / "listik"
BEFORE = "head\n<<<<<<< HEAD\nmain\n||||||| base\nbase\n=======\ntask\n>>>>>>> t1\ntail\n"
AFTER = "head\nmain\ntask\ntail\n"
CFG = {"jev_api_key": "test-jev-secret", "jev_url": "https://jev.invalid/decisions",
       "jev_model": "test-jev-model"}
DISABLED = {**CFG, "jev_api_key": ""}


def payload():
    return {"task": {"id": "t1"},
            "files": [{"path": "a.txt", "before": BEFORE, "after": AFTER}]}


def reply(task_kept=0.9, main_kept=0.8, clean=1):
    return {"answers": {name: {"type": "noul", "noul": p} for name, p in
                        (("task_kept", task_kept), ("main_kept", main_kept), ("clean", clean))},
            "usage": None, "model": "provider-version"}


def snapshot(conn):
    return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("tasks", "deps", "comments", "events")}


def seed(conn):
    a = store.create_task(conn, title="A", project="demo")["id"]
    b = store.create_task(conn, title="B", project="demo")["id"]
    store.add_dep(conn, b, a, dep_type="relates-to")
    store.add_comment(conn, a, "existing journal", kind="journal")


class ExtractTests(unittest.TestCase):
    def test_diff3_and_context_limit(self):
        hunks = swarm_llm.extract_conflicts("a\nb\nc\n" + BEFORE + "x\ny\nz\n")
        self.assertEqual(hunks, [{"main": "main", "base": "base", "task": "task",
                                 "ctx_before": ["b", "c", "head"],
                                 "ctx_after": ["tail", "x", "y"]}])

    def test_no_base(self):
        hunks = swarm_llm.extract_conflicts(BEFORE.replace("||||||| base\nbase\n", ""))
        self.assertIsNone(hunks[0]["base"])
        self.assertEqual(hunks[0]["main"], "main")
        self.assertEqual(hunks[0]["task"], "task")

    def test_two_blocks_do_not_enter_each_others_context(self):
        hunks = swarm_llm.extract_conflicts(BEFORE + BEFORE)
        self.assertEqual(len(hunks), 2)
        self.assertEqual(hunks[0]["ctx_after"], ["tail", "head"])
        self.assertEqual(hunks[1]["ctx_before"], ["tail", "head"])

    def test_adjacent_blocks_have_empty_shared_context(self):
        block = "<<<<<<< HEAD\nm\n=======\nt\n>>>>>>> t1\n"
        hunks = swarm_llm.extract_conflicts(block + block)
        self.assertEqual(hunks[0]["ctx_after"], [])
        self.assertEqual(hunks[1]["ctx_before"], [])

    def test_strict_markers_are_content(self):
        ordinary = "a ======= b\n========\n<<<<<<<x\n======= \n|||||||x\n>>>>>>>x"
        self.assertEqual(swarm_llm.extract_conflicts(ordinary), [])
        hunks = swarm_llm.extract_conflicts(
            f"<<<<<<< HEAD\n{ordinary}\n=======\n{ordinary}\n>>>>>>> t1")
        self.assertEqual(hunks[0]["main"], ordinary)
        self.assertEqual(hunks[0]["task"], ordinary)

    def test_no_markers(self):
        self.assertEqual(swarm_llm.extract_conflicts("plain\ntext\n"), [])
        self.assertEqual(swarm_llm.extract_conflicts(""), [])

    def test_malformed_markers_report_line(self):
        for text, line in (("head\n<<<<<<< HEAD\nmain", 2),
                           ("head\n=======", 2), ("head\n>>>>>>> t1", 2),
                           ("<<<<<<< HEAD\n<<<<<<< other", 2),
                           ("<<<<<<< HEAD\n>>>>>>> t1", 2),
                           ("<<<<<<< HEAD\n=======\n=======", 3),
                           ("<<<<<<< HEAD\n=======\n||||||| base", 3)):
            with self.subTest(text=text), self.assertRaisesRegex(
                    errors.BadArgument, f"^маркеры конфликта не разобраны: строка {line}$"):
                swarm_llm.extract_conflicts(text)


class RegionTests(unittest.TestCase):
    def test_between_anchors(self):
        self.assertEqual(swarm_llm.resolved_regions(AFTER, swarm_llm.extract_conflicts(BEFORE)),
                         ["main\ntask"])

    def test_file_boundaries(self):
        for left, right, expected in (([], ["tail"], "head\nmain\ntask"),
                                      (["head"], [], "main\ntask\ntail"),
                                      ([], [], "head\nmain\ntask\ntail")):
            with self.subTest(left=left, right=right):
                self.assertEqual(swarm_llm.resolved_regions(
                    AFTER, [{"ctx_before": left, "ctx_after": right}]), [expected])

    def test_missing_anchor_does_not_move_cursor(self):
        for left, right in ((["missing"], ["tail"]), (["tail"], ["missing"])):
            with self.subTest(left=left):
                hunks = [{"ctx_before": left, "ctx_after": right},
                         {"ctx_before": ["head"], "ctx_after": ["tail"]}]
                self.assertEqual(swarm_llm.resolved_regions(AFTER, hunks), [None, "main\ntask"])

    def test_repeated_anchors_use_first_after_cursor(self):
        hunk = {"ctx_before": ["left"], "ctx_after": ["right"]}
        after = "left\nfirst\nright\nleft\nsecond\nright\nleft\nthird\nright"
        self.assertEqual(swarm_llm.resolved_regions(after, [hunk, hunk]), ["first", "second"])

    def test_overlapping_anchors_between_nearby_blocks(self):
        self.assertEqual(swarm_llm.resolved_regions(
            AFTER + AFTER, swarm_llm.extract_conflicts(BEFORE + BEFORE)),
            ["main\ntask", "main\ntask"])

    def test_anchor_is_whole_sequence_and_empty_resolution_is_valid(self):
        hunks = [{"ctx_before": ["a", "b"], "ctx_after": ["c", "d"]}]
        self.assertEqual(swarm_llm.resolved_regions("a\nx\nb\na\nb\nc\nd", hunks), [""])


class StateTests(unittest.TestCase):
    def test_normal_state(self):
        state, reason = swarm_llm.merge_state("a.txt", BEFORE, AFTER)
        self.assertIsNone(reason)
        self.assertEqual(state, {"path": "a.txt", "hunks": [
            {"main": "main", "base": "base", "task": "task", "resolved": "main\ntask"}]})

    def test_missing_anchor_falls_back_at_size_limit(self):
        for size in (1, 40_000):
            with self.subTest(size=size):
                after = "x" * size
                state, reason = swarm_llm.merge_state("a.txt", BEFORE, after)
                self.assertIsNone(reason)
                self.assertEqual(state["resolved_file"], after)
                self.assertIsNone(state["hunks"][0]["resolved"])

    def test_missing_anchor_large_file_is_skipped(self):
        self.assertEqual(swarm_llm.merge_state("a.txt", BEFORE, "x" * 40_001),
                         (None, "не удалось сопоставить куски, файл велик"))

    def test_no_markers_and_malformed_markers_are_skipped(self):
        self.assertEqual(swarm_llm.merge_state("a.txt", "plain", AFTER),
                         (None, "в «до» нет маркеров конфликта"))
        self.assertEqual(swarm_llm.merge_state("a.txt", "<<<<<<< HEAD", AFTER),
                         (None, "маркеры конфликта не разобраны: строка 1"))

    def test_large_serialized_state_is_skipped(self):
        before = BEFORE.replace("\nmain\n", "\n" + "м" * 100_001 + "\n")
        self.assertEqual(swarm_llm.merge_state("a.txt", before, AFTER),
                         (None, "слишком большой для jev"))

    def test_serialized_limit_is_inclusive(self):
        state, _ = swarm_llm.merge_state("a.txt", BEFORE, AFTER)
        size = len(util.json_dumps(state))
        with mock.patch.object(swarm_llm, "MERGE_MAX_STATE_CHARS", size):
            self.assertEqual(swarm_llm.merge_state("a.txt", BEFORE, AFTER), (state, None))
        with mock.patch.object(swarm_llm, "MERGE_MAX_STATE_CHARS", size - 1):
            self.assertEqual(swarm_llm.merge_state("a.txt", BEFORE, AFTER),
                             (None, "слишком большой для jev"))


class JudgeTests(unittest.TestCase):
    def test_ok_and_inclusive_threshold(self):
        for values in ((0.9, 0.8, 1), (0.7, 0.7, 0.7)):
            with self.subTest(values=values), mock.patch.object(
                    swarm_llm, "decide", return_value=reply(*values)):
                out = swarm_llm.judge_merge(payload(), cfg_settings=CFG)
            self.assertTrue(out["ok"])
            self.assertEqual(out["model"], CFG["jev_model"])
            self.assertIsNone(out["skipped"])
            self.assertEqual(out["files"], [{"path": "a.txt", "verdict": "ok",
                                           "answers": dict(zip(
                                               ("task_kept", "main_kept", "clean"), values))}])

    def test_reject_reason_contains_only_failed_questions(self):
        for values, reason in (((0.31, 0.8, 1), "task_kept=0.31"),
                               ((0.31, 0.7, 0.55), "task_kept=0.31, clean=0.55")):
            with self.subTest(values=values), mock.patch.object(
                    swarm_llm, "decide", return_value=reply(*values)):
                out = swarm_llm.judge_merge(payload(), cfg_settings=CFG)
            self.assertFalse(out["ok"])
            self.assertEqual(out["files"][0]["verdict"], "reject")
            self.assertEqual(out["files"][0]["reason"], reason)

    def test_decide_receives_cards_hunks_questions_and_options(self):
        data = payload()
        data["task"].update(title="Task", description="private detail", acceptance="а" * 1600)
        data["others"] = [{"id": "m2", "title": "Main", "description": "unused",
                           "acceptance": "б" * 1600}, {"id": "m1"}]
        original = copy.deepcopy(data)
        opener = object()
        with mock.patch.object(swarm_llm, "decide", return_value=reply()) as decide:
            swarm_llm.judge_merge(data, cfg_settings=CFG, opener=opener, timeout=17)
        args = decide.call_args.kwargs
        state = args["state"]
        self.assertEqual(state["task_card"],
                         {"id": "t1", "title": "Task", "acceptance": "а" * 1500 + "…"})
        self.assertEqual(state["main_cards"], [
            {"id": "m2", "title": "Main", "acceptance": "б" * 1500 + "…"},
            {"id": "m1", "title": "", "acceptance": ""}])
        self.assertEqual(state["path"], "a.txt")
        self.assertEqual(state["hunks"], [{"main": "main", "base": "base", "task": "task",
                                          "resolved": "main\ntask"}])
        self.assertEqual(set(args["questions"]), {"task_kept", "main_kept", "clean"})
        for question in args["questions"].values():
            self.assertEqual(question["type"], "noul")
            self.assertTrue(question["instructions"])
            self.assertEqual(set(question["criteria"]), {"true", "false"})
        self.assertIs(args["cfg_settings"], CFG)
        self.assertIs(args["opener"], opener)
        self.assertEqual(args["timeout"], 17)
        self.assertEqual(data, original)

    def test_second_file_skipped_and_first_verdict_preserved(self):
        data = payload()
        data["files"].append({"path": "b.txt", "before": "plain", "after": "plain"})
        for probability in (0.9, 0.1):
            with self.subTest(probability=probability), mock.patch.object(
                    swarm_llm, "decide", return_value=reply(probability)) as decide:
                out = swarm_llm.judge_merge(data, cfg_settings=CFG)
            self.assertEqual(out["ok"], probability >= 0.7)
            self.assertEqual(out["files"][1], {"path": "b.txt", "verdict": "skipped",
                                              "reason": "в «до» нет маркеров конфликта",
                                              "answers": {}})
            decide.assert_called_once()

    def test_error_stops_processing_without_losing_reject(self):
        data = payload()
        data["files"] *= 3
        with mock.patch.object(swarm_llm, "decide", side_effect=[
                reply(0.31), swarm_llm.SwarmLlmError("unavailable")]) as decide, \
                self.assertLogs("listik.swarm_llm", level="WARNING"):
            out = swarm_llm.judge_merge(data, cfg_settings=CFG)
        self.assertFalse(out["ok"])
        self.assertEqual(out["skipped"], "ошибка: unavailable")
        self.assertEqual(len(out["files"]), 1)
        self.assertEqual(decide.call_count, 2)

    def test_error_on_first_file_is_skip_not_reject(self):
        with mock.patch.object(swarm_llm, "decide", side_effect=swarm_llm.SwarmLlmError("timeout")), \
                self.assertLogs("listik.swarm_llm", level="WARNING"):
            out = swarm_llm.judge_merge(payload(), cfg_settings=CFG)
        self.assertEqual(out, {"ok": True, "model": CFG["jev_model"],
                               "skipped": "ошибка: timeout", "files": []})

    def test_disabled_and_settings_fallback(self):
        with mock.patch.object(swarm_llm, "settings", return_value=DISABLED) as settings, \
                mock.patch.object(swarm_llm, "decide") as decide, \
                mock.patch.object(swarm_llm, "jev_enabled", wraps=swarm_llm.jev_enabled) as enabled:
            out = swarm_llm.judge_merge(payload())
        self.assertEqual(out, {"ok": True, "model": None, "skipped": "не настроен", "files": []})
        settings.assert_called_once_with()
        enabled.assert_called_once_with(DISABLED)
        decide.assert_not_called()

    def test_rejected_key_never_leaks(self):
        for status in (401, 403):
            error = urllib.error.HTTPError(CFG["jev_url"], status, "no", {},
                                           io.BytesIO(CFG["jev_api_key"].encode()))
            with self.subTest(status=status), self.assertLogs("listik.swarm_llm") as logs:
                out = swarm_llm.judge_merge(payload(), cfg_settings=CFG,
                                           opener=mock.Mock(side_effect=error))
            self.assertTrue(out["ok"])
            self.assertIn(f"HTTP {status}", out["skipped"])
            self.assertNotIn(CFG["jev_api_key"], json.dumps(out) + " ".join(logs.output))

    def test_input_validation_including_when_disabled(self):
        invalid = [(None, "payload"), ([], "payload"), ({}, "task"),
                   ({**payload(), "task": []}, "task"),
                   ({"task": {"id": "t1"}}, "files")]
        for value in (None, {}, [], "files"):
            invalid.append(({**payload(), "files": value}, "files"))
        for value in (None, {}, "others"):
            invalid.append(({**payload(), "others": value}, "others"))
        invalid.append(({**payload(), "others": [None]}, "others[0]"))
        invalid.append(({**payload(), "others": [{"id": "m1"}, {}]}, "others[1].id"))
        for card_field in ("task", "others[0]"):
            for field in ("id", "title", "description", "acceptance"):
                for value in (None, 1, [], {}):
                    data = payload()
                    card = {"id": "id", field: value}
                    if card_field == "task":
                        data["task"] = card
                    else:
                        data["others"] = [card]
                    invalid.append((data, f"{card_field}.{field}"))
            for card in ({}, {"id": ""}, {"id": "  "}):
                data = payload()
                if card_field == "task":
                    data["task"] = card
                else:
                    data["others"] = [card]
                invalid.append((data, f"{card_field}.id"))
        invalid.append(({**payload(), "files": [None]}, "files[0]"))
        for field in ("path", "before", "after"):
            for value in (None, 1, {}, []):
                data = payload()
                data["files"][0][field] = value
                invalid.append((data, f"files[0].{field}"))
            data = payload()
            del data["files"][0][field]
            invalid.append((data, f"files[0].{field}"))
        with mock.patch.object(swarm_llm, "decide") as decide:
            for data, field in invalid:
                with self.subTest(field=field, data=data), self.assertRaises(errors.BadArgument) as exc:
                    swarm_llm.judge_merge(data, cfg_settings=DISABLED)
                self.assertIn(field, str(exc.exception))
        decide.assert_not_called()


class CliTests(TempDbTestCase):
    def setUp(self):
        super().setUp()
        self.input_path = self.tmp_path / "input.json"
        self.input_path.write_text(json.dumps(payload()), encoding="utf-8")
        self.config_path = self.tmp_path / "config.toml"
        self.config_path.write_text("", encoding="utf-8")

    def _run(self, *args, stdin=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("LISTIK_")}
        env.update(LISTIK_HOME=str(self.tmp_path), LISTIK_DB=str(self.db_path),
                   LISTIK_CONFIG=str(self.config_path), LISTIK_SWARM_JEV_API_KEY="")
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", "arbiter-check", *args],
                              input=stdin, capture_output=True, text=True, env=env,
                              cwd=LISTIK_BIN.parent.parent)

    def test_file_and_stdin_json(self):
        for path, stdin in ((str(self.input_path), None), ("-", json.dumps(payload()))):
            with self.subTest(path=path):
                result = self._run("--input", path, "--json", stdin=stdin)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout),
                                 {"ok": True, "model": None, "skipped": "не настроен", "files": []})

    def test_text(self):
        result = self._run("--input", str(self.input_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         "проверка слияния t1: пропущена (не настроен), модель None")

    def test_bad_json_and_non_object(self):
        for text in ("{", "[]", "null", '"text"'):
            with self.subTest(text=text):
                result = self._run("--input", "-", "--json", stdin=text)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], "bad_argument")

    def test_input_required_and_help_documents_format(self):
        result = self._run("--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "bad_argument")
        result = self._run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("docs/API.md", result.stdout)
        self.assertIn("арбитра слияния", result.stdout)

    def test_cli_leaves_database_unchanged(self):
        seed(self.conn)
        before = snapshot(self.conn)
        result = self._run("--input", str(self.input_path), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot(self.conn), before)

    def test_local_dispatch_does_not_open_database(self):
        with mock.patch.object(swarm_llm, "settings", return_value=DISABLED), \
                mock.patch("listik.client.db_mod.init") as init:
            out = client.local_call("swarm_arbiter_check", payload=payload())
        self.assertTrue(out["ok"])
        init.assert_not_called()

    def test_not_a_write_op_and_call_contract(self):
        cli = runpy.run_path(str(LISTIK_BIN))
        self.assertNotIn("swarm_arbiter_check", cli["WRITE_OPS"])
        cmd = cli["cmd_arbiter_check"]
        args = cli["build_parser"]().parse_args(
            ["--local", "arbiter-check", "--input", str(self.input_path), "--json"])
        out = {"ok": True, "model": None, "skipped": "не настроен", "files": []}
        call = mock.Mock(return_value=out)
        with mock.patch.dict(cmd.__globals__, {"call": call}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cmd(args), 0)
        call.assert_called_once_with("swarm_arbiter_check", args, "/api/swarm/arbiter-check",
                                     method="POST", body=payload(),
                                     local_kwargs={"payload": payload()}, timeout=600)

    def test_cli_ok_reject_and_unavailable_exit_codes(self):
        main = runpy.run_path(str(LISTIK_BIN))["main"]
        for response, code, label in ((reply(), 0, "ok"),
                                      (reply(0.31), 1, "reject"),
                                      (swarm_llm.SwarmLlmError("timeout"), 0,
                                       "пропущена (ошибка: timeout)")):
            for json_mode in (False, True):
                kwargs = {"side_effect": response} if isinstance(response, Exception) else {
                    "return_value": response}
                stdout = io.StringIO()
                argv = ["--local", "arbiter-check", "--input", str(self.input_path)]
                if json_mode:
                    argv.append("--json")
                with self.subTest(code=code, json=json_mode), \
                        mock.patch.object(swarm_llm, "settings", return_value=CFG), \
                        mock.patch.object(swarm_llm, "decide", **kwargs), \
                        mock.patch.object(swarm_llm.logger, "warning"), \
                        contextlib.redirect_stdout(stdout):
                    self.assertEqual(main(argv), code)
                text = stdout.getvalue()
                self.assertNotIn(CFG["jev_api_key"], text)
                if json_mode:
                    self.assertEqual(json.loads(text)["ok"], code == 0)
                else:
                    self.assertIn(f"проверка слияния t1: {label}, модель {CFG['jev_model']}", text)
                    if code:
                        self.assertIn("  a.txt: reject — task_kept=0.31", text)


class HttpTests(OwnerHttpCase):
    config_text = LOCAL_CONFIG

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {swarm_llm.ENV_JEV_API_KEY: "",
                                          swarm_llm.ENV_JEV_MODEL: ""})
        env.start()
        self.addCleanup(env.stop)

    def test_disabled(self):
        with mock.patch("listik.server.swarm_llm.decide") as decide:
            status, _, out = self.call("POST", "/api/swarm/arbiter-check", AUTH, payload())
        self.assertEqual(status, 200, out)
        self.assertEqual(out["data"],
                         {"ok": True, "model": None, "skipped": "не настроен", "files": []})
        decide.assert_not_called()

    def test_enabled_no_writes_or_publish(self):
        conn = db_mod.init(self.db_path)
        seed(conn)
        before = snapshot(conn)
        with mock.patch.dict(os.environ, {swarm_llm.ENV_JEV_API_KEY: CFG["jev_api_key"]}), \
                mock.patch("listik.server.swarm_llm.decide", return_value=reply()), \
                mock.patch("listik.server.publish") as publish, \
                mock.patch("listik.server.get_conn") as get_conn:
            status, _, out = self.call("POST", "/api/swarm/arbiter-check", AUTH, payload())
        self.assertEqual(status, 200, out)
        self.assertEqual(out["data"]["files"][0]["verdict"], "ok")
        self.assertEqual(snapshot(conn), before)
        publish.assert_not_called()
        get_conn.assert_not_called()
        self.assertNotIn(CFG["jev_api_key"], json.dumps(out))

    def test_bad_payload(self):
        status, _, out = self.call("POST", "/api/swarm/arbiter-check", AUTH,
                                   {**payload(), "files": []})
        self.assertEqual(status, 400)
        self.assertEqual(out["code"], "bad_argument")
        self.assertIn("files", out["error"])

    def test_method_and_auth(self):
        status, _, _ = self.call("GET", "/api/swarm/arbiter-check", AUTH)
        self.assertEqual(status, 405)
        status, _, _ = self.call("POST", "/api/swarm/arbiter-check", {}, payload())
        self.assertEqual(status, 401)

    def test_defensive_swarm_error_status(self):
        with mock.patch("listik.server.swarm_llm.judge_merge",
                        side_effect=swarm_llm.SwarmLlmError("timeout", status=504)):
            status, _, out = self.call("POST", "/api/swarm/arbiter-check", AUTH, payload())
        self.assertEqual(status, 504)
        self.assertEqual(out["code"], "server_error")


if __name__ == "__main__":
    unittest.main()
