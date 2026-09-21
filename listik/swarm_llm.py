"""Единственная точка вызова модели в рое: `listik plan`/`listik rescope` (порции b/c
шага swarm-6) зовут модель ровно через `complete_json` этого модуля — больше нигде в
рое HTTP или подпроцесс к LLM не создаётся.

Два канала:

- HTTP, OpenAI-совместимый `POST <base_url>/chat/completions` со Structured Outputs
  (`response_format: json_schema`, `strict: true`) — раздел `[swarm]` `config.toml`
  (`api_key`, `base_url`, `model`) даёт настройки, как `[assistant]`/`[deepgram]`.
- Внешняя команда (`[swarm].command`, argv) — pi/opencode или что угодно, что читает
  запрос JSON на stdin и печатает JSON-объект на stdout; ключ этому каналу не нужен.

`[swarm]` — пользовательский раздел: в `config.DEFAULTS` его нет (иначе `ensure_token`/
`save` дописывали бы пустой `api_key` в чужой `config.toml`), дефолты живут в
`settings()` этого модуля — то же решение, что у `assistant.settings()`.

`api_key` никогда не попадает ни в `SwarmLlmError.message`/`.hint`, ни в лог: тело
ответа провайдера логируется через `assistant.log_upstream`, которая заменяет ключ на
`***` (провайдер может эхоить присланный ключ при 401/403). Форму ответа (соответствие
`schema`) этот модуль не проверяет — это дело вызывающего (`plan`/`rescope`).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import urllib.error
import urllib.request

from . import assistant
from . import deps
from . import errors
from . import util

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "z-ai/glm-5.3-flash"
TIMEOUT = 180.0  # один вызов модели; вызывающий может передать больше

SWARM_AUTHOR = deps.RESOURCE_BLOCK_AUTHOR

ENV_API_KEY = "LISTIK_SWARM_API_KEY"
ENV_BASE_URL = "LISTIK_SWARM_BASE_URL"
ENV_MODEL = "LISTIK_SWARM_MODEL"

logger = logging.getLogger("listik.swarm_llm")


class SwarmLlmError(errors.ListikError):
    """Ошибка вызова модели роя: печатается CLI штатно, сервер берёт `status`."""

    def __init__(self, message: str, *, status: int = 502, code: str = errors.SERVER_ERROR,
                 hint: str = ""):
        super().__init__(message, code=code, hint=hint, exit_code=1, status=status)


def settings(cfg: dict | None = None) -> dict:
    """Настройки канала модели из `[swarm]`; переменные окружения сильнее файла."""
    cfg = cfg if cfg is not None else util.load_config()
    section = cfg.get("swarm") if isinstance(cfg, dict) else None
    if not isinstance(section, dict):
        section = {}

    api_key = section.get("api_key")
    api_key = api_key.strip() if isinstance(api_key, str) else ""
    env_api_key = (os.environ.get(ENV_API_KEY) or "").strip()
    if env_api_key:
        api_key = env_api_key

    base_url = section.get("base_url")
    base_url = base_url.strip() if isinstance(base_url, str) and base_url.strip() else ""
    env_base_url = (os.environ.get(ENV_BASE_URL) or "").strip()
    if env_base_url:
        base_url = env_base_url
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")

    model = section.get("model")
    model = model.strip() if isinstance(model, str) and model.strip() else ""
    env_model = (os.environ.get(ENV_MODEL) or "").strip()
    if env_model:
        model = env_model
    model = model or DEFAULT_MODEL

    raw_command = section.get("command")
    if raw_command in (None, [], ()):
        command: list[str] = []
    elif isinstance(raw_command, list) and all(isinstance(x, str) for x in raw_command):
        command = list(raw_command)
    else:
        raise errors.BadArgument("[swarm].command: ожидался список строк argv")

    return {"api_key": api_key, "base_url": base_url, "model": model, "command": command}


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _clip(value, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def parse_json(content) -> dict:
    """Терпимый разбор ответа модели: своя копия `assistant.parse_suggestion`."""
    if not isinstance(content, str):
        raise SwarmLlmError("ответ модели роя не JSON-объект: " + _clip(content, 200))
    text = content.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = util.json_loads(text)
    except util.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise SwarmLlmError(
                "ответ модели роя не JSON-объект: " + _clip(content, 200)) from None
        try:
            data = util.json_loads(text[start:end + 1])
        except util.JSONDecodeError as exc:
            raise SwarmLlmError(
                "ответ модели роя не JSON-объект: " + _clip(content, 200)) from exc
    if not isinstance(data, dict):
        raise SwarmLlmError("ответ модели роя не JSON-объект: " + _clip(content, 200))
    return data


def _run_command(argv: list[str], input_text: str, timeout: float):
    return subprocess.run(argv, input=input_text, capture_output=True, text=True,
                          timeout=timeout)


def _via_command(command: list[str], messages: list[dict], schema: dict, *, name: str,
                 model: str, runner, timeout: float) -> dict:
    run = runner or _run_command
    input_text = util.json_dumps({"name": name, "model": model, "messages": messages,
                                  "schema": schema})
    try:
        result = run(command, input_text, timeout)
    except subprocess.TimeoutExpired as exc:
        raise SwarmLlmError(f"команда модели не ответила за {timeout:.0f} с", status=504) from exc
    except OSError as exc:
        raise SwarmLlmError(f"команда модели не запустилась: {exc}", status=503,
                            hint="[swarm].command в config.toml") from exc
    if result.returncode != 0:
        assistant.log_upstream(f"команда модели завершилась с кодом {result.returncode}",
                               result.stderr or "", target=logger)
        raise SwarmLlmError(f"команда модели завершилась с кодом {result.returncode}",
                            status=502)
    return parse_json(result.stdout)


def _via_http(messages: list[dict], schema: dict, *, name: str, cfg_settings: dict,
             opener, timeout: float) -> dict:
    api_key = cfg_settings["api_key"]
    if not api_key:
        raise SwarmLlmError(
            "модель роя не настроена: добавь api_key в config.toml, раздел [swarm] "
            "(или LISTIK_SWARM_API_KEY)", status=503)

    payload = {
        "model": cfg_settings["model"],
        "messages": messages,
        "temperature": 0,
        "stream": False,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": name, "strict": True, "schema": schema}},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    url = assistant.endpoint(cfg_settings["base_url"])
    request = urllib.request.Request(
        url, data=util.json_dumps(payload).encode("utf-8"), headers=headers, method="POST")
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — тело ошибки уже не важно
            detail = ""
        assistant.log_upstream(f"модель роя ответила HTTP {exc.code}", detail, api_key,
                               target=logger)
        if exc.code in (401, 403):
            raise SwarmLlmError(
                f"модель роя отклонила ключ (HTTP {exc.code}): проверь [swarm].api_key",
                status=502) from exc
        raise SwarmLlmError(f"модель роя ответила ошибкой HTTP {exc.code}", status=502) from exc
    except urllib.error.URLError as exc:
        raise SwarmLlmError(
            f"модель роя недоступна ({cfg_settings['base_url']}): {exc.reason}",
            status=504) from exc
    except TimeoutError as exc:
        raise SwarmLlmError(
            f"модель роя не ответила за {timeout:.0f} с ({cfg_settings['base_url']})",
            status=504) from exc

    try:
        data = util.json_loads(raw)
    except util.JSONDecodeError as exc:
        assistant.log_upstream("ответ модели роя не JSON", raw, api_key, target=logger)
        raise SwarmLlmError("ответ модели роя не JSON", status=502) from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        assistant.log_upstream("в ответе модели роя нет choices[0].message.content", raw,
                               api_key, target=logger)
        raise SwarmLlmError(
            "в ответе модели роя нет choices[0].message.content", status=502) from exc
    return parse_json(content)


def complete_json(messages: list[dict], schema: dict, *, name: str, cfg_settings: dict,
                  opener=None, runner=None, timeout: float = TIMEOUT) -> dict:
    """Один вызов модели роя; возвращает распарсенный JSON-объект.

    `schema` — JSON Schema объекта, уходит провайдеру как есть (HTTP-канал); `name` —
    имя схемы (`"swarm_plan"`, `"swarm_rescope_extract"`, …). Валидацию формы ответа
    против `schema` делает вызывающий, не этот модуль.
    """
    command = cfg_settings.get("command") or []
    if command:
        return _via_command(command, messages, schema, name=name,
                           model=cfg_settings["model"], runner=runner, timeout=timeout)
    return _via_http(messages, schema, name=name, cfg_settings=cfg_settings, opener=opener,
                    timeout=timeout)
