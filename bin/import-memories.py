#!/usr/bin/env python3
"""Импорт памятей (bd remember) из старых .beads-экспортов в таблицу memories.

Запуск: python3 bin/import-memories.py
Ключ памяти: <project>/<key> — чтобы одинаковые ключи разных проектов не сталкивались.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from listik import db, paths, store  # noqa: E402


def main() -> int:
    conn = db.init()
    file = Path(paths.HUB_DIR) / "migrate"
    files = sorted(Path(paths.PROJECTS_ROOT).glob("*/.beads/issues.jsonl"))
    files += sorted(Path(paths.PROJECTS_ROOT).glob("*/*/.beads/issues.jsonl"))
    files += sorted(Path(paths.PROJECTS_ROOT).glob("*/*/*/.beads/issues.jsonl"))
    added = 0
    for path in sorted(set(files)):
        project = str(path.parent.parent.relative_to(paths.PROJECTS_ROOT))
        for line in path.open(encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("_type") != "memory":
                continue
            key = str(obj.get("key") or "").strip()
            body = str(obj.get("value") or obj.get("content") or "").strip()
            if not key or not body:
                continue
            full_key = f"{project}/{key}"
            exists = conn.execute("SELECT 1 FROM memories WHERE key = ?", (full_key,)).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO memories(key, project, body, source, created_at, updated_at) "
                "VALUES(?,?,?, 'beads', ?, ?)",
                (full_key, project, body, obj.get("created_at") or store.now_iso(),
                 obj.get("updated_at") or obj.get("created_at") or store.now_iso()),
            )
            conn.execute("DELETE FROM memory_fts WHERE memory_key = ?", (full_key,))
            conn.execute("INSERT INTO memory_fts(memory_key, body) VALUES(?,?)", (full_key, body))
            added += 1
    conn.commit()
    total = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
    print(f"добавлено памятей: {added}, всего: {total} (файл {file.name if False else 'listik.db'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
