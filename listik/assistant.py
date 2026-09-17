"""Помощник DeepSeek при создании задачи.

Доска не ходит в DeepSeek напрямую: она зовёт серверный эндпоинт
`POST /api/assistant/suggest`, а уже сервер читает ключ из `config.toml`
(раздел `[assistant]`: `api_key`, `base_url`, `model`) и делает запрос к
OpenAI-совместимому `chat/completions`. Ключ никогда не покидает сервер.

Помощник получает текст одного поля формы (заголовок, описание, приёмка,
`spec_path`) и контекст карточки, а возвращает строго JSON-объект:

    {
      "text": "<переписанный текст поля>",
      "acceptance": ["<критерий, которого нет в текущей приёмке>", ...],
      "complexity": {"level": "low|medium|high", "reason": "<почему>"},
      "route": {"key": "<ключ маршрута из routes.json>", "reason": "<почему>"}
    }

Ответ модели нормализуется: `acceptance` — список непустых строк без дублей,
`complexity.level` — только из `COMPLEXITY_LEVELS`, `route.key` — только из
видимых записей `GET /api/routes` (чужой ключ молча отбрасывается, чтобы доска
не выбирала маршрут, которого нет). Любая ошибка — `AssistantError` с HTTP-статусом
и машинным кодом из `errors.py`; сервер переносит их в тело ответа как есть.

Сеть в тестах не трогается: `chat` принимает `opener` (по умолчанию
`urllib.request.urlopen`), поэтому HTTP к DeepSeek подменяется моком.

Тело ответа DeepSeek в ошибку для клиента не проксируется: при 401/403 провайдер
может эхоить присланный `api_key`, а сообщение `AssistantError` уходит в браузер и
на доску. Тело (обрезанное и без ключа) пишется только в лог сервера.
"""
from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request

from . import errors as errors_mod
from . import util

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
#: Таймаут одного запроса к DeepSeek: помощник в форме, человек ждёт ответа.
TIMEOUT = 60.0

#: Поля формы создания задачи, которые умеет обслуживать помощник.
FIELDS = ("title", "description", "acceptance", "spec_path")
#: Уровни когнитивной сложности: только они доезжают до доски.
COMPLEXITY_LEVELS = ("low", "medium", "high")
#: Ключи контекста карточки, которые уходят в промпт (лишние отбрасываются).
CONTEXT_KEYS = ("type", "priority", "project", "title", "description", "acceptance",
                "spec_path")

#: Лимиты промпта: форма не должна отправлять в DeepSeek простыню.
MAX_TEXT_CHARS = 4000
MAX_CONTEXT_CHARS = 2000
#: Общий предохранитель: 24k символов ≈ 8k токенов — с запасом меньше контекста модели.
MAX_TOTAL_CHARS = 24000
MAX_ACCEPTANCE_ITEMS = 10
#: Сколько маршрутов максимум показываем модели (routes.json обычно короче).
MAX_ROUTES = 40
#: Сколько символов тела ошибки DeepSeek писать в лог сервера (в ответ — нисколько).
MAX_LOGGED_ERROR_CHARS = 500

logger = logging.getLogger("listik.assistant")

SYSTEM_PROMPT = """\
Ты — помощник при создании задач в трекере Listik. Тебе дают одно поле формы
(заголовок, описание · ТЗ, критерии приёмки или путь к ТЗ) и контекст карточки.
Перепиши поле яснее и конкретнее и предложи то, чего не хватает.

Ответь строго одним JSON-объектом, без markdown и пояснений:
{
  "text": "переписанный текст того поля, которое просили",
  "acceptance": ["критерий приёмки, которого нет в текущей приёмке"],
  "complexity": {"level": "low|medium|high", "reason": "почему такая сложность"},
  "route": {"key": "ключ маршрута из списка routes или null", "reason": "почему"}
}

Правила:
- пиши по-русски, кратко и по делу;
- "text" не выдумывает новых требований: формулирует то, что уже есть в контексте;
- "acceptance" — от 0 до 5 проверяемых критериев, каждый одной строкой, без нумерации;
  уже перечисленные в приёмке критерии не повторяй;
- "complexity" — когнитивная сложность для исполнителя: low (механическая работа),
  medium (понятная задача на несколько шагов), high (много контекста и развилок);
- "route.key" — только из маршрутов списка "routes"; если ни один не подходит,
  верни null;
- если поле пустое, предложи формулировку по контексту карточки.
"""


class AssistantError(Exception):
    """Ошибка помощника: текст по-русски + HTTP-статус и машинный код для сервера."""

    def __init__(self, message: str, *, status: int = 502,
                 code: str = errors_mod.SERVER_ERROR):
        super().__init__(message)
        self.message = str(message)
        self.status = status
        self.code = code


def settings(cfg: dict | None = None) -> dict:
    """Настройки помощника из `[assistant]`; значения по умолчанию — для пустых полей."""
    cfg = cfg if cfg is not None else util.load_config()
    section = cfg.get("assistant") or {}
    if not isinstance(section, dict):
        section = {}
    base_url = section.get("base_url")
    model = section.get("model")
    api_key = section.get("api_key")
    return {
        "api_key": api_key.strip() if isinstance(api_key, str) else "",
        "base_url": (base_url.strip().rstrip("/") if isinstance(base_url, str) and base_url.strip()
                     else DEFAULT_BASE_URL),
        "model": (model.strip() if isinstance(model, str) and model.strip() else DEFAULT_MODEL),
    }


def status(cfg: dict | None = None) -> dict:
    """Состояние помощника для доски: `enabled=false` — кнопки прячутся.

    Ключ наружу не отдаётся ни в каком виде — только факт его наличия.
    """
    cfg_settings = settings(cfg)
    return {
        "enabled": bool(cfg_settings["api_key"]),
        "model": cfg_settings["model"],
        "base_url": cfg_settings["base_url"],
    }


def endpoint(base_url: str) -> str:
    """URL chat/completions: `base_url` можно задать и с `/v1`, и без него."""
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _clip(value: str, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def log_upstream(what: str, body: str, api_key: str = "",
                 target: logging.Logger | None = None) -> None:
    """Тело ответа провайдера — только в лог сервера, обрезанное и без api_key.

    Ответ апстрима никогда не попадает в `AssistantError.message`: сообщение
    видит браузер (и доска), а при 401/403 провайдер может эхоить наш ключ.
    `target` — логгер другого модуля (расшифровка речи пишет в `listik.voice`).
    """
    text = body if isinstance(body, str) else str(body)
    if api_key:
        text = text.replace(api_key, "***")
    (target or logger).warning("%s: %s", what, _clip(text, MAX_LOGGED_ERROR_CHARS))


def _clean_context(context) -> dict:
    """Контекст карточки: только известные ключи, каждый — обрезанная строка."""
    if not isinstance(context, dict):
        return {}
    out: dict = {}
    for key in CONTEXT_KEYS:
        value = context.get(key)
        if value is None:
            continue
        if key == "priority":
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                continue
            continue
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text:
            out[key] = _clip(text, MAX_CONTEXT_CHARS)
    return out


def route_candidates(routes: list[dict] | None = None) -> list[dict]:
    """Видимые записи маршрутов в виде, который уходит модели.

    `routes` — записи из базы (`routes_store.list_routes`); их передаёт вызывающий
    (сервер — из своего соединения). `routes=None` — пустой список: скрытые записи
    не предлагаются.
    """
    out: list[dict] = []
    for record in routes or []:
        if not isinstance(record, dict) or not record.get("visible", False):
            continue
        item: dict = {
            "key": record.get("key"),
            "kind": record.get("kind"),
            "title": record.get("title"),
            "hint": record.get("hint") or "",
        }
        roles = record.get("roles")
        if isinstance(roles, dict):
            item["roles"] = {role: cell.get("provider") for role, cell in roles.items()
                             if isinstance(cell, dict)}
        harness = record.get("harness")
        if harness:
            item["harness"] = harness
        out.append(item)
        if len(out) >= MAX_ROUTES:
            break
    return out


def _messages(field: str, text: str, context: dict, routes: list[dict]) -> list[dict]:
    payload = {
        "field": field,
        "text": _clip(text, MAX_TEXT_CHARS),
        "context": context,
        "routes": routes,
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": util.json_dumps(payload)},
    ]


def chat(messages: list[dict], cfg_settings: dict, *, opener=None,
         timeout: float = TIMEOUT) -> str:
    """Один запрос к DeepSeek; возвращает текст ответа модели.

    `opener` — шов для тестов: по умолчанию `urllib.request.urlopen`, мок
    подменяет HTTP целиком, реальная сеть в тестах не участвует.
    """
    payload = {
        "model": cfg_settings["model"],
        "messages": messages,
        "temperature": 0.2,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {cfg_settings['api_key']}",
    }
    url = endpoint(cfg_settings["base_url"])
    request = urllib.request.Request(
        url, data=util.json_dumps(payload).encode("utf-8"),
        headers=headers, method="POST")
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — тело ошибки уже не важно
            detail = ""
        log_upstream(f"DeepSeek ответил HTTP {exc.code}", detail,
                     cfg_settings["api_key"])
        if exc.code in (401, 403):
            message = ("DeepSeek отклонил ключ (HTTP %d): проверьте [assistant].api_key "
                       "в config.toml" % exc.code)
        else:
            message = f"DeepSeek ответил ошибкой HTTP {exc.code}"
        raise AssistantError(message, status=502) from exc
    except urllib.error.URLError as exc:
        raise AssistantError(
            f"DeepSeek недоступен ({cfg_settings['base_url']}): {exc.reason}",
            status=504) from exc
    except TimeoutError as exc:
        raise AssistantError(
            f"DeepSeek не ответил за {timeout:.0f} с ({cfg_settings['base_url']})",
            status=504) from exc

    try:
        data = util.json_loads(raw)
    except util.JSONDecodeError as exc:
        log_upstream("ответ DeepSeek не JSON", raw, cfg_settings["api_key"])
        raise AssistantError("ответ DeepSeek не JSON", status=502) from exc
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log_upstream("в ответе DeepSeek нет choices[0].message.content", raw,
                     cfg_settings["api_key"])
        raise AssistantError(
            "в ответе DeepSeek нет choices[0].message.content", status=502) from exc


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_suggestion(content: str) -> dict:
    """Разобрать JSON-ответ модели: терпимо к ```-обёртке и тексту вокруг объекта."""
    if not isinstance(content, str):
        raise AssistantError("DeepSeek вернул ответ не строкой", status=502)
    text = content.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = util.json_loads(text)
    except util.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise AssistantError(
                f"не удалось разобрать ответ DeepSeek как JSON: {_clip(content, 200)}",
                status=502) from None
        try:
            data = util.json_loads(text[start:end + 1])
        except util.JSONDecodeError as exc:
            raise AssistantError(
                f"не удалось разобрать ответ DeepSeek как JSON: {_clip(content, 200)}",
                status=502) from exc
    if not isinstance(data, dict):
        raise AssistantError("ответ DeepSeek — не JSON-объект", status=502)
    return data


def _normalize_suggestion(data: dict, candidates: list[dict]) -> dict:
    """Привести ответ модели к форме, на которую опирается доска."""
    text = data.get("text")
    suggestion: dict = {"text": text.strip() if isinstance(text, str) else ""}

    raw_acceptance = data.get("acceptance")
    acceptance: list[str] = []
    seen: set[str] = set()
    if isinstance(raw_acceptance, list):
        for item in raw_acceptance:
            if not isinstance(item, str):
                continue
            line = item.strip().lstrip("-•*").strip()
            if not line or line in seen:
                continue
            seen.add(line)
            acceptance.append(line)
            if len(acceptance) >= MAX_ACCEPTANCE_ITEMS:
                break
    suggestion["acceptance"] = acceptance

    complexity = None
    raw_complexity = data.get("complexity")
    if isinstance(raw_complexity, dict):
        level = str(raw_complexity.get("level") or "").strip().lower()
        if level in COMPLEXITY_LEVELS:
            reason = raw_complexity.get("reason")
            complexity = {"level": level,
                          "reason": reason.strip() if isinstance(reason, str) else ""}
    suggestion["complexity"] = complexity

    route = None
    raw_route = data.get("route")
    if isinstance(raw_route, dict):
        key = raw_route.get("key")
        if isinstance(key, str):
            key = key.strip()
            found = next((item for item in candidates if item.get("key") == key), None)
            if found is not None:
                reason = raw_route.get("reason")
                route = {
                    "key": found["key"],
                    "kind": found.get("kind"),
                    "title": found.get("title"),
                    "hint": found.get("hint") or "",
                    "reason": reason.strip() if isinstance(reason, str) else "",
                }
    suggestion["route"] = route
    return suggestion


def suggest(field, text: str = "", context: dict | None = None, *,
            cfg: dict | None = None, routes: list[dict] | None = None,
            opener=None, timeout: float = TIMEOUT) -> dict:
    """Спросить DeepSeek про одно поле формы. Возвращает нормализованное предложение.

    `routes` — записи маршрутов из базы (их передаёт вызывающий); модель выбирает
    маршрут только из них, чужой ключ отбрасывается.
    """
    if field not in FIELDS:
        raise AssistantError(
            f"неизвестное поле {field!r}; допустимы: {', '.join(FIELDS)}",
            status=400, code=errors_mod.BAD_ARGUMENT)
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    clean_context = _clean_context(context)
    if not text.strip() and not clean_context:
        raise AssistantError("поле пустое и контекста нет — нечего проверять",
                             status=400, code=errors_mod.BAD_ARGUMENT)

    cfg_settings = settings(cfg)
    if not cfg_settings["api_key"]:
        raise AssistantError(
            "помощник не настроен: добавьте api_key в config.toml, раздел [assistant]",
            status=503, code=errors_mod.SERVER_ERROR)

    candidates = route_candidates(routes)
    messages = _messages(field, text, clean_context, candidates)
    total = sum(len(message["content"]) for message in messages)
    if total > MAX_TOTAL_CHARS:
        raise AssistantError("слишком длинный запрос к помощнику", status=400,
                             code=errors_mod.BAD_ARGUMENT)

    content = chat(messages, cfg_settings, opener=opener, timeout=timeout)
    data = parse_suggestion(content)
    return {
        "field": field,
        "model": cfg_settings["model"],
        "suggestion": _normalize_suggestion(data, candidates),
    }
