"""Справочник скилов конвейеров `plugins/feature-pipeline/skills/*` (шаг listik-8jgz, порция c).

Скилы дают заголовок (`name`) и подсказку (`description`) для маршрутов
`kind=pipeline`, но не роли: роли живут только в таблице `routes`
(:mod:`listik.routes_store`) и меняются только ввозом файла — см.
`GET /api/routes/sync` и критичный инвариант шага. Установленный (не собранный
из исходников) Listik может быть без каталога `plugins/` вовсе: тогда сверка
обязана отдавать пустые расхождения и не прятать ни один маршрут — все функции
здесь на такой случай отвечают пустыми, а не бросают исключение.

Примитивный разбор YAML-заголовка `SKILL.md` (строки между двумя `---`,
`ключ: значение`, кавычки снимаются) — тащить YAML-парсер в stdlib-проект
нельзя, а частота файлов и простота содержимого этого не требуют.
"""
from __future__ import annotations

from . import paths

SKILLS_DIR = paths.ROOT_DIR / "plugins" / "feature-pipeline" / "skills"

#: Куда обрезается `hint` (первое предложение `description`) — описания скилов
#: очень длинные, а справочник маршрутов должен помещаться строкой.
HINT_MAX_CHARS = 120


def _parse_frontmatter(text: str) -> dict[str, str]:
    """YAML-заголовок SKILL.md примитивно: `ключ: значение` между `---`/`---`."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def _first_sentence(text: str, *, max_chars: int = HINT_MAX_CHARS) -> str:
    """Первое предложение, обрезанное по границе слова до `max_chars` с многоточием."""
    text = (text or "").strip()
    if not text:
        return ""
    for sep in (". ", "! ", "? "):
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx + 1].strip()
            break
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(",.;:· ") + "…"


def skill_keys() -> list[str]:
    """Ключи скилов — имена подкаталогов с `SKILL.md`; нет каталога — пустой список."""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p.name for p in SKILLS_DIR.iterdir()
                 if p.is_dir() and (p / "SKILL.md").is_file())


def skills_available() -> bool:
    """Каталог скилов есть и в нём хотя бы один скил."""
    return bool(skill_keys())


def skill_info(key: str) -> dict | None:
    """`{"key", "title", "hint", "skill_path"}` по ключу скила; нет скила — `None`.

    `title` — поле `name` из frontmatter (нет — сам ключ), `hint` — первое
    предложение `description`, `skill_path` — путь к `SKILL.md` относительно
    корня репозитория.
    """
    md_path = SKILLS_DIR / key / "SKILL.md"
    if not md_path.is_file():
        return None
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return None
    front = _parse_frontmatter(text)
    title = front.get("name") or key
    hint = _first_sentence(front.get("description", ""))
    rel = md_path.relative_to(paths.ROOT_DIR)
    return {"key": key, "title": title, "hint": hint, "skill_path": str(rel)}


# ------------------------------------------------- запускаторы (каталог ролей)

PLUGINS_DIR = paths.ROOT_DIR / "plugins"

#: Вендор по умолчанию для роли, запускаемой скилом этого плагина. Многоканальные
#: запускаторы (`pi`, `opencode`) дают канал параметром `params.channel`, поэтому их
#: вендор здесь — только значение по умолчанию: в маршруте `provider` ставит автор.
LAUNCHER_PROVIDERS: dict[str, str] = {
    "dsh": "deepseek",
    "grok": "grok",
    "codex": "openai",
    "opencode": "glm",
    "pi": "glm",
    "devin": "devin",
}


#: Запускаторы, живущие вне этого репозитория: плагин grok ставится отдельно
#: (`~/.claude/plugins`), каталога в `plugins/` у него нет, но ссылаться на него из
#: расклада ролей маршрута можно — поэтому он есть в каталоге с `skill_path: None`.
EXTERNAL_LAUNCHERS: dict[str, dict] = {
    "grok:delegate": {"title": "delegate", "hint": "Grok Build CLI: задача уходит в grok",
                      "provider": "grok"},
}


def _is_launcher(name: str) -> bool:
    """Каталог скила — запускатор: `delegate` или `*-delegate`."""
    return name == "delegate" or name.endswith("-delegate")


def _launcher_md(plugin: str, skill: str):
    return PLUGINS_DIR / plugin / "skills" / skill / "SKILL.md"


def launcher_keys() -> list[str]:
    """Ключи `плагин:скил` всех скилов-запускаторов; нет каталога `plugins/` — пусто."""
    out: list[str] = []
    try:
        plugins = sorted(p for p in PLUGINS_DIR.iterdir() if p.is_dir())
    except OSError:
        return []
    for plugin in plugins:
        try:
            candidates = sorted(s for s in (plugin / "skills").iterdir() if s.is_dir())
        except OSError:
            continue
        for skill in candidates:
            if _is_launcher(skill.name) and (skill / "SKILL.md").is_file():
                out.append(f"{plugin.name}:{skill.name}")
    out.extend(key for key in EXTERNAL_LAUNCHERS if key not in out)
    return sorted(out)


def launchers_available() -> bool:
    """Есть хотя бы один скил-запускатор."""
    return bool(launcher_keys())


def launcher_info(key: str) -> dict | None:
    """`{"key","plugin","skill","title","hint","provider","skill_path"}` или `None`.

    `None` — когда в ключе не ровно одно `":"` либо `SKILL.md` нет/не читается.
    `title` — `name` из frontmatter (нет — имя каталога скила), `hint` — первое
    предложение `description` (нет — пустая строка), `provider` — из
    `LAUNCHER_PROVIDERS` (плагина нет в словаре — `None`).
    """
    if not isinstance(key, str) or key.count(":") != 1:
        return None
    plugin, skill = key.split(":", 1)
    if not plugin or not skill:
        return None
    md_path = _launcher_md(plugin, skill)
    if not md_path.is_file():
        external = EXTERNAL_LAUNCHERS.get(key)
        if external is None:
            return None
        return {"key": key, "plugin": plugin, "skill": skill, "title": external["title"],
                "hint": external["hint"], "provider": external["provider"],
                "skill_path": None}
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return None
    front = _parse_frontmatter(text)
    return {
        "key": key,
        "plugin": plugin,
        "skill": skill,
        "title": front.get("name") or skill,
        "hint": _first_sentence(front.get("description", "")),
        "provider": LAUNCHER_PROVIDERS.get(plugin),
        "skill_path": str(md_path.relative_to(paths.ROOT_DIR)),
    }


def launchers() -> list[dict]:
    """Каталог запускаторов в порядке ключей; пропавший на ходу скил пропускается."""
    out: list[dict] = []
    for key in launcher_keys():
        info = launcher_info(key)
        if info is not None:
            out.append(info)
    return out
