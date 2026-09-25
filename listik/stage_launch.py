"""Этапный запуск роя (`driver="swarm"`): расклад этап↔роль, разбор ответа
(последней строки вывода) харнесса, переходы и порции. Протокол и контракт —
`docs/specs/swarm-stage-launch.md`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from pathlib import Path

from . import actors as actors_mod
from . import errors as errors_mod
from . import harnesses_store
from . import store
from . import store_helpers

#: Этап → роль в раскладе маршрута. Порядок списка — порядок прохода карточки.
STAGE_ROLES: tuple[tuple[str, str], ...] = (
    ("s1-spec", "spec"),
    ("s2-review", "critic"),
    ("s3-impl", "impl"),
    ("s4-judge", "judge"),
)
STAGE_TO_ROLE = dict(STAGE_ROLES)

#: Ответы (последняя строка вывода), которые принимает этап. Чужая строка — как пустой ответ.
#: `вопрос`/`не смог` годятся на любом этапе, включая приёмку; `зелёный`/`красный`
#: — только у судьи, `готово` — только не у него.
ANSWERS: dict[str, frozenset[str]] = {
    "spec": frozenset({"готово", "вопрос", "не смог"}),
    "critic": frozenset({"готово", "вопрос", "не смог"}),
    "impl": frozenset({"готово", "вопрос", "не смог"}),
    "judge": frozenset({"вопрос", "не смог", "зелёный", "красный"}),
}

#: Сколько читаем с конца `.out` (ответ + текст вопроса/правок над ним).
OUT_READ_LIMIT = 64 * 1024
#: Хвост stdout в текст вопроса человеку и в тело `VERDICT: FAIL`.
TAIL_LIMIT = 4000
#: Хвост, который можно прицепить второй строкой к «не сдал работу».
FAIL_TAIL_LIMIT = 1000
#: Сколько оставить от строки, обрезанной началом окна `.out`: мегабайт склейки
#: не должен вытеснить из `TAIL_LIMIT` строки ближе к ответу.
CUT_LINE_KEEP = 500

SWARM_ACTOR = "agent:listik"


# ------------------------------------------------------------------ расклад

def is_swarm(record: dict | None) -> bool:
    """Маршрут едет роем: `driver=swarm` (у `kind=swarm` он такой всегда)."""
    return bool(record) and record.get("driver") == "swarm"


def role_of_stage(stage: str | None) -> str | None:
    """Роль этапа; пустой этап незапущенной карточки — это `s1-spec`."""
    return STAGE_TO_ROLE.get((stage or "").strip() or "s1-spec")


def _cell_argv(cell: dict, harness_record: dict | None) -> list | None:
    """argv роли: `argv` ячейки `kind=swarm`, `command` ячейки конвейера с
    `driver=swarm`, иначе argv харнесса по умолчанию."""
    argv = cell.get("argv") or cell.get("command")
    if not argv and harness_record:
        argv = harness_record.get("argv")
    return argv or None


def _cell_prompt(cell: dict) -> str:
    """Промпт роли — последний аргумент argv: свой `prompt` ячейки, иначе
    `SWARM_PROMPT` (протокол ответа последней строкой). `prompt` харнесса не наследуем:
    это текст прямой выдачи — он велит самому делать claim/stage/done, чего
    харнесс роя делать не должен (docs/specs/swarm-stage-launch.md)."""
    prompt = cell.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt
    return harnesses_store.SWARM_PROMPT


def resolve_role(conn: sqlite3.Connection, record: dict, role: str | None) -> dict | None:
    """Роль этапа в виде `{role, harness, argv, prompt}`; None — роли нет.

    Роль есть, когда у ячейки задан `harness` и находится команда: свой argv
    (`argv` у `kind=swarm`, `command` у конвейера с `driver=swarm`) или argv
    харнесса по умолчанию. Ячейка без харнесса и харнесс без команды —
    «нет роли с командой», это не сбой маршрута: этап пропускается.
    """
    if not role:
        return None
    roles = record.get("roles") or {}
    cell = roles.get(role)
    if not isinstance(cell, dict):
        return None
    harness = (cell.get("harness") or "").strip() if isinstance(cell.get("harness"), str) else ""
    if not harness:
        return None
    harness_record = harnesses_store.by_key(conn, harness)
    argv = _cell_argv(cell, harness_record)
    if not argv:
        return None
    return {"role": role, "harness": harness, "argv": list(argv),
            "prompt": _cell_prompt(cell)}


def next_stage_with_role(conn: sqlite3.Connection, record: dict,
                         stage: str | None) -> str | None:
    """Ближайший этап после `stage`, у которого есть роль с командой."""
    current = (stage or "").strip() or "s1-spec"
    stages = [s for s, _ in STAGE_ROLES]
    try:
        start = stages.index(current) + 1
    except ValueError:
        start = 0
    for next_stage, role in STAGE_ROLES[start:]:
        if resolve_role(conn, record, role) is not None:
            return next_stage
    return None


def has_roles(conn: sqlite3.Connection, record: dict) -> bool:
    """Есть ли у маршрута хоть одна роль с командой."""
    return any(resolve_role(conn, record, role) is not None for _, role in STAGE_ROLES)


# ------------------------------------------------------------------ порции

def portions(conn: sqlite3.Connection, task_id: str) -> list:
    """Не отменённые дети по `parent-child` — порции нарезки `s1-spec`."""
    try:
        return conn.execute(
            "SELECT t.id, t.status, t.stage, t.holder, t.launched_by, t.launch_route "
            "FROM deps d JOIN tasks t ON t.id = d.issue_id "
            "WHERE d.depends_on = ? AND d.dep_type IN ('parent-child','parent') "
            "AND t.status != 'cancelled' ORDER BY t.created_at, t.rowid",
            (task_id,)).fetchall()
    except sqlite3.OperationalError:
        return []


def has_portions(conn: sqlite3.Connection, task_id: str) -> bool:
    return bool(portions(conn, task_id))


def portion_statuses(conn: sqlite3.Connection, task_id: str) -> list:
    """Статусы всех детей `parent-child`, включая отменённых."""
    try:
        return [row["status"] for row in conn.execute(
            "SELECT t.status FROM deps d JOIN tasks t ON t.id = d.issue_id "
            "WHERE d.depends_on = ? AND d.dep_type IN ('parent-child','parent')",
            (task_id,)).fetchall()]
    except sqlite3.OperationalError:
        return []


def portions_cancelled_only(conn: sqlite3.Connection, row) -> bool:
    """Нарезка была, но каждая порция `cancelled` и ни одной `done`
    (docs/specs/swarm-stage-launch.md): такого родителя рой не запускает —
    на нём вопрос человеку. Признак гаснет, как только родитель ушёл с
    `s1-spec` (этап позже — значит, нарезку уже разобрали)."""
    statuses = portion_statuses(conn, row["id"])
    if not statuses or not all(status == "cancelled" for status in statuses):
        return False
    return (row["stage"] or "").strip() in ("", "s1-spec")


def _slice_parent(conn: sqlite3.Connection, task_id: str, record: dict,
                  children: list) -> None:
    """`готово` от `spec`, и порции уже заведены: родитель дальше не идёт.

    Родитель остаётся на `s1-spec` без держателя; каждой ещё не начатой порции —
    маршрут родителя (если своего нет), `launch_driver='swarm'` и первый этап
    после `s1-spec`, у которого есть роль. `suggested-blocks` между порциями
    этого родителя подтверждаются в жёсткие `blocks`.
    """
    ids = ", ".join(row["id"] for row in children)
    store.add_comment(conn, task_id,
                      f"рой: нарезано на {ids}, родитель дальше по ролям не идёт",
                      author=SWARM_ACTOR, kind="journal")
    first = next_stage_with_role(conn, record, "s1-spec")
    if first is None:
        store.set_needs_owner(
            conn, task_id, value=True, actor=SWARM_ACTOR,
            text="рой: порции заведены, но после s1-spec роли нет")
        return
    portion_ids = {row["id"] for row in children}
    parent_scope = conn.execute("SELECT write_scope FROM tasks WHERE id = ?",
                                (task_id,)).fetchone()
    parent_scope = store_helpers.json_list(parent_scope["write_scope"]) \
        if parent_scope else []
    for row in children:
        # Уже начатую порцию (этап — любой, включая `s1-spec`, держатель или
        # запуск) не переписываем (docs/specs/swarm-stage-launch.md): маршрут
        # карточке с этапом уже не записать (`route_change_denied`).
        if row["status"] != "open" or (row["stage"] or "").strip() \
                or (row["holder"] or "").strip() or (row["launched_by"] or "").strip():
            continue
        # Маршрут меняется, только пока карточка «просто заведена» (без этапа,
        # держателя и запуска) — поэтому до записи этапа и отдельной правкой.
        if not (row["launch_route"] or "").strip():
            store.update_task(conn, row["id"], actor=SWARM_ACTOR,
                              launch_route=record["key"],
                              note=f"рой: порция нарезки {task_id}, "
                                   f"маршрут {record['key']}")
        store.update_task(conn, row["id"], actor=SWARM_ACTOR,
                          stage=first, holder="",
                          note=f"рой: порция нарезки {task_id}, этап {first}")
        # `launch_driver` не в UPDATABLE — её пишет только лаунчер/нарезка.
        # Порция ещё не начата, но снимок у неё мог остаться от прошлого
        # захвата — переписываем на `swarm` (docs/specs/swarm-stage-launch.md).
        conn.execute("UPDATE tasks SET launch_driver = 'swarm' WHERE id = ?",
                     (row["id"],))
        # Порция без своей области пишет туда же, что родитель: иначе после
        # `s2-review` рой повесит на неё вопрос `unscoped`. Свою область не трогаем.
        own_scope = conn.execute("SELECT write_scope FROM tasks WHERE id = ?",
                                 (row["id"],)).fetchone()["write_scope"]
        if parent_scope and not store_helpers.json_list(own_scope):
            store.update_task(conn, row["id"], actor=SWARM_ACTOR, write_scope=parent_scope,
                              note=f"рой: write_scope родителя {task_id}")
            store.add_comment(conn, row["id"], f"write_scope унаследован от {task_id}",
                              author=SWARM_ACTOR, kind="journal")
    # Порядок порций: предложения агента между порциями этого родителя
    # подтверждаем жёсткими `blocks`; чужие рёбра и рёбра наружу не трогаем.
    marks = ",".join("?" * len(portion_ids))
    links = conn.execute(
        f"SELECT issue_id, depends_on FROM deps WHERE dep_type = 'suggested-blocks' "
        f"AND issue_id IN ({marks}) AND depends_on IN ({marks})",
        (*portion_ids, *portion_ids)).fetchall()
    for link in links:
        store.add_dep(conn, link["issue_id"], link["depends_on"], "blocks",
                      created_by=SWARM_ACTOR, confirm=True)


def merge_single_portion(conn: sqlite3.Connection, parent_id: str, *, file: str,
                         checklist: str | None = None, child_id: str | None = None,
                         actor: str | None = None, harness: str | None = None) -> None:
    """Одна порция — дочерней карточки нет, путь `s1→s4` проходит сам родитель
    (listik-zr05, порция e): его `spec_path` — файл порции, ребёнок (если был)
    переводится на `discovered-from` и отменяется. Идемпотентна."""
    row = store_helpers.task_row(conn, parent_id)
    old = (row["spec_path"] or "").strip()
    same = bool(old) and os.path.realpath(old) == os.path.realpath(file)
    child = None
    if child_id:
        child = next((c for c in store.child_cards(conn, parent_id)
                      if c["id"] == child_id), None)
    if same and child is None:
        return
    base = store.project_path(conn, row["project"])
    fields = {"spec_path": file}
    if checklist and _abs_path(base, checklist).is_file():
        fields["checklist_path"] = checklist
    if child is not None and not store_helpers.json_list(row["write_scope"]):
        child_scope = conn.execute("SELECT write_scope FROM tasks WHERE id = ?",
                                   (child["id"],)).fetchone()["write_scope"]
        if store_helpers.json_list(child_scope):
            fields["write_scope"] = store_helpers.json_list(child_scope)
    match = re.match(rf"^{re.escape(parent_id)}\.([a-z])\.md$", os.path.basename(file))
    letter = match.group(1) if match else os.path.basename(file)
    store.update_task(conn, parent_id, actor=actor, harness=harness,
                      note=f"одна порция {letter}", **fields)
    if old and not same:
        store.add_comment(conn, parent_id, f"ТЗ шага: {old}", author=actor,
                          harness=harness, kind="journal")
    store.add_comment(conn, parent_id, f"одна порция {letter} — ведёт сам родитель",
                      author=actor, harness=harness, kind="journal")
    if child is None:
        return
    # Сначала связь: `sync_epic` вернёт тип родителя до отмены ребёнка.
    for dep_type in _PARENT_TYPES:
        store.remove_dep(conn, child["id"], parent_id, dep_type)
    store.add_dep(conn, child["id"], parent_id, "discovered-from", created_by=actor)
    if child["status"] not in store.FINAL_STATUSES:
        store.update_task(conn, child["id"], actor=actor, harness=harness,
                          status="cancelled", close_reason="слита в родителя",
                          note=f"одна порция: слита в {parent_id}")


def _single_fresh(conn: sqlite3.Connection, children: list):
    """Единственная порция, не начатая и с `spec_path`, — её сливают в родителя."""
    if len(children) != 1 or _started(children[0]):
        return None
    row = conn.execute("SELECT id, spec_path, checklist_path FROM tasks WHERE id = ?",
                       (children[0]["id"],)).fetchone()
    return row if (row["spec_path"] or "").strip() else None


def is_swarm_task(conn: sqlite3.Connection, row) -> bool:
    """Карточка едет роем: снимок `launch_driver`, а до первого запуска — живой
    `driver` её маршрута."""
    snapshot = row["launch_driver"] if "launch_driver" in row.keys() else None
    if snapshot:
        return snapshot == "swarm"
    route_key = (row["launch_route"] or "").strip()
    if not route_key:
        return False
    try:
        from . import routes_store
        return is_swarm(routes_store.get_route(conn, route_key))
    except Exception:  # noqa: BLE001 — маршрут могли удалить: считаем не-роем
        return False


# ------------------------------------------------------------------ ответ этапа

def out_path_of(launch_log: str | None) -> Path | None:
    """Файл stdout рядом с `launch_log`: суффикс `.out` вместо `.log`."""
    if not launch_log:
        return None
    path = Path(launch_log)
    if path.suffix == ".log":
        return path.with_suffix(".out")
    return Path(str(path) + ".out")


def read_answer(path: Path | None) -> tuple[str, str]:
    """Ответ этапа — последняя непустая строка stdout — и текст над ним.

    Маркер в конце, как вердикт: харнессы вроде `devin -p` печатают в stdout
    промежуточные реплики без переводов строки, и начало вывода склеено
    (listik-utw9). Читаем последние 64 КиБ, UTF-8 с заменой ошибок. Строка,
    обрезанная началом окна, ответом не бывает, но в тексте над ним остаётся
    её хвост (`CUT_LINE_KEEP`). Нет файла или пусто — `("", "")`.
    """
    if path is None:
        return "", ""
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            start = max(0, size - OUT_READ_LIMIT)
            # Байт левее окна — перевод строки: первая строка окна целая.
            cut = False
            if start:
                fh.seek(start - 1)
                cut = fh.read(1) not in (b"\n", b"\r")
            fh.seek(start)
            raw = fh.read(OUT_READ_LIMIT)
    except OSError:
        return "", ""
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    if cut and lines:
        if len(lines) == 1:
            return "", ""
        lines[0] = lines[0][-CUT_LINE_KEEP:]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return "", ""
    return lines[-1].strip(), "\n".join(lines[:-1])


def _release_capture(conn: sqlite3.Connection, task_id: str, dispatch_id) -> None:
    """Снять `launched_by`/`dispatch_id` — исход роя разобран, запуск кончился."""
    conn.execute("UPDATE tasks SET launched_by = NULL, dispatch_id = NULL "
                 "WHERE id = ? AND dispatch_id IS ?", (task_id, dispatch_id))


def _clear_holder(conn: sqlite3.Connection, task_id: str, note: str) -> None:
    row = conn.execute("SELECT holder FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is not None and (row["holder"] or "").strip():
        store.update_task(conn, task_id, actor=SWARM_ACTOR, holder="", note=note)


def apply_outcome(conn: sqlite3.Connection, task_id: str, *, notify=None) -> None:
    """Разобрать последнюю строку `.out` завершившегося процесса и повести карточку.

    Вызывается из `_track`/`recover`/опросчика после записи кода выхода —
    только когда `dispatch_id` ещё наш (ограждение делает вызывающий). Любой
    исход заканчивается снятием `launched_by`/`dispatch_id`, чтобы рой не видел
    завершённый запуск не-`done` как сбой режима скила.
    """
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return
    dispatch_id = row["dispatch_id"]
    launch_log = row["launch_log"]
    stage = (row["stage"] or "").strip() or "s1-spec"
    role = STAGE_TO_ROLE.get(stage)
    first, tail = read_answer(out_path_of(launch_log))
    tail = tail[-TAIL_LIMIT:]
    route_key = (row["launch_route"] or "").strip()
    record = None
    if route_key:
        try:
            from . import routes_store
            record = routes_store.get_route(conn, route_key)
        except Exception:  # noqa: BLE001 — маршрут могли удалить
            record = None
    try:
        if role is None:
            # Этап без роли (done и т.п.) — разбирать нечего.
            store.add_comment(conn, task_id,
                              f"рой: этап {stage or '—'} без роли — ответ процесса "
                              f"не применяю (последняя строка: {first or 'пусто'})",
                              author=SWARM_ACTOR, kind="journal")
            return
        allowed = ANSWERS.get(role, frozenset())
        if first == "готово" and first in allowed:
            children = portions(conn, task_id)
            if stage == "s1-spec" and role == "spec" and children:
                single = _single_fresh(conn, children)
                if single is None:
                    _clear_holder(conn, task_id, "рой: нарезка, держатель снят")
                    _slice_parent(conn, task_id, record or {"key": route_key,
                                                          "roles": {}}, children)
                    return
                # Одна не начатая порция — её ведёт сам родитель, дальше как без нарезки.
                merge_single_portion(conn, task_id, file=single["spec_path"],
                                     checklist=single["checklist_path"],
                                     child_id=single["id"], actor=SWARM_ACTOR)
            nxt = next_stage_with_role(conn, record or {}, stage) if record else None
            if nxt is not None:
                # Одним next_stage: handoff снимает держателя сам, а sticky
                # (s3-impl→s4-judge) оставит — тогда снимаем отдельно.
                store.next_stage(conn, task_id, to_stage=nxt, actor=SWARM_ACTOR,
                                 note=f"рой: ответ «готово», этап {stage} → {nxt}")
                _clear_holder(conn, task_id,
                              "рой: ответ «готово», держатель снят")
                store.add_comment(
                    conn, task_id,
                    f"рой: ответ «готово», этап {stage} → {nxt}, держатель снят",
                    author=SWARM_ACTOR, kind="journal")
            else:
                _clear_holder(conn, task_id, "рой: роли кончились, держатель снят")
                store.set_needs_owner(
                    conn, task_id, value=True, actor=SWARM_ACTOR,
                    text=f"рой: после {stage} роли нет, карточку не закрываю. "
                         "Сними флаг — запущу тот же этап снова.")
        elif first == "зелёный" and role == "judge":
            store.add_comment(conn, task_id, "VERDICT: PASS", author=SWARM_ACTOR,
                              kind="verdict")
            store.update_task(conn, task_id, actor=SWARM_ACTOR,
                              stage="done", status="done", holder="",
                              note="рой: ответ «зелёный», карточка закрыта")
            store.add_comment(conn, task_id,
                              "рой: ответ «зелёный», карточка закрыта",
                              author=SWARM_ACTOR, kind="journal")
        elif first == "красный" and role == "judge":
            body = tail.strip() or (
                f"1. приёмка ответила «красный» без списка правок — "
                f"лог {launch_log} — разбери лог и поправь")
            store.add_comment(conn, task_id, f"VERDICT: FAIL\n{body}",
                              author=SWARM_ACTOR, kind="verdict")
            _clear_holder(conn, task_id, "рой: красный вердикт, держатель снят")
            store.add_comment(conn, task_id,
                              "рой: ответ «красный», возврат на s3-impl, держатель снят",
                              author=SWARM_ACTOR, kind="journal")
        elif first == "вопрос" and first in allowed:
            text = tail.strip() or (
                f"рой: этап {stage} ({role}) ответил «вопрос» без текста. "
                f"Лог: {launch_log}")
            _clear_holder(conn, task_id, "рой: вопрос человеку, держатель снят")
            store.set_needs_owner(conn, task_id, value=True, text=text,
                                  actor=SWARM_ACTOR)
        else:
            # `не смог`, пустой вывод и любая чужая строка — один исход.
            shown = first or "пусто"
            text = (f"рой: этап {stage} ({role}) не сдал работу "
                    f"(последняя строка: {shown}). Лог: {launch_log}")
            rest = tail.strip()[-FAIL_TAIL_LIMIT:]
            if rest:
                text += f"\n{rest}"
            _clear_holder(conn, task_id, "рой: работа не сдана, держатель снят")
            store.set_needs_owner(conn, task_id, value=True, text=text,
                                  actor=SWARM_ACTOR)
    except Exception as exc:  # noqa: BLE001 — исход не потерять: вопрос человеку
        try:
            store.set_needs_owner(
                conn, task_id, value=True, actor=SWARM_ACTOR,
                text=f"рой: не разобрал исход этапа: {errors_mod.message_of(exc)}")
        except Exception:  # noqa: BLE001 — соединение могло умереть вместе с исходом
            pass
    finally:
        _release_capture(conn, task_id, dispatch_id)
        conn.commit()


# ------------------------------------------------------------------ перезапуск

#: Поля запуска, которые `restart_task` сбрасывает в NULL.
LAUNCH_FIELDS = ("launched_by", "launch_pid", "launched_at", "launch_log",
                 "launch_exit_code", "launch_finished_at", "launch_error", "dispatch_id")
#: Пути порции, которые уходят в архив перезапуска.
_PORTION_PATHS = ("spec_path", "checklist_path", "decision_path")
#: Собственные пути родителя: их файлы не переносятся, даже если имя подходит.
_OWN_PATHS = ("spec_path", "checklist_path", "decision_path", "journal_path", "review_path")
_PARENT_TYPES = ("parent-child", "parent")


def _conflict(message: str, hint: str = "") -> errors_mod.ListikError:
    """409: подсказка и в тексте (по HTTP `hint` не уходит), и в `hint`."""
    text = f"{message}; {hint}" if hint else message
    return errors_mod.ListikError(text, code=errors_mod.CONFLICT, hint=hint, status=409)


def _started(child) -> bool:
    """Порция начата — как в `_slice_parent`; `cancelled` начатой не считается."""
    if child["status"] == "cancelled":
        return False
    return bool(child["status"] != "open" or (child["stage"] or "").strip()
                or (child["holder"] or "").strip() or (child["launched_by"] or "").strip())


def _abs_path(base: str, value: str) -> Path:
    """Путь карточки: относительный — от `projects.path` проекта, не от cwd."""
    path = Path(value).expanduser()
    if not path.is_absolute() and base:
        path = Path(base) / path
    return Path(os.path.abspath(path))


def _archive_no(step_dir: Path, task_id: str, path: Path) -> int | None:
    """Номер архива, если `path` уже лежит в `<dir>/<id>.restart-K/`."""
    if path.parent.parent != step_dir:
        return None
    match = re.fullmatch(re.escape(task_id) + r"\.restart-(\d+)", path.parent.name)
    return int(match.group(1)) if match else None


def _step_files(step_dir: Path, task_id: str, own: set, base: str, child) -> list:
    """`(поле, значение, путь)` путей порции этого шага: в каталоге шага с именем
    `<id>.…` (не собственный путь родителя) или уже в архиве перезапуска."""
    out = []
    for field in _PORTION_PATHS:
        value = (child[field] or "").strip()
        if not value:
            continue
        path = _abs_path(base, value)
        in_step = (path.parent == step_dir and path.name.startswith(f"{task_id}.")
                   and path not in own)
        if in_step or _archive_no(step_dir, task_id, path) is not None:
            out.append((field, value, path))
    return out


def _children(conn: sqlite3.Connection, row, step_dir: Path | None, own: set,
              base: str) -> tuple[list, list]:
    """`(дети по parent-child/parent, осиротевшие порции)` в порядке создания.

    Осиротевшие — дети по `discovered-from` с файлом порции этого шага (закрытые —
    только с путём ещё в каталоге шага): прошлый перезапуск упал после смены связей
    (идемпотентность).
    Без каталога шага их не узнать — тогда только дети по `parent-child`.
    """
    task_id = row["id"]
    linked = conn.execute(
        "SELECT DISTINCT t.*, t.rowid AS rid FROM deps d JOIN tasks t ON t.id = d.issue_id "
        "WHERE d.depends_on = ? AND d.dep_type IN (?,?) ORDER BY t.created_at, t.rowid",
        (task_id, *_PARENT_TYPES)).fetchall()
    orphans = []
    if step_dir is not None:
        ids = {child["id"] for child in linked}
        for child in conn.execute(
                "SELECT DISTINCT t.*, t.rowid AS rid FROM deps d JOIN tasks t ON t.id = d.issue_id "
                "WHERE d.depends_on = ? AND d.dep_type = 'discovered-from' "
                "ORDER BY t.created_at, t.rowid", (task_id,)).fetchall():
            if child["id"] in ids:
                continue
            found = _step_files(step_dir, task_id, own, base, child)
            if child["status"] in store.FINAL_STATUSES:
                # Закрытую — только с путём ещё в каталоге шага: порции прошлых
                # удачных перезапусков уже смотрят в свой архив.
                found = [f for f in found
                         if _archive_no(step_dir, task_id, f[2]) is None]
            if found:
                orphans.append(child)
    return list(linked), orphans


def _plan_archive(row, children: list, step_dir: Path, own: set, base: str,
                  parent_spec: Path) -> tuple[Path | None, list, list]:
    """Что делать с файлами: `(архив, переносы, переписи путей)`.

    Переносы — `(src, dst)`; переписи — `(child_id, поле, новое значение)`. Файл,
    которого нет на месте, но есть в последнем архиве, — уже перенесён (повтор после
    сбоя): архив тогда тот же `K`, и оставшиеся файлы доносятся туда же. Ничего не
    меняет на диске: конфликт имён отказывает до первого переноса.
    """
    task_id = row["id"]
    numbers = []
    if step_dir.is_dir():
        for entry in step_dir.iterdir():
            number = _archive_no(step_dir, task_id, entry / "x")
            if number is not None and entry.is_dir():
                numbers.append(number)
    last = max(numbers, default=0)
    reuse: set[int] = set()
    pending = []  # (child_id, field, value, path, уже перенесён в K | None)
    for child in children:
        for field, value, path in _step_files(step_dir, task_id, own, base, child):
            number = _archive_no(step_dir, task_id, path)
            if number is not None:
                reuse.add(number)  # путь уже переписан на архив
                continue
            if path.exists():
                pending.append((child["id"], field, value, path, None))
            elif last and (step_dir / f"{task_id}.restart-{last}" / path.name).exists():
                reuse.add(last)
                pending.append((child["id"], field, value, path, last))
            # файла нет нигде — путь оставляем как есть
    need_copy = parent_spec.is_file()
    if not pending and not reuse and not need_copy:
        return None, [], []
    number = max(reuse) if reuse else last + 1
    archive = step_dir / f"{task_id}.restart-{number}"
    moves, rewrites, seen = [], [], {}
    for child_id, field, value, path, _moved in pending:
        dst = archive / path.name
        if path.exists() and path not in seen:
            if dst.exists():
                raise _conflict(f"в архиве {archive} уже есть {path.name}",
                                "разберись с файлами вручную и повтори listik restart")
            seen[path] = dst
            moves.append((path, dst))
        new = str(dst) if Path(value).is_absolute() else str(
            Path(value).parent / archive.name / path.name)
        rewrites.append((child_id, field, new))
    return archive, moves, rewrites


def restart_task(conn: sqlite3.Connection, task_id: str, *, stage: str | None = None,
                 route: str | None = None, note: str | None = None,
                 actor: str | None = None, harness: str | None = None) -> dict:
    """Перезапустить карточку роя с этапа: «этап выбран, никто не держит, запуска нет».

    Порядок жёсткий: все проверки → перенос файлов → запись в базу. На `s1-spec`
    ещё и снимает порции: связь → `discovered-from`, отмена, файлы — в архив
    `<каталог шага>/<id>.restart-N/`. Рой запустит роль этапа на следующем тике.
    """
    from . import routes_store
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    if row["status"] in store.FINAL_STATUSES:
        raise _conflict(f"задача {task_id} закрыта ({row['status']}): перезапускать нечего")
    if not is_swarm_task(conn, row):
        raise errors_mod.BadArgument(
            f"перезапуск только для карточек роя; этап карточки скила переводят: "
            f"listik stage {task_id} --to <этап>")
    if (row["launched_by"] or "").strip() and not (row["launch_finished_at"] or "").strip():
        raise _conflict(f"у задачи {task_id} идёт запуск ({row['launched_by']})",
                        f"сначала отзови его: listik revoke {task_id}")
    route = (route or "").strip() or None
    if route is not None:
        keys = [r["key"] for r in routes_store.list_routes(conn)]
        if route not in keys:
            raise _conflict(f"маршрута {route} нет в базе", f"есть: {', '.join(keys)}")
    target = (stage or "").strip() or (row["stage"] or "").strip() or "s1-spec"
    if target not in STAGE_TO_ROLE:
        raise errors_mod.BadArgument(
            f"этап {target} не перезапускается; есть: {', '.join(STAGE_TO_ROLE)}")
    children_all = store.epic_children(conn, task_id)
    if children_all and target != "s1-spec":
        ids = ", ".join(c["id"] for c in children_all)
        raise _conflict(f"эпик: перезапускай подзадачи: {ids}",
                        "listik restart <id подзадачи>")
    route_key = route or (row["launch_route"] or "").strip()
    try:
        record = routes_store.get_route(conn, route_key) if route_key else {}
    except errors_mod.NotFound:
        record = {}
    if resolve_role(conn, record, STAGE_TO_ROLE[target]) is None:
        with_role = [s for s, r in STAGE_ROLES if resolve_role(conn, record, r) is not None]
        raise _conflict(f"у маршрута {route_key or '—'} нет роли этапа {target}",
                        f"роль есть у этапов: {', '.join(with_role) or 'ни у одного'}")

    base = store.project_path(conn, row["project"])
    spec = (row["spec_path"] or "").strip()
    parent_spec = _abs_path(base, spec) if spec else None
    step_dir = parent_spec.parent if parent_spec else None
    own = {_abs_path(base, row[f]) for f in _OWN_PATHS if (row[f] or "").strip()}
    linked, orphans = ([], [])
    if target == "s1-spec":
        linked, orphans = _children(conn, row, step_dir, own, base)
        started = [c["id"] for c in linked if _started(c)]
        if started:
            raise _conflict(f"порции уже начаты: {', '.join(started)}",
                            "перезапусти их по отдельности или закрой")
    children = sorted([*linked, *orphans], key=lambda c: (c["created_at"] or "", c["rid"]))
    archive = None
    moves: list = []
    rewrites: list = []
    if children and parent_spec is not None:
        archive, moves, rewrites = _plan_archive(row, children, step_dir, own, base,
                                                 parent_spec)

    # --- файлы: перенос и копия ТЗ родителя (до записи в базу)
    if archive is not None:
        archive.mkdir(parents=True, exist_ok=True)
        for src, dst in moves:
            shutil.move(str(src), str(dst))
        copy = archive / parent_spec.name
        if parent_spec.is_file() and not copy.exists():
            shutil.copy2(parent_spec, copy)

    # --- база
    actor_key, _kind = actors_mod.resolve(actor, conn)
    route_from = None
    if route is not None:
        route_from = row["launch_route"]
        labels = store.labels_after_route_change(
            conn, store.route_labels_from_row(row), route)
        if labels is None:
            labels = store.route_labels_from_row(row)
        conn.execute("UPDATE tasks SET launch_route = ?, labels = ?, launch_driver = NULL, "
                     "updated_at = ? WHERE id = ?",
                     (route, json.dumps(labels, ensure_ascii=False), store.now_iso(), task_id))
        store.event(conn, task_id, "route", from_value=route_from, to_value=route,
                    actor=actor_key, harness=harness, note="перезапуск")
        store._index_task(conn, task_id)
        conn.commit()
        store.add_comment(conn, task_id, f"маршрут: {route_from or '—'} → {route}",
                          author=actor, kind="journal", harness=harness)

    if children:
        # Сначала связи всех детей: иначе `sync_epic` отменил бы родителя, у
        # которого все подзадачи отменены. Закрытых детей отвязываем первыми —
        # пока остаются незакрытые, эпик не отменится и при частичном снятии.
        for child in sorted(children, key=lambda c: c["status"] not in store.FINAL_STATUSES):
            store.add_dep(conn, child["id"], task_id, "discovered-from", created_by=actor)
            for dep_type in _PARENT_TYPES:
                if conn.execute("SELECT 1 FROM deps WHERE issue_id = ? AND depends_on = ? "
                                "AND dep_type = ?", (child["id"], task_id, dep_type)).fetchone():
                    store.remove_dep(conn, child["id"], task_id, dep_type)
        # Повтор после сбоя в `sync_epic` последнего `remove_dep`: тип эпика вернуть.
        store.sync_epic(conn, task_id)
        for child_id, field, value in rewrites:
            store.update_task(conn, child_id, actor=actor, harness=harness,
                              note=f"перезапуск {task_id}: файл в архиве", **{field: value})
        reason = f"рой: перезапуск {task_id} с s1-spec, порция снята"
        for child in children:
            fresh = conn.execute("SELECT status FROM tasks WHERE id = ?",
                                 (child["id"],)).fetchone()
            if fresh["status"] not in store.FINAL_STATUSES:
                store.update_task(conn, child["id"], actor=actor, harness=harness,
                                  status="cancelled", close_reason=reason, note=reason)
        if parent_spec is None:
            store.add_comment(conn, task_id, "архив не сделан: у родителя нет spec_path",
                              author=actor, kind="journal", harness=harness)

    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if (row["launch_log"] or "").strip():
        store.add_comment(conn, task_id, f"прежний лог запуска: {row['launch_log']}",
                          author=actor, kind="journal", harness=harness)
    fields = {}
    if (row["stage"] or "") != target:
        fields["stage"] = target
    if (row["holder"] or "").strip():
        fields["holder"] = ""
    if fields:
        store.update_task(conn, task_id, actor=actor, harness=harness,
                          note=f"рой: перезапуск с {target}", **fields)
    if row["needs_owner"]:
        store.set_needs_owner(conn, task_id, value=False, actor=actor, harness=harness,
                              text=f"рой: вопрос снят перезапуском с {target}")
    conn.execute(f"UPDATE tasks SET {', '.join(f + ' = NULL' for f in LAUNCH_FIELDS)} "
                 "WHERE id = ?", (task_id,))
    conn.commit()
    text = f"рой: перезапуск с {target}"
    detached = [c["id"] for c in children]
    if detached:
        text += f", порции сняты: {', '.join(detached)}"
        if archive is not None:
            text += f", файлы в {archive}"
    text += "."
    if (note or "").strip():
        text += f" {note.strip()}"
    store.add_comment(conn, task_id, text, author=actor, kind="journal", harness=harness)
    out = store.get_task(conn, task_id)
    out.update(restarted_from=target, detached=detached,
               archive_dir=str(archive) if archive is not None else None)
    if route is not None:
        out["route_from"] = route_from
    return out


def adopt_portions(conn: sqlite3.Connection, task_id: str, *, actor: str | None = None,
                   harness: str | None = None) -> dict:
    """«Принять нарезку» (`listik portions adopt`): порции заведены, но исход
    `s1-spec` не засчитан — тот же `_slice_parent` с маршрутом родителя."""
    from . import routes_store
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    if row["status"] in store.FINAL_STATUSES:
        raise _conflict(f"задача {task_id} закрыта ({row['status']}): принимать нечего")
    if not is_swarm_task(conn, row):
        raise _conflict(f"задача {task_id} не роя: порции принимает только рой",
                        f"порции скила ведут сами: listik show {task_id}")
    stage_now = (row["stage"] or "").strip()
    if stage_now not in ("", "s1-spec"):
        raise _conflict(f"задача {task_id} на этапе {stage_now}: нарезку принимают на s1-spec",
                        f"вернуть на нарезку: listik restart {task_id} --stage s1-spec")
    route_key = (row["launch_route"] or "").strip()
    try:
        record = routes_store.get_route(conn, route_key) if route_key else None
    except errors_mod.NotFound:
        record = None
    if record is None:
        raise _conflict(f"маршрута {route_key or '—'} нет в базе",
                        f"задай маршрут: listik set {task_id} route=<ключ>")
    before = portions(conn, task_id)
    single = _single_fresh(conn, before)
    if single is not None:
        merge_single_portion(conn, task_id, file=single["spec_path"],
                             checklist=single["checklist_path"], child_id=single["id"],
                             actor=actor, harness=harness)
        stage = next_stage_with_role(conn, record, "s1-spec")
        if stage is not None:
            store.next_stage(conn, task_id, to_stage=stage, actor=actor, harness=harness,
                             note=f"одна порция слита, этап s1-spec → {stage}")
            _clear_holder(conn, task_id, "рой: одна порция слита, держатель снят")
            if conn.execute("SELECT needs_owner FROM tasks WHERE id = ?",
                            (task_id,)).fetchone()["needs_owner"]:
                store.set_needs_owner(conn, task_id, value=False, actor=actor,
                                      harness=harness,
                                      text="порция слита в родителя: listik portions adopt")
        else:
            _clear_holder(conn, task_id, "рой: роли кончились, держатель снят")
            store.set_needs_owner(
                conn, task_id, value=True, actor=SWARM_ACTOR,
                text="рой: после s1-spec роли нет, карточку не закрываю. "
                     "Сними флаг — запущу тот же этап снова.")
        conn.commit()
        out = store.get_task(conn, task_id)
        out.update(adopted=[], stage=stage, merged=True)
        return out
    fresh = [c["id"] for c in before if not _started(c)]
    if not fresh:
        raise _conflict(f"у задачи {task_id} нет не начатых порций",
                        f"нарезать заново: listik restart {task_id} --stage s1-spec")
    stage = next_stage_with_role(conn, record, "s1-spec")
    adopted = fresh if stage is not None else []
    _clear_holder(conn, task_id, "рой: порции приняты, держатель снят")
    _slice_parent(conn, task_id, record, portions(conn, task_id))
    if stage is not None and conn.execute(
            "SELECT needs_owner FROM tasks WHERE id = ?", (task_id,)).fetchone()["needs_owner"]:
        store.set_needs_owner(conn, task_id, value=False, actor=actor, harness=harness,
                              text="порции приняты: listik portions adopt")
    conn.commit()
    out = store.get_task(conn, task_id)
    out.update(adopted=adopted, stage=stage, merged=False)
    return out
