"""Перевод проектов на Listik: блок правил в AGENTS.md/CLAUDE.md каждого проекта.

Пока в проекте написано «используй bd», агент продолжит писать задачи в мёртвый трекер,
и на доске их не будет видно. Блок вставляется между маркерами, поэтому:

* повторный запуск обновляет только этот блок и ничего не дублирует;
* чужой текст в файле остаётся как есть;
* `--remove` убирает блок (откат перехода).

Ничего, кроме инструкций, скрипт не меняет: `.beads` и хуки проекта не трогает.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import paths

BEGIN = "<!-- BEGIN LISTIK -->"
END = "<!-- END LISTIK -->"

# Маркеры ищутся только как самостоятельная строка, а не как подстрока где угодно
# в тексте — иначе простое упоминание маркеров в прозе (например, в описании этого
# же модуля) распознаётся как настоящий блок и заменяется целиком.
_BEGIN_RE = re.compile(rf"^{re.escape(BEGIN)}$", re.MULTILINE)
_END_RE = re.compile(rf"^{re.escape(END)}$", re.MULTILINE)


def _find_block(text: str) -> tuple[int, int] | None:
    """Позиции (start, end) настоящего блока (маркеры на отдельных строках) или None."""
    m_begin = _BEGIN_RE.search(text)
    if not m_begin:
        return None
    m_end = _END_RE.search(text, m_begin.end())
    if not m_end:
        return None
    return m_begin.start(), m_end.end()

TARGETS = ("AGENTS.md", "CLAUDE.md")

INTRO = """## Трекер задач — Listik, а не beads

Задачи ведутся в Listik: `~/Projects/Listik/bin/listik`. Каталог `.beads` в проекте —
архив: его никто не обновляет, писать туда нельзя (новые задачи должны попадать на общую
доску, а не в мёртвый трекер).

Полные правила: `~/Projects/Listik/AGENTS.md`, контракт данных: `~/Projects/Listik/API.md`.

"""


def _compute_body() -> str:
    protocol_path = paths.ROOT_DIR / "docs" / "harness-protocol.md"
    if not protocol_path.exists():
        raise FileNotFoundError(
            f"docs/harness-protocol.md не найден ({protocol_path}) — "
            "блок правил без протокола ставить нельзя"
        )
    protocol = protocol_path.read_text(encoding="utf-8")
    return INTRO + protocol


def body() -> str:
    """Собирает блок для AGENTS.md/CLAUDE.md: вводный абзац + канонический протокол.

    Читает docs/harness-protocol.md в момент вызова (не на импорте), чтобы правки
    протокола подхватывались без перезапуска.
    """
    return _compute_body()


def block(body: str | None = None) -> str:
    if body is None:
        body = _compute_body()
    return f"{BEGIN}\n{body}{END}\n"


def upsert(path: Path, *, dry_run: bool = False, body: str | None = None) -> str:
    """Возвращает: added | updated | unchanged | skipped."""
    if not path.exists():
        if dry_run:
            return "skipped"
        path.write_text(block(body), encoding="utf-8")
        return "added"
    text = path.read_text(encoding="utf-8")
    new_block = block(body)
    found = _find_block(text)
    if found is not None:
        start, end = found
        if text[start:end + 1] == new_block:
            return "unchanged"
        updated = text[:start] + new_block.rstrip("\n") + text[end:]
        if not dry_run:
            path.write_text(updated, encoding="utf-8")
        return "updated"
    if dry_run:
        return "added"
    path.write_text(text.rstrip("\n") + "\n\n" + new_block, encoding="utf-8")
    return "added"


def remove(path: Path, *, dry_run: bool = False) -> str:
    if not path.exists():
        return "skipped"
    text = path.read_text(encoding="utf-8")
    found = _find_block(text)
    if found is None:
        return "skipped"
    start, end = found
    cleaned = (text[:start] + text[end:]).strip("\n") + "\n"
    if not dry_run:
        path.write_text(cleaned, encoding="utf-8")
    return "removed"


def migrate_all(project_dirs: list[Path], *, dry_run: bool = False, remove_block: bool = False,
                verbose: bool = True) -> dict:
    report = {"added": [], "updated": [], "unchanged": [], "skipped": [], "removed": []}
    for project in project_dirs:
        for name in TARGETS:
            path = project / name
            if not path.exists():
                report["skipped"].append(str(path))
                continue
            result = remove(path, dry_run=dry_run) if remove_block else upsert(path, dry_run=dry_run)
            report[result].append(str(path))
            if verbose and result in ("added", "updated", "removed"):
                print(f"  {result:9s} {path}")
    return report
