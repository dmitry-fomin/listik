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
from . import store_helpers as store_helpers_mod
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
                "dropped": [], "cycles": [], "cycles_from": None, "applied": None}

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
                "cycles": db_cycles, "cycles_from": "db", "applied": None}

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

    tasks_view: dict = {}
    model_edges: list[list[str]] = []
    dropped: list[dict] = []
    cycles: list[list[str]] = []
    attempts = 0
    for attempts in range(1, MAX_ATTEMPTS + 1):
        data = complete_json(messages, PLAN_SCHEMA, name="swarm_plan",
                             cfg_settings=cfg_settings, opener=opener, runner=runner,
                             timeout=timeout)
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
                "cycles_from": "model", "applied": None}

    applied = None
    if apply:
        applied = deps.apply_planned_blocks(conn, working=ids, edges=model_edges)

    return {"project": project, "stage": stage, "model": cfg_settings["model"],
            "attempts": attempts, "tasks": tasks_out, "edges": model_edges, "fixed": fixed,
            "previous": previous, "dropped": dropped, "cycles": [], "cycles_from": None,
            "applied": applied}
