"""Скрипт second-opinion: потоковый запрос, разбор SSE, понятные сетевые ошибки.

Провайдер — локальный ThreadingHTTPServer, сетевые отказы curl — поддельный curl
первым в PATH. Живых запросов наружу нет.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
CONSULT_SH = REPO_DIR / "plugins" / "second-opinion" / "skills" / "ask" / "scripts" / "consult.sh"

KEY_VALUE = "mock-key-value-7f3a91"

FAKE_CURL = """#!{python}
import json, os, re, stat, sys, time
argv = sys.argv[1:]
with open(os.environ["FAKE_CURL_ARGV"], "w") as f:
    json.dump(argv, f)
if "-K" in argv:
    cfg = argv[argv.index("-K") + 1]
    paths = {{"config": cfg}}
    with open(cfg) as f:
        for line in f:
            m = re.match(r'^data-binary = "@(.*)"$', line.rstrip("\\n"))
            if m:
                paths["body"] = m.group(1)
            m = re.match(r'^output = "(.*)"$', line.rstrip("\\n"))
            if m:
                paths["output"] = m.group(1)
    modes = {{k: stat.S_IMODE(os.stat(p).st_mode) for k, p in paths.items()}}
    with open(os.environ["FAKE_CURL_MODES"], "w") as f:
        json.dump(modes, f)
time.sleep(float(os.environ.get("FAKE_CURL_SLEEP", "0")))
sys.stderr.write("curl: (16) Error in the HTTP2 framing layer\\n")
sys.exit(int(os.environ["FAKE_CURL_RC"]))
"""


def chunk(content=None, reasoning=None, finish=None, usage="absent"):
    delta = {"role": "assistant"}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    choice = {"index": 0, "delta": delta}
    if finish is not None:
        choice["finish_reason"] = finish
    obj = {"id": "x", "object": "chat.completion.chunk", "model": "mock-model", "choices": [choice]}
    if usage != "absent":
        obj["usage"] = usage
    return obj


def data(obj, space=True):
    payload = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return ("data: " if space else "data:") + payload + "\n\n"


USAGE = {"prompt_tokens": 8042, "completion_tokens": 34376, "total_tokens": 42418}


def t1_body(usage_mid="absent", final_usage=USAGE):
    parts = [
        data(chunk(reasoning="СЕКРЕТНОЕ-РАССУЖДЕНИЕ-1", usage=usage_mid)),
        ": keep-alive\n\n",
        data(chunk(reasoning="СЕКРЕТНОЕ-РАССУЖДЕНИЕ-2", usage=usage_mid)),
        data(chunk(content="Hello", usage=usage_mid)),
        ": keep-alive\n\n",
        data(chunk(content=", world", usage=usage_mid)),
        data(chunk(content="", finish="stop", usage=final_usage)),
        data("[DONE]"),
    ]
    return "".join(parts)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            self.server.requests.append(json.loads(raw))
        except ValueError:
            self.server.requests.append(raw)
        try:
            self.server.scenario(self)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


def respond(handler, body, status=200, ctype="text/event-stream"):
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))
    handler.wfile.flush()


@unittest.skipUnless(all(shutil.which(b) for b in ("bash", "curl", "jq")), "нужны bash, curl и jq в PATH")
class ConsultStreamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="so-consult-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.requests = []
        server.stop = threading.Event()
        server.scenario = lambda h: respond(h, t1_body())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.server = server

        def _stop():
            server.stop.set()
            server.shutdown()
            server.server_close()

        self.addCleanup(_stop)
        port = server.server_address[1]
        script = self.tmp / CONSULT_SH.name
        shutil.copy(CONSULT_SH, script)
        (self.tmp / "providers.conf").write_text(
            f"mock|http://127.0.0.1:{port}/v1|MOCK_SO_KEY|mock-model\n", encoding="utf-8")
        self.script = script
        self.log = self.tmp / "usage.jsonl"

    def run_script(self, *args, prompt="Проверь гипотезу.", env=None, fake_curl=False):
        path = os.environ.get("PATH", "")
        if fake_curl:
            bindir = self.tmp / "fakebin"
            bindir.mkdir(exist_ok=True)
            fake = bindir / "curl"
            fake.write_text(FAKE_CURL.format(python=sys.executable), encoding="utf-8")
            fake.chmod(0o755)
            for name in ("argv.json", "modes.json"):
                (self.tmp / name).unlink(missing_ok=True)
            path = f"{bindir}{os.pathsep}{path}"
        full_env = {
            "PATH": path,
            "HOME": str(self.tmp),
            "LANG": os.environ.get("LANG") or "en_US.UTF-8",
            "MOCK_SO_KEY": KEY_VALUE,
            "FAKE_CURL_ARGV": str(self.tmp / "argv.json"),
            "FAKE_CURL_MODES": str(self.tmp / "modes.json"),
        }
        if os.environ.get("TMPDIR"):
            full_env["TMPDIR"] = os.environ["TMPDIR"]
        full_env.update(env or {})
        started = time.monotonic()
        proc = subprocess.run(["bash", str(self.script), "mock", *args], input=prompt,
                              capture_output=True, text=True, cwd=self.tmp, env=full_env, timeout=90)
        proc.elapsed = time.monotonic() - started
        return proc

    def log_lines(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    # T1
    def test_stream_concatenates_content(self):
        proc = self.run_script("--no-system")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "Hello, world\n")
        self.assertNotIn("РАССУЖДЕНИЕ", proc.stdout)
        req = self.server.requests[0]
        self.assertIs(req["stream"], True)
        self.assertIs(req["stream_options"]["include_usage"], True)
        self.assertEqual(req["max_tokens"], 65536)
        self.assertEqual([m["role"] for m in req["messages"]], ["user"])

    # T2
    def test_stream_with_system_prompt(self):
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        req = self.server.requests[0]
        self.assertEqual(len(req["messages"]), 2)
        self.assertEqual(req["messages"][0]["role"], "system")
        self.assertIs(req["stream"], True)

    # T3
    def test_log_tokens_from_last_non_null_usage(self):
        self.server.scenario = lambda h: respond(h, t1_body(usage_mid=None))
        proc = self.run_script("--no-system", "--log", str(self.log))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = self.log_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["status"], "ok")
        self.assertEqual(lines[0]["provider"], "mock")
        self.assertEqual(lines[0]["tokens_in"], USAGE["prompt_tokens"])
        self.assertEqual(lines[0]["tokens_out"], USAGE["completion_tokens"])

    # T4
    def test_error_chunk_mid_stream(self):
        body = data(chunk(content="частичный текст")) + data({"error": {"message": "boom-upstream"}})
        self.server.scenario = lambda h: respond(h, body)
        proc = self.run_script("--no-system")
        self.assertEqual(proc.returncode, 6)
        self.assertIn("boom-upstream", proc.stderr)
        self.assertEqual(proc.stdout, "")

    # T5
    def test_truncated_stream(self):
        body = data(chunk(content="раз ")) + data(chunk(content="два"))
        self.server.scenario = lambda h: respond(h, body)
        proc = self.run_script("--no-system", "--log", str(self.log))
        self.assertEqual(proc.returncode, 6)
        self.assertIn("поток оборвался", proc.stderr)
        self.assertIn("другого провайдера", proc.stderr)
        self.assertRegex(proc.stderr, r"через \d+ с")
        self.assertEqual(proc.stdout, "")
        self.assertEqual(self.log_lines()[0]["status"], "error")

    # T6
    def test_non_stream_json_still_parsed(self):
        body = json.dumps({"object": "chat.completion", "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "обычный ответ"}, "finish_reason": "stop"}]})
        self.server.scenario = lambda h: respond(h, body, ctype="application/json")
        proc = self.run_script("--no-system")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "обычный ответ\n")

    # T7
    def test_http_429(self):
        body = json.dumps({"error": {"message": "rate limited"}})
        self.server.scenario = lambda h: respond(h, body, status=429, ctype="application/json")
        proc = self.run_script("--no-system")
        self.assertEqual(proc.returncode, 5)

    # T8
    def test_connection_dropped_codes(self):
        for code in (16, 18, 52, 55, 56, 92):
            with self.subTest(code=code):
                proc = self.run_script("--no-system", fake_curl=True, env={"FAKE_CURL_RC": str(code)})
                self.assertEqual(proc.returncode, 6)
                self.assertIn("соединение оборвано", proc.stderr)
                self.assertIn(f"curl exit {code}", proc.stderr)
                self.assertIn("другого провайдера", proc.stderr)
                self.assertRegex(proc.stderr, r"через \d+ с")
                self.assertNotIn("base_url", proc.stderr)
                self.assertEqual(proc.stdout, "")
                argv = json.loads((self.tmp / "argv.json").read_text(encoding="utf-8"))
                self.assertNotIn(KEY_VALUE, " ".join(argv))
                modes = json.loads((self.tmp / "modes.json").read_text(encoding="utf-8"))
                self.assertEqual(sorted(modes), ["body", "config", "output"])
                for name, mode in modes.items():
                    self.assertEqual(mode, 0o600, name)

    # T9
    def test_curl_timeout(self):
        proc = self.run_script("--no-system", fake_curl=True, env={"FAKE_CURL_RC": "28"})
        self.assertEqual(proc.returncode, 6)
        self.assertIn("таймаут", proc.stderr)
        self.assertIn("curl exit 28", proc.stderr)
        self.assertIn("SECOND_OPINION_IDLE_TIME", proc.stderr)
        self.assertIn("SECOND_OPINION_MAX_TIME", proc.stderr)
        self.assertEqual(proc.stdout, "")

    # T10
    def test_cannot_connect(self):
        for code in (6, 7):
            with self.subTest(code=code):
                proc = self.run_script("--no-system", fake_curl=True, env={"FAKE_CURL_RC": str(code)})
                self.assertEqual(proc.returncode, 6)
                self.assertIn("не удалось соединиться", proc.stderr)
                self.assertIn(f"curl exit {code}", proc.stderr)
                self.assertIn("base_url", proc.stderr)
                self.assertRegex(proc.stderr, r"через \d+ с")
                self.assertEqual(proc.stdout, "")

    # T11
    def test_idle_stream_is_cut(self):
        def scenario(h):
            h.send_response(200)
            h.send_header("Content-Type", "text/event-stream")
            h.end_headers()
            h.wfile.write(data(chunk(content="начало")).encode("utf-8"))
            h.wfile.flush()
            h.server.stop.wait(30)

        self.server.scenario = scenario
        proc = self.run_script("--no-system", env={"SECOND_OPINION_IDLE_TIME": "2"})
        self.assertEqual(proc.returncode, 6)
        self.assertIn("curl exit 28", proc.stderr)
        self.assertLess(proc.elapsed, 20)
        self.assertEqual(proc.stdout, "")

    # T12
    def test_bad_timeout_values(self):
        for name, value in (("SECOND_OPINION_IDLE_TIME", "abc"), ("SECOND_OPINION_MAX_TIME", "0")):
            with self.subTest(name=name):
                proc = self.run_script("--no-system", env={name: value})
                self.assertEqual(proc.returncode, 2)
                self.assertIn(name, proc.stderr)
                self.assertEqual(self.server.requests, [])

    # T13
    def test_large_stream_is_fast(self):
        body = "".join(data(chunk(content="x")) for _ in range(20000))
        body += data(chunk(content="", finish="stop", usage=USAGE)) + data("[DONE]")
        self.server.scenario = lambda h: respond(h, body)
        proc = self.run_script("--no-system")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout), 20000 + 1)
        self.assertLess(proc.elapsed, 20)

    # T14
    def test_secret_guard(self):
        proc = self.run_script("--no-system", prompt="ключ sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(self.server.requests, [])

    # T15
    def test_broken_chunk(self):
        body = (data(chunk(content="текст")) + "data: {oops\n\n"
                + data(chunk(content="", finish="stop")) + data("[DONE]"))
        self.server.scenario = lambda h: respond(h, body)
        proc = self.run_script("--no-system")
        self.assertEqual(proc.returncode, 6)
        self.assertIn("не похож на JSON", proc.stderr)
        self.assertIn("{oops", proc.stderr)
        self.assertEqual(proc.stdout, "")

    # T16
    def test_other_curl_error(self):
        proc = self.run_script("--no-system", fake_curl=True, env={"FAKE_CURL_RC": "35"})
        self.assertEqual(proc.returncode, 6)
        self.assertIn("сетевая ошибка", proc.stderr)
        self.assertIn("curl exit 35", proc.stderr)
        self.assertRegex(proc.stderr, r"через \d+ с")
        self.assertEqual(proc.stdout, "")

    # T17
    def test_data_without_space(self):
        body = (data(chunk(content="А"), space=False) + data(chunk(content="Б"))
                + data(chunk(content="В"), space=False)
                + data(chunk(content="", finish="stop"), space=False) + data("[DONE]", space=False))
        self.server.scenario = lambda h: respond(h, body)
        proc = self.run_script("--no-system")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "АБВ\n")

    # T18
    def test_no_usage_gives_zero_tokens(self):
        self.server.scenario = lambda h: respond(h, t1_body(final_usage="absent"))
        proc = self.run_script("--no-system", "--log", str(self.log))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        line = self.log_lines()[0]
        self.assertEqual(line["status"], "ok")
        self.assertEqual(line["tokens_in"], 0)
        self.assertEqual(line["tokens_out"], 0)

    # T19
    def test_last_non_empty_finish_reason(self):
        body = (data(chunk(finish="content_filter")) + data(chunk(finish="length"))
                + data({"id": "x", "object": "chat.completion.chunk", "choices": [], "usage": USAGE})
                + data("[DONE]"))
        self.server.scenario = lambda h: respond(h, body)
        proc = self.run_script("--no-system")
        self.assertEqual(proc.returncode, 6)
        self.assertIn("max_tokens", proc.stderr)
        self.assertEqual(proc.stdout, "")

    # T20
    def test_seconds_measured_from_request(self):
        proc = self.run_script("--no-system", fake_curl=True,
                               env={"FAKE_CURL_RC": "16", "FAKE_CURL_SLEEP": "2"})
        self.assertEqual(proc.returncode, 6)
        m = re.search(r"через (\d+) с", proc.stderr)
        self.assertIsNotNone(m, proc.stderr)
        self.assertGreaterEqual(int(m.group(1)), 1)
        self.assertLessEqual(int(m.group(1)), 10)


if __name__ == "__main__":
    unittest.main()
