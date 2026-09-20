"""Валидация и нормализация областей `read_scope`/`write_scope`.

Обе области — списки относительных путей от корня проекта. `write_scope` — что
задача правит (по нему планировщик роя разводит задачи по волнам, чтобы две
задачи с пересекающимися областями записи не запускались одновременно);
`read_scope` — что задача читает (подсказка воркеру, в разведение волн не
участвует).

Каталог в списке означает всё его поддерево: пересечение областей — это один
путь, равный другому, или являющийся его префиксом по сегментам (например,
`docs/` покрывает `docs/API.md`, но не `docs-old/x`). Пересечение считается
здесь (`covers`/`scopes_intersect`); планировщик роя (swarm-2) их зовёт, а
не пересчитывает сам.
"""
from __future__ import annotations

import posixpath

from . import errors as errors_mod

#: Имена полей-областей — в этом порядке их проверяет `store.update_task`.
FIELDS = ("read_scope", "write_scope")

_FORBIDDEN_CHARS = ("*", "?", "[")


def normalize_scope(value, *, field: str) -> list[str]:
    """Проверяет и нормализует значение поля-области.

    `value` должен быть списком строк — относительных путей от корня проекта.
    Возвращает нормализованный список без дубликатов (порядок первого
    вхождения сохраняется) или поднимает `errors.BadArgument` с текстом,
    который начинается с имени поля и называет негодный элемент.

    Существование пути на диске не проверяется — задача может создавать новые
    файлы, а у проекта может не быть локального `path`.
    """
    if not isinstance(value, list):
        raise errors_mod.BadArgument(f"{field}: ожидался список строк")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise errors_mod.BadArgument(
                f"{field}: элемент {item!r} должен быть строкой")
        item = item.strip()
        if not item:
            raise errors_mod.BadArgument(f"{field}: пустой путь недопустим")
        if "\x00" in item or "\n" in item or "\r" in item:
            raise errors_mod.BadArgument(
                f"{field}: путь {item!r} содержит недопустимый символ")
        if "\\" in item:
            raise errors_mod.BadArgument(
                f"{field}: путь {item!r} использует \\ вместо / как разделитель")
        if item.startswith("/") or item.startswith("~"):
            raise errors_mod.BadArgument(
                f"{field}: путь {item!r} — абсолютный, нужен путь от корня проекта")
        if len(item) >= 2 and item[1] == ":" and item[0].isalpha():
            raise errors_mod.BadArgument(
                f"{field}: путь {item!r} — диск Windows, нужен путь от корня проекта")
        if any(ch in item for ch in _FORBIDDEN_CHARS):
            raise errors_mod.BadArgument(
                f"{field}: путь {item!r} содержит шаблон — шаблоны не поддерживаются")

        norm = posixpath.normpath(item)
        if norm == "." or norm == ".." or norm.startswith("../"):
            raise errors_mod.BadArgument(
                f"{field}: путь {item!r} выходит за корень проекта")

        if norm not in seen:
            seen.add(norm)
            normalized.append(norm)

    return normalized


def covers(entry: str, path: str) -> bool:
    """True, если `path` — сам `entry` либо лежит в его поддереве.

    Хвостовой `/` у любого из аргументов не мешает; пустая строка ничего не
    покрывает и ничем не покрывается.
    """
    entry = entry.rstrip("/")
    path = path.rstrip("/")
    if not entry or not path:
        return False
    return path == entry or path.startswith(entry + "/")


def scopes_intersect(a: list[str], b: list[str]) -> bool:
    """True, если есть пара `(x из a, y из b)`, где один покрывает другой.

    Пустой список ни с чем не пересекается — решение о том, что делать с
    пустой областью (`unscoped`), принимает вызывающий (`deps.waves`), не эта
    функция.
    """
    for x in a:
        for y in b:
            if covers(x, y) or covers(y, x):
                return True
    return False
