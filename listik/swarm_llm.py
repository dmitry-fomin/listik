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
from . import documents
from . import errors
from . import scope as scope_mod
from . import store
from . import store_helpers as store_helpers_mod
from . import swarm_watch
from . import util

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "z-ai/glm-5.3-flash"
TIMEOUT = 180.0  # один вызов модели; вызывающий может передать больше

SWARM_AUTHOR = deps.RESOURCE_BLOCK_AUTHOR

ENV_API_KEY = "LISTIK_SWARM_API_KEY"
ENV_BASE_URL = "LISTIK_SWARM_BASE_URL"
ENV_MODEL = "LISTIK_SWARM_MODEL"

JEV_DEFAULT_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_DEFAULT_MODEL = "typesafe/jev-1.13"
JEV_TIMEOUT = 30.0
ENV_JEV_API_KEY = "LISTIK_SWARM_JEV_API_KEY"
ENV_JEV_URL = "LISTIK_SWARM_JEV_URL"
ENV_JEV_MODEL = "LISTIK_SWARM_JEV_MODEL"
JEV_EDGE_DROP_BELOW = 0.2  # ponytail: порог не калиброван; поднимать по журналу jev.dropped

logger = logging.getLogger("listik.swarm_llm")


class SwarmLlmError(errors.ListikError):
    """Ошибка вызова модели роя: печатается CLI штатно, сервер берёт `status`."""

    def __init__(self, message: str, *, status: int = 502, code: str = errors.SERVER_ERROR,
                 hint: str = ""):
        super().__init__(message, code=code, hint=hint, exit_code=1, status=status)


def model_name(cfg: dict | None = None) -> str:
    """Итоговое имя модели роя из конфига и окружения.

    Резолвинг модели намеренно отделён от полной :func:`settings`: он не читает
    ключ и не валидирует внешний ``command``.
    """
    cfg = cfg if cfg is not None else util.load_config()
    section = cfg.get("swarm") if isinstance(cfg, dict) else None
    if not isinstance(section, dict):
        section = {}
    model = section.get("model")
    model = model.strip() if isinstance(model, str) and model.strip() else ""
    env_model = (os.environ.get(ENV_MODEL) or "").strip()
    return env_model or model or DEFAULT_MODEL


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

    model = model_name(cfg)

    jev_api_key = section.get("jev_api_key")
    jev_api_key = jev_api_key.strip() if isinstance(jev_api_key, str) else ""
    env_jev_api_key = (os.environ.get(ENV_JEV_API_KEY) or "").strip()
    if env_jev_api_key:
        jev_api_key = env_jev_api_key

    jev_url = section.get("jev_url")
    jev_url = jev_url.strip() if isinstance(jev_url, str) and jev_url.strip() else ""
    env_jev_url = (os.environ.get(ENV_JEV_URL) or "").strip()
    if env_jev_url:
        jev_url = env_jev_url
    jev_url = (jev_url or JEV_DEFAULT_URL).rstrip("/")

    jev_model = section.get("jev_model")
    jev_model = jev_model.strip() if isinstance(jev_model, str) and jev_model.strip() else ""
    env_jev_model = (os.environ.get(ENV_JEV_MODEL) or "").strip()
    if env_jev_model:
        jev_model = env_jev_model
    jev_model = jev_model or JEV_DEFAULT_MODEL

    raw_command = section.get("command")
    if raw_command in (None, [], ()):
        command: list[str] = []
    elif isinstance(raw_command, list) and all(isinstance(x, str) for x in raw_command):
        command = list(raw_command)
    else:
        raise errors.BadArgument("[swarm].command: ожидался список строк argv")

    return {"api_key": api_key, "base_url": base_url, "model": model, "command": command,
            "jev_api_key": jev_api_key, "jev_url": jev_url, "jev_model": jev_model}


def jev_enabled(cfg_settings: dict) -> bool:
    return bool(cfg_settings.get("jev_api_key"))


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


def _jev_error(raw: str, api_key: str, message: str) -> SwarmLlmError:
    message = message[:200]
    assistant.log_upstream(message, raw, api_key=api_key, target=logger)
    return SwarmLlmError(message, status=502)


def decide(state, questions: dict, *, cfg_settings: dict, opener=None,
           timeout: float = JEV_TIMEOUT) -> dict:
    """Сделать один типизированный запрос к OpenRouter Decisions API (jev)."""
    api_key = cfg_settings.get("jev_api_key", "")
    if not api_key:
        raise SwarmLlmError(
            "jev не настроен: добавь jev_api_key в config.toml, раздел [swarm] "
            "(или LISTIK_SWARM_JEV_API_KEY)", status=503)

    url = cfg_settings["jev_url"]
    payload = {"model": cfg_settings["jev_model"],
               "state": state, "questions": questions}
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "Authorization": f"Bearer {api_key}"}
    request = urllib.request.Request(
        url,
        data=util.json_dumps(payload).encode("utf-8"), headers=headers, method="POST")
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — тело ошибки может быть недоступно
            raw = ""
        if exc.code in (401, 403):
            raise _jev_error(
                raw, api_key,
                f"jev отклонил ключ (HTTP {exc.code}): проверь [swarm].jev_api_key") from exc
        raise _jev_error(raw, api_key, f"jev ответил ошибкой HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        detail = f"jev недоступен ({url}): {exc.reason}".replace(api_key, "***")
        raise SwarmLlmError(
            detail, status=504) from exc
    except TimeoutError as exc:
        raise SwarmLlmError(f"jev не ответил за {timeout:.0f} с", status=504) from exc

    try:
        data = util.json_loads(raw)
    except util.JSONDecodeError as exc:
        raise _jev_error(raw, api_key, "jev ответил не JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise _jev_error(raw, api_key, "jev: в ответе нет answers")

    answers = data["answers"]
    for name, question in questions.items():
        answer = answers.get(name)
        safe_name = name.replace(api_key, "***")
        detail = "нет ответа" if answer is None else "неверная форма"
        if not isinstance(answer, dict):
            raise _jev_error(raw, api_key,
                             f"jev: негодный ответ на {safe_name}: {detail}")
        expected_type = question.get("type") if isinstance(question, dict) else None
        if answer.get("type") != expected_type:
            detail = f"type должен быть {expected_type!r}"
        elif expected_type == "noul":
            value = answer.get("noul")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                detail = "noul должен быть числом от 0 до 1"
            elif not 0 <= value <= 1:
                detail = "noul должен быть числом от 0 до 1"
            else:
                continue
        elif expected_type == "choice":
            value = answer.get("choice")
            criteria = question.get("criteria") if isinstance(question, dict) else None
            if not isinstance(value, str) or not isinstance(criteria, dict) or value not in criteria:
                detail = "choice отсутствует в criteria"
            else:
                continue
        else:
            detail = "неподдерживаемый type"
        raise _jev_error(raw, api_key, f"jev: негодный ответ на {safe_name}: {detail}")

    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = None
    model = data.get("model")
    if not isinstance(model, str):
        model = None
    return {"answers": answers, "usage": usage, "model": model}


# --- проверка результата арбитра слияния через jev ------------------------------------

JEV_MERGE_ACCEPT = 0.7          # ponytail: порог не калиброван; крутить по verdict.json
MERGE_CONTEXT_LINES = 3
MERGE_MAX_STATE_CHARS = 100_000  # ≈25k токенов; контекст jev — 32k
MERGE_FILE_FALLBACK_CHARS = 40_000

MERGE_QUESTIONS = {
    "task_kept": {
        "type": "noul",
        "instructions": (
            "В каждом resolved (или в resolved_file) сохранено то, что сторона task "
            "добавила или изменила относительно base? Учитывай task_card."),
        "criteria": {"true": "Изменения стороны task сохранены во всех кусках.",
                     "false": "Изменения стороны task потеряны хотя бы в одном куске."},
    },
    "main_kept": {
        "type": "noul",
        "instructions": (
            "В каждом resolved (или в resolved_file) сохранено то, что сторона main "
            "добавила или изменила относительно base? Учитывай main_cards."),
        "criteria": {"true": "Изменения стороны main сохранены во всех кусках.",
                     "false": "Изменения стороны main потеряны хотя бы в одном куске."},
    },
    "clean": {
        "type": "noul",
        "instructions": (
            "Результат в resolved (или в resolved_file) цельный: один фрагмент не "
            "повторён дважды, нет обрывков строк и маркеров конфликта?"),
        "criteria": {"true": "Результат цельный, без повторов, обрывков и маркеров.",
                     "false": "Есть повтор, обрывок строки или маркер конфликта."},
    },
}


def extract_conflicts(before: str) -> list[dict]:
    """Разобрать diff3/обычные конфликты, не захватывая соседние блоки в якоря."""
    lines = before.splitlines()
    blocks: list[tuple[int, int, dict]] = []
    start = base = separator = None
    for i, line in enumerate(lines):
        if line.startswith("<<<<<<< "):
            if start is not None:
                raise errors.BadArgument(f"маркеры конфликта не разобраны: строка {i + 1}")
            start = i
        elif line.startswith("||||||| "):
            if start is None or base is not None or separator is not None:
                raise errors.BadArgument(f"маркеры конфликта не разобраны: строка {i + 1}")
            base = i
        elif line == "=======":
            if start is None or separator is not None:
                raise errors.BadArgument(f"маркеры конфликта не разобраны: строка {i + 1}")
            separator = i
        elif line.startswith(">>>>>>> "):
            if start is None or separator is None:
                raise errors.BadArgument(f"маркеры конфликта не разобраны: строка {i + 1}")
            blocks.append((start, i, {
                "main": "\n".join(lines[start + 1:base if base is not None else separator]),
                "base": "\n".join(lines[base + 1:separator]) if base is not None else None,
                "task": "\n".join(lines[separator + 1:i]),
            }))
            start = base = separator = None
    if start is not None:
        raise errors.BadArgument(f"маркеры конфликта не разобраны: строка {start + 1}")

    hunks = []
    for n, (start, end, hunk) in enumerate(blocks):
        previous_end = blocks[n - 1][1] + 1 if n else 0
        next_start = blocks[n + 1][0] if n + 1 < len(blocks) else len(lines)
        hunks.append({**hunk,
                      "ctx_before": lines[max(previous_end, start - MERGE_CONTEXT_LINES):start],
                      "ctx_after": lines[end + 1:min(next_start, end + 1 + MERGE_CONTEXT_LINES)]})
    return hunks


def resolved_regions(after: str, hunks: list[dict]) -> list[str | None]:
    """Найти области между якорями; неудачный поиск не сдвигает курсор."""
    lines = after.splitlines()

    def find(anchor: list[str], start: int) -> int | None:
        for i in range(start, len(lines) - len(anchor) + 1):
            if lines[i:i + len(anchor)] == anchor:
                return i
        return None

    regions: list[str | None] = []
    cursor = 0
    for hunk in hunks:
        left, right = hunk["ctx_before"], hunk["ctx_after"]
        start = find(left, cursor) if left else cursor
        if start is None:
            regions.append(None)
            continue
        start += len(left)
        end = find(right, start) if right else len(lines)
        if end is None:
            regions.append(None)
            continue
        regions.append("\n".join(lines[start:end]))
        # Правый якорь может быть левым якорем следующего близкого конфликта.
        cursor = end
    return regions


def merge_state(path, before, after) -> tuple[dict | None, str | None]:
    """Снимок конфликтных кусков и результата либо причина пропуска файла."""
    try:
        hunks = extract_conflicts(before)
    except errors.BadArgument as exc:
        return None, str(exc)
    if not hunks:
        return None, "в «до» нет маркеров конфликта"
    regions = resolved_regions(after, hunks)
    state = {"path": path, "hunks": [
        {"main": hunk["main"], "base": hunk["base"], "task": hunk["task"],
         "resolved": region} for hunk, region in zip(hunks, regions)]}
    if any(region is None for region in regions):
        if len(after) > MERGE_FILE_FALLBACK_CHARS:
            return None, "не удалось сопоставить куски, файл велик"
        state["resolved_file"] = after
    if len(util.json_dumps(state)) > MERGE_MAX_STATE_CHARS:
        return None, "слишком большой для jev"
    return state, None


def _merge_card(card, field: str) -> dict:
    if not isinstance(card, dict):
        raise errors.BadArgument(f"{field}: ожидался объект")
    if not isinstance(card.get("id"), str) or not card["id"].strip():
        raise errors.BadArgument(f"{field}.id: ожидалась непустая строка")
    for name in ("title", "description", "acceptance"):
        if name in card and not isinstance(card[name], str):
            raise errors.BadArgument(f"{field}.{name}: ожидалась строка")
    return {"id": card["id"], "title": card.get("title", ""),
            "acceptance": _clip(card.get("acceptance", ""), 1500)}


def judge_merge(payload, *, cfg_settings=None, opener=None, timeout=JEV_TIMEOUT) -> dict:
    """Проверить снимки слияния без записи в БД; недоступный jev — пропуск."""
    if not isinstance(payload, dict):
        raise errors.BadArgument("payload: ожидался объект")
    task_card = _merge_card(payload.get("task"), "task")
    others = payload.get("others", [])
    if not isinstance(others, list):
        raise errors.BadArgument("others: ожидался список")
    main_cards = [_merge_card(card, f"others[{i}]") for i, card in enumerate(others)]
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise errors.BadArgument("files: ожидался непустой список")
    for i, file in enumerate(files):
        if not isinstance(file, dict):
            raise errors.BadArgument(f"files[{i}]: ожидался объект")
        for name in ("path", "before", "after"):
            if not isinstance(file.get(name), str):
                raise errors.BadArgument(f"files[{i}].{name}: ожидалась строка")

    cfg_settings = cfg_settings or settings()
    if not jev_enabled(cfg_settings):
        return {"ok": True, "model": None, "skipped": "не настроен", "files": []}

    results = []
    skipped = None
    for file in files:
        state, reason = merge_state(file["path"], file["before"], file["after"])
        if state is None:
            results.append({"path": file["path"], "verdict": "skipped",
                            "reason": reason, "answers": {}})
            continue
        try:
            reply = decide(state={"task_card": task_card, "main_cards": main_cards, **state},
                           questions=MERGE_QUESTIONS, cfg_settings=cfg_settings,
                           opener=opener, timeout=timeout)
        except SwarmLlmError as exc:
            skipped = f"ошибка: {exc.message}"
            logger.warning("проверка слияния jev пропущена: %s", exc.message)
            break
        answers = {name: reply["answers"][name]["noul"] for name in MERGE_QUESTIONS}
        failed = [f"{name}={p:.2f}" for name, p in answers.items() if p < JEV_MERGE_ACCEPT]
        result = {"path": file["path"], "verdict": "reject" if failed else "ok",
                  "answers": answers}
        if failed:
            result["reason"] = ", ".join(failed)
        results.append(result)
    return {"ok": not any(item["verdict"] == "reject" for item in results),
            "model": cfg_settings["jev_model"], "skipped": skipped, "files": results}


# --- проход `listik plan`: грубые зависимости между открытыми задачами проекта -----------

MAX_FIELD_CHARS = 4000          # описание/приёмка одной карточки в промпте
MAX_TOTAL_CHARS = 400_000       # весь текст сообщений одного вызова (≈100k токенов)
MAX_ATTEMPTS = 2                # вызовов модели на проход: первый + один повтор после цикла
MAX_REASON_CHARS = 300

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["tasks"],
    "properties": {"tasks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "depends_on", "reason"],
        "properties": {"id": {"type": "string"},
                       "depends_on": {"type": "array", "items": {"type": "string"}},
                       "reason": {"type": "string"}}}}}}

PLAN_PROMPT = (
    "Ты — планировщик очереди задач в трекере Listik. Тебе дают открытые задачи одного "
    "проекта (id, заголовок, описание, приёмка), уже известные жёсткие зависимости fixed "
    "(их менять нельзя — это факт) и previous — твой прошлый ответ, который можно "
    "уточнять.\n"
    "Расставь грубые зависимости: задача B ждёт задачу A (B.depends_on содержит A), если "
    "ТЗ или реализацию B нельзя написать, не зная результата A: A вводит схему, API, "
    "модуль, команду или формат, на который B опирается. Цель — понять, какие ТЗ можно "
    "писать параллельно; точность не нужна, нужен порядок.\n"
    "Правила: только id из списка; зависимость «на всякий случай» не ставь — "
    "сомневаешься, не ставь; общая тема, общий родитель, соседние файлы — не зависимость; "
    "циклов быть не должно (если A ждёт B, B не ждёт A ни напрямую, ни через другие); "
    "reason — одна короткая фраза, почему B ждёт A (пустая строка, если depends_on пуст).\n"
    "Ответь одним JSON-объектом по схеме {\"tasks\": [{\"id\", \"depends_on\", \"reason\"}]} "
    "— по одной записи на каждую задачу списка."
)

RETRY_PROMPT = (
    "В твоём ответе цикл: {cycles}; Убери хотя бы одно ребро в каждом цикле (оставь то, "
    "что важнее для порядка написания ТЗ) и верни исправленный полный ответ по той же "
    "схеме."
)


def _format_cycles(cycles: list[list[str]]) -> str:
    return "; ".join(" → ".join([*cycle, cycle[0]]) for cycle in cycles)


def working_set(conn, *, project: str, stage: str | None = None) -> list:
    """Рабочее множество прохода plan: открытые задачи проекта, порядок как у `deps.waves`."""
    if not (project or "").strip():
        raise errors.BadArgument("нужен проект: план считается по одному проекту")
    stage = (stage or "").strip() or None
    where = ["archived = 0",
             f"status IN ({','.join('?' * len(deps.OPEN_STATUSES))})",
             "project = ?"]
    params: list = [*deps.OPEN_STATUSES, project]
    if stage is not None:
        where.append("stage = ?")
        params.append(stage)
    return deps._fetch(
        conn,
        f"SELECT * FROM tasks WHERE {' AND '.join(where)} "
        "ORDER BY priority ASC, created_at ASC, id ASC",
        tuple(params),
    )


def fixed_edges(conn, working: list[str]) -> tuple[list[list[str]], list[list[str]]]:
    """Смысловые жёсткие рёбра (`fixed`) и свои прошлые машинные `blocks` (`previous`)
    внутри рабочего множества, в порядке `working` (по позже, затем по раньше)."""
    if not working:
        return [], []
    order_index = {tid: i for i, tid in enumerate(working)}
    marks = ",".join("?" * len(working))
    rows = deps._fetch(
        conn,
        "SELECT issue_id, depends_on, dep_type, created_by FROM deps "
        f"WHERE dep_type IN ({','.join('?' * len(deps.SEMANTIC_HARD))}) "
        f"AND issue_id IN ({marks}) AND depends_on IN ({marks})",
        (*deps.SEMANTIC_HARD, *working, *working),
    )

    def sort_key(pair):
        earlier, later = pair
        return (order_index[later], order_index[earlier])

    fixed_pairs: list[tuple[str, str]] = []
    previous_pairs: list[tuple[str, str]] = []
    for r in rows:
        pair = (r["depends_on"], r["issue_id"])  # (раньше, позже)
        if r["dep_type"] == "blocks" and r["created_by"] == SWARM_AUTHOR:
            previous_pairs.append(pair)
        else:
            fixed_pairs.append(pair)
    fixed = [list(p) for p in sorted(set(fixed_pairs), key=sort_key)]
    previous = [list(p) for p in sorted(set(previous_pairs), key=sort_key)]
    return fixed, previous


def normalize_graph(data: dict, ids: list[str]) -> tuple[dict, list[list[str]], list[dict]]:
    """Нормализует сырой ответ модели: `tasks_view` (по всем `ids`), `edges` (позже по
    порядку `ids`, затем раньше), `dropped` (отброшенные записи/ссылки)."""
    id_set = set(ids)
    order_index = {tid: i for i, tid in enumerate(ids)}
    raw_tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(raw_tasks, list):
        raw_tasks = []

    dropped: list[dict] = []
    depends_by_id: dict[str, list[str]] = {}
    reason_by_id: dict[str, str] = {}

    for entry in raw_tasks:
        if not isinstance(entry, dict):
            continue
        tid = entry.get("id")
        if not isinstance(tid, str) or tid not in id_set:
            dropped.append({"id": tid, "why": "unknown_task"})
            continue
        raw_depends = entry.get("depends_on")
        if not isinstance(raw_depends, list):
            raw_depends = []
        deps_list = depends_by_id.setdefault(tid, [])
        for dep in raw_depends:
            if not isinstance(dep, str) or dep not in id_set:
                dropped.append({"id": tid, "depends_on": dep, "why": "unknown_id"})
                continue
            if dep == tid:
                dropped.append({"id": tid, "depends_on": dep, "why": "self"})
                continue
            if dep not in deps_list:
                deps_list.append(dep)
        reason = entry.get("reason")
        reason = reason if isinstance(reason, str) else ""
        reason_by_id[tid] = reason[:MAX_REASON_CHARS]

    tasks_view: dict[str, dict] = {}
    edges_pairs: list[tuple[str, str]] = []
    for tid in ids:
        depends_on = depends_by_id.get(tid, [])
        tasks_view[tid] = {"depends_on": list(depends_on),
                           "reason": reason_by_id.get(tid, "")}
        for dep in depends_on:
            edges_pairs.append((dep, tid))

    edges_pairs.sort(key=lambda pair: (order_index[pair[1]], order_index[pair[0]]))
    edges = [list(p) for p in edges_pairs]
    return tasks_view, edges, dropped


def _group_by_later(pairs: list[list[str]], ids: list[str]) -> list[dict]:
    """`[раньше, позже]` → `[{"id": позже, "depends_on": [раньше, …]}]`, в порядке `ids`."""
    order_index = {tid: i for i, tid in enumerate(ids)}
    groups: dict[str, list[str]] = {}
    for earlier, later in pairs:
        groups.setdefault(later, []).append(earlier)
    return [{"id": later, "depends_on": groups[later]}
           for later in sorted(groups, key=lambda tid: order_index[tid])]


def _graph_pass(messages: list[dict], schema: dict, *, name: str, ids: list[str],
                fixed: list[list[str]], cfg_settings: dict, opener, runner,
                timeout: float) -> tuple[dict, list[list[str]], list[dict], list[list[str]], int]:
    """Общий цикл уточнения графа `blocks` от модели, используемый `plan` и `rescope`:
    вызов модели, нормализация ответа, проверка цикла, до `MAX_ATTEMPTS` попыток с
    `RETRY_PROMPT`. Возвращает `(tasks_view, edges, dropped, cycles, attempts)`."""
    tasks_view: dict = {}
    model_edges: list[list[str]] = []
    dropped: list[dict] = []
    cycles: list[list[str]] = []
    attempts = 0
    for attempts in range(1, MAX_ATTEMPTS + 1):
        data = complete_json(messages, schema, name=name, cfg_settings=cfg_settings,
                             opener=opener, runner=runner, timeout=timeout)
        tasks_view, model_edges, dropped = normalize_graph(data, ids)
        incoming: dict[str, set[str]] = {}
        for earlier, later in (*fixed, *model_edges):
            incoming.setdefault(later, set()).add(earlier)
        cycles = deps.find_cycles(ids, incoming)
        if not cycles:
            break
        if attempts < MAX_ATTEMPTS:
            messages = messages + [
                {"role": "assistant", "content": util.json_dumps(data)},
                {"role": "user", "content": RETRY_PROMPT.format(
                    cycles=_format_cycles(cycles))},
            ]
    return tasks_view, model_edges, dropped, cycles, attempts


JEV_EDGE_INSTRUCTIONS = "Задачу b нельзя спланировать и написать её ТЗ, не зная результата задачи a"
JEV_EDGE_TRUE = ("a вводит схему, API, модуль, команду, формат или решение, на которое b "
                 "прямо опирается; reason это подтверждает")
JEV_EDGE_FALSE = ("общая тема, общий родитель, соседние файлы, порядок «для удобства» или "
                  "зависимость «на всякий случай» — b можно спланировать, не зная результата a")


def _jev_card(task_id: str, by_id: dict) -> dict:
    row = by_id[task_id]
    return {"id": task_id, "title": row["title"],
            "description": _clip(row["description"] or "", MAX_FIELD_CHARS),
            "acceptance": _clip(row["acceptance"] or "", MAX_FIELD_CHARS)}


def check_plan_edges(model_edges: list[list[str]], *, fixed: list[list[str]], by_id: dict,
                     tasks_view: dict, cfg_settings: dict, opener=None,
                     timeout: float = JEV_TIMEOUT) -> tuple[list[list[str]], dict]:
    """Проверить предложенные моделью рёбра jev и снять только явно лишние."""
    fixed_set = {tuple(edge) for edge in fixed}
    checked = 0
    dropped: list[dict] = []
    kept: list[list[str]] = []
    try:
        for edge in model_edges:
            earlier, later = edge
            if tuple(edge) in fixed_set:
                kept.append(edge)
                continue
            reason = tasks_view.get(later, {}).get("reason", "")
            state = {"a": _jev_card(earlier, by_id), "b": _jev_card(later, by_id),
                     "reason": reason}
            questions = {"needed": {"type": "noul", "instructions": JEV_EDGE_INSTRUCTIONS,
                                     "criteria": {"true": JEV_EDGE_TRUE, "false": JEV_EDGE_FALSE}}}
            result = decide(state, questions, cfg_settings=cfg_settings, opener=opener,
                            timeout=timeout)
            checked += 1
            p = result["answers"]["needed"]["noul"]
            if p < JEV_EDGE_DROP_BELOW:
                dropped.append({"edge": list(edge), "p": p})
            else:
                kept.append(edge)
    except SwarmLlmError as exc:
        message = f"ошибка: {exc.message}"
        logger.warning(message)
        return [list(edge) for edge in model_edges], {
            "model": cfg_settings.get("jev_model") or JEV_DEFAULT_MODEL,
            "checked": checked, "dropped": [], "skipped": message}
    return kept, {"model": cfg_settings.get("jev_model") or JEV_DEFAULT_MODEL,
                  "checked": checked, "dropped": dropped, "skipped": None}


def plan(conn, *, project: str, stage: str | None = None, apply: bool = False,
         cfg: dict | None = None, opener=None, runner=None, timeout: float = TIMEOUT) -> dict:
    """Проход plan: грубый граф `blocks` между открытыми задачами проекта от модели.

    Без `apply` — сухой прогон (ничего не пишет). С `apply` — при отсутствии циклов
    пишет граф модели жёсткими `blocks` от `SWARM_AUTHOR` через
    `deps.apply_planned_blocks`. Цикл (в базе или у модели после `MAX_ATTEMPTS`
    попыток) — рёбра не записываются, ответ отдаёт `cycles`.
    """
    stage = (stage or "").strip() or None
    rows = working_set(conn, project=project, stage=stage)
    ids = [r["id"] for r in rows]
    by_id = {r["id"]: r for r in rows}
    cfg_settings = settings(cfg)

    if not ids:
        return {"project": project, "stage": stage, "model": cfg_settings["model"],
                "attempts": 0, "tasks": {}, "edges": [], "fixed": [], "previous": [],
                "dropped": [], "cycles": [], "cycles_from": None, "applied": None,
                "jev": {"model": None, "checked": 0, "dropped": [],
                        "skipped": "не вызывался"}}

    fixed, previous = fixed_edges(conn, ids)
    incoming_fixed: dict[str, set[str]] = {}
    for earlier, later in fixed:
        incoming_fixed.setdefault(later, set()).add(earlier)
    db_cycles = deps.find_cycles(ids, incoming_fixed)
    if db_cycles:
        return {"project": project, "stage": stage, "model": cfg_settings["model"],
                "attempts": 0,
                "tasks": {tid: {"title": by_id[tid]["title"], "depends_on": [], "reason": ""}
                         for tid in ids},
                "edges": [], "fixed": fixed, "previous": previous, "dropped": [],
                "cycles": db_cycles, "cycles_from": "db", "applied": None,
                "jev": {"model": None, "checked": 0, "dropped": [],
                        "skipped": "не вызывался"}}

    payload_tasks = []
    for tid in ids:
        row = by_id[tid]
        payload_tasks.append({
            "id": tid,
            "title": row["title"],
            "description": _clip(row["description"] or "", MAX_FIELD_CHARS),
            "acceptance": _clip(row["acceptance"] or "", MAX_FIELD_CHARS),
            "stage": row["stage"],
            "labels": store_helpers_mod.json_list(row["labels"]),
        })
    payload = {
        "project": project,
        "tasks": payload_tasks,
        "fixed": _group_by_later(fixed, ids),
        "previous": _group_by_later(previous, ids),
    }

    messages = [{"role": "system", "content": PLAN_PROMPT},
               {"role": "user", "content": util.json_dumps(payload)}]
    total_chars = sum(len(m["content"]) for m in messages)
    if total_chars > MAX_TOTAL_CHARS:
        raise errors.BadArgument(
            f"слишком много текста для одного вызова модели: {total_chars} символов — "
            "ограничь --stage")

    tasks_view, model_edges, dropped, cycles, attempts = _graph_pass(
        messages, PLAN_SCHEMA, name="swarm_plan", ids=ids, fixed=fixed,
        cfg_settings=cfg_settings, opener=opener, runner=runner, timeout=timeout)

    tasks_out = {
        tid: {"title": by_id[tid]["title"],
             "depends_on": tasks_view.get(tid, {}).get("depends_on", []),
             "reason": tasks_view.get(tid, {}).get("reason", "")}
        for tid in ids
    }

    if cycles:
        return {"project": project, "stage": stage, "model": cfg_settings["model"],
                "attempts": attempts, "tasks": tasks_out, "edges": [], "fixed": fixed,
                "previous": previous, "dropped": dropped, "cycles": cycles,
                "cycles_from": "model", "applied": None,
                "jev": {"model": None, "checked": 0, "dropped": [],
                        "skipped": "не вызывался"}}

    if jev_enabled(cfg_settings):
        model_edges, jev = check_plan_edges(
            model_edges, fixed=fixed, by_id=by_id, tasks_view=tasks_view,
            cfg_settings=cfg_settings, opener=opener)
        remaining = {tuple(edge) for edge in model_edges}
        for tid in ids:
            original = tasks_out[tid]["depends_on"]
            tasks_out[tid]["depends_on"] = [
                earlier for earlier in original if (earlier, tid) in remaining]
    else:
        jev = {"model": None, "checked": 0, "dropped": [], "skipped": "не настроен"}

    applied = None
    if apply:
        applied = deps.apply_planned_blocks(conn, working=ids, edges=model_edges)

    return {"project": project, "stage": stage, "model": cfg_settings["model"],
            "attempts": attempts, "tasks": tasks_out, "edges": model_edges, "fixed": fixed,
            "previous": previous, "dropped": dropped, "cycles": [], "cycles_from": None,
            "applied": applied, "jev": jev}


# --- проход `listik rescope`: области read_scope/write_scope из ТЗ + уточнение графа ------

MERGED_MARK = "рой: влито:"     # маркер барьера swarm-6 (swarm/barrier.mjs, MERGED_MARK); дубль
MAX_SPEC_CHARS = 60_000         # текст одного ТЗ в промпте
MAX_SUMMARY_CHARS = 600
MAX_DRIFT_RECORDS = 200         # последних записей копилки в промпт графа

EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["read_scope", "write_scope", "summary"],
    "properties": {"read_scope": {"type": "array", "items": {"type": "string"}},
                   "write_scope": {"type": "array", "items": {"type": "string"}},
                   "summary": {"type": "string"}},
}

EXTRACT_PROMPT = (
    "Тебе дают карточку задачи (id, заголовок, описание, приёмка), текст её ТЗ и "
    "drift — факты о том, какие файлы эта задача уже трогала вне объявленной области. "
    "Выпиши write_scope — файлы и каталоги (пути от корня проекта, как они названы в "
    "ТЗ, включая новые файлы, которые ТЗ велит создать), которые исполнитель будет "
    "менять; read_scope — что он будет читать, не меняя (полнота не обязательна); "
    "summary — два-три предложения: что делает задача и на чей результат опирается.\n"
    "Правила: только пути из ТЗ, не выдумывай; путь каталога означает всё его "
    "поддерево — бери каталог, если ТЗ правит в нём много файлов; без шаблонов (*, ?) "
    "и без абсолютных путей; тесты и документация, которые ТЗ велит править, — тоже "
    "write_scope; файлы из drift включи в write_scope.\n"
    "Ответь одним JSON-объектом по схеме {\"read_scope\", \"write_scope\", \"summary\"}."
)

GRAPH_PROMPT = (
    "Ты — планировщик очереди задач в трекере Listik. Тебе дают открытые задачи "
    "одного проекта: у каждой summary (о чём задача), read_scope и write_scope (какие "
    "файлы она читает/правит по её ТЗ), уже известные жёсткие зависимости fixed (их "
    "менять нельзя — это факт), previous — твой прошлый ответ, который можно уточнять, "
    "и drift — записи о том, что объявленные области на деле оказались шире.\n"
    "Расставь грубые зависимости: задача B ждёт задачу A (B.depends_on содержит A), "
    "если по выжимкам B опирается на результат A: A вводит то, что B читает или "
    "правит. Одинаковый write_scope сам по себе — не зависимость, конфликты по файлам "
    "разводит планировщик волн, не ты; drift показывает, что объявленные области на "
    "деле шире — учитывай это, решая, чей результат кому нужен.\n"
    "Правила: только id из списка; сомневаешься — не ставь; общая тема, общий "
    "родитель, соседние файлы — не зависимость; циклов быть не должно (если A ждёт B, "
    "B не ждёт A ни напрямую, ни через другие); reason — одна короткая фраза, почему B "
    "ждёт A (пустая строка, если depends_on пуст).\n"
    "Ответь одним JSON-объектом по схеме {\"tasks\": [{\"id\", \"depends_on\", "
    "\"reason\"}]} — по одной записи на каждую задачу списка."
)


def _clean_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
    return out


def _uncovered(touched: list[str], declared: list[str]) -> list[str]:
    return [f for f in touched if not any(scope_mod.covers(d, f) for d in declared)]


def drift_records(conn, *, project: str, extra: list | None = None) -> tuple[list[dict], int]:
    """Копилка расхождений «объявил X, тронул Y»: из журнальных записей
    (`SCOPE_MARK`/`MERGED_MARK`, автор `SWARM_AUTHOR`) карточек проекта плюс `extra`
    (`--drift`). Возвращает `(records, ignored)` — записи в порядке времени."""
    records: list[dict] = []
    ignored = 0

    rows = deps._fetch(
        conn,
        "SELECT c.task_id, c.text, c.created_at FROM comments c "
        "JOIN tasks t ON t.id = c.task_id "
        "WHERE t.project = ? AND t.archived = 0 AND c.author = ? "
        "AND (c.text LIKE ? OR c.text LIKE ?) ORDER BY c.created_at, c.id",
        (project, SWARM_AUTHOR, swarm_watch.SCOPE_MARK + "%", MERGED_MARK + "%"),
    )
    for row in rows:
        text = row["text"] or ""
        if text.startswith(swarm_watch.SCOPE_MARK):
            mark, source = swarm_watch.SCOPE_MARK, "watch"
        elif text.startswith(MERGED_MARK):
            mark, source = MERGED_MARK, "merged"
        else:
            ignored += 1
            continue
        try:
            data = util.json_loads(text[len(mark):].strip())
        except util.JSONDecodeError:
            ignored += 1
            continue
        if not isinstance(data, dict):
            ignored += 1
            continue
        declared = _clean_str_list(data.get("declared"))
        if source == "watch":
            touched = _clean_str_list(data.get("files"))
            outside = list(touched)
        else:
            touched = _clean_str_list(data.get("files"))
            outside = (_clean_str_list(data.get("outside")) if "outside" in data
                      else _uncovered(touched, declared))
        records.append({"task": row["task_id"], "declared": declared, "touched": touched,
                        "outside": outside, "source": source, "ts": row["created_at"]})

    if extra is not None:
        valid_ids = {r["id"] for r in deps._fetch(
            conn, "SELECT id FROM tasks WHERE project = ? AND archived = 0", (project,))}
        for item in extra:
            if not isinstance(item, dict):
                ignored += 1
                continue
            task = item.get("task")
            if not isinstance(task, str) or not task or task not in valid_ids:
                ignored += 1
                continue
            declared = _clean_str_list(item.get("declared"))
            touched_raw = item.get("touched")
            if touched_raw is None:
                touched_raw = item.get("files")
            touched = _clean_str_list(touched_raw)
            outside = (_clean_str_list(item.get("outside")) if "outside" in item
                      else _uncovered(touched, declared))
            records.append({"task": task, "declared": declared, "touched": touched,
                            "outside": outside, "source": "file", "ts": None})

    return records, ignored


def rescope(conn, *, project: str, tasks: list[str] | None = None, drift: list | None = None,
           apply: bool = False, cfg: dict | None = None, opener=None, runner=None,
           timeout: float = TIMEOUT) -> dict:
    """Проход rescope: `read_scope`/`write_scope` из ТЗ готовых задач проекта плюс
    уточнение графа `blocks` по выжимкам, областям, чужим рёбрам и копилке
    расхождений «объявил X, тронул Y». Без `apply` — сухой прогон.
    """
    rows = working_set(conn, project=project)
    ids = [r["id"] for r in rows]
    by_id = {r["id"]: r for r in rows}
    cfg_settings = settings(cfg)

    if tasks:
        id_set = set(ids)
        for tid in tasks:
            if tid not in id_set:
                raise errors.BadArgument(f"задача {tid} не в рабочем множестве проекта")
        wanted = set(tasks)
        extract_ids = [tid for tid in ids if tid in wanted]
    else:
        extract_ids = list(ids)

    if drift is not None and not isinstance(drift, list):
        raise errors.BadArgument("drift: ожидался список записей")

    records, ignored = drift_records(conn, project=project, extra=drift)
    own: dict[str, list[dict]] = {}
    for rec in records:
        own.setdefault(rec["task"], []).append(rec)

    fixed, previous = fixed_edges(conn, ids)
    incoming_fixed: dict[str, set[str]] = {}
    for earlier, later in fixed:
        incoming_fixed.setdefault(later, set()).add(earlier)
    db_cycles = deps.find_cycles(ids, incoming_fixed)

    # --- фаза 1: извлечение областей из ТЗ, по одной задаче за вызов ---------------

    new: dict[str, dict] = {}
    changed: dict[str, bool] = {}
    unspecced: dict[str, str] = {}
    unscoped: list[str] = []
    invalid: dict[str, str] = {}
    unscoped_summary: dict[str, str] = {}
    extracted = 0

    for tid in extract_ids:
        row = by_id[tid]
        try:
            doc = documents.get_document(conn, tid, "spec")
        except errors.NotFound:
            unspecced[tid] = "нет spec_path"
            continue
        if doc["status"] != "ok":
            unspecced[tid] = doc.get("error") or "файл ТЗ не прочитан"
            continue

        own_records = own.get(tid, [])
        user_payload = {
            "task": {"id": tid, "title": row["title"],
                     "description": _clip(row["description"] or "", MAX_FIELD_CHARS),
                     "acceptance": _clip(row["acceptance"] or "", MAX_FIELD_CHARS),
                     "spec_path": row["spec_path"]},
            "spec": (doc.get("content") or "")[:MAX_SPEC_CHARS],
            "drift": [{"touched": r["touched"], "declared": r["declared"],
                      "outside": r["outside"], "source": r["source"]} for r in own_records],
        }
        messages = [{"role": "system", "content": EXTRACT_PROMPT},
                   {"role": "user", "content": util.json_dumps(user_payload)}]
        data = complete_json(messages, EXTRACT_SCHEMA, name="swarm_rescope_extract",
                             cfg_settings=cfg_settings, opener=opener, runner=runner,
                             timeout=timeout)
        extracted += 1

        read_scope_raw = data.get("read_scope") if isinstance(data, dict) else None
        if not isinstance(read_scope_raw, list):
            read_scope_raw = []
        write_scope_raw = data.get("write_scope") if isinstance(data, dict) else None
        if not isinstance(write_scope_raw, list):
            write_scope_raw = []
        outside_own: list[str] = []
        for r in own_records:
            for f in r["outside"]:
                if f not in outside_own:
                    outside_own.append(f)
        write_scope_raw = list(write_scope_raw) + [f for f in outside_own
                                                    if f not in write_scope_raw]

        try:
            read_scope_norm = scope_mod.normalize_scope(read_scope_raw, field="read_scope")
            write_scope_norm = scope_mod.normalize_scope(write_scope_raw, field="write_scope")
        except errors.BadArgument as exc:
            invalid[tid] = str(exc)
            continue

        summary_raw = data.get("summary") if isinstance(data, dict) else None
        summary = summary_raw[:MAX_SUMMARY_CHARS] if isinstance(summary_raw, str) else ""

        if not write_scope_norm:
            unscoped.append(tid)
            unscoped_summary[tid] = summary
            continue

        current_read = store_helpers_mod.json_list(row["read_scope"])
        current_write = store_helpers_mod.json_list(row["write_scope"])
        new[tid] = {"read_scope": read_scope_norm, "write_scope": write_scope_norm,
                   "summary": summary}
        changed[tid] = (read_scope_norm != current_read or write_scope_norm != current_write)

    # --- вид каждой задачи для графа и ответа: из `new`, иначе с карточки ----------

    view: dict[str, dict] = {}
    for tid in ids:
        row = by_id[tid]
        if tid in new:
            view[tid] = {"read_scope": new[tid]["read_scope"],
                        "write_scope": new[tid]["write_scope"],
                        "summary": new[tid]["summary"], "source": "spec"}
        else:
            summary = unscoped_summary.get(tid) or _clip(row["description"] or "",
                                                          MAX_SUMMARY_CHARS)
            view[tid] = {"read_scope": store_helpers_mod.json_list(row["read_scope"]),
                        "write_scope": store_helpers_mod.json_list(row["write_scope"]),
                        "summary": summary, "source": "card"}

    # --- фаза 2: граф ---------------------------------------------------------------

    tasks_graph: dict = {tid: {"depends_on": [], "reason": ""} for tid in ids}
    edges: list[list[str]] = []
    dropped: list[dict] = []
    cycles: list[list[str]] = []
    attempts = 0

    if db_cycles:
        cycles, cycles_from = db_cycles, "db"
    elif not ids:
        cycles_from = None
    else:
        payload_tasks = [{"id": tid, "title": by_id[tid]["title"],
                          "summary": view[tid]["summary"],
                          "read_scope": view[tid]["read_scope"],
                          "write_scope": view[tid]["write_scope"]} for tid in ids]
        drift_payload = [{"task": r["task"], "declared": r["declared"],
                          "outside": r["outside"], "source": r["source"]}
                         for r in records[-MAX_DRIFT_RECORDS:]]
        payload = {"project": project, "tasks": payload_tasks,
                  "fixed": _group_by_later(fixed, ids),
                  "previous": _group_by_later(previous, ids), "drift": drift_payload}
        messages = [{"role": "system", "content": GRAPH_PROMPT},
                   {"role": "user", "content": util.json_dumps(payload)}]
        total_chars = sum(len(m["content"]) for m in messages)
        if total_chars > MAX_TOTAL_CHARS:
            raise errors.BadArgument(
                f"слишком много текста для одного вызова модели: {total_chars} "
                "символов — ограничь --task")
        tasks_graph, edges, dropped, cycles, attempts = _graph_pass(
            messages, PLAN_SCHEMA, name="swarm_rescope_graph", ids=ids, fixed=fixed,
            cfg_settings=cfg_settings, opener=opener, runner=runner, timeout=timeout)
        cycles_from = "model" if cycles else None

    # --- запись (только с `apply`) ---------------------------------------------------

    applied = None
    if apply:
        scopes_applied: list[str] = []
        for tid in ids:
            if tid in new and changed.get(tid):
                store.update_task(conn, tid, actor=SWARM_AUTHOR,
                                  note="rescope: области из ТЗ",
                                  read_scope=new[tid]["read_scope"],
                                  write_scope=new[tid]["write_scope"])
                scopes_applied.append(tid)
        edges_applied = None if cycles else deps.apply_planned_blocks(
            conn, working=ids, edges=edges)
        applied = {"scopes": scopes_applied, "edges": edges_applied}

    # --- метрика качества ТЗ ---------------------------------------------------------

    tasks_total = deps._fetch(
        conn, "SELECT COUNT(*) AS n FROM tasks WHERE project = ? AND archived = 0",
        (project,))[0]["n"]
    tasks_with_drift = len({r["task"] for r in records})
    outside_files = len({f for r in records for f in r["outside"]})
    ratio = round(tasks_with_drift / tasks_total, 2) if tasks_total else 0.0
    drift_out = {"records": len(records), "ignored": ignored,
                "tasks_with_drift": tasks_with_drift, "tasks_total": tasks_total,
                "outside_files": outside_files, "ratio": ratio}

    # --- ответ -------------------------------------------------------------------

    tasks_out = {}
    for tid in ids:
        graph_info = tasks_graph.get(tid, {})
        tasks_out[tid] = {"title": by_id[tid]["title"],
                          "read_scope": view[tid]["read_scope"],
                          "write_scope": view[tid]["write_scope"],
                          "summary": view[tid]["summary"], "source": view[tid]["source"],
                          "changed": changed.get(tid, False),
                          "depends_on": graph_info.get("depends_on", []),
                          "reason": graph_info.get("reason", "")}

    return {"project": project, "model": cfg_settings["model"], "extracted": extracted,
            "attempts": attempts, "tasks": tasks_out, "unspecced": unspecced,
            "unscoped": unscoped, "invalid": invalid, "drift": drift_out, "edges": edges,
            "fixed": fixed, "previous": previous, "dropped": dropped, "cycles": cycles,
            "cycles_from": cycles_from, "applied": applied}
