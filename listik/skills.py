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
