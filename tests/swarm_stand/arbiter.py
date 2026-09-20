"""Заглушка арбитра конфликтов ребейза для стенда роя.

Настоящий арбитр (модель-судья, разбирающая конфликт по смыслу) пишется отдельной
порцией и живёт вне `listik/`; здесь — заглушка «обе стороны»: конфликтный блок
заменяется сначала верхней, потом нижней стороной, маркеры убираются. Никакого
вызова модели, никакой сети.
"""

from __future__ import annotations

from pathlib import Path

_TOP_MARKER = "<<<<<<< "
_MID_MARKER = "======="
_BOTTOM_MARKER = ">>>>>>> "


def resolve_both_sides(text: str) -> str | None:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    found = False
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if not line.startswith(_TOP_MARKER):
            out.append(line)
            i += 1
            continue

        found = True
        i += 1
        top: list[str] = []
        while i < n and not lines[i].startswith(_MID_MARKER):
            if lines[i].startswith(_TOP_MARKER) or lines[i].startswith(_BOTTOM_MARKER):
                return None
            top.append(lines[i])
            i += 1
        if i >= n:
            return None  # блок не закрыт: не нашли =======
        i += 1  # пропускаем строку =======

        bottom: list[str] = []
        while i < n and not lines[i].startswith(_BOTTOM_MARKER):
            if lines[i].startswith(_TOP_MARKER) or lines[i].startswith(_MID_MARKER):
                return None
            bottom.append(lines[i])
            i += 1
        if i >= n:
            return None  # блок не закрыт: не нашли >>>>>>>
        i += 1  # пропускаем строку >>>>>>>

        out.extend(top)
        out.extend(bottom)

    if not found:
        return None
    return "".join(out)


def resolve_file(path: Path) -> bool:
    text = Path(path).read_text()
    resolved = resolve_both_sides(text)
    if resolved is None:
        return False
    Path(path).write_text(resolved)
    return True
