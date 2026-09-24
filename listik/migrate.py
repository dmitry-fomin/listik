"""Перевод проектов на Listik: блок правил в AGENTS.md/CLAUDE.md каждого проекта.

Блок вставляется между маркерами, поэтому:

* повторный запуск обновляет только этот блок и ничего не дублирует;
* чужой текст в файле остаётся как есть;
* `--remove` убирает блок (откат перехода).

Заодно проект получает строку `.worktrees/` в своём `.gitignore`: рабочие деревья задач
конвейеры и прямые харнессы заводят в `<проект>/.worktrees/<id>`, и без этой строки они
светятся в `git status` основного дерева. Строка дописывается, только если её ещё нет;
`--remove` её не трогает — он откатывает блок протокола, а не чужие правила git.

Ещё проект получает симлинк `.agents/skills/listik` на скил из установки Listik
(`skill_source()`, через `app/current`, чтобы переживать обновления): его читают харнессы,
которые ищут скилы в `<cwd>/.agents/skills` (codex, opencode, pi, grok). Симлинк с абсолютным
путём в чужой git не нужен, поэтому рядом с ним в `.gitignore` идёт строка `.agents/skills/listik`
— только если симлинк там действительно лежит. Настоящий каталог на этом месте (репозиторий
Listik сам зарегистрирован проектом) не трогается. `--remove` снимает симлинк, но не строку.

Тело блока в AGENTS.md — протокол из `<проект>/docs/harness-protocol.md`, если такой файл
есть у проекта, иначе из `docs/harness-protocol.md` запущенной копии Listik (шаблон установки).
Проектный файл приоритетнее: установленная копия со старым протоколом иначе откатывает блок
в репозитории, где протокол свежее (инцидент 20.09.2026 — пропал абзац «Revoked authority»,
listik-e8za). Тело для CLAUDE.md — всегда `CLAUDE_BODY`.

Первая строка протокола `<!-- listik-protocol: N -->` — версия протокола (не версия Listik);
при правке протокола N увеличивают руками. Блок, чья метка больше метки ставящегося тела,
`upsert` не перезаписывает (`skipped-newer`), пока не передан `force` (`init-projects --force`);
блок без метки — версия 0.
"""
from __future__ import annotations

import os
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


_VERSION_RE = re.compile(r"^<!-- listik-protocol: (\S+) -->$", re.MULTILINE)


def protocol_version(text: str) -> int:
    """Версия протокола из строки `<!-- listik-protocol: N -->`; нет метки или N не целое — 0."""
    m = _VERSION_RE.search(text)
    if not m or not m.group(1).isdecimal():
        return 0
    return int(m.group(1))


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

#: Строка, которой рабочие деревья задач закрываются от git (см. listik-airk).
GITIGNORE_ENTRY = ".worktrees/"

#: Скил с тем же протоколом для харнессов, читающих `.agents/skills` (от корня репозитория).
SKILL_REL = ".agents/skills/listik"

# CLAUDE.md читает только Claude Code, а у него есть скил listik:listik с полным протоколом —
# туда идёт указание на скил. Внешние харнессы (dsh, grok, codex) читают AGENTS.md и скила
# не имеют, поэтому AGENTS.md получает протокол целиком.
CLAUDE_BODY = """## Listik

Задачи этого проекта ведутся в Listik. Любое действие с задачами — завести задачу, записать
проблему, найденную по ходу другой работы, задать вопрос человеку, взять, передать, перевести
этап или закрыть — делай через скил `listik:listik`: вызови его до первой команды. Не заводи
TODO в чате или в файлах вместо карточки. Скила нет — полный протокол в блоке Listik файла
`AGENTS.md` этого проекта.
"""


def body_for(name: str, project_dir: Path | None = None) -> str:
    """Тело блока для файла `name`: CLAUDE.md — указание на скил, остальные — полный протокол."""
    return CLAUDE_BODY if name == "CLAUDE.md" else _compute_body(project_dir)


def _compute_body(project_dir: Path | None = None) -> str:
    if project_dir is not None:
        project_path = project_dir / "docs" / "harness-protocol.md"
        if project_path.exists():
            return project_path.read_text(encoding="utf-8")
    protocol_path = paths.ROOT_DIR / "docs" / "harness-protocol.md"
    if not protocol_path.exists():
        project_note = (f"; проектного {project_dir / 'docs' / 'harness-protocol.md'} тоже нет"
                        if project_dir is not None else "")
        raise FileNotFoundError(
            f"docs/harness-protocol.md не найден ({protocol_path}){project_note} — "
            "блок правил без протокола ставить нельзя"
        )
    protocol = protocol_path.read_text(encoding="utf-8")
    return protocol


def body(project_dir: Path | None = None) -> str:
    """Собирает блок для AGENTS.md/CLAUDE.md: вводный абзац + канонический протокол.

    Читает docs/harness-protocol.md в момент вызова (не на импорте), чтобы правки
    протокола подхватывались без перезапуска.
    """
    return _compute_body(project_dir)


def block(body: str | None = None) -> str:
    if body is None:
        body = _compute_body()
    return f"{BEGIN}\n{body}{END}\n"


def upsert(path: Path, *, dry_run: bool = False, body: str | None = None,
           force: bool = False) -> str:
    """Возвращает: added | updated | unchanged | skipped | skipped-newer.

    `skipped-newer` — в файле блок с меткой протокола новее тела; `force` перезаписывает его.
    """
    if body is None:
        body = body_for(path.name, path.parent)
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
        if not force and protocol_version(text[start:end]) > protocol_version(body):
            return "skipped-newer"
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


def skill_source() -> Path:
    """Каталог скила в установке: через `app/current` (без `resolve()` — иначе симлинк
    после обновления Listik останется на старой версии), в dev-checkout — из репозитория."""
    current = paths.DATA_DIR / "app" / "current"
    if current.is_dir():
        return current / SKILL_REL
    return paths.ROOT_DIR / SKILL_REL


def ensure_skill_link(project_dir: Path, *, dry_run: bool = False) -> str:
    """Симлинк `<проект>/.agents/skills/listik` → `skill_source()`.

    Возвращает: `added` | `updated` (был на другой путь) | `unchanged` | `skipped`
    (нет каталога проекта, нет скила в установке или на месте настоящий каталог/файл).
    """
    source = skill_source()
    if not project_dir.is_dir() or not (source / "SKILL.md").exists():
        return "skipped"
    link = project_dir / SKILL_REL
    target = str(source)
    if os.path.islink(link):
        if os.readlink(link) == target:
            return "unchanged"
        status = "updated" if link.exists() else "added"  # битый симлинк — как новый
        if not dry_run:
            link.unlink()
            link.symlink_to(target)
        return status
    if link.exists():
        return "skipped"
    if not dry_run:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
    return "added"


def remove_skill_link(project_dir: Path, *, dry_run: bool = False) -> str:
    """Снять симлинк скила (`removed`); настоящий каталог или файл не трогается (`unchanged`)."""
    link = project_dir / SKILL_REL
    if not os.path.islink(link):
        return "unchanged"
    if not dry_run:
        link.unlink()
    return "removed"


def _norm_entry(entry: str) -> str:
    return entry.strip().lstrip("/").rstrip("/")


def _gitignore_has_entry(text: str, entry: str = GITIGNORE_ENTRY) -> bool:
    """Есть ли в этом .gitignore строка `entry` — с ведущим `/` или без, с хвостовым `/`
    или без. Комментарии и пустые строки не считаются."""
    wanted = _norm_entry(entry)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _norm_entry(line) == wanted:
            return True
    return False


def ensure_gitignore(project_dir: Path, *, dry_run: bool = False) -> str:
    """Гарантирует строки `.worktrees/` и (если в проекте лежит симлинк скила)
    `.agents/skills/listik` в `<проект>/.gitignore`.

    Возвращает: `added` — файла не было и он создан; `updated` — дописана хотя бы одна
    строка; `unchanged` — всё уже было; `skipped` — каталога проекта нет. При `dry_run`
    файл не пишется, а возвращается то, что было бы сделано.
    """
    if not project_dir.is_dir():
        return "skipped"
    entries = [GITIGNORE_ENTRY]
    # Проверка пассивная: симлинк заводит ensure_skill_link, здесь только закрываем его от git.
    if os.path.islink(project_dir / SKILL_REL):
        entries.append(SKILL_REL)
    path = project_dir / ".gitignore"
    if not path.exists():
        if not dry_run:
            path.write_text("".join(e + "\n" for e in entries), encoding="utf-8")
        return "added"
    text = path.read_text(encoding="utf-8")
    missing = [e for e in entries if not _gitignore_has_entry(text, e)]
    if not missing:
        return "unchanged"
    # Чужой текст не переписываем: только добиваем перевод строки, если файл им не кончался.
    tail = "" if text.endswith("\n") or not text else "\n"
    if not dry_run:
        path.write_text(text + tail + "".join(e + "\n" for e in missing), encoding="utf-8")
    return "updated"


def migrate_all(project_dirs: list[Path], *, dry_run: bool = False, remove_block: bool = False,
                verbose: bool = True, force: bool = False) -> dict:
    report: dict = {"added": [], "updated": [], "unchanged": [], "skipped": [], "removed": [],
                    "gitignore": [], "skill_link": [], "skipped-newer": []}
    for project in project_dirs:
        link = project / SKILL_REL
        if remove_block:
            result = remove_skill_link(project, dry_run=dry_run)
        else:
            result = ensure_skill_link(project, dry_run=dry_run)
            if (result == "skipped" and verbose and project.is_dir()
                    and not (skill_source() / "SKILL.md").exists()):
                print(f"  {'skipped':9s} {link} (в установке нет скила: {skill_source()})")
        if result in ("added", "updated", "removed"):
            report["skill_link"].append(str(link))
            if verbose:
                print(f"  {result:9s} {link}")
        # Строку в .gitignore заводим и проекту без AGENTS.md/CLAUDE.md: деревья задач
        # появляются в нём независимо от того, прописан ли уже блок протокола.
        if not remove_block:
            result = ensure_gitignore(project, dry_run=dry_run)
            if result in ("added", "updated"):
                report["gitignore"].append(str(project / ".gitignore"))
                if verbose:
                    print(f"  {result:9s} {project / '.gitignore'}")
        for name in TARGETS:
            path = project / name
            if not path.exists():
                report["skipped"].append(str(path))
                continue
            result = (remove(path, dry_run=dry_run) if remove_block
                      else upsert(path, dry_run=dry_run, force=force))
            report[result].append(str(path))
            if verbose and result in ("added", "updated", "removed"):
                print(f"  {result:9s} {path}")
            elif verbose and result == "skipped-newer":
                text = path.read_text(encoding="utf-8")
                start, end = _find_block(text)
                have = protocol_version(text[start:end])
                want = protocol_version(body_for(name, project))
                print(f"  ! пропущен  {path}: блок протокола новее шаблона ({have} > {want}), "
                      "перезапись — init-projects --force")
    return report
