"""Идемпотентный импорт задач из выгрузок WriterLLM (старый трекер на dolt).

Источник — `bd export` (JSONL, одна запись на строку) или `bd list --json`
(JSON-массив без `comments`/`_type`). Ключ идемпотентности —
`(tasks.source='writerllm', tasks.project, tasks.external_ref)`: повторный запуск на
том же файле не создаёт дублей задач, дописывает недостающие связи и комментарии, но
не переписывает уже импортированные поля (`--update` появится отдельной порцией).

`dry_run=True` не должен приводить ни к одной записи в базу — см. `import_file` и
комментарии по коду: любой SQL кроме `SELECT` и любой вызов `store.*`, который сам
делает `INSERT/UPDATE/commit`, стоит за проверкой `if not dry_run`.

`--project` сверяется с уже стоящими на доске slug'ами без учёта регистра
(`store.existing_slug`): выгрузка ложится в существующий проект, а не заводит дубль
`writerllm`/`WriterLLM` (listik-ovjr).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import actors as actors_mod
from . import store

logger = logging.getLogger("listik.import_writerllm")

_STATUS_MAP = {
    "open": "open",
    "in_progress": "in_progress", "inprogress": "in_progress",
    "doing": "in_progress", "in-progress": "in_progress",
    "blocked": "blocked",
    "review": "review",
    "closed": "done", "done": "done", "complete": "done", "completed": "done",
    "cancelled": "cancelled", "canceled": "cancelled",
    "deferred": "open", "todo": "open", "pending": "open",
}

_PRIORITY_NAMES = {"urgent": 0, "critical": 0, "high": 1, "normal": 2, "medium": 2, "low": 3}

# Поля содержания, которые сравниваются в режиме --update, и сырые ключи, по наличию
# которых в исходной записи определяется, участвует ли поле в сравнении вообще
# (PATCH-семантика: отсутствующий в выгрузке ключ не стирает значение в Listik).
_UPDATE_FIELD_SOURCE_KEYS = {
    "title": ("title", "name"),
    "description": ("description", "body", "content"),
    "acceptance": ("acceptance_criteria", "acceptance", "criteria"),
    "design": ("design", "design_notes"),
    "notes": ("notes", "note"),
    "result": ("result", "outcome"),
    "status": ("status", "state"),
    "priority": ("priority", "importance"),
    "issue_type": ("issue_type", "type"),
    "assignee": ("assignee", "assigned_to"),
    "labels": ("labels", "tags"),
    "close_reason": ("close_reason",),
}

_DIFF_VALUE_MAX = 200


# ------------------------------------------------------------------ чтение источника

def _load_records(path: Path) -> tuple[list[tuple[int, dict | None, str | None, Any]], str | None]:
    """Разбирает `path` в список сырых записей.

    Возвращает (entries, source_error). Каждый элемент entries — это
    (line, raw_dict_or_None, error_or_None, raw_for_report); при ошибке `raw_dict_or_None`
    равен None, а `raw_for_report` содержит исходную строку/значение для отчёта.
    source_error не None, если сам файл нечитаем/не JSON целиком (каталог, отсутствие,
    не-JSON) — тогда entries пуст и total=0.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], str(exc)

    entries: list[tuple[int, dict | None, str | None, Any]] = []
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                entries.append((number, None, f"invalid JSON on line {number}: {exc.msg}", line))
                continue
            if not isinstance(value, dict):
                entries.append((number, None, "record is not an object", line))
                continue
            entries.append((number, value, None, value))
        return entries, None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], f"invalid JSON: {exc.msg}"
    if isinstance(data, dict):
        for key in ("tasks", "issues", "records", "items"):
            if key in data:
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return [], "top-level JSON must be an object or array"
    for index, value in enumerate(data):
        if not isinstance(value, dict):
            entries.append((index, None, "record is not an object", value))
            continue
        entries.append((index, value, None, value))
    return entries, None


# ------------------------------------------------------------------ вспомогательные разборы

def _first_nonempty(raw: dict, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key not in raw:
            continue
        value = raw[key]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default


def _as_list(value: Any) -> list:
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _hash_external_ref(source_path: Path, raw: dict) -> str:
    try:
        resolved = source_path.expanduser().resolve()
    except OSError:
        resolved = source_path.expanduser()
    norm_path = re.sub(r"[\\/]+", "/", str(resolved)).lower()
    title = str(_first_nonempty(raw, "title", "name", default="") or "")
    norm_title = re.sub(r"\s+", " ", title.strip()).lower()
    digest = hashlib.sha256((norm_path + "\0" + norm_title).encode("utf-8")).hexdigest()
    return "writerllm:" + digest[:24]


def _map_status(raw_value: Any) -> tuple[str, bool]:
    if raw_value is None:
        return "open", False
    key = str(raw_value).strip().lower()
    if not key:
        return "open", False
    if key in _STATUS_MAP:
        return _STATUS_MAP[key], False
    return "open", True


def _map_priority(raw_value: Any) -> tuple[int, bool]:
    if raw_value is None:
        return 2, False
    if isinstance(raw_value, bool):
        return 2, True
    if isinstance(raw_value, (int, float)):
        ival = int(raw_value)
        return (ival, False) if 0 <= ival <= 4 else (2, True)
    text = str(raw_value).strip().lower()
    if not text:
        return 2, False
    if text in _PRIORITY_NAMES:
        return _PRIORITY_NAMES[text], False
    if re.fullmatch(r"-?\d+", text):
        ival = int(text)
        return (ival, False) if 0 <= ival <= 4 else (2, True)
    return 2, True


def _map_labels(raw_value: Any) -> list[str]:
    if isinstance(raw_value, list):
        return [str(x).strip() for x in raw_value if str(x).strip()]
    if isinstance(raw_value, str) and raw_value.strip():
        return [p.strip() for p in re.split(r"[,;]", raw_value) if p.strip()]
    return []


def _parse_dependency_item(item: Any) -> tuple[str | None, str, str | None]:
    if isinstance(item, dict):
        target = _first_nonempty(item, "depends_on_id", "depends_on", "external_ref",
                                  "external_id", "dependency", "id", "task_id")
        dep_type = _first_nonempty(item, "type", "dep_type", "relation", default="blocks")
        created_by = item.get("created_by")
    elif isinstance(item, str):
        target, dep_type, created_by = item, "blocks", None
    else:
        target, dep_type, created_by = None, "blocks", None
    target = str(target).strip() if target is not None and str(target).strip() else None
    return target, str(dep_type), created_by


def _parse_comment_item(item: Any) -> dict | None:
    if isinstance(item, str):
        text, author, created_at, cid, raw_kind = item, None, None, None, None
    elif isinstance(item, dict):
        text = _first_nonempty(item, "text", "body")
        author = _first_nonempty(item, "author", "created_by")
        created_at = _first_nonempty(item, "created_at", "timestamp")
        cid = _first_nonempty(item, "id")
        raw_kind = item.get("kind")
    else:
        return None
    if not text:
        return None
    text = str(text)
    kind = str(raw_kind) if raw_kind else ("journal" if "[listik]" in text else "comment")
    return {"id": str(cid).strip() if cid else None, "author": author, "text": text,
            "created_at": created_at, "kind": kind}


def _resolve_actor(conn: sqlite3.Connection, raw_value: Any, dry_run: bool) -> str | None:
    if not raw_value:
        return None
    key, kind = actors_mod.resolve(raw_value, conn)
    if not dry_run:
        actors_mod.remember(conn, raw_value, key, kind)
    return key


# ------------------------------------------------------------------ построение record

def _build_record(raw: dict, line: int, source_path: Path, conn: sqlite3.Connection,
                   dry_run: bool) -> tuple[dict | None, list[dict], list[dict]]:
    """Приводит сырую запись к внутреннему record.

    Возвращает (record, warnings, errors). `record` — None, если запись целиком
    не годится (пустой заголовок); ошибки внутри `errors` при этом уже полностью
    заполнены (where/line/external_ref/target_ref/error/raw), их достаточно
    добавить в отчёт. Ошибки пустых целей связи не обнуляют record — они лишь
    выбрасывают эту одну пару зависимостей.
    """
    warnings: list[dict] = []
    errors: list[dict] = []

    ext_raw = _first_nonempty(raw, "external_ref", "external_id", "id", "task_id", "uuid")
    external_ref = str(ext_raw).strip() if ext_raw is not None else _hash_external_ref(source_path, raw)

    title_raw = _first_nonempty(raw, "title", "name")
    title = str(title_raw).strip() if title_raw is not None else ""
    if not title:
        errors.append({"where": "record", "line": line, "external_ref": external_ref,
                       "target_ref": None, "error": "empty title", "raw": raw})
        return None, warnings, errors

    status_raw = _first_nonempty(raw, "status", "state")
    status, status_warn = _map_status(status_raw)
    if status_warn:
        warnings.append({"external_ref": external_ref, "field": "status",
                         "value": status_raw, "used": status})

    priority_raw = _first_nonempty(raw, "priority", "importance")
    priority, priority_warn = _map_priority(priority_raw)
    if priority_warn:
        warnings.append({"external_ref": external_ref, "field": "priority",
                         "value": priority_raw, "used": priority})

    close_reason = _first_nonempty(raw, "close_reason")
    result = _first_nonempty(raw, "result", "outcome")
    if not result and status in ("done", "cancelled"):
        result = close_reason
    result = str(result) if result is not None else ""

    assignee = _resolve_actor(conn, _first_nonempty(raw, "assignee", "assigned_to"), dry_run)
    created_by = _resolve_actor(conn, _first_nonempty(raw, "created_by", "author"), dry_run)
    holder = _resolve_actor(conn, _first_nonempty(raw, "holder", "current_holder"), dry_run)

    dependencies: list[dict] = []
    for item in _as_list(_first_nonempty(raw, "dependencies", "depends_on", "deps",
                                          "blocked_by", default=[])):
        target, dep_type, dep_created_by = _parse_dependency_item(item)
        if not target:
            errors.append({"where": "dependency", "line": line, "external_ref": external_ref,
                           "target_ref": None, "error": "empty dependency reference", "raw": item})
            continue
        dependencies.append({"target": target, "dep_type": dep_type, "created_by": dep_created_by})

    comments: list[dict] = []
    for item in _as_list(_first_nonempty(raw, "comments", "journal_entries", "history",
                                          default=[])):
        parsed = _parse_comment_item(item)
        if parsed:
            comments.append(parsed)

    record = {
        "external_ref": external_ref,
        "title": title,
        "description": str(_first_nonempty(raw, "description", "body", "content", default="") or ""),
        "acceptance": str(_first_nonempty(raw, "acceptance_criteria", "acceptance", "criteria",
                                          default="") or ""),
        "design": str(_first_nonempty(raw, "design", "design_notes", default="") or ""),
        "notes": str(_first_nonempty(raw, "notes", "note", default="") or ""),
        "result": result,
        "status": status,
        "priority": priority,
        "issue_type": str(_first_nonempty(raw, "issue_type", "type", default="task")),
        "assignee": assignee,
        "created_by": created_by,
        "labels": _map_labels(_first_nonempty(raw, "labels", "tags", default=[])),
        "created_at": _first_nonempty(raw, "created_at"),
        "updated_at": _first_nonempty(raw, "updated_at", "modified_at"),
        "started_at": _first_nonempty(raw, "started_at"),
        "closed_at": _first_nonempty(raw, "closed_at", "completed_at", "finished_at"),
        "close_reason": close_reason,
        "holder": holder,
        "holder_at": _first_nonempty(raw, "holder_at"),
        "holder_note": _first_nonempty(raw, "holder_note"),
        "spec_path": _first_nonempty(raw, "spec_path", "spec"),
        "journal_path": _first_nonempty(raw, "journal_path", "journal", "log_path"),
        "checklist_path": _first_nonempty(raw, "checklist_path", "checklist"),
        "dependencies": dependencies,
        "comments": comments,
        "raw": raw,
        "line": line,
    }
    return record, warnings, errors


# ------------------------------------------------------------------ комментарии

def _comment_id(task_id: str, text: str, created_at_from_record: str | None) -> str:
    digest = hashlib.sha256((text + "\0" + (created_at_from_record or "")).encode("utf-8")).hexdigest()
    return f"{task_id}:wl:{digest[:16]}"


def _find_existing_comment(conn: sqlite3.Connection, task_id: str, text: str,
                           cid: str, item_created_at: str | None) -> bool:
    if conn.execute("SELECT 1 FROM comments WHERE id = ?", (cid,)).fetchone():
        return True
    dup = conn.execute("SELECT created_at FROM comments WHERE task_id = ? AND text = ?",
                       (task_id, text)).fetchone()
    if dup is not None and (not item_created_at or item_created_at == dup["created_at"]):
        return True
    return False


def _count_new_comments(conn: sqlite3.Connection, task_id: str, record: dict) -> int:
    """Только чтение — сколько из комментариев record ещё не в базе у task_id."""
    count = 0
    for item in record["comments"]:
        text = item["text"]
        cid = item["id"] or _comment_id(task_id, text, item["created_at"])
        if not _find_existing_comment(conn, task_id, text, cid, item["created_at"]):
            count += 1
    return count


def _write_comments(conn: sqlite3.Connection, task_id: str, record: dict) -> int:
    inserted = 0
    for item in record["comments"]:
        text = item["text"]
        item_created_at = item["created_at"]
        cid = item["id"] or _comment_id(task_id, text, item_created_at)
        if _find_existing_comment(conn, task_id, text, cid, item_created_at):
            continue
        author_key = _resolve_actor(conn, item["author"], dry_run=False)
        created_at = item_created_at or record["created_at"] or store.now_iso()
        conn.execute(
            "INSERT INTO comments(id, task_id, author, kind, text, created_at) VALUES(?,?,?,?,?,?)",
            (cid, task_id, author_key, item["kind"], text, created_at),
        )
        store._index_comment(conn, cid)  # noqa: SLF001 — импорт идёт мимо обычного API
        inserted += 1
    return inserted


# ------------------------------------------------------------------ --update: diff содержания

def _content_diff(existing_row: sqlite3.Row, record: dict, raw: dict) -> dict[str, tuple]:
    """{field: (было, стало)} только для полей содержания, у которых в `raw` есть
    хотя бы один исходный ключ (см. `_UPDATE_FIELD_SOURCE_KEYS`) и значение отличается
    от того, что уже в базе."""
    diff: dict[str, tuple] = {}
    for field, keys in _UPDATE_FIELD_SOURCE_KEYS.items():
        if not any(key in raw for key in keys):
            continue
        new = record[field]
        if field == "labels":
            old_raw = existing_row["labels"]
            try:
                old_list = json.loads(old_raw) if old_raw else []
            except (TypeError, ValueError):
                old_list = []
            new_list = new if isinstance(new, list) else []
            if old_list == new_list:
                continue
            diff[field] = (old_list, new_list)
        elif field == "priority":
            old_val = existing_row["priority"]
            try:
                old_int = int(old_val) if old_val is not None else None
            except (TypeError, ValueError):
                old_int = old_val
            if old_int == new:
                continue
            diff[field] = (old_val, new)
        else:
            old_val = existing_row[field]
            old_str = "" if old_val is None else str(old_val)
            new_str = "" if new is None else str(new)
            if old_str == new_str:
                continue
            diff[field] = (old_val, new)
    return diff


def _diff_display(value: Any) -> str:
    if isinstance(value, list):
        text = ", ".join(str(v) for v in value) if value else "—"
    elif value is None or value == "":
        text = "—"
    else:
        text = str(value)
    if len(text) > _DIFF_VALUE_MAX:
        text = text[:_DIFF_VALUE_MAX] + "…"
    return text


def _journal_text(diff: dict[str, tuple]) -> str:
    lines = ["[import-from-bd] update:"]
    for field, (old, new) in diff.items():
        lines.append(f"{field}: {_diff_display(old)} → {_diff_display(new)}")
    return "\n".join(lines)


# ------------------------------------------------------------------ отчёт

def _add_example(report: dict, action: str, payload: dict) -> None:
    bucket = report["examples"][action]
    if len(bucket) < 5:
        bucket.append(payload)


def _push_error(report: dict, entry: dict) -> None:
    report["errors"].append(entry)
    logger.error("writerllm import: %s: %s (external_ref=%s, line=%s)",
                entry["where"], entry["error"], entry["external_ref"], entry["line"])
    _add_example(report, "error", {"external_ref": entry["external_ref"], "where": entry["where"],
                                    "error": entry["error"]})


# ------------------------------------------------------------------ основной вход

def import_file(conn: sqlite3.Connection, path: str | Path, *, project: str | None = None,
                dry_run: bool = False, update: bool = False) -> dict:
    source_path = Path(path).expanduser()
    slug = str(project) if project else "writerllm"
    # Slug — ключ проекта, и сверка идёт без учёта регистра: `--project WriterLLM` при уже
    # стоящем на доске `writerllm` пишет задачи в него, а не заводит дубль (listik-ovjr).
    # Заодно не рвётся идемпотентность: ключ — (source, project, external_ref), и второй
    # запуск с другим регистром находит те же задачи, а не создаёт их заново.
    slug = store.existing_slug(conn, slug) or slug
    try:
        source_display = str(source_path.resolve())
    except OSError:
        source_display = str(source_path)

    report: dict[str, Any] = {
        "source": source_display, "project": slug, "dry_run": dry_run, "update": update,
        "total": 0, "created": 0, "updated": 0, "skipped": 0, "ignored": 0,
        "dependencies": 0, "comments": 0, "errors": [], "warnings": [],
        "examples": {"create": [], "update": [], "skip": [], "error": []},
    }

    entries, source_error = _load_records(source_path)
    if source_error is not None:
        _push_error(report, {"where": "source", "line": None, "external_ref": None,
                             "target_ref": None, "error": source_error, "raw": None})
        report["ok"] = not report["errors"]
        return report

    report["total"] = len(entries)
    mapping: dict[str, str] = {}
    prepared: list[dict] = []

    for line, raw, err, raw_for_report in entries:
        if err is not None:
            _push_error(report, {"where": "record", "line": line, "external_ref": None,
                                 "target_ref": None, "error": err, "raw": raw_for_report})
            continue

        rec_type = raw.get("_type")
        if rec_type is not None and rec_type != "issue":
            report["ignored"] += 1
            continue

        record, warnings, build_errors = _build_record(raw, line, source_path, conn, dry_run)
        report["warnings"].extend(warnings)
        if record is None:
            for entry in build_errors:
                _push_error(report, entry)
            continue
        for entry in build_errors:
            _push_error(report, entry)

        ext = record["external_ref"]
        existing = conn.execute(
            "SELECT * FROM tasks WHERE source='writerllm' AND project=? AND external_ref=?",
            (slug, ext)).fetchone()

        if existing:
            tid = existing["id"]
            mapping[ext] = tid
            diff = _content_diff(existing, record, raw) if update else {}
            if diff:
                report["updated"] += 1
                if not dry_run:
                    try:
                        fields = {field: new for field, (_old, new) in diff.items()}
                        store.update_task(conn, tid, actor=record["created_by"],
                                          note="импорт WriterLLM --update", **fields)
                        store.add_comment(conn, tid, _journal_text(diff),
                                         author=record["created_by"], kind="journal")
                    except Exception as exc:  # noqa: BLE001 — импорт продолжается
                        _push_error(report, {"where": "record", "line": line,
                                             "external_ref": ext, "target_ref": None,
                                             "error": str(exc), "raw": raw_for_report})
                _add_example(report, "update", {"id": tid, "external_ref": ext,
                                                "title": record["title"],
                                                "fields": list(diff.keys())})
            else:
                report["skipped"] += 1
                _add_example(report, "skip", {"id": tid, "external_ref": ext, "title": record["title"]})
            try:
                if dry_run:
                    report["comments"] += _count_new_comments(conn, tid, record)
                else:
                    report["comments"] += _write_comments(conn, tid, record)
                    conn.commit()
            except Exception as exc:  # noqa: BLE001 — импорт продолжается, ошибка фиксируется
                _push_error(report, {"where": "comment", "line": line, "external_ref": ext,
                                     "target_ref": None, "error": str(exc), "raw": raw_for_report})
            prepared.append({"record": record, "task_id": tid})
            continue

        old_id = ext
        collision = bool(conn.execute("SELECT 1 FROM tasks WHERE id=?", (old_id,)).fetchone())
        tid = store.gen_id(conn, slug, prefix=slug) if collision else old_id
        mapping[ext] = tid
        report["created"] += 1
        example_entry = {"id": tid, "external_ref": ext, "title": record["title"]}
        if collision:
            example_entry["remapped"] = True
        _add_example(report, "create", example_entry)

        if dry_run:
            report["comments"] += len(record["comments"])
            prepared.append({"record": record, "task_id": tid})
            continue

        try:
            store.create_task(
                conn, task_id=tid, project=slug, source="writerllm", external_ref=ext,
                created_at=record["created_at"], created_by=record["created_by"],
                title=record["title"], description=record["description"],
                acceptance=record["acceptance"], design=record["design"], notes=record["notes"],
                result=record["result"], status=record["status"], priority=record["priority"],
                issue_type=record["issue_type"], assignee=record["assignee"],
                labels=record["labels"], spec_path=record["spec_path"],
                journal_path=record["journal_path"], checklist_path=record["checklist_path"],
            )
            direct_updates = {k: record[k] for k in
                              ("started_at", "closed_at", "close_reason", "holder",
                               "holder_at", "holder_note") if record[k]}
            if direct_updates:
                sets = ", ".join(f"{k} = ?" for k in direct_updates)
                conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?",
                            (*direct_updates.values(), tid))
        except Exception as exc:  # noqa: BLE001 — запись не создана, следующая обрабатывается
            report["created"] -= 1
            mapping.pop(ext, None)
            report["examples"]["create"] = [e for e in report["examples"]["create"]
                                            if e.get("id") != tid]
            _push_error(report, {"where": "record", "line": line, "external_ref": ext,
                                 "target_ref": None, "error": str(exc), "raw": raw_for_report})
            continue

        try:
            report["comments"] += _write_comments(conn, tid, record)
        except Exception as exc:  # noqa: BLE001 — задача уже создана, ошибка фиксируется отдельно
            _push_error(report, {"where": "comment", "line": line, "external_ref": ext,
                                 "target_ref": None, "error": str(exc), "raw": raw_for_report})

        if record["updated_at"]:
            conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (record["updated_at"], tid))
        store._index_task(conn, tid)  # noqa: SLF001 — импорт идёт мимо обычного API
        store.event(conn, tid, "import", actor=record["created_by"],
                   note=f"writerllm {ext} из {source_path}",
                   ts=record["updated_at"] or record["created_at"])
        conn.commit()
        prepared.append({"record": record, "task_id": tid})

    # Связи — вторым проходом, после того как все задачи известны (в т.ч. пропущенные).
    for item in prepared:
        record, tid = item["record"], item["task_id"]
        ext = record["external_ref"]
        for dep in record["dependencies"]:
            dep_ext, dep_type, dep_created_by = dep["target"], dep["dep_type"], dep["created_by"]
            target = mapping.get(dep_ext)
            if not target:
                row = conn.execute(
                    "SELECT id FROM tasks WHERE source='writerllm' AND project=? AND external_ref=?",
                    (slug, dep_ext)).fetchone()
                target = row["id"] if row else None
            if not target:
                _push_error(report, {"where": "dependency", "line": record["line"],
                                     "external_ref": ext, "target_ref": dep_ext,
                                     "error": "target task not found", "raw": record["raw"]})
                continue
            exists = conn.execute(
                "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? AND dep_type=?",
                (tid, target, dep_type)).fetchone()
            if dry_run:
                if not exists:
                    report["dependencies"] += 1
                continue
            if exists:
                continue
            try:
                store.add_dep(conn, tid, target, dep_type, created_by=dep_created_by, confirm=True)
                report["dependencies"] += 1
            except ValueError as exc:
                _push_error(report, {"where": "dependency", "line": record["line"],
                                     "external_ref": ext, "target_ref": dep_ext,
                                     "error": str(exc), "raw": record["raw"]})

    if not dry_run:
        store.recompute_blocked(conn)
        conn.execute(
            "INSERT INTO projects(slug, title, kind, imported_at, import_note) "
            "VALUES(?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET "
            "imported_at=excluded.imported_at, import_note=excluded.import_note",
            (slug, slug, "writerllm", store.now_iso(), f"{report['created']} задач из WriterLLM"),
        )
        conn.commit()

    report["ok"] = not report["errors"]
    return report
