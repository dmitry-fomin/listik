"""Фикстура канала `[swarm].command`: заменяет модель роя в тестах CLI-подпроцесса.

Читает запрос JSON (`{"name", "model", "messages", "schema"}`) со stdin, берёт следующий
(по счёту вызовов с этим `name`) ответ из файла `FAKE_MODEL_REPLIES` (JSON:
`{"<name>": [ответ1, ответ2, …]}`; последний ответ списка повторяется на все следующие
вызовы), дописывает запрос строкой в `FAKE_MODEL_CALLS` (JSONL) и печатает ответ
JSON-объектом на stdout. Никакой сети — годится и для оффлайн CI.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    name = request.get("name")

    calls_path = os.environ.get("FAKE_MODEL_CALLS")
    attempt_index = 0
    if calls_path and os.path.exists(calls_path):
        with open(calls_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if json.loads(line).get("name") == name:
                    attempt_index += 1

    if calls_path:
        with open(calls_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(request, ensure_ascii=False) + "\n")

    replies_path = os.environ.get("FAKE_MODEL_REPLIES")
    replies = []
    if replies_path and os.path.exists(replies_path):
        with open(replies_path, encoding="utf-8") as fh:
            replies = (json.load(fh) or {}).get(name) or []

    if replies:
        index = min(attempt_index, len(replies) - 1)
        reply = replies[index]
    else:
        reply = {"tasks": []}

    sys.stdout.write(json.dumps(reply, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
