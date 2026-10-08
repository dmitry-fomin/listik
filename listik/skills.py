"""Справочник скилов конвейеров трёх плагинов пресетов (шаг listik-8jgz, порция c; listik-d9rj).

Пресеты живут в `plugins/pipeline-full`, `plugins/pipeline-cc` и `plugins/pipeline-claude`
(`PIPELINE_PLUGINS`), скил — каталог `plugins/<плагин>/skills/<имя>` с `SKILL.md`. Ключ
маршрута — имя плагина без префикса `pipeline-`, дефис и имя каталога:
`plugins/pipeline-full/skills/high` → `full-high`, `plugins/pipeline-cc/skills/sol` → `cc-sol`;
команда маршрута зовёт скил `/<плагин>:<имя>` (`skill_ref`). У записи маршрута плагин —
явное поле `plugin` (порция d): по нему `skill_of`/`skill_md` находят скил записи, а
`skill_info`/`skill_ref` без `plugin=` выводят плагин из ключа каталога.

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

#: Плагины пресетов конвейера; ключ скила — `<плагин без "pipeline-">-<каталог скила>`.
PIPELINE_PLUGINS = ("pipeline-full", "pipeline-cc", "pipeline-claude")
PIPELINE_PREFIX = "pipeline-"
#: Корень плагинов пресетов — отдельно от `PLUGINS_DIR` запускаторов: тесты подменяют их независимо.
PIPELINE_PLUGINS_DIR = paths.ROOT_DIR / "plugins"

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


def _dir_skills(directory) -> list[str]:
    """Имена подкаталогов с `SKILL.md`; нет каталога — пустой список."""
    if not directory.is_dir():
        return []
    return [p.name for p in directory.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()]


def _plugin_skills_dir(plugin: str):
    return PIPELINE_PLUGINS_DIR / plugin / "skills"


def _split_key(key: str) -> tuple[str, str] | None:
    """`(плагин, каталог скила)` по ключу `full-high`; префикс не из трёх плагинов — `None`."""
    if not isinstance(key, str):
        return None
    prefix, sep, skill = key.partition("-")
    plugin = PIPELINE_PREFIX + prefix
    if not sep or not skill or plugin not in PIPELINE_PLUGINS:
        return None
    return plugin, skill


def skill_keys() -> list[str]:
    """Отсортированные ключи `<плагин без pipeline->-<скил>` всех скилов трёх плагинов."""
    return sorted(f"{plugin[len(PIPELINE_PREFIX):]}-{name}"
                  for plugin in PIPELINE_PLUGINS
                  for name in _dir_skills(_plugin_skills_dir(plugin)))


def skills_available() -> bool:
    """Хотя бы у одного из трёх плагинов пресетов есть хотя бы один скил."""
    return any(_dir_skills(_plugin_skills_dir(plugin)) for plugin in PIPELINE_PLUGINS)


def skill_of(plugin, key) -> str | None:
    """Имя каталога скила записи с плагином `plugin`: ключ без `<плагин без pipeline->-`.

    `skill_of("pipeline-full", "full-high")` → `"high"`. Плагин не из
    `PIPELINE_PLUGINS`, ключ не начинается с его префикса или после префикса пусто —
    `None`. Файлов не читает.
    """
    if plugin not in PIPELINE_PLUGINS or not isinstance(key, str):
        return None
    prefix = plugin[len(PIPELINE_PREFIX):] + "-"
    if not key.startswith(prefix) or len(key) == len(prefix):
        return None
    return key[len(prefix):]


def skill_md(plugin: str, skill: str):
    """Путь к `SKILL.md` скила `skill` плагина `plugin` (наличие не проверяется)."""
    return _plugin_skills_dir(plugin) / skill / "SKILL.md"


def _parts(key, plugin) -> tuple[str, str] | None:
    """`(плагин, скил)`: по `plugin` записи, если он передан, иначе по префиксу ключа."""
    if plugin is None:
        return _split_key(key)
    skill = skill_of(plugin, key)
    return None if skill is None else (plugin, skill)


def skill_ref(key: str, plugin: str | None = None) -> str | None:
    """Имя скила для команды и текстов: `full-high` → `/pipeline-full:high`.

    Чистое преобразование строки: файлов не читает и наличие `SKILL.md` не
    проверяет (`cc-fake` → `/pipeline-cc:fake`). Префикс ключа не `full`/`cc`/
    `claude`, дефиса нет или после него пусто — `None`. Передан `plugin` — плагин
    берётся из него, а не из префикса ключа (`skill_of`).
    """
    parts = _parts(key, plugin)
    if parts is None:
        return None
    plugin, skill = parts
    return f"/{plugin}:{skill}"


def skill_info(key: str, plugin: str | None = None) -> dict | None:
    """`{"key", "plugin", "skill", "title", "hint", "skill_path"}` по ключу скила; нет скила — `None`.

    `plugin` — имя плагина (`pipeline-full`), `skill` — имя каталога скила,
    `title` — поле `name` из frontmatter (нет — сам ключ), короткое, без имени
    плагина; `hint` — первое предложение `description`, `skill_path` — путь к
    `SKILL.md` относительно корня репозитория. Префикс ключа не из трёх плагинов
    или нет `SKILL.md` — `None`. Передан `plugin` (поле записи маршрута) — плагин
    берётся из него, а не из префикса ключа.
    """
    parts = _parts(key, plugin)
    if parts is None:
        return None
    plugin, skill = parts
    md_path = skill_md(plugin, skill)
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
    return {"key": key, "plugin": plugin, "skill": skill, "title": title, "hint": hint,
            "skill_path": str(rel)}


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
