"""Голосовой ввод задачи: расшифровка записи (Deepgram) и черновик (DeepSeek).

Доска не ходит к провайдерам напрямую: она зовёт серверные эндпоинты
`POST /api/assistant/transcribe` и `POST /api/assistant/draft`, а уже сервер
читает ключи из `config.toml` (разделы `[deepgram]`: `api_key`, `base_url`,
`model`, `language` и `[assistant]`) и делает запросы сам. Ключи никогда не
покидают сервер.

`transcribe` отправляет сырые байты записи в Deepgram (`POST
<base_url>/v1/listen`, `Authorization: Token <api_key>`) и возвращает
`{"transcript", "model"}`; пустая расшифровка (тишина) ошибкой не считается.

`draft` отдаёт DeepSeek рассказ человека, список неархивных проектов и видимые
маршруты из базы, а получает черновик задачи
`{project, type, title, description, acceptance, route}`: все шесть ключей есть
всегда, а поле, не прошедшее проверку, становится `null`. Черновик ничего не
создаёт — задачу заводит человек через форму.

Сеть в тестах не трогается: `transcribe` и `draft` принимают `opener` (по
умолчанию `urllib.request.urlopen`), поэтому HTTP к провайдерам подменяется
моком. Тело ответа провайдера в ошибку для клиента не проксируется — оно
пишется только в лог сервера (`log_upstream`), с замаскированным ключом.
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request

from . import assistant as assistant_mod
from . import errors as errors_mod
from . import util

DEFAULT_BASE_URL = "https://api.deepgram.com"
DEFAULT_MODEL = "nova-2"
DEFAULT_LANGUAGE = "ru"
#: Таймаут одного запроса: человек ждёт расшифровку прямо у формы.
TIMEOUT = 60.0
#: Сколько байт аудио принимаем; больше — 400 (в base64 это ~13,7 МБ).
MAX_AUDIO_BYTES = 10 * 1024 * 1024
#: Лимит рассказа для черновика: длинную запись человек режет сам.
MAX_TEXT_CHARS = 8000
#: Сколько проектов максимум уходит в промпт (у маршрутов свой лимит в assistant).
MAX_PROJECTS = 100
#: Типы задач, которые принимает трекер.
TYPES = ("epic", "task", "bug")

logger = logging.getLogger("listik.voice")

DRAFT_SYSTEM_PROMPT = """\
Ты — помощник при создании задач в трекере Listik. Человек наговорил голосом,
что нужно сделать. Разбери рассказ и верни черновик задачи.

Ответь строго одним JSON-объектом, без markdown и пояснений:
{
  "project": "slug проекта из списка projects или null",
  "type": "epic|task|bug или null",
  "title": "короткий заголовок задачи или null",
  "description": "описание · ТЗ или null",
  "acceptance": ["проверяемый критерий приёмки"],
  "route": {"key": "ключ маршрута из списка routes или null", "reason": "почему"}
}

Правила:
- пиши по-русски, кратко и по делу;
- "project" — только точный slug из списка "projects"; не подходит ни один — null;
- "type" — только "epic", "task" или "bug";
- "route.key" — только из маршрутов списка "routes"; если ни один не подходит,
  верни null;
- поле, которое не следует из рассказа, верни как null и не выдумывай его;
- "acceptance" — от 0 до 10 проверяемых критериев, каждый одной строкой, без нумерации;
- ничего не создавай: это черновик для человека, задачу заведёт он сам.
"""


def settings(cfg: dict | None = None) -> dict:
    """Настройки расшифровки из `[deepgram]`; пустые поля — значения по умолчанию.

    Раздел пользовательский (как `[assistant]`): в `config.DEFAULTS` его нет,
    Listik его не создаёт и не перезаписывает, а дефолты живут здесь.
    """
    cfg = cfg if cfg is not None else util.load_config()
    section = cfg.get("deepgram") or {}
    if not isinstance(section, dict):
        section = {}
    api_key = section.get("api_key")
    base_url = section.get("base_url")
    model = section.get("model")
    language = section.get("language")
    return {
        "api_key": api_key.strip() if isinstance(api_key, str) else "",
        "base_url": (base_url.strip().rstrip("/") if isinstance(base_url, str) and base_url.strip()
                     else DEFAULT_BASE_URL),
        "model": (model.strip() if isinstance(model, str) and model.strip() else DEFAULT_MODEL),
        "language": (language.strip() if isinstance(language, str) and language.strip()
                     else DEFAULT_LANGUAGE),
    }


def available(cfg: dict | None = None) -> bool:
    """Голос включён доске: непусты оба ключа — `[deepgram]` и `[assistant]`."""
    cfg = cfg if cfg is not None else util.load_config()
    return bool(settings(cfg)["api_key"]) and bool(assistant_mod.settings(cfg)["api_key"])


def listen_url(cfg_settings: dict) -> str:
    """URL Deepgram с параметрами распознавания: модель, язык, пунктуация."""
    query = urllib.parse.urlencode({
        "model": cfg_settings["model"],
        "language": cfg_settings["language"],
        "smart_format": "true",
        "punctuate": "true",
    })
    return f"{cfg_settings['base_url']}/v1/listen?{query}"


def transcribe(audio: bytes, mime: str, *, cfg: dict | None = None, opener=None,
               timeout: float = TIMEOUT) -> dict:
    """Расшифровать запись через Deepgram. Возвращает `{"transcript", "model"}`.

    `opener` — шов для тестов, как у `assistant.chat`: по умолчанию
    `urllib.request.urlopen`, мок подменяет HTTP целиком.
    """
    cfg_settings = settings(cfg)
    if not cfg_settings["api_key"]:
        raise assistant_mod.AssistantError(
            "распознавание речи не настроено: добавьте api_key в config.toml, "
            "раздел [deepgram]",
            status=503, code=errors_mod.SERVER_ERROR)
    if not isinstance(audio, (bytes, bytearray)) or not audio:
        raise assistant_mod.AssistantError("пустая запись", status=400,
                                           code=errors_mod.BAD_ARGUMENT)
    if len(audio) > MAX_AUDIO_BYTES:
        raise assistant_mod.AssistantError("запись длиннее допустимого (10 МБ)", status=400,
                                           code=errors_mod.BAD_ARGUMENT)
    # Параметры после `;` (codecs=opus) — часть типа записи: уходят как есть.
    content_type = mime.strip() if isinstance(mime, str) else ""
    if not content_type.startswith("audio/"):
        raise assistant_mod.AssistantError(
            "запись не похожа на аудио: mime должен начинаться с audio/",
            status=400, code=errors_mod.BAD_ARGUMENT)

    request = urllib.request.Request(
        listen_url(cfg_settings), data=bytes(audio),
        headers={
            "Authorization": f"Token {cfg_settings['api_key']}",
            "Content-Type": content_type,
        },
        method="POST")
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — тело ошибки уже не важно
            detail = ""
        assistant_mod.log_upstream(f"Deepgram ответил HTTP {exc.code}", detail,
                                   cfg_settings["api_key"], target=logger)
        if exc.code in (401, 403):
            message = ("Deepgram отклонил ключ (HTTP %d): проверьте [deepgram].api_key "
                       "в config.toml" % exc.code)
        else:
            message = f"Deepgram ответил ошибкой HTTP {exc.code}"
        raise assistant_mod.AssistantError(message, status=502) from exc
    except urllib.error.URLError as exc:
        raise assistant_mod.AssistantError(
            f"Deepgram недоступен ({cfg_settings['base_url']}): {exc.reason}",
            status=504) from exc
    except TimeoutError as exc:
        raise assistant_mod.AssistantError(
            f"Deepgram не ответил за {timeout:.0f} с ({cfg_settings['base_url']})",
            status=504) from exc

    try:
        data = util.json_loads(raw)
    except util.JSONDecodeError as exc:
        assistant_mod.log_upstream("ответ Deepgram не JSON", raw, cfg_settings["api_key"],
                                   target=logger)
        raise assistant_mod.AssistantError("ответ Deepgram не JSON", status=502) from exc

    transcript = None
    try:
        value = data["results"]["channels"][0]["alternatives"][0]["transcript"]
        if isinstance(value, str):
            transcript = value
    except (KeyError, IndexError, TypeError):
        pass
    if transcript is None:
        assistant_mod.log_upstream(
            "в ответе Deepgram нет results.channels[0].alternatives[0].transcript", raw,
            cfg_settings["api_key"], target=logger)
        raise assistant_mod.AssistantError(
            "в ответе Deepgram нет results.channels[0].alternatives[0].transcript",
            status=502)
    return {"transcript": transcript.strip(), "model": cfg_settings["model"]}


def _clean_projects(projects) -> list[dict]:
    """Проекты для промпта: `{slug, title}`, по slug по возрастанию, первые 100."""
    out: list[dict] = []
    for item in projects or []:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        title = item.get("title")
        out.append({"slug": slug.strip(),
                    "title": title.strip() if isinstance(title, str) else ""})
    out.sort(key=lambda project: project["slug"])
    return out[:MAX_PROJECTS]


def _clean_acceptance(raw) -> list[str] | None:
    """Критерии приёмки: непустые строки без дублей и маркеров, первые 10."""
    if not isinstance(raw, list):
        return None
    items: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        line = item.strip().lstrip("-•*").strip()
        if not line or line in seen:
            continue
        seen.add(line)
        items.append(line)
        if len(items) >= assistant_mod.MAX_ACCEPTANCE_ITEMS:
            break
    return items or None


def _normalize_draft(data: dict, projects: list[dict], routes: list[dict]) -> dict:
    """Привести ответ модели к черновику: всё непрошедшее правило — `null`."""
    slugs = {project["slug"] for project in projects}
    raw_project = data.get("project")
    # Сверка точная: `slug` проекта — ключ, а не текст для переформулировки.
    project = raw_project if isinstance(raw_project, str) and raw_project in slugs else None

    raw_type = data.get("type")
    draft_type = raw_type.strip().lower() if isinstance(raw_type, str) else None
    if draft_type not in TYPES:
        draft_type = None

    raw_title = data.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    raw_description = data.get("description")
    description = raw_description.strip() if isinstance(raw_description, str) else ""

    route = None
    raw_route = data.get("route")
    if isinstance(raw_route, dict):
        key = raw_route.get("key")
        if isinstance(key, str):
            found = next((item for item in routes if item.get("key") == key.strip()), None)
            if found is not None:
                reason = raw_route.get("reason")
                route = {
                    "key": found["key"],
                    "kind": found.get("kind"),
                    "title": found.get("title"),
                    "hint": found.get("hint") or "",
                    "reason": reason.strip() if isinstance(reason, str) else "",
                }

    return {
        "project": project,
        "type": draft_type,
        "title": title or None,
        "description": description or None,
        "acceptance": _clean_acceptance(data.get("acceptance")),
        "route": route,
    }


def draft(text: str, *, projects: list[dict], cfg: dict | None = None,
          routes: list[dict] | None = None, opener=None, timeout: float = TIMEOUT) -> dict:
    """Собрать черновик задачи из рассказа. Ничего не создаёт.

    `projects` — неархивные проекты (`{slug, title}`, обычно
    `store.list_projects`); `routes` — записи маршрутов из базы (их передаёт
    вызывающий). Модель выбирает только из этих списков.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    clean_text = text.strip()
    if not clean_text:
        raise assistant_mod.AssistantError("нечего разбирать: запись пустая", status=400,
                                           code=errors_mod.BAD_ARGUMENT)
    if len(clean_text) > MAX_TEXT_CHARS:
        raise assistant_mod.AssistantError(
            f"рассказ длиннее {MAX_TEXT_CHARS} символов — разбейте его на части",
            status=400, code=errors_mod.BAD_ARGUMENT)

    cfg_settings = assistant_mod.settings(cfg)
    if not cfg_settings["api_key"]:
        raise assistant_mod.AssistantError(
            "помощник не настроен: добавьте api_key в config.toml, раздел [assistant]",
            status=503, code=errors_mod.SERVER_ERROR)

    clean_projects = _clean_projects(projects)
    candidates = assistant_mod.route_candidates(routes)
    messages = [
        {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
        {"role": "user", "content": util.json_dumps(
            {"text": clean_text, "projects": clean_projects, "routes": candidates})},
    ]
    content = assistant_mod.chat(messages, cfg_settings, opener=opener, timeout=timeout)
    data = assistant_mod.parse_suggestion(content)
    return {
        "model": cfg_settings["model"],
        "draft": _normalize_draft(data, clean_projects, candidates),
    }
