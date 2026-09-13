"""Разовый импорт задач из старых .beads-трекеров в Listik.

Хаб больше не синхронизируется с beads: это одна миграция, после которой источник
истины — Listik. ID задач сохраняются как есть (external_ref указывает на beads),
чтобы старые ссылки в коммитах и журналах остались рабочими.

Дубликаты трекеров (один префикс в двух каталогах — например, Dif/TN-feed-redesign
и Dif/TelegramNews, оба tn-*) разрешаются один раз: побеждает каталог с большим
числом задач, проигравший помечается и его задачи не импортируются.
"""
from __future__ import annotations

import collections
import json
import sqlite3
from pathlib import Path

from . import db as db_mod
from . import paths
from . import store
from . import textutil


def discover(root: Path | None = None, max_depth: int = 6) -> list[dict]:
    """Находит .beads-проекты и читает их задачи."""
    import os

    root = Path(root or paths.PROJECTS_ROOT)
    skip = {".git", "node_modules", "vendor", ".venv", "venv", "dist", "build", ".idea"}
    found: list[Path] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root):
        depth = len(Path(dirpath).parts) - root_depth
        if depth >= max_depth:
            dirnames[:] = []
            continue
        if ".beads" in dirnames:
            found.append(Path(dirpath) / ".beads")
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]

    out: list[dict] = []
    for beads in sorted(found):
        issues_file = beads / "issues.jsonl"
        project_dir = beads.parent
        slug = str(project_dir.relative_to(root))
        entry = {"slug": slug, "beads_dir": str(beads), "path": str(project_dir),
                 "issues": [], "prefix": None, "memories": []}
        if issues_file.exists():
            for line in issues_file.open(encoding="utf-8", errors="replace"):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = obj.get("_type") or "issue"
                if kind == "issue" and obj.get("id"):
                    entry["issues"].append(obj)
                elif kind == "memory":
                    entry["memories"].append(obj)
        if entry["issues"]:
            pref = collections.Counter(i["id"].rsplit("-", 1)[0] for i in entry["issues"])
            entry["prefix"] = pref.most_common(1)[0][0]
        out.append(entry)
    return out


def resolve_duplicates(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Убирает каталоги-двойники одного трекера."""
    by_prefix: dict[str, list[dict]] = collections.defaultdict(list)
    for e in entries:
        if e["prefix"] and e["issues"]:
            by_prefix[e["prefix"]].append(e)
    skipped: list[dict] = []
    keep: list[dict] = []
    for entry in entries:
        if not entry["issues"]:
            keep.append(entry)
            continue
        rivals = by_prefix.get(entry["prefix"], [])
        if len(rivals) < 2:
            keep.append(entry)
            continue
        winner = max(rivals, key=lambda e: (len(e["issues"]), str(e["beads_dir"])))
        if entry is winner:
            keep.append(entry)
        else:
            skipped.append({
                "slug": entry["slug"], "prefix": entry["prefix"],
                "issues": len(entry["issues"]), "winner": winner["slug"],
            })
    return keep, skipped


def import_all(
    conn: sqlite3.Connection,
    *,
    root: Path | None = None,
    only: str | None = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    entries = discover(root)
    if only:
        entries = [e for e in entries if only in e["slug"]]
    keep, skipped = resolve_duplicates(entries)

    report = {"projects": 0, "tasks": 0, "comments": 0, "deps": 0, "memories": 0,
              "skipped_duplicates": skipped, "skipped_tasks": 0, "dry_run": dry_run,
              "per_project": []}

    for entry in keep:
        if not entry["issues"]:
            continue
        report["projects"] += 1
        imported = 0
        for obj in entry["issues"]:
            tid = obj["id"]
            existing = conn.execute("SELECT source FROM tasks WHERE id = ?", (tid,)).fetchone()
            if existing:
                report["skipped_tasks"] += 1
                continue
            labels = _labels(obj.get("labels"))
            stage = _stage_from(labels, obj.get("comments") or [])
            if dry_run:
                imported += 1
                continue
            conn.execute(
                """
                INSERT INTO tasks(id, project, title, description, acceptance, design, notes,
                                  result, status, stage, stage_at, priority, issue_type, assignee,
                                  holder, needs_owner, labels, source, external_ref, created_at,
                                  created_by, updated_at, started_at, closed_at, close_reason, archived)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tid, entry["slug"], obj.get("title") or "", obj.get("description") or "",
                    obj.get("acceptance_criteria") or "", obj.get("design") or "",
                    obj.get("notes") or "", (obj.get("close_reason") or "") if obj.get("status") == "done" else "",
                    _status(obj.get("status")), stage, None,
                    int(obj.get("priority") or 2), obj.get("issue_type") or "task",
                    _actor(conn, obj.get("assignee")), None, 0,
                    json.dumps(labels, ensure_ascii=False), "beads", tid,
                    obj.get("created_at"), obj.get("created_by"), obj.get("updated_at"),
                    obj.get("started_at"), obj.get("closed_at"), obj.get("close_reason"),
                    0,
                ),
            )
            store._index_task(conn, tid)  # noqa: SLF001 — импорт идёт мимо обычного API
            imported += 1
            report["tasks"] += 1

            for c in obj.get("comments") or []:
                cid = c.get("id") or f"{tid}:{c.get('created_at')}"
                conn.execute(
                    "INSERT INTO comments(id, task_id, author, kind, text, created_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                    (cid, tid, c.get("author"),
                     "journal" if "[listik]" in (c.get("text") or "") else "comment",
                     c.get("text") or "", c.get("created_at")),
                )
                store._index_comment(conn, cid)  # noqa: SLF001
                report["comments"] += 1

            for d in obj.get("dependencies") or []:
                dep_id = d.get("depends_on_id") or d.get("depends_on")
                if not dep_id:
                    continue
                conn.execute(
                    "INSERT INTO deps(issue_id, depends_on, dep_type, created_at, created_by) "
                    "VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
                    (tid, dep_id, d.get("type") or "blocks", d.get("created_at"), d.get("created_by")),
                )
                report["deps"] += 1

        if not dry_run:
            conn.execute(
                "INSERT INTO projects(slug, title, kind, path, imported_at, import_note) "
                "VALUES(?,?,'beads',?,?,?) ON CONFLICT(slug) DO UPDATE SET "
                "kind='beads', path=excluded.path, imported_at=excluded.imported_at, "
                "import_note=excluded.import_note",
                (entry["slug"], entry["slug"], entry["path"], store.now_iso(),
                 f"{imported} задач из beads"),
            )
        report["per_project"].append({"slug": entry["slug"], "tasks": imported,
                                      "memories": len(entry["memories"])})
        if verbose:
            print(f"  {entry['slug']}: {imported} задач")
        report["memories"] += len(entry["memories"])

    # Проекты без задач — тоже регистрируем, чтобы было видно пустые трекеры
    for entry in keep:
        if entry["issues"] or dry_run:
            continue
        conn.execute(
            "INSERT INTO projects(slug, title, kind, path, imported_at, import_note) "
            "VALUES(?,?,'beads',?,?,?) ON CONFLICT(slug) DO NOTHING",
            (entry["slug"], entry["slug"], entry["path"], store.now_iso(), "пустой трекер"),
        )

    if not dry_run:
        conn.execute("UPDATE projects SET kind='beads' WHERE imported_at IS NOT NULL")
        store.recompute_blocked(conn)
        db_mod.seed_actors(conn)
        conn.commit()
    return report


def _labels(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            return [x.strip() for x in raw.split(",") if x.strip()]
    return []


def _status(raw: str | None) -> str:
    return {"open": "open", "in_progress": "in_progress", "blocked": "blocked",
            "deferred": "open", "closed": "done"}.get((raw or "open").lower(), "open")


def _stage_from(labels: list[str], comments: list[dict]) -> str | None:
    stage = None
    for lb in labels:
        low = lb.strip().lower()
        if low.startswith("pl:") and low[3:] in ("s1-spec", "s2-review", "s3-impl", "s4-judge", "done"):
            stage = low[3:]
    for c in comments:
        for line in (c.get("text") or "").splitlines():
            line = line.strip()
            if line.startswith("[listik]") and "stage=" in line:
                for token in line.split():
                    if token.startswith("stage="):
                        val = token.split("=", 1)[1]
                        if val in ("s1-spec", "s2-review", "s3-impl", "s4-judge", "done"):
                            stage = val
    return stage


def _actor(conn: sqlite3.Connection, raw: str | None) -> str | None:
    from . import actors as actors_mod

    key, kind = actors_mod.resolve(raw, conn)
    if raw:
        actors_mod.remember(conn, raw, key, kind)
    return key
